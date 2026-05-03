# Fix Schema Sync Corruption & Centralize Column Migrations

## Context

After adding the `download_status` column to the admin DB client's `initialize_schema()`, the Turso remote database was never updated with the same column. This creates a schema mismatch: local DB has 29 columns in recordings, Turso has 28. When libsql sync applies WAL frames from a 28-column remote onto a 29-column local DB, the B-tree page structure becomes inconsistent, causing "database disk image is malformed" errors on every read.

A secondary bug exists in `RECORDING_COLUMNS_FOR_JOIN` (schema.py:191-199): the explicit column list has columns in a different order than what `Recording.from_row()` expects, causing **silent data corruption** in the app's catalog service JOIN queries (values for `created_at`, `updated_at`, `youtube_url`, `visibility_status`, `deleted_at`, `download_status` get swapped).

**Root cause:** There is no single source of truth for column migrations. Three code paths each duplicate the migration logic independently:

1. `admin/db/client.py:initialize_schema()` — admin local DB init
2. `admin/commands/db.py:turso-bootstrap` — Turso remote schema
3. `app/db/read_client.py:_migrate_schema()` — app read-only replica

When a new column is added to one path but not the others, schema drift causes sync corruption.

## Current DB State

| Location | State | Columns (recordings) | Notes |
|----------|-------|---------------------|-------|
| `~/.config/sow-admin/db/sow.db` | **CORRUPTED** | Unknown | Cannot read; WAL/frame mismatch |
| `~/.config/sow-admin/db/sow_backup.db` | OK | 28 (no `download_status`) | 685 songs, 73 recordings |
| `~/.config/sow/db/sow.db` | Empty | 0 bytes | Never populated |
| Turso remote | Presumed 28 cols | No `download_status` | Was bootstrapped before column was added |

## Architecture Decisions

1. **`db sync` auto-migrates** — applies column migrations to both local and Turso before syncing. No manual `db init` or `turso-bootstrap` needed for ongoing column additions.
2. **`turso-bootstrap` → `scripts/turso-init.py`** — one-time infrastructure provisioning only. Removed from CLI to prevent accidental re-runs. Includes `--seed` flag for initial data migration.
3. **Centralized `COLUMN_MIGRATIONS`** in `schema.py` — single source of truth, referenced by all code paths.
4. **`db sync` integrity check** — refuses to sync if local DB is corrupted, suggests recovery.

## Plan

### 1. Centralize column migrations into `schema.py`

**File:** `src/stream_of_worship/admin/db/schema.py`

Add a `COLUMN_MIGRATIONS` list and `apply_column_migrations()` function:

```python
COLUMN_MIGRATIONS = [
    ("recordings", "youtube_url", "TEXT"),
    ("recordings", "visibility_status", "TEXT"),
    ("songs", "deleted_at", "TIMESTAMP"),
    ("recordings", "deleted_at", "TIMESTAMP"),
    ("recordings", "download_status", "TEXT DEFAULT 'pending'"),
]


def apply_column_migrations(cursor) -> None:
    """Apply all column migrations (idempotent). Safe to call on any DB."""
    for table, column, col_type in COLUMN_MIGRATIONS:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        except sqlite3.OperationalError:
            pass
```

Any future column addition only needs one entry here. All code paths call `apply_column_migrations()`.

### 2. Fix `RECORDING_COLUMNS_FOR_JOIN` column order

**File:** `src/stream_of_worship/admin/db/schema.py:191-199`

In SQLite, columns added via `ALTER TABLE ADD COLUMN` are appended after the columns defined in `CREATE TABLE`. The current explicit column list puts `youtube_url`, `visibility_status`, `download_status` before `created_at`, `updated_at`, `deleted_at`, but `Recording.from_row()` expects them after.

**Current (broken) order:**
```
r.youtube_url, r.visibility_status, r.download_status, r.created_at, r.updated_at, r.deleted_at
```

**Fixed order (matches `from_row()` expectations for 29 cols, models.py:214-220):**
```
r.created_at, r.updated_at, r.youtube_url, r.visibility_status, r.deleted_at, r.download_status
```

Full corrected constant:

```python
RECORDING_COLUMNS_FOR_JOIN = """
    r.content_hash, r.hash_prefix, r.song_id, r.original_filename,
    r.file_size_bytes, r.imported_at, r.r2_audio_url, r.r2_stems_url,
    r.r2_lrc_url, r.duration_seconds, r.tempo_bpm, r.musical_key,
    r.musical_mode, r.key_confidence, r.loudness_db, r.beats,
    r.downbeats, r.sections, r.embeddings_shape, r.analysis_status,
    r.analysis_job_id, r.lrc_status, r.lrc_job_id, r.created_at,
    r.updated_at, r.youtube_url, r.visibility_status, r.deleted_at,
    r.download_status
"""
```

This matches `Recording.from_row()` positional index mapping (models.py:214-220) and fixes the silent data corruption in the app's catalog service.

### 3. Refactor `initialize_schema()` to use centralized migrations

**File:** `src/stream_of_worship/admin/db/client.py:184-252`

Replace the five individual `ALTER TABLE` blocks (lines 199-234) with:

```python
apply_column_migrations(cursor)
```

Keep the `visibility_status` data migration (UPDATE recordings SET visibility_status = 'published' ...) as a separate step after `apply_column_migrations()`, since it's a data migration, not a schema migration.

### 4. Refactor `ReadOnlyClient._migrate_schema()` to use centralized migrations

**File:** `src/stream_of_worship/app/db/read_client.py:110-117`

Replace:
```python
for table in ("songs", "recordings"):
    try:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN deleted_at TIMESTAMP")
    except Exception:
        pass
```

With:
```python
from stream_of_worship.admin.db.schema import apply_column_migrations
apply_column_migrations(cursor)
```

### 5. Make `db sync` self-healing (integrity check + auto-migrate)

**File:** `src/stream_of_worship/admin/db/client.py:125-142`

New `db sync` flow:

```
1. PRAGMA integrity_check
   ├─ OK → continue
   └─ CORRUPT → raise SyncError with recovery instructions

2. apply_column_migrations(cursor)
   → adds missing columns to local DB
   → on next sync/commit, propagates to Turso

3. conn.sync()
   → both sides now have matching schemas → safe

4. Update sync_metadata timestamp
```

Implementation:

```python
def sync(self) -> None:
    if not self.is_turso_enabled:
        raise SyncError("Turso sync is not configured")

    # Pre-sync: check local DB integrity
    cursor = self.connection.cursor()
    try:
        cursor.execute("PRAGMA integrity_check")
        result = cursor.fetchone()
        if result and result[0] != "ok":
            raise SyncError(
                f"Local database is corrupted ('{result[0]}'). "
                f"Recovery: delete {self.db_path} and all sidecar files, "
                f"then run 'db sync' to recreate from Turso."
            )
    except sqlite3.DatabaseError as e:
        if "malformed" in str(e).lower():
            raise SyncError(
                f"Local database is corrupted. "
                f"Recovery: delete {self.db_path} and all sidecar files, "
                f"then run 'db sync' to recreate from Turso. "
                f"Original error: {e}"
            )
        raise

    # Pre-sync: ensure local schema is up to date
    apply_column_migrations(cursor)

    try:
        conn = self.connection
        conn.sync()
        self.update_sync_metadata("last_sync_at", datetime.now().isoformat())
    except Exception as e:
        raise SyncError(f"Sync failed: {e}", cause=e)
```

### 6. Add schema-mismatch auto-recovery to `SyncService`

**File:** `src/stream_of_worship/admin/services/sync.py:193-238`

Extend `_execute_sync_with_recovery()` to handle "malformed" errors:

```python
if ("malformed" in error_msg.lower()) and attempt < max_attempts:
    client.close()
    # Recovery: delete local DB and re-sync from Turso
    db_path = Path(self.db_path)
    for f in db_path.parent.glob(f"{db_path.name}*"):
        f.unlink(missing_ok=True)
    # Retry sync — libsql will recreate from Turso
    return self._execute_sync_with_recovery(attempt=attempt + 1, max_attempts=max_attempts)
```

After recovery re-sync, `apply_column_migrations()` runs automatically in the next `sync()` call (step 5 above), bringing the fresh replica to the latest schema.

### 7. Move `turso-bootstrap` to `scripts/turso-init.py`

**Remove from CLI:** `src/stream_of_worship/admin/commands/db.py`

Remove the `turso_bootstrap` command and the `tokens` command from the `db` CLI group. The `db` subgroup becomes:

- `db init` — first-time local DB creation
- `db sync` — routine sync (auto-migrates columns)
- `db status` — show DB info
- `db reset` — destructive reset
- `db path` — show DB path

**New file:** `scripts/turso-init.py`

One-time infrastructure provisioning script. Not part of the admin CLI — its one-time nature is explicit.

```
Usage: python scripts/turso-init.py [--seed] [--force]

Options:
  --seed   Copy local data to Turso after schema creation
  --force  Overwrite existing remote data (with --seed)
```

Implementation (ported from `turso-bootstrap` command):

1. Load admin config
2. Connect to Turso via libsql (embedded replica)
3. Create schema: tables, indexes, triggers (via `ALL_SCHEMA_STATEMENTS`)
4. Run `apply_column_migrations(cursor)` — adds all migration columns including `download_status`
5. If `--seed`: copy local data to Turso (batched, transactional)
6. `conn.sync()` → push to remote
7. Print success/failure

### 8. Recover the corrupted local DB

**Steps (after code fixes are deployed):**

```bash
# 1. Delete the corrupted local DB + sidecar files
rm -f ~/.config/sow-admin/db/sow.db ~/.config/sow-admin/db/sow.db-*

# 2. Re-init (creates fresh local schema)
sow_admin db init

# 3. Re-sync (auto-migrates + syncs from Turso)
sow_admin db sync

# 4. Verify
sow_admin db status
sow_admin audio list
sow_admin catalog list
```

**Why not use `--seed`?** Turso already has all 685 songs and 73 recordings from the prior successful sync. Seeding from the backup is unnecessary and risks introducing `download_status = NULL` for existing rows (since the backup has only 28 columns). Letting the data flow from Turso → local is the normal direction and ensures the `DEFAULT 'pending'` applies correctly.

## Files to Modify

| File | Change |
|------|--------|
| `src/.../admin/db/schema.py` | Add `COLUMN_MIGRATIONS` + `apply_column_migrations()`. Fix `RECORDING_COLUMNS_FOR_JOIN` column order. |
| `src/.../admin/db/client.py` | Refactor `initialize_schema()` to use `apply_column_migrations()`. Add integrity check + auto-migrate in `sync()`. |
| `src/.../admin/commands/db.py` | Remove `turso-bootstrap` and `tokens` commands. Keep `init`, `sync`, `status`, `reset`, `path`. |
| `src/.../app/db/read_client.py` | Refactor `_migrate_schema()` to use `apply_column_migrations()`. |
| `src/.../admin/services/sync.py` | Add "malformed" auto-recovery in `_execute_sync_with_recovery()`. |
| `scripts/turso-init.py` | New file — one-time Turso provisioning (moved from `turso-bootstrap` + `--seed`). |

## Future Column Addition Checklist

When adding a new column in the future:

1. Add the column to `CREATE_TABLE` in `schema.py` (for fresh DBs)
2. Add entry to `COLUMN_MIGRATIONS` in `schema.py` (for existing DBs)
3. Add the column to `RECORDING_COLUMNS_FOR_JOIN` / `SONG_COLUMNS_FOR_JOIN` in the correct position (matching actual SQLite column order — columns from CREATE TABLE first, then ALTER TABLE columns appended)
4. Update `RECORDING_COLUMN_COUNT` / `SONG_COLUMN_COUNT`
5. Update `from_row()` positional mapping in `models.py`
6. Done — `db sync` auto-applies the new column to both local and Turso

## Verification

1. **Migration centralization test:** Add a temporary new column entry to `COLUMN_MIGRATIONS`. Run `sow_admin db sync` and verify it appears in both local DB and Turso.

2. **Column order test:** Run the app's catalog service and verify `Recording.from_row()` returns correct values for `created_at`, `updated_at`, `youtube_url`, `visibility_status`, `deleted_at`, `download_status` (no silent swaps).

3. **Integrity check test:** Corrupt the local WAL file manually, then run `sow_admin db sync` and verify it detects corruption and suggests recovery.

4. **Auto-recovery test:** Simulate a malformed DB, run `sow_admin db sync` (via SyncService), and verify the auto-recovery path deletes local DB and re-syncs from Turso.

5. **Full recovery test (immediate fix for current corruption):**
   ```bash
   rm -f ~/.config/sow-admin/db/sow.db ~/.config/sow-admin/db/sow.db-*
   sow_admin db init
   sow_admin db sync
   sow_admin audio list    # should work without "malformed" error
   sow_admin catalog list  # should work without "malformed" error
   ```

6. **turso-init script test:**
   ```bash
   python scripts/turso-init.py --seed  # one-time setup
   sow_admin db sync                     # subsequent sync works
   ```

7. **Existing tests:**
   ```bash
   PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ \
     --ignore=tests/services/analysis \
     --ignore=services/qwen3/tests \
     --ignore=services/analysis/tests -v
   ```
