# Admin R2 Backup & Restore — Implementation Plan v3

## Summary

Add three `sow-admin maintenance` commands for full Cloudflare R2 disaster recovery:

- `backup-r2 --output DIR [--chunk-size 10GiB] [--format table|json] [-c CONFIG]`
- `verify-r2-backup --dir DIR [--format table|json]`
- `restore-r2 --dir DIR [--prefix PREFIX ...] [--skip-existing|--overwrite-existing] [--confirm] [--format table|json] [-c CONFIG]`

This revision keeps the v2 chunked-tar direction but tightens the plan around data-loss
prevention, live-bucket consistency, metadata preservation, archive safety, and operator
ergonomics.

Primary changes from v2:

- Restore refuses target conflicts by default instead of overwriting after `--confirm`.
- Backups detect source-object changes during the backup window.
- Restores preserve object HTTP/custom metadata.
- Tar members use safe internal names instead of raw R2 keys.
- Size flags accept human-readable values such as `500MiB`, `10GiB`, and raw bytes.
- Restore reads each chunk once per operation group instead of reopening tar files per object.

## Risk Review of v2

### Data loss

- v2 restore overwrites existing R2 objects by default once `--confirm` is passed. That is too
  risky for production buckets because a single wrong config can replace live data.
- v2 has no full preflight conflict gate. It can partially restore before later uploads fail,
  leaving the bucket in a mixed state.
- v2 does not preserve `Content-Type`, cache headers, content disposition, content encoding, or
  user metadata, so restored objects can behave differently even when bytes are correct.

### Command ergonomics

- `--chunk-size` is bytes-only, which is easy to mistype for multi-GB archives.
- `--skip-existing` makes safety opt-in. Safer restore behavior should be the default.
- v2 dry-run action naming says `"restore"` even when restore would overwrite; operators need
  conflict-specific actions.

### Runtime issues

- v2 describes `list_all_objects()` as streaming, but backup still needs total size, object count,
  progress totals, and later object iteration. A single generator pass is not enough unless the
  implementation stores an inventory.
- v2 restore opens `chunk-{chunk_index}.tar` for every object, which is slow for large chunks and
  can become pathological for thousands of objects per chunk.
- The current `R2Client` uses a 30-second read timeout and 2 retry attempts. Large streaming
  downloads/uploads need explicit backup-oriented timeout/retry behavior or operation-level retry.
- `tarfile.addfile()` with a streaming reader depends on reading exactly the declared size. The
  plan should treat short reads as fatal and compare consumed bytes with manifest size.

### Operational issues

- Raw R2 keys as tar member names are unsafe if someone manually extracts the archive; keys such
  as absolute paths or `../` segments must never become tar paths.
- v2 cleanup on interrupt deletes `<output>.part/` without specifying ownership checks. That can
  delete operator data if the path already existed.
- v2 allows a non-point-in-time backup with only documentation. The desired behavior is active
  change detection and failure if a self-consistent backup cannot be established.
- v2 does not define machine-readable JSON output well enough to avoid progress text corrupting
  automation output.

## Archive Format

Backups are directories created atomically through an owned partial directory:

```text
<output-dir>/
├── manifest.json
├── chunk-000000.tar
├── chunk-000001.tar
└── chunk-000002.tar
```

Implementation creates `<output-dir>.part/` first, writes an ownership marker such as
`.sow-r2-backup-partial`, and renames to `<output-dir>/` only after all chunks and manifest are
complete. Cleanup may delete only a partial directory containing that marker.

Tar members never use raw R2 keys. Each object is stored with a safe generated internal path:

```text
chunk-000000.tar
└── objects/
    ├── 000000000001.bin
    ├── 000000000002.bin
    └── 000000000003.bin
```

`manifest.json` maps internal names back to R2 keys and metadata:

```json
{
  "version": 3,
  "created_at": "2026-06-18T12:00:00+00:00",
  "inventory_started_at": "2026-06-18T11:59:30+00:00",
  "inventory_completed_at": "2026-06-18T11:59:45+00:00",
  "bucket": "stream-of-worship",
  "endpoint_url": "https://<account>.r2.cloudflarestorage.com",
  "region": "auto",
  "chunk_size_bytes": 10737418240,
  "object_count": 1234,
  "total_bytes": 5678901234,
  "chunk_count": 3,
  "consistency": {
    "mode": "initial-inventory-with-post-download-head-check",
    "max_changed_object_retries": 2
  },
  "objects": [
    {
      "key": "abc123def456/audio.mp3",
      "member_name": "objects/000000000001.bin",
      "size": 5000000,
      "sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "chunk_index": 0,
      "etag": "d41d8cd98f00b204e9800998ecf8427e",
      "last_modified": "2024-01-01T00:00:00+00:00",
      "content_type": "audio/mpeg",
      "cache_control": null,
      "content_disposition": null,
      "content_encoding": null,
      "metadata": {}
    }
  ]
}
```

Objects created after `inventory_started_at` are not included. Objects changed or deleted after
inventory are retried; if they cannot be captured consistently, the backup fails and the partial
directory is cleaned up.

## Commands

### `backup-r2`

```bash
sow-admin maintenance backup-r2 --output DIR [--chunk-size 10GiB] [--format table|json] [-c CONFIG]
```

Behavior:

- Refuse if `--output` exists.
- Refuse if `<output>.part` exists, unless it is an owned partial and a future cleanup command is
  added. v3 should not silently reuse or delete it at startup.
- Parse `--chunk-size` with binary suffixes: `KiB`, `MiB`, `GiB`, `TiB`; decimal suffixes:
  `KB`, `MB`, `GB`, `TB`; and raw integer bytes.
- Enforce a sensible minimum chunk size, e.g. `64MiB`, to avoid huge file counts from mistakes.
- Build a start-of-backup inventory from R2 list pages. Store inventory in memory for normal
  buckets; if needed, use a JSONL temp file to avoid unbounded memory.
- Sum inventory bytes and check local free space before writing. Required space:
  `total_bytes * 1.1 + 50MiB`.
- For each inventory object:
  - Stream `get_object` into the current tar member through a hashing reader.
  - Verify bytes read equals expected content length.
  - Run a post-download `head_object`.
  - Compare post-download size, ETag, and LastModified with inventory metadata.
  - Retry changed objects up to the configured retry count.
  - Fail the whole backup if an object remains unstable or disappears.
- Rotate chunks before adding an object when the current chunk is non-empty and adding the object
  would exceed `chunk_size_bytes`.
- Preserve metadata needed for restore: content type, cache control, content disposition, content
  encoding, and user metadata.
- On success, write `manifest.json` using atomic temp-file rename, then rename `.part` to final.
- On interrupt or exception, close open streams/tars and delete only owned partial output.

Output:

- Table mode prints progress and a summary.
- JSON mode writes only machine-readable JSON to stdout; progress and diagnostics must go to
  stderr or be disabled.

### `verify-r2-backup`

```bash
sow-admin maintenance verify-r2-backup --dir DIR [--format table|json]
```

Behavior:

- Pure local operation; no config or R2 credentials required.
- Validate `manifest.json` exists and has `version: 3`.
- Validate manifest invariants before reading tar data:
  - unique object keys
  - unique member names
  - valid chunk indexes
  - `object_count`, `chunk_count`, and `total_bytes` match object rows
  - member names are relative, normalized, and under `objects/`
- For each chunk:
  - require `chunk-%06d.tar` to exist
  - open with `tarfile`
  - reject non-regular members
  - reject duplicate members
  - reject orphan members
  - compare member size
  - stream member content and compare SHA-256
- Report all detected verification errors when practical, then exit 1 if any exist.
- Empty backups with `object_count: 0` and `chunk_count: 0` verify successfully.

### `restore-r2`

```bash
sow-admin maintenance restore-r2 --dir DIR [--prefix PREFIX ...] \
  [--skip-existing|--overwrite-existing] [--confirm] [--format table|json] [-c CONFIG]
```

Behavior:

- Always run local verification before any restore planning or upload.
- Filter manifest objects by repeatable `--prefix`. No prefix means all objects.
- If no objects match, print a clear no-op summary and exit 0.
- Build a restore plan before uploads:
  - `create`: target object does not exist
  - `conflict`: target object exists and neither conflict flag is set
  - `skip`: target object exists and `--skip-existing` is set
  - `overwrite`: target object exists and `--overwrite-existing` is set
- `--skip-existing` and `--overwrite-existing` are mutually exclusive.
- Dry-run is default and performs HEAD checks only; it never uploads.
- Apply mode with unresolved conflicts aborts before any upload.
- Apply mode uploads `create` and `overwrite` rows only.
- Uploads preserve stored object metadata through `upload_fileobj(..., extra_args=...)`.
- Restore groups selected rows by `chunk_index`, opens each chunk once, and uploads matching
  members from that chunk.
- If an upload fails, continue with remaining planned uploads, report failures, and exit 1.

Restore safety defaults:

- `--confirm` alone can create missing objects but cannot replace existing objects.
- Replacing existing objects requires both `--confirm` and `--overwrite-existing`.
- Preserving existing objects requires both `--confirm` and `--skip-existing`.

## Implementation Plan

### R2 client additions

Extend `src/stream_of_worship/admin/services/r2.py` with backup-oriented methods:

- `iter_objects() -> Iterator[dict]`
  - Uses `list_objects_v2` paginator.
  - Returns key, size, etag, last_modified.
- `get_object_stream(s3_key: str) -> dict`
  - Wraps `get_object`.
  - Returns body stream, content length, etag, last modified, and restore metadata fields.
- `head_object(s3_key: str) -> dict | None`
  - Returns size, etag, last_modified, and metadata fields; returns `None` on 404/NoSuchKey.
- `upload_fileobj(fileobj, s3_key: str, extra_args: dict | None = None) -> str`
  - Calls boto3 `upload_fileobj` with optional metadata/content headers.

Keep these wrappers thin and consistent with existing `R2Client` error handling. Non-404
`ClientError` exceptions should propagate.

### Backup service module

Create `src/stream_of_worship/admin/services/r2_backup.py` with:

- `MANIFEST_VERSION = 3`
- `DEFAULT_CHUNK_SIZE_BYTES = 10 * 1024 * 1024 * 1024`
- `parse_size(value: str) -> int`
- `HashingReader`
- `build_inventory(...)`
- `write_backup(...)`
- `verify_archive(...)`
- `plan_restore(...)`
- `restore_from_archive(...)`

Use `pathlib.Path` for all local file handling.

### Maintenance commands

Add thin command functions to `src/stream_of_worship/admin/commands/maintenance.py`.

- Validate `--format` with existing `_validate_choice`.
- Reuse `_load_clients`, `_load_r2`, `_print_manifest`, and `_print_json` where compatible.
- Do not require DB access for `verify-r2-backup`.
- For `backup-r2` and `restore-r2`, loading DB via `_load_clients` is acceptable for
  consistency with existing helpers, but the command logic should not query or mutate DB state.

## Testing

### Unit tests

Add focused tests for `R2Client` wrappers:

- paginated object listing
- empty bucket listing
- streaming `get_object`
- `head_object` found and not found
- metadata-aware `upload_fileobj`

Add `tests/admin/test_r2_backup.py`:

- parse size values: bytes, `MiB`, `GiB`, decimal suffixes, invalid suffixes, too-small chunks
- manifest structure and invariant validation
- safe member-name generation
- backup creates manifest and chunks
- empty bucket backup
- chunk boundary and large-object chunk behavior
- short read fails backup
- changed object retries and succeeds
- changed object exceeds retry budget and fails cleanup
- deleted object during backup fails cleanup
- insufficient disk fails before writing chunks
- interrupt/failure removes only owned `.part`
- verify OK
- verify rejects missing manifest, unsupported version, corrupt tar, missing chunk, orphan,
  duplicate member, duplicate key, non-regular member, size mismatch, and hash mismatch
- restore dry-run performs no upload
- restore conflict default aborts apply before upload
- restore skip-existing skips conflicts
- restore overwrite-existing uploads conflicts
- restore mutually exclusive conflict flags fail
- restore prefix filtering
- restore preserves metadata
- restore opens each chunk once for grouped selected objects

### Command tests

Use the existing `CliRunner` pattern:

- `backup-r2` creates a new backup directory
- `backup-r2` refuses existing output and existing partial output
- `backup-r2 --chunk-size 10GiB` parses successfully
- `verify-r2-backup` succeeds on a valid backup and fails on a bad backup
- `restore-r2` dry-run shows create/conflict/skip/overwrite actions
- `restore-r2 --confirm` aborts on conflict without upload
- `restore-r2 --confirm --skip-existing` skips existing objects
- `restore-r2 --confirm --overwrite-existing` uploads existing objects
- `--format json` output is parseable JSON

### Test commands

```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest \
  tests/admin/test_r2.py tests/admin/test_r2_backup.py -v

PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest \
  tests/admin/test_audio_soft_delete_maintenance.py -v
```

## Manual Operations

```bash
# Backup
uv run --extra admin sow-admin maintenance backup-r2 \
  --output /tmp/sow-r2-backup \
  --chunk-size 10GiB \
  -c ~/.config/stream-of-worship-admin/config.toml

# Verify
uv run --extra admin sow-admin maintenance verify-r2-backup \
  --dir /tmp/sow-r2-backup

# Restore dry-run
uv run --extra admin sow-admin maintenance restore-r2 \
  --dir /tmp/sow-r2-backup

# Restore only missing objects
uv run --extra admin sow-admin maintenance restore-r2 \
  --dir /tmp/sow-r2-backup \
  --confirm

# Restore while preserving existing target objects
uv run --extra admin sow-admin maintenance restore-r2 \
  --dir /tmp/sow-r2-backup \
  --skip-existing \
  --confirm

# Restore and intentionally overwrite target conflicts
uv run --extra admin sow-admin maintenance restore-r2 \
  --dir /tmp/sow-r2-backup \
  --overwrite-existing \
  --confirm
```

## Assumptions and Out of Scope

Assumptions:

- Backup scope is the entire configured R2 bucket.
- The consistency target is a self-consistent backup of objects present in the initial inventory.
- Objects created after inventory starts are intentionally excluded.
- Restore prioritizes avoiding data loss over convenience.
- Local backup files are trusted only after `verify-r2-backup` succeeds.

Out of scope:

- Compression.
- Encryption of local backup archives.
- Incremental or differential backups.
- Resume of interrupted backups.
- Bandwidth limiting.
- Cross-bucket `--bucket` override.
- Point-in-time snapshot semantics for objects created after inventory begins.
- Database backup or restore.
