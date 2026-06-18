# Admin R2 Backup & Restore v3 — Handover

**Date:** 2026-06-18
**Spec:** `specs/admin-r2-backup-restore-v3.md`
**Branch:** `admin-r2-backup-restore-v3` (local only, not yet committed or pushed)

## Status: Implementation ~90% Complete, 2 Failing Tests

All code is written and 129 of 131 tests pass. Two command-level tests fail due to a JSON output contamination issue that needs a small fix in `maintenance.py`.

## What Was Implemented

Per `specs/admin-r2-backup-restore-v3.md`, three `sow-admin maintenance` commands for full Cloudflare R2 disaster recovery:

- `backup-r2` — Full bucket backup to chunked tar archives with manifest
- `verify-r2-backup` — Pure local verification of backup archives
- `restore-r2` — Restore objects from backup with conflict safety defaults

### Files Created

1. **`src/stream_of_worship/admin/services/r2_backup.py`** — Core backup/restore service module
   - `parse_size()` — Human-readable size parsing (KiB, MiB, GiB, TiB, KB, MB, GB, TB, raw bytes)
   - `HashingReader` — Stream wrapper that computes SHA-256 and tracks bytes read
   - `build_inventory()` — Builds start-of-backup inventory from R2 list pages
   - `write_backup()` — Writes chunked tar archives with atomic directory rename, consistency checking
   - `verify_archive()` — Pure local verification with manifest invariant validation
   - `plan_restore()` — Builds restore plan with create/conflict/skip/overwrite actions
   - `restore_from_archive()` — Executes restore, grouped by chunk, continues on failures
   - `load_manifest()` — Loads and validates manifest version
   - Constants: `MANIFEST_VERSION=3`, `DEFAULT_CHUNK_SIZE_BYTES=10GiB`, `MIN_CHUNK_SIZE_BYTES=64MiB`

2. **`tests/admin/test_r2_backup.py`** — Unit tests for backup service (79 tests)
3. **`tests/admin/test_r2_backup_commands.py`** — Command-level tests via CliRunner (18 tests)

### Files Modified

1. **`src/stream_of_worship/admin/services/r2.py`** — Added 4 backup-oriented methods to `R2Client`:
   - `iter_objects()` — Paginated listing of all bucket objects
   - `get_object_stream(s3_key)` — Streaming download with all metadata fields
   - `head_object(s3_key)` — HEAD check returning metadata or None on 404
   - `upload_fileobj(fileobj, s3_key, extra_args)` — Upload with metadata preservation

2. **`src/stream_of_worship/admin/commands/maintenance.py`** — Added 3 Typer commands:
   - `backup_r2` — `backup-r2 --output DIR [--chunk-size] [--format] [-c]`
   - `verify_r2_backup` — `verify-r2-backup --dir DIR [--format]` (no config required)
   - `restore_r2` — `restore-r2 --dir DIR [--prefix] [--skip-existing|--overwrite-existing] [--confirm] [--format] [-c]`

3. **`tests/admin/test_r2.py`** — Added test classes for new R2Client methods:
   - `TestIterObjects` — Paginated listing and empty bucket
   - `TestGetObjectStream` — Streaming download with metadata
   - `TestHeadObject` — Found, not found (404), non-404 error propagation
   - `TestUploadFileobj` — With and without extra_args

## Remaining Work

### 1. Fix 2 Failing Tests (IMMEDIATE PRIORITY)

**Root cause:** In `restore_r2` command (`maintenance.py` line ~800-801), the "Dry run only" message is printed via `console.print()` to stdout even in `--format json` mode. This contaminates the JSON output.

**Failing tests:**
- `tests/admin/test_r2_backup_commands.py::TestRestoreR2Command::test_restore_json_output`
- `tests/admin/test_r2_backup_commands.py::TestRestoreR2Command::test_restore_prefix_filtering`

**Fix:** In `src/stream_of_worship/admin/commands/maintenance.py`, the `restore_r2` function, around line 800, change:

```python
    if not confirm:
        console.print("[yellow]Dry run only. Re-run with --confirm to apply.[/yellow]")
        return
```

to:

```python
    if not confirm:
        if format_ != "json":
            console.print("[yellow]Dry run only. Re-run with --confirm to apply.[/yellow]")
        return
```

This ensures that in JSON mode, only the JSON object goes to stdout. The same pattern should be checked in the `backup_r2` command (which already uses `progress_console = Console(file=sys.stderr)` for JSON mode, so it may be fine).

After fixing, run:
```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest \
  tests/admin/test_r2_backup_commands.py::TestRestoreR2Command::test_restore_json_output \
  tests/admin/test_r2_backup_commands.py::TestRestoreR2Command::test_restore_prefix_filtering -v
```

### 2. Run Full Test Suite

After fixing the 2 tests, run the full test suite to ensure no regressions:

```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest \
  tests/admin/test_r2.py tests/admin/test_r2_backup.py tests/admin/test_r2_backup_commands.py -v

# Also run existing maintenance tests to ensure no regressions:
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest \
  tests/admin/test_audio_soft_delete_maintenance.py -v
```

### 3. Commit, Push, and Create PR

After all tests pass:

```bash
git add specs/admin-r2-backup-restore-v3.md \
  src/stream_of_worship/admin/services/r2.py \
  src/stream_of_worship/admin/services/r2_backup.py \
  src/stream_of_worship/admin/commands/maintenance.py \
  tests/admin/test_r2.py \
  tests/admin/test_r2_backup.py \
  tests/admin/test_r2_backup_commands.py

git commit -m "feat(admin): add R2 backup, verify, and restore maintenance commands

Implements specs/admin-r2-backup-restore-v3.md with three new sow-admin
maintenance commands for full Cloudflare R2 disaster recovery:

- backup-r2: Full bucket backup to chunked tar archives with manifest,
  consistency checking via post-download HEAD, and atomic directory creation
- verify-r2-backup: Pure local verification (no R2 credentials needed)
- restore-r2: Restore with conflict safety defaults, metadata preservation,
  and chunk-grouped uploads

Key safety features:
- Restore refuses target conflicts by default (no overwrite without
  --overwrite-existing --confirm)
- Backups detect source-object changes during backup window with retries
- Tar members use safe internal names (objects/000000000001.bin) not raw R2 keys
- Partial backup directories use ownership marker for safe cleanup
- Short reads treated as fatal errors
- JSON output mode sends progress to stderr, JSON to stdout"

git push -u origin admin-r2-backup-restore-v3
```

Then create a PR via `gh pr create` with a detailed description covering:
- Summary of the three commands
- Archive format (manifest.json + chunk-NNNNNN.tar)
- Safety defaults (conflict refusal, metadata preservation, atomic creation)
- Test coverage (131 tests across unit and command levels)

### 4. Wait for Code Review and Address Feedback

After creating the PR, wait 5 minutes, then:
1. Read the PR review feedback using `gh pr view <PR_NUMBER> --comments`
2. Address each comment appropriately
3. Reply to each comment inline and resolve each comment
4. Push fixes and re-run tests

## Architecture Notes

### Archive Format

```
<output-dir>/
├── manifest.json          # Maps safe member names to R2 keys + metadata
├── chunk-000000.tar       # Tar with objects/000000000001.bin, etc.
├── chunk-000001.tar
└── chunk-000002.tar
```

Backup creates `<output-dir>.part/` first with a `.sow-r2-backup-partial` ownership marker, then renames to `<output-dir>/` only after success. Cleanup deletes only directories containing the marker.

### Consistency Model

- `initial-inventory-with-post-download-head-check`
- Objects are inventoried at backup start
- After download, a HEAD check compares size/ETag/LastModified
- Changed objects retry up to 2 times
- If an object remains unstable or disappears, the backup fails and cleans up

### Restore Safety Defaults

- `--confirm` alone: creates missing objects, cannot replace existing
- `--confirm --skip-existing`: skips existing objects
- `--confirm --overwrite-existing`: overwrites existing objects
- `--skip-existing` and `--overwrite-existing` are mutually exclusive
- Dry-run is default (HEAD checks only, no uploads)
- Always verifies backup before planning restore

### Key Design Decisions

1. **Chunk size minimum (64MiB)** is enforced at the CLI level (`backup_r2` command), not in `write_backup()` itself. This allows tests to use small chunk sizes.

2. **Short read handling**: `tarfile.addfile()` raises `OSError("unexpected end of data")` when the fileobj provides fewer bytes than `tarinfo.size`. This is caught and re-raised as `BackupError`.

3. **JSON output**: In JSON mode, progress messages go to stderr (`Console(file=sys.stderr)`), and only JSON goes to stdout via `print()`. The CliRunner mixes stdout/stderr, so tests extract JSON by finding the `{` character.

4. **`verify-r2-backup` requires no config**: It's a pure local operation. The command does not call `_load_clients` or `_load_r2`.

5. **`backup-r2` and `restore-r2` load DB via `_load_clients`**: This is for consistency with existing helpers, but the command logic does not query or mutate DB state.

## Test Commands

```bash
# Run all R2 backup tests
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest \
  tests/admin/test_r2.py tests/admin/test_r2_backup.py tests/admin/test_r2_backup_commands.py -v

# Run existing maintenance tests (regression check)
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

# Verify (no config needed)
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
