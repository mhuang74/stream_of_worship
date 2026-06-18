# Admin R2 Backup & Restore — Implementation Plan v2

## Summary

Add three commands to the existing `sow-admin maintenance` Typer group for backing up
the entire Cloudflare R2 bucket to local **chunked tar archives**, verifying archive
integrity (size + SHA-256), and restoring objects from archives back to R2.

**Commands:**

- `sow-admin maintenance backup-r2 --output DIR [--chunk-size SIZE]` — download every R2
  object into a directory containing a `manifest.json` and one or more `.tar` chunk files.
- `sow-admin maintenance verify-r2-backup --dir DIR` — check every chunk against the
  manifest (file count, sizes, SHA-256 hashes, orphans).
- `sow-admin maintenance restore-r2 --dir DIR [--prefix ...] [--skip-existing] [--confirm]`
  — verify archive, then restore objects to R2 (dry-run by default; overwrite or skip
  existing on apply).

## Motivation

There is currently no built-in `sow-admin` command for R2 disaster recovery. The only
documented backup procedure is a manual `aws s3 sync` between buckets
(`docs/deployment-plan-webapp-v2.md:268`). A CLI-native backup/restore pair enables:

- Disaster recovery to local files without AWS CLI tooling.
- Cross-environment migration (swap config, restore from a backup taken elsewhere).
- Pre-maintenance snapshots before running destructive commands like `purge-r2-waste`
  or `purge-soft-deletes`.

## Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Backup scope | Entire bucket (all objects) | Consistent with v1; selective restore via `--prefix` |
| Archive format | **Directory** containing `manifest.json` + `chunk-NNN.tar` files | TB-scale safety: no single multi-terabyte file, individual chunks can be verified/moved independently |
| Chunking | Configurable `--chunk-size` (default 10 GiB) | Keeps individual files manageable; balances filesystem limits with file count |
| Tar format per chunk | `tarfile.PAX_FORMAT` | No 8 GiB file limit (unlike USTAR); supports long paths |
| Restore conflict handling | Dry-run + `--confirm` + `--skip-existing` | `--skip-existing` protects live data; dry-run is default |
| Restore target | Config bucket only (no `--bucket` override) | Cross-env via config swap (same as v1) |
| Command naming | `backup-r2`, `verify-r2-backup`, `restore-r2` | Consistent with existing `list-r2-waste` / `purge-r2-waste` |
| Manifest | `manifest.json` at archive root; schema version 2 | Separate from tar chunks allows fast metadata access without reading multi-GB files |
| Selective restore | Repeatable `--prefix` flag; default restores everything | Same as v1 |
| Output path | Required `--output DIR` (must not already exist) | Directory is created atomically via `.part` suffix |
| Compression | Uncompressed tar only (audio/flac already compressed) | Same as v1; can be added later via `--gzip` |
| Concurrency | Sequential transfers with Rich progress bar | Same as v1; debuggable and back-pressure friendly |
| Restore pre-check | Always verify archive against manifest before applying | Same as v1 |
| Integrity check | **SHA-256 per object** (computed during streaming download) | Detects bit-rot, truncated streams with matching size, and corrupted downloads |
| Verify CLI | **No `--config` flag** | Pure local operation; avoids confusion |

## Archive Format

### Directory structure

```
<output-dir>/
├── manifest.json              # Metadata + full object list with chunk assignments
├── chunk-000.tar              # Subset of objects (target: ≤ chunk_size_bytes)
├── chunk-001.tar
└── chunk-002.tar
```

Each `chunk-NNN.tar` contains objects stored at paths matching their R2 key (no `objects/`
prefix needed because manifest is outside the tar files):

```
chunk-000.tar
├── abc123def456/
│   ├── audio.mp3
│   ├── lyrics.lrc
│   └── analysis.json
├── renders/
│   └── <job-id>/output.mp4
└── build-dependencies/ffmpeg/...
```

### Manifest schema (`manifest.json`) — Version 2

```json
{
  "version": 2,
  "created_at": "2026-06-18T12:00:00",
  "bucket": "stream-of-worship",
  "endpoint_url": "https://<account>.r2.cloudflarestorage.com",
  "region": "auto",
  "chunk_size_bytes": 10737418240,
  "object_count": 1234,
  "total_bytes": 5678901234,
  "chunk_count": 3,
  "objects": [
    {
      "key": "abc123def456/audio.mp3",
      "size": 5000000,
      "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "chunk_index": 0,
      "etag": "d41d8cd98f00b204e9800998ecf8427e",
      "last_modified": "2024-01-01T00:00:00+00:00"
    }
  ]
}
```

- `version`: manifest schema version (`2` for this revision).
- `chunk_size_bytes`: The target maximum bytes per chunk (actual chunk may exceed slightly
  for individual large objects).
- `objects[].sha256`: SHA-256 of the object content, computed during streaming download.
- `objects[].chunk_index`: Which `chunk-NNN.tar` contains this object.
- `objects[].etag`: R2 ETag (reference only; not used for verification due to multipart
  upload non-MD5 etags).

## Commands

### `backup-r2`

```
sow-admin maintenance backup-r2 --output DIR [--chunk-size SIZE] [-c CONFIG]

Options:
  --output DIR           Output directory (must not already exist)
  --chunk-size SIZE      Target chunk size in bytes (default: 10737418240 = 10 GiB)
  -c, --config PATH      Admin config file path
```

**Flow:**

1. Load config + R2 client via existing `_load_clients` / `_load_r2` helpers.
2. Validate `--output` does not already exist → exit 1 with red error if it does.
3. **Disk space pre-check:**
   - Call `r2_client.list_all_objects()` as a **streaming generator** (yield pages).
   - Sum `size` to estimate total bytes.
   - Check available disk space at `--output` parent directory.
   - Require: `available > total_bytes * 1.1 + 50 MiB` (10% overhead for tar metadata +
     manifest + safety margin).
   - If insufficient → exit 1 with red error showing required vs available space.
4. Create `<output>.part/` directory (`mkdir(parents=True)`).
5. Build manifest dict (version, timestamps, bucket metadata, chunk size).
6. Open `chunk-000.tar` for writing in PAX format.
7. For each object (Rich progress bar: object N/M, bytes downloaded, current chunk):
   - `r2_client.get_object_stream(key)` → `(content_length, body_stream)`.
   - Wrap `body_stream` in a `HashingReader` that computes SHA-256 on the fly.
   - If adding this object would exceed `chunk_size_bytes` and current chunk is not empty:
     close current tar, increment chunk index, open new tar.
   - Create `TarInfo(name=key, size=content_length)`.
   - `tar.addfile(info, hashing_reader)` — streams body directly into tar.
   - Record object metadata (key, size, sha256, chunk_index, etag, last_modified) in manifest.
   - Close the body stream.
8. Close final tar.
9. Write `manifest.json` into `<output>.part/` (atomic write via temp file + rename).
10. Atomically rename `<output>.part/` → `<output>/`.
11. Print summary table: object count, total bytes (MB), chunk count, output directory,
    duration.

**Signal handling:**
- Register `SIGINT`/`SIGTERM` handlers at step 4.
- On interrupt: close open tar, delete `<output>.part/` directory, print "Backup cancelled;
  partial files cleaned up.", exit 130.

**Edge cases:**
- Empty bucket → directory contains only `manifest.json` with `object_count: 0`, no chunk
  files.
- Single object larger than `chunk_size_bytes` → stored in its own chunk (chunk may exceed
  target size for that one object).
- `--output` parent directory doesn't exist → create with `mkdir(parents=True)`.
- R2 list or download error mid-backup → close tar, delete `<output>.part/`, print error,
  exit 1.
- Disk full mid-write → `tarfile` raises `OSError`; catch, clean up `<output>.part/`, exit 1.

### `verify-r2-backup`

```
sow-admin maintenance verify-r2-backup --dir DIR [--format table|json]

Options:
  --dir DIR              Backup directory path (required)
  --format               table|json output (default: table)
```

**Flow:**

1. Validate `--dir` exists and is a directory.
2. Read and parse `manifest.json`. If missing → exit 1.
3. If `manifest.version != 2` → exit 1 with "unsupported manifest version".
4. Build a lookup: `expected = {key: {size, sha256, chunk_index}}`.
5. For each chunk file from `0` to `chunk_count - 1`:
   - Verify `chunk-NNN.tar` exists.
   - Open tar for reading.
   - Iterate tar members:
     - Check member size matches expected size.
     - Read member content and compute SHA-256, compare to expected.
     - If key not in expected → record as orphan.
     - Remove from expected set.
   - Close tar.
   - Print per-chunk progress (Rich progress bar or spinner).
6. Any remaining expected keys → record as missing.
7. Print result:
   - If no errors: green "OK: N objects verified across M chunks, X bytes".
   - If errors: red table of errors (missing / size-mismatch / hash-mismatch / orphan),
     exit 1.

**Does not touch R2 or require credentials** — pure local file verification.

**Edge cases:**
- Missing chunk file → error type `missing_chunk`.
- Corrupt tar (Python `tarfile` raises `tarfile.TarError`) → error type `corrupt_chunk`.
- Empty backup directory with valid manifest (object_count: 0) → OK.

### `restore-r2`

```
sow-admin maintenance restore-r2 --dir DIR [--prefix PREFIX ...] [--skip-existing]
                                 [--confirm] [--format table|json] [-c CONFIG]

Options:
  --dir DIR              Backup directory path (required)
  --prefix PREFIX        Restore only keys starting with this prefix (repeatable)
  --skip-existing        Skip objects that already exist in R2 (default: overwrite)
  --confirm              Apply restore (dry-run by default)
  --format               table|json output (default: table)
  -c, --config PATH      Admin config file path
```

**Flow:**

1. Load config + R2 client via `_load_clients` / `_load_r2`.
2. **Verify archive** (run `verify-r2-backup` logic inline). If verification fails →
   print errors, exit 1. Do not proceed.
3. Read manifest, filter objects by `--prefix` (if any provided).
4. **Dry-run** (default): for each filtered object, determine action:
   - `"restore"` — object does not exist in R2 or `--skip-existing` is not set.
   - `"skip"` — object exists in R2 and `--skip-existing` is set.
   Print manifest via `_print_manifest` helper. Print yellow
   "Dry run only. Re-run with --confirm to apply."
5. **Apply** (`--confirm`): for each object to restore (Rich progress bar):
   - If `--skip-existing` and object exists in R2 (head check) → record as skipped, continue.
   - Open `chunk-{chunk_index}.tar`, `tar.extractfile(key)` → file-like object.
   - `r2_client.upload_fileobj(fileobj, key)` → uploads to R2.
   - Record success or failure.
6. Print summary: restored count, skipped count, total bytes, any failures.

**Edge cases:**
- `--prefix` matches nothing → print "no objects matched any prefix", exit 0.
- `--skip-existing` with no matching new objects → all skipped, exit 0.
- Upload fails for one object → record failure, continue with remaining, exit 1 at end.
- Archive verification fails → abort before any R2 mutation.
- Object exists in R2 but differs in size → still overwritten unless `--skip-existing`
  (restore does not compare content; that's the user's responsibility).

## Implementation

### 1. R2Client additions (`src/stream_of_worship/admin/services/r2.py`)

Add four methods to the `R2Client` class (after existing `upload_bytes` at line 516):

```python
def list_all_objects(self) -> Iterator[list[dict]]:
    """Yield pages of object metadata from the bucket.

    Uses the list_objects_v2 paginator with 1000-object pages.
    Memory-efficient for buckets with millions of objects.

    Yields:
        Lists of dicts: {"key": str, "size": int, "etag": str|None,
                          "last_modified": str|None}
    """

def get_object_stream(self, s3_key: str) -> tuple[int, object]:
    """Return (content_length, body_stream) for streaming download.

    Caller is responsible for reading and closing body_stream.

    Args:
        s3_key: Full S3 key (path within bucket)

    Returns:
        Tuple of (content_length, body_stream) where body_stream is a
        botocore StreamingBody

    Raises:
        ClientError: On non-404 errors (permission, credential, network).
    """

def upload_fileobj(self, fileobj, s3_key: str) -> str:
    """Upload a file-like object to R2 under the given key (overwrites if exists).

    Args:
        fileobj: Readable file-like object (e.g. tar extractfile result)
        s3_key: Full S3 key (path within bucket)

    Returns:
        S3-style URL of the uploaded object
    """

def head_object(self, s3_key: str) -> dict | None:
    """Check if an object exists in R2 and return metadata.

    Args:
        s3_key: Full S3 key

    Returns:
        Dict with "size", "etag", "last_modified" if exists; None if not found.

    Raises:
        ClientError: On non-404 errors.
    """
```

These are thin wrappers over `boto3` paginator / `get_object` / `upload_fileobj` /
`head_object`, consistent with existing methods.

### 2. New service module (`src/stream_of_worship/admin/services/r2_backup.py`)

Holds the tar/manifest/verify/restore logic, keeping `maintenance.py` from bloating.

**Constants:**
```python
MANIFEST_VERSION = 2
MANIFEST_NAME = "manifest.json"
DEFAULT_CHUNK_SIZE_BYTES = 10 * 1024 * 1024 * 1024  # 10 GiB
TAR_FORMAT = tarfile.PAX_FORMAT
```

**Classes / Functions:**

```python
class HashingReader:
    """Wraps a readable stream to compute SHA-256 and total bytes on the fly."""

    def __init__(self, stream: object) -> None:
        ...

    def read(self, size: int = -1) -> bytes:
        ...

    def digest(self) -> str:
        """Return hex-encoded SHA-256 of all bytes read so far."""

    def total_bytes(self) -> int:
        """Return total bytes consumed."""


def build_manifest(bucket: str, endpoint_url: str, region: str,
                   chunk_size_bytes: int) -> dict:
    """Initialize the manifest dict with metadata (object list populated later)."""


def write_backup(r2_client: R2Client, output: Path, chunk_size_bytes: int,
                 console: Console) -> dict:
    """Create the chunked backup: list objects, write manifest, stream-download each.

    Writes to `<output>.part/` and atomically renames to `<output>/` on success.
    Registers signal handlers to clean up partial directory on interrupt.

    Args:
        r2_client: Initialized R2Client
        output: Output directory path (must not exist)
        chunk_size_bytes: Target bytes per tar chunk
        console: Rich Console for progress output

    Returns:
        Summary dict: {object_count, total_bytes, chunk_count, output_path,
                       duration_seconds}
    """


def verify_archive(dir_path: Path, console: Console) -> tuple[bool, list[dict]]:
    """Verify backup directory against manifest.

    Checks every chunk tar: size and SHA-256 for each object, orphan detection,
    missing chunk detection.

    Args:
        dir_path: Path to the backup directory
        console: Rich Console for progress output

    Returns:
        Tuple of (ok, errors) where errors is a list of dicts:
        {"type": "missing"|"size_mismatch"|"hash_mismatch"|"orphan"|"missing_chunk"
                  |"corrupt_chunk",
         "key": str, "chunk_index": int|None,
         "expected": str|int|None, "actual": str|int|None}
    """


def restore_from_archive(r2_client: R2Client, dir_path: Path,
                         prefixes: list[str], skip_existing: bool,
                         confirm: bool, console: Console) -> list[dict]:
    """Verify, filter by prefixes, dry-run or apply restore.

    Args:
        r2_client: Initialized R2Client
        dir_path: Path to the backup directory
        prefixes: List of key prefixes to filter (empty = all)
        skip_existing: If True, skip objects that already exist in R2
        confirm: If True, upload objects to R2; if False, dry-run only
        console: Rich Console for progress output

    Returns:
        List of manifest rows: {key, size, action, status}
        where action is "restore" or "skip", status is "ok", "failed", or "skipped".
    """
```

### 3. Maintenance commands (`src/stream_of_worship/admin/commands/maintenance.py`)

Three thin command functions decorated with `@app.command(...)`, reusing existing helpers
(`_load_clients`, `_load_r2`, `_print_manifest`, `_validate_choice`).

```python
@app.command("backup-r2")
def backup_r2(
    output: Path = typer.Option(..., "--output", help="Output directory (must not exist)"),
    chunk_size: int = typer.Option(10 * 1024**3, "--chunk-size", help="Target chunk size in bytes (default: 10 GiB)"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Back up the entire R2 bucket to local chunked tar archives."""
    ...

@app.command("verify-r2-backup")
def verify_r2_backup(
    dir: Path = typer.Option(..., "--dir", help="Backup directory path"),
    format_: str = typer.Option("table", "--format", help="table|json"),
) -> None:
    """Verify a backup directory against its manifest."""
    ...

@app.command("restore-r2")
def restore_r2(
    dir: Path = typer.Option(..., "--dir", help="Backup directory path"),
    prefixes: list[str] = typer.Option([], "--prefix", help="Restore only keys with this prefix (repeatable)"),
    skip_existing: bool = typer.Option(False, "--skip-existing", help="Skip objects that already exist in R2"),
    confirm: bool = typer.Option(False, "--confirm"),
    format_: str = typer.Option("table", "--format", help="table|json"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Restore objects from a backup directory to R2."""
    ...
```

No changes to `main.py` needed (commands added to existing `maintenance` group via decorator).

## Files to Modify

| File | Changes |
|---|---|
| `src/stream_of_worship/admin/services/r2.py` | Add `list_all_objects()` (generator), `get_object_stream()`, `upload_fileobj()`, `head_object()` to `R2Client` |
| `src/stream_of_worship/admin/services/r2_backup.py` | **New file.** Chunked tar logic, manifest build, verify, restore, `HashingReader` |
| `src/stream_of_worship/admin/commands/maintenance.py` | Add `backup-r2`, `verify-r2-backup`, `restore-r2` command functions |
| `tests/admin/test_r2.py` | Add tests for 4 new `R2Client` methods |
| `tests/admin/test_r2_backup.py` | **New file.** Tests for manifest build, chunked archive write, verify, restore filtering, signal handling, disk space checks |

## Testing

### Unit tests — R2Client (`tests/admin/test_r2.py`)

Follow existing pattern: `@patch("stream_of_worship.admin.services.r2.boto3.client")`,
`r2_env` fixture for credentials.

- `test_list_all_objects_yields_pages` — mock paginator returns 2 pages; assert generator
  yields pages with correct size/etag.
- `test_list_all_objects_empty_bucket` — no Contents; assert yields one empty page.
- `test_get_object_stream_returns_length_and_body` — mock `get_object`; assert
  `(ContentLength, Body)` tuple.
- `test_upload_fileobj_calls_upload_fileobj` — mock `_client.upload_fileobj`; assert
  correct bucket/key/fileobj.
- `test_head_object_returns_metadata` — mock `head_object`; assert size/etag dict.
- `test_head_object_not_found_returns_none` — mock 404; assert None.

### Unit tests — r2_backup service (`tests/admin/test_r2_backup.py`)

- `test_build_manifest_structure` — assert version, chunk_size, totals, empty object list.
- `test_hashing_reader_computes_sha256` — feed known bytes, assert correct digest.
- `test_write_backup_creates_directory_with_manifest_and_chunks` — mock R2Client; write to
  tmp dir; assert manifest + chunk files exist, atomic rename occurred (no `.part` left).
- `test_write_backup_empty_bucket` — directory contains only manifest.json, chunk_count: 0.
- `test_write_backup_chunking_boundary` — 3 objects totaling > chunk_size; assert correct
  chunk assignment in manifest.
- `test_write_backup_large_object_exceeds_chunk` — single object > chunk_size; assert it
  gets its own chunk.
- `test_write_backup_cleans_up_on_interrupt` — simulate SIGINT mid-write; assert `.part`
  directory removed.
- `test_write_backup_refuses_insufficient_disk` — mock disk space < required; assert exit.
- `test_verify_archive_ok` — valid backup → `(True, [])`.
- `test_verify_archive_missing_object` — manifest lists key not in tar → error type `missing`.
- `test_verify_archive_size_mismatch` — tar member size != manifest size → error.
- `test_verify_archive_hash_mismatch` — tar member content hash != manifest → error.
- `test_verify_archive_orphan` — tar member not in manifest → error type `orphan`.
- `test_verify_archive_missing_chunk` — chunk file referenced by manifest missing → error.
- `test_verify_archive_no_manifest` — directory without manifest.json → fail.
- `test_verify_archive_unsupported_version` — manifest version 1 → fail.
- `test_restore_dry_run_no_upload` — mock R2Client; assert `upload_fileobj` never called.
- `test_restore_confirm_uploads_all` — assert `upload_fileobj` called for each object.
- `test_restore_with_prefix_filters` — only matching keys uploaded.
- `test_restore_skip_existing` — mock `head_object` returns metadata; assert skipped.
- `test_restore_aborts_on_verify_failure` — corrupt backup → no upload calls.

### Command tests (`tests/admin/test_audio_soft_delete_maintenance.py` or new file)

Follow existing `CliRunner` pattern:

- `test_backup_r2_creates_directory` — invoke with `--output tmp/`; assert dir exists,
  exit 0.
- `test_backup_r2_refuses_existing_output` — pre-create output dir; assert exit 1.
- `test_verify_r2_backup_ok` — backup then verify; assert exit 0.
- `test_restore_r2_dry_run` — assert dry-run notice, no uploads.
- `test_restore_r2_confirm` — assert uploads called.
- `test_restore_r2_skip_existing` — assert head checks and skips.

### Test commands

```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/test_r2.py tests/admin/test_r2_backup.py -v
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/test_audio_soft_delete_maintenance.py -v
```

### Manual tests

```bash
# Backup (requires R2 credentials in env)
export SOW_R2_ACCESS_KEY_ID=... SOW_R2_SECRET_ACCESS_KEY=...
uv run --extra admin sow-admin maintenance backup-r2 --output /tmp/sow-backup -c ~/.config/stream-of-worship-admin/config.toml

# Verify
uv run --extra admin sow-admin maintenance verify-r2-backup --dir /tmp/sow-backup

# Restore dry-run
uv run --extra admin sow-admin maintenance restore-r2 --dir /tmp/sow-backup

# Restore selective dry-run
uv run --extra admin sow-admin maintenance restore-r2 --dir /tmp/sow-backup --prefix abc123def456/

# Restore skip existing
uv run --extra admin sow-admin maintenance restore-r2 --dir /tmp/sow-backup --skip-existing --confirm

# Restore apply (overwrite)
uv run --extra admin sow-admin maintenance restore-r2 --dir /tmp/sow-backup --confirm
```

## Edge Cases

- **Empty bucket** → directory contains only `manifest.json` with `object_count: 0`;
  verify passes; restore is a no-op.
- **Very large objects** → streaming via `get_object_stream` + `HashingReader` +
  `tarfile.addfile` avoids loading full object into memory. Object may exceed chunk size
  and occupy its own chunk.
- **Partial backup failure** (network error mid-download) → `.part` directory is cleaned
  up automatically (signal handler or exception handler). No misleading partial artifact
  remains.
- **`--output` exists** → refuse with error (no silent overwrite).
- **Restore to non-empty bucket** → overwrites existing objects by default; `--skip-existing`
  preserves them. Dry-run shows exactly what will happen.
- **Manifest version mismatch** → verify reports unsupported version; restore aborts.
- **Tar corruption** → verify fails (`tarfile.TarError` or hash mismatch); restore aborts.
- **Object key with special characters** → PAX format handles long paths and special
  characters correctly.
- **Disk full** → pre-flight check prevents starting; if disk fills during write
  (race condition), exception handler cleans up `.part` directory.
- **Concurrent backups** → atomic rename prevents two successful backups to same output,
  though race on existence check is still possible (document: use unique output paths).
- **R2 object added/deleted during backup** → snapshot is not point-in-time consistent.
  Document recommendation: run during low-traffic windows or after putting uploads on hold.

## Out of Scope

- No gzip compression (audio/flac already compressed; can be added later via `--gzip`
  flag per chunk).
- No parallel/concurrent transfers (sequential for simplicity and debuggability).
- No incremental/differential backups (always full bucket snapshot).
- No cross-bucket restore via `--bucket` override (use config swap).
- No encryption of the backup archive (R2 objects are not encrypted at rest beyond R2's
  own; local file encryption is the user's responsibility).
- No resume of interrupted backups (re-run from scratch; partial `.part` is cleaned up).
- No bandwidth limiting (can be added later via `--rate-limit` flag).
- No point-in-time consistency guarantee (R2 is strongly consistent, but objects may
  change during a long backup window).

## Risk Mitigation Summary

| Risk | v1 State | v2 Mitigation |
|---|---|---|
| Undetected content corruption | Size-only verify | SHA-256 per object in manifest |
| Partial backup mistaken for valid | Broken `.tar` left on disk | Atomic `.part` → final rename; signal cleanup |
| Accidental overwrite on restore | Overwrite by default | `--skip-existing` flag |
| Single multi-TB file | Single `.tar` | Chunked archives (default 10 GiB) |
| Memory exhaustion on large buckets | Full list in memory | Generator-based `list_all_objects()` |
| Disk full mid-backup | No check | Pre-flight disk space check with 10% overhead |
| Tar format 8 GiB limit | Unspecified (USTAR default) | Explicit `PAX_FORMAT` |
| Verify ignores `--config` | Confusing UX | `--config` removed from verify |
| No progress on verify | Silent operation | Per-chunk progress bar |
| Inconsistent output formatting | Only restore has `--format` | All commands support `--format table|json` |
| Signal interrupt leaves garbage | No handling | SIGINT/SIGTERM handler cleans `.part` |
