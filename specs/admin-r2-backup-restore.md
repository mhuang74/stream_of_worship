# Admin R2 Backup & Restore — Implementation Plan

## Summary

Add three commands to the existing `sow-admin maintenance` Typer group for backing up
the entire Cloudflare R2 bucket to a local uncompressed `.tar` archive, verifying archive
integrity, and restoring objects from an archive back to the R2 bucket.

**Commands:**

- `sow-admin maintenance backup-r2 --output PATH` — download every R2 object into a
  single `.tar` file with a manifest.
- `sow-admin maintenance verify-r2-backup --file PATH` — check the archive against its
  manifest (file count, sizes, orphans).
- `sow-admin maintenance restore-r2 --file PATH [--prefix ...] [--confirm]` — verify
  archive, then restore objects to R2 (dry-run by default, overwrite on `--confirm`).

## Motivation

There is currently no built-in `sow-admin` command for R2 disaster recovery. The only
documented backup procedure is a manual `aws s3 sync` between buckets
(`docs/deployment-plan-webapp-v2.md:268`). A CLI-native backup/restore pair enables:

- Disaster recovery to a local file without AWS CLI tooling.
- Cross-environment migration (swap config, restore from a backup taken elsewhere).
- Pre-maintenance snapshots before running destructive commands like `purge-r2-waste`
  or `purge-soft-deletes`.

## Design Decisions

| Decision | Choice |
|---|---|
| Backup scope | Entire bucket (all objects: recording prefixes, renders/, thumbnails/, temp/, build-dependencies/) |
| Archive format | Single uncompressed `.tar` file |
| Restore conflict handling | Dry-run + `--confirm`; overwrite existing R2 objects by default |
| Restore target | Config bucket only (no `--bucket` override; cross-env via config swap) |
| Command naming | `backup-r2`, `verify-r2-backup`, `restore-r2` (consistent with `list-r2-waste`/`purge-r2-waste`) |
| Manifest | `manifest.json` written into the archive; dedicated `verify-r2-backup` command |
| Selective restore | Repeatable `--prefix` flag; default restores everything |
| Output path | Required `--output` flag (no auto-timestamping) |
| Compression | Uncompressed tar only (audio/flac already compressed) |
| Concurrency | Sequential transfers with Rich progress bar |
| Restore pre-check | Always verify archive against manifest before applying |

## Archive Format

### Tar structure

```
<output>.tar
├── manifest.json              # Metadata + object list (see schema below)
└── objects/                   # All R2 objects; member path = R2 key
    ├── abc123def456/
    │   ├── audio.mp3
    │   ├── lyrics.lrc
    │   ├── analysis.json
    │   └── stems/vocals_dry.flac
    ├── renders/
    │   └── <job-id>/output.mp4
    └── build-dependencies/ffmpeg/...
```

All R2 objects are stored under the `objects/` prefix inside the tar to guarantee no
collision with `manifest.json`.

### Manifest schema (`manifest.json`)

```json
{
  "version": 1,
  "created_at": "2026-06-18T12:00:00",
  "bucket": "stream-of-worship",
  "endpoint_url": "https://<account>.r2.cloudflarestorage.com",
  "region": "auto",
  "object_count": 1234,
  "total_bytes": 5678901234,
  "objects": [
    {
      "key": "abc123def456/audio.mp3",
      "size": 5000000,
      "etag": "d41d8cd98f00b204e9800998ecf8427e",
      "last_modified": "2024-01-01T00:00:00+00:00"
    }
  ]
}
```

- `version`: manifest schema version (int, starts at 1 for future migrations).
- `objects[].etag`: R2 ETag (MD5 for non-multipart; quoted-stripped). Used for reference
  only — verify checks size, not etag (multipart uploads have non-MD5 etags).
- `objects[].last_modified`: ISO 8601 string from R2.

## Commands

### `backup-r2`

```
sow-admin maintenance backup-r2 --output PATH [-c CONFIG]

Options:
  --output PATH          Output .tar file path (required; must not already exist)
  -c, --config PATH      Admin config file path
```

**Flow:**

1. Load config + R2 client via existing `_load_clients` / `_load_r2` helpers
   (`maintenance.py:24`, `maintenance.py:33`).
2. Validate `--output` does not already exist → exit 1 with red error if it does.
3. Call `r2_client.list_all_objects()` → returns list of
   `{key, size, etag, last_modified}` dicts for every object in the bucket.
4. Build manifest dict (version, timestamps, bucket metadata, object list, totals).
5. Open tar for writing (`tarfile.open(output, "w")`).
6. Write `manifest.json` as first tar member (serialize via `json.dumps`, add via
   `tarfile.addfile` with `io.BytesIO`).
7. For each object (Rich progress bar: object N/M, bytes downloaded):
   - `r2_client.get_object_stream(key)` → returns `(content_length, body_stream)`.
   - Create `TarInfo(name=f"objects/{key}", size=content_length)`.
   - `tar.addfile(info, body_stream)` — streams body directly into tar (no temp file).
   - Close the body stream.
8. Close tar.
9. Print summary table: object count, total bytes (MB), output path, duration.

**Edge cases:**

- Empty bucket → tar contains only `manifest.json` with `object_count: 0`.
- `--output` parent directory doesn't exist → create with `mkdir(parents=True)`.
- R2 list error mid-backup → tar is left on disk (partial); print error and exit 1.
  Document that partial archives fail verification.

### `verify-r2-backup`

```
sow-admin maintenance verify-r2-backup --file PATH [-c CONFIG]

Options:
  --file PATH            Backup .tar file path (required)
  -c, --config PATH      Admin config file path (optional; for reference only)
```

**Flow:**

1. Open tar for reading (`tarfile.open(file, "r")`).
2. Extract and parse `manifest.json`. If missing → exit 1 with
   "manifest.json not found in archive".
3. Build a set of expected keys from `manifest.objects` → `{key: size}`.
4. Iterate tar members under `objects/`:
   - Strip `objects/` prefix → R2 key.
   - If key in expected set: check `member.size == expected_size`. Mismatch → record error.
   - If key not in expected set → record as orphan.
   - Remove from expected set.
5. Any remaining expected keys → record as missing.
6. Print result:
   - If no errors: green "OK: N objects verified, M bytes".
   - If errors: red table of errors (missing / size-mismatch / orphan), exit 1.

**Does not touch R2 or require credentials** — pure local file verification. The
`--config` flag is accepted for consistency but unused (no R2 client loaded).

### `restore-r2`

```
sow-admin maintenance restore-r2 --file PATH [--prefix PREFIX ...] [--confirm] [--format table|json] [-c CONFIG]

Options:
  --file PATH            Backup .tar file path (required)
  --prefix PREFIX        Restore only keys starting with this prefix (repeatable)
  --confirm              Apply restore (dry-run by default)
  --format               table|json output (default: table)
  -c, --config PATH      Admin config file path
```

**Flow:**

1. Load config + R2 client via `_load_clients` / `_load_r2`.
2. **Verify archive** (run `verify-r2-backup` logic inline). If verification fails →
   print errors, exit 1. Do not proceed.
3. Read manifest, filter objects by `--prefix` (if any provided, keep objects whose
   `key` starts with any given prefix).
4. Build restore manifest rows: `{key, size, action: "restore"}`.
5. **Dry-run** (default): print manifest via `_print_manifest` helper
   (`maintenance.py:96`). Print yellow "Dry run only. Re-run with --confirm to apply."
   (matches existing convention at `maintenance.py:562`).
6. **Apply** (`--confirm`): for each object (Rich progress bar):
   - `tar.extractfile(f"objects/{key}")` → file-like object.
   - `r2_client.upload_fileobj(fileobj, key)` → uploads to R2 (overwrites if exists).
7. Print summary: restored count, total bytes, any failures.

**Edge cases:**

- `--prefix` matches nothing → print "no objects matched any prefix", exit 0.
- Upload fails for one object → record failure, continue with remaining, exit 1 at end.
- Archive verification fails → abort before any R2 mutation.

## Implementation

### 1. R2Client additions (`src/stream_of_worship/admin/services/r2.py`)

Add three methods to the `R2Client` class (after existing `upload_bytes` at line 516):

```python
def list_all_objects(self) -> list[dict]:
    """List every object in the bucket with key, size, etag, last_modified.

    Uses the list_objects_v2 paginator with 1000-object pages.

    Returns:
        List of dicts: {"key": str, "size": int, "etag": str|None,
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
```

These are thin wrappers over `boto3` paginator / `get_object` / `upload_fileobj`,
consistent with existing methods like `download_file` (`r2.py:395`) and `upload_bytes`
(`r2.py:499`).

### 2. New service module (`src/stream_of_worship/admin/services/r2_backup.py`)

Holds the tar/manifest logic, keeping `maintenance.py` from bloating. Functions:

```python
MANIFEST_VERSION = 1
MANIFEST_NAME = "manifest.json"
OBJECTS_PREFIX = "objects/"

def build_manifest(bucket: str, endpoint_url: str, region: str,
                   objects: list[dict]) -> dict:
    """Assemble the manifest dict from R2 object metadata.

    Args:
        bucket: R2 bucket name
        endpoint_url: R2 endpoint URL
        region: R2 region
        objects: List of object dicts from list_all_objects()

    Returns:
        Manifest dict with version, timestamps, bucket metadata,
        object list, and totals (object_count, total_bytes).
    """

def write_backup(r2_client: R2Client, output: Path,
                 console: Console) -> dict:
    """Create the tar archive: list objects, write manifest, stream-download each.

    Args:
        r2_client: Initialized R2Client
        output: Output .tar file path (must not exist)
        console: Rich Console for progress output

    Returns:
        Summary dict: {object_count, total_bytes, output_path, duration_seconds}
    """

def verify_archive(file_path: Path) -> tuple[bool, list[dict]]:
    """Verify tar against manifest.

    Args:
        file_path: Path to the .tar backup file

    Returns:
        Tuple of (ok, errors) where errors is a list of dicts:
        {"type": "missing"|"size_mismatch"|"orphan", "key": str,
         "expected": int|None, "actual": int|None}
    """

def restore_from_archive(r2_client: R2Client, file_path: Path,
                         prefixes: list[str], confirm: bool,
                         console: Console) -> list[dict]:
    """Verify, filter by prefixes, dry-run or apply.

    Args:
        r2_client: Initialized R2Client
        file_path: Path to the .tar backup file
        prefixes: List of key prefixes to filter (empty = all)
        confirm: If True, upload objects to R2; if False, dry-run only
        console: Rich Console for progress output

    Returns:
        List of manifest rows: {key, size, action, status?}
    """
```

### 3. Maintenance commands (`src/stream_of_worship/admin/commands/maintenance.py`)

Three thin command functions decorated with `@app.command(...)`, reusing existing helpers
(`_load_clients`, `_load_r2`, `_print_manifest`, `_validate_choice`). Each follows the
established pattern: `--config`/`-c` option, dry-run + `--confirm` for destructive ops,
yellow dry-run notice.

No changes to `main.py` needed (commands added to existing `maintenance` group via
decorator).

```python
@app.command("backup-r2")
def backup_r2(
    output: Path = typer.Option(..., "--output", help="Output .tar file path (must not exist)"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Back up the entire R2 bucket to a local .tar archive."""
    ...

@app.command("verify-r2-backup")
def verify_r2_backup(
    file: Path = typer.Option(..., "--file", help="Backup .tar file path"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Verify a backup .tar archive against its manifest."""
    ...

@app.command("restore-r2")
def restore_r2(
    file: Path = typer.Option(..., "--file", help="Backup .tar file path"),
    prefixes: list[str] = typer.Option([], "--prefix", help="Restore only keys with this prefix (repeatable)"),
    confirm: bool = typer.Option(False, "--confirm"),
    format_: str = typer.Option("table", "--format", help="table|json"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Restore objects from a backup .tar archive to R2."""
    ...
```

## Files to Modify

| File | Changes |
|---|---|
| `src/stream_of_worship/admin/services/r2.py` | Add `list_all_objects()`, `get_object_stream()`, `upload_fileobj()` methods to `R2Client` |
| `src/stream_of_worship/admin/services/r2_backup.py` | **New file.** Tar/manifest build, verify, restore logic |
| `src/stream_of_worship/admin/commands/maintenance.py` | Add `backup-r2`, `verify-r2-backup`, `restore-r2` command functions |
| `tests/admin/test_r2.py` | Add tests for 3 new `R2Client` methods |
| `tests/admin/test_r2_backup.py` | **New file.** Tests for manifest build, archive verify, restore filtering |

## Testing

### Unit tests — R2Client (`tests/admin/test_r2.py`)

Follow existing pattern: `@patch("stream_of_worship.admin.services.r2.boto3.client")`,
`r2_env` fixture for credentials.

- `test_list_all_objects_returns_all_keys` — mock paginator returns 2 pages; assert all
  keys collected with correct size/etag.
- `test_list_all_objects_empty_bucket` — no Contents; assert empty list.
- `test_get_object_stream_returns_length_and_body` — mock `get_object`; assert
  `(ContentLength, Body)` tuple.
- `test_upload_fileobj_calls_upload_fileobj` — mock `_client.upload_fileobj`; assert
  correct bucket/key/fileobj.

### Unit tests — r2_backup service (`tests/admin/test_r2_backup.py`)

- `test_build_manifest_structure` — assert version, totals, object list.
- `test_write_backup_creates_tar_with_manifest_and_objects` — mock R2Client; write to
  tmp tar; open and assert manifest + object members.
- `test_write_backup_empty_bucket` — tar contains only manifest.json.
- `test_verify_archive_ok` — valid tar → `(True, [])`.
- `test_verify_archive_missing_object` — manifest lists key not in tar → error type
  `missing`.
- `test_verify_archive_size_mismatch` — tar member size != manifest size → error.
- `test_verify_archive_orphan` — tar member not in manifest → error type `orphan`.
- `test_verify_archive_no_manifest` — tar without manifest.json → fail.
- `test_restore_dry_run_no_upload` — mock R2Client; assert `upload_fileobj` never called.
- `test_restore_confirm_uploads_all` — assert `upload_fileobj` called for each object.
- `test_restore_with_prefix_filters` — only matching keys uploaded.
- `test_restore_aborts_on_verify_failure` — corrupt tar → no upload calls.

### Command tests (`tests/admin/test_audio_soft_delete_maintenance.py` or new file)

Follow existing `CliRunner` pattern (`test_audio_soft_delete_maintenance.py:25`):

- `test_backup_r2_creates_tar` — invoke with `--output tmp.tar`; assert file exists,
  exit 0.
- `test_backup_r2_refuses_existing_output` — pre-create output file; assert exit 1.
- `test_verify_r2_backup_ok` — backup then verify; assert exit 0.
- `test_restore_r2_dry_run` — assert dry-run notice, no uploads.
- `test_restore_r2_confirm` — assert uploads called.

### Test commands

```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/test_r2.py tests/admin/test_r2_backup.py -v
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/test_audio_soft_delete_maintenance.py -v
```

### Manual tests

```bash
# Backup (requires R2 credentials in env)
export SOW_R2_ACCESS_KEY_ID=... SOW_R2_SECRET_ACCESS_KEY=...
uv run --extra admin sow-admin maintenance backup-r2 --output /tmp/sow-backup.tar -c ~/.config/stream-of-worship-admin/config.toml

# Verify
uv run --extra admin sow-admin maintenance verify-r2-backup --file /tmp/sow-backup.tar

# Restore dry-run
uv run --extra admin sow-admin maintenance restore-r2 --file /tmp/sow-backup.tar

# Restore selective dry-run
uv run --extra admin sow-admin maintenance restore-r2 --file /tmp/sow-backup.tar --prefix abc123def456/

# Restore apply
uv run --extra admin sow-admin maintenance restore-r2 --file /tmp/sow-backup.tar --confirm
```

## Edge Cases

- **Empty bucket** → backup produces valid tar with `object_count: 0`; verify passes;
  restore is a no-op.
- **Very large objects** → streaming via `get_object_stream` + `tarfile.addfile` avoids
  loading full object into memory.
- **Partial backup failure** (network error mid-download) → tar left on disk; verify will
  report missing objects; user re-runs backup.
- **`--output` exists** → refuse with error (no silent overwrite).
- **Restore to non-empty bucket** → overwrites existing objects by default (dry-run shows
  what will be overwritten).
- **Manifest version mismatch** → verify reports unsupported version; restore aborts.
- **Tar corruption** → verify fails (Python `tarfile` raises `tarfile.TarError`); restore
  aborts.
- **Object key with `objects/` prefix in R2** → stored as `objects/objects/{key}` in tar;
  manifest records original key; restore strips correctly. No collision with manifest.

## Out of Scope

- No gzip compression (audio/flac already compressed; can be added later via `--gzip`
  flag).
- No parallel/concurrent transfers (sequential for simplicity and debuggability).
- No incremental/differential backups (always full bucket snapshot).
- No cross-bucket restore via `--bucket` override (use config swap).
- No encryption of the backup archive (R2 objects are not encrypted at rest beyond R2's
  own; local file encryption is the user's responsibility).
- No resume of interrupted backups (re-run from scratch).
- No etag verification on restore (multipart uploads have non-MD5 etags; size check is
  sufficient).
