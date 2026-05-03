# Fix Schema Sync Corruption & Centralize Column Migrations (v2)

## Context

After adding the `download_status` column to the admin DB client's `initialize_schema()`, the Turso remote database was never updated with the same column. This creates a schema mismatch: local DB has 29 columns in recordings, Turso has 28. When libsql sync applies WAL frames from a 28-column remote onto a 29-column local DB, the B-tree page structure becomes inconsistent, causing "database disk image is malformed" errors on every read.

A secondary bug exists in `RECORDING_COLUMNS_FOR_JOIN` (schema.py:191-199): the explicit column list has columns in a different order than what `Recording.from_row()` expects, causing **silent data corruption** in the app's catalog service JOIN queries (values for `created_at`, `updated_at`, `youtube_url`, `visibility_status`, `deleted_at`, `download_status` get swapped).

**Root cause:** There is no single source of truth for column migrations. Three code paths each duplicate the migration logic independently:

1. `admin/db/client.py:initialize_schema()` — admin local DB init
2. `admin/commands/db.py:turso-bootstrap` — Turso remote schema
3. `app/db/read_client.py:_migrate_schema()` — app read-only replica

When a new column is added to one path but not the others, schema drift causes sync corruption.

**Latent fragility:** `from_row()` uses positional index mapping (`row[0]`, `row[1]`, ...). Different DBs created at different schema versions can have different physical column orders. Any column order change silently corrupts data in all `SELECT *` and explicit-column-list query paths. This affects not just JOIN queries but also the admin client's `list_recordings_with_songs` (uses `r.*`) and the app's `ReadOnlyClient` (uses `SELECT *` throughout).

## Current DB State

| Location | State | Columns (recordings) | Notes |
|----------|-------|---------------------|-------|
| `~/.config/sow-admin/db/sow.db` | **CORRUPTED** | Unknown | Cannot read; WAL/frame mismatch |
| `~/.config/sow-admin/db/sow_backup.db` | OK | 28 (no `download_status`) | 685 songs, 73 recordings |
| `~/.config/sow/db/sow.db` | Empty | 0 bytes | Never populated |
| Turso remote | Presumed 28 cols | No `download_status` | Was bootstrapped before column was added |

## Architecture Decisions

1. **`db sync` auto-migrates with two-phase sync** — applies column migrations to local DB, commits, pushes schema to Turso, then pulls data. Prevents B-tree corruption from schema mismatch during WAL frame application.
2. **`turso-bootstrap` → `scripts/turso-init.py`** — one-time infrastructure provisioning only. Removed from CLI to prevent accidental re-runs. Includes `--seed` flag for initial data migration.
3. **Centralized `COLUMN_MIGRATIONS`** in `schema.py` — single source of truth, referenced by all code paths.
4. **`db sync` integrity check** — refuses to sync if local DB is corrupted, suggests recovery.
5. **Dict-based `from_row()`** — eliminates column-order fragility by mapping column names to fields instead of relying on positional indices. Prevents the entire class of silent data corruption bugs.
6. **Post-sync schema validation** — verifies column counts match after sync to detect mismatch before it causes corruption.
7. **Safe auto-recovery** — creates timestamped backup before deleting local DB; verifies Turso health before nuking local data.

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

**Ordering constraint:** Entries MUST be in the same order the columns were historically added. Reordering entries changes the physical column position in SQLite (since `ALTER TABLE ADD COLUMN` appends columns), which would break `SELECT *` + `from_row()` until the dict-based refactor (step 9) is complete.

### 2. Fix `RECORDING_COLUMNS_FOR_JOIN` column order (immediate fix)

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

**Note:** This is an immediate fix for the JOIN queries. Step 9 (dict-based `from_row()`) will make this ordering constraint unnecessary for all query paths.

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

### 5. Make `db sync` self-healing (integrity check + two-phase sync + post-sync validation)

**File:** `src/stream_of_worship/admin/db/client.py:125-142`

New `db sync` flow:

```
1. PRAGMA integrity_check
   ├─ OK → continue
   └─ CORRUPT → raise SyncError with recovery instructions

2. apply_column_migrations(cursor)
   → adds missing columns to local DB

3. conn.commit()
   → persists schema changes to local DB
   → CRITICAL: ensures DDL is committed before WAL frame application

4. conn.sync()  (Phase 1: push to Turso)
   → propagates schema changes to Turso
   → now both sides have matching schema

5. conn.sync()  (Phase 2: pull from Turso)
   → applies Turso WAL frames to local DB
   → safe because both sides now have matching schema

6. Post-sync validation: PRAGMA table_info
   → verify local column count matches expected count
   ├─ MATCH → continue
   └─ MISMATCH → raise SyncError (schema drift detected after sync)

7. Update sync_metadata timestamp
```

**Why two-phase sync?** If `apply_column_migrations()` adds a new column to local (making it 29 cols) but Turso still has 28 cols, a single `conn.sync()` may apply Turso's 28-column WAL frames to the 29-column local DB before pushing the schema change. This is exactly how the original B-tree corruption occurred. Two-phase sync guarantees both sides have matching schemas before any WAL frame application.

Implementation:

```python
from stream_of_worship.admin.db.schema import (
    apply_column_migrations,
    RECORDING_COLUMN_COUNT,
    SONG_COLUMN_COUNT,
)

def sync(self) -> None:
    if not self.is_turso_enabled:
        raise SyncError("Turso sync is not configured")

    cursor = self.connection.cursor()

    # Pre-sync: check local DB integrity
    try:
        cursor.execute("PRAGMA integrity_check")
        result = cursor.fetchone()
        if result and result[0] != "ok":
            raise SyncError(
                f"Local database is corrupted ('{result[0]}'). "
                f"Recovery: run 'db sync --force' to recreate from Turso, "
                f"or manually delete {self.db_path} and all sidecar files."
            )
    except sqlite3.DatabaseError as e:
        if "malformed" in str(e).lower():
            raise SyncError(
                f"Local database is corrupted. "
                f"Recovery: run 'db sync --force' to recreate from Turso, "
                f"or manually delete {self.db_path} and all sidecar files. "
                f"Original error: {e}"
            )
        raise

    # Pre-sync: ensure local schema is up to date
    apply_column_migrations(cursor)

    # Commit schema changes BEFORE syncing
    # This ensures DDL is persisted locally before any WAL frame application
    self.connection.commit()

    # Phase 1: Push schema changes to Turso
    try:
        self.connection.sync()
    except Exception as e:
        raise SyncError(f"Schema push sync failed: {e}", cause=e)

    # Phase 2: Pull data from Turso (safe — both sides now have matching schema)
    try:
        self.connection.sync()
    except Exception as e:
        raise SyncError(f"Data pull sync failed: {e}", cause=e)

    # Post-sync validation: verify schema matches expected column counts
    self._validate_schema(cursor)

    self.update_sync_metadata("last_sync_at", datetime.now().isoformat())


def _validate_schema(self, cursor) -> None:
    """Verify local DB schema has expected column counts after sync.

    Detects schema drift that could cause B-tree corruption on next sync.
    """
    expected = {"recordings": RECORDING_COLUMN_COUNT, "songs": SONG_COLUMN_COUNT}
    for table, expected_count in expected.items():
        try:
            cursor.execute(f"PRAGMA table_info({table})")
            actual_count = len(cursor.fetchall())
            if actual_count != expected_count:
                raise SyncError(
                    f"Schema mismatch after sync: {table} has {actual_count} columns, "
                    f"expected {expected_count}. This may indicate a migration was not "
                    f"applied. Run 'db init' to apply missing migrations."
                )
        except sqlite3.OperationalError:
            pass  # Table doesn't exist yet (fresh DB)
```

### 6. Add safe schema-mismatch auto-recovery to `SyncService`

**File:** `src/stream_of_worship/admin/services/sync.py:193-238`

Extend `_execute_sync_with_recovery()` to handle "malformed" errors with safe recovery:

**Key differences from v1:**
- Creates timestamped backup before deleting local DB (prevents data loss if Turso is stale)
- Verifies Turso health before nuking local data (prevents destroying local data when Turso is also broken)
- Limits to one auto-recovery attempt per `execute_sync()` call

```python
def _execute_sync_with_recovery(self, attempt: int = 1, max_attempts: int = 2) -> SyncResult:
    client = DatabaseClient(
        self.db_path,
        turso_url=self.turso_url,
        turso_token=self.turso_token or os.environ.get("SOW_TURSO_TOKEN"),
    )

    try:
        client.sync()

        return SyncResult(
            success=True,
            message="Sync completed successfully",
            records_synced=None,
        )
    except SyncError as e:
        error_msg = str(e)

        # Auto-recovery for "malformed" corruption
        if "malformed" in error_msg.lower() and attempt < max_attempts:
            client.close()

            # Step 1: Verify Turso is healthy before nuking local data
            if not self._verify_turso_health():
                raise SyncNetworkError(
                    "Cannot auto-recover: Turso remote appears unhealthy. "
                    "Manual intervention required. "
                    f"Original error: {e}"
                )

            # Step 2: Create timestamped backup before deletion
            self._backup_local_db()

            # Step 3: Delete local DB and sidecar files
            self._delete_local_db()

            # Step 4: Retry sync — libsql will recreate from Turso
            return self._execute_sync_with_recovery(
                attempt=attempt + 1, max_attempts=max_attempts
            )

        # Existing recovery for metadata file corruption
        if "metadata file does not" in error_msg.lower() and attempt < max_attempts:
            client.close()
            self._recover_from_missing_metadata()
            return self._execute_sync_with_recovery(
                attempt=attempt + 1, max_attempts=max_attempts
            )

        raise SyncNetworkError(f"Sync failed: {e}")
    finally:
        client.close()


def _verify_turso_health(self) -> bool:
    """Check that Turso remote is reachable and has valid schema.

    Returns:
        True if Turso appears healthy, False otherwise.
    """
    try:
        conn = libsql.connect(
            ":memory:",
            sync_url=self.turso_url,
            auth_token=self.turso_token or os.environ.get("SOW_TURSO_TOKEN", ""),
        )
        conn.sync()
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(recordings)")
        columns = cursor.fetchall()
        conn.close()
        return len(columns) > 0
    except Exception:
        return False


def _backup_local_db(self) -> Path:
    """Create timestamped backup of local DB before recovery.

    Returns:
        Path to the backup directory.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    backup_dir = self.db_path.parent / f"{self.db_path.name}.bak-{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    # Move db file and all sidecar files into backup
    if self.db_path.exists():
        shutil.move(self.db_path, backup_dir / self.db_path.name)
    for sibling in self.db_path.parent.glob(f"{self.db_path.name}-*"):
        shutil.move(sibling, backup_dir / sibling.name)

    return backup_dir


def _delete_local_db(self) -> None:
    """Delete local DB and all sidecar files."""
    if self.db_path.exists():
        self.db_path.unlink()
    for f in self.db_path.parent.glob(f"{self.db_path.name}-*"):
        if f.is_dir():
            shutil.rmtree(f)
        else:
            f.unlink(missing_ok=True)
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
  --seed    Copy local data to Turso after schema creation
  --force   Overwrite existing remote data (with --seed). Requires confirmation prompt.
```

Implementation (ported from `turso-bootstrap` command):

1. Load admin config
2. Connect to Turso via libsql (embedded replica)
3. Create schema: tables, indexes, triggers (via `ALL_SCHEMA_STATEMENTS`)
4. Run `apply_column_migrations(cursor)` — adds all migration columns including `download_status`
5. `conn.commit()` — persist schema
6. If `--seed`: copy local data to Turso (batched, transactional)
7. `conn.sync()` → push to remote
8. Print success/failure

**Safety guard:** When `--force` is used with existing remote data, require interactive confirmation (typing the database name), matching the `db reset --confirm` pattern:

```python
if remote_song_count > 0 and force:
    console.print(f"[red]WARNING: Remote has {remote_song_count} songs.[/red]")
    console.print(f"Type the database name to confirm overwrite: ")
    confirmation = input().strip()
    if confirmation != expected_db_name:
        console.print("[red]Confirmation did not match. Aborting.[/red]")
        sys.exit(1)
```

### 8. Recover the corrupted local DB

**Steps (after code fixes are deployed):**

```bash
# 1. Delete the corrupted local DB + sidecar files
rm -f ~/.config/sow-admin/db/sow.db ~/.config/sow-admin/db/sow.db-*

# 2. Re-init (creates fresh local schema)
sow_admin db init

# 3. Re-sync (auto-migrates + two-phase syncs from Turso)
sow_admin db sync

# 4. Verify
sow_admin db status
sow_admin audio list
sow_admin catalog list
```

**Why not use `--seed`?** Turso already has all 685 songs and 73 recordings from the prior successful sync. Seeding from the backup is unnecessary and risks introducing `download_status = NULL` for existing rows (since the backup has only 28 columns). Letting the data flow from Turso → local is the normal direction and ensures the `DEFAULT 'pending'` applies correctly.

### 9. Dict-based `from_row()` refactor (eliminates column-order fragility)

**Files:**
- `src/stream_of_worship/admin/db/models.py` — `Song.from_row()`, `Recording.from_row()`
- `src/stream_of_worship/admin/db/client.py` — all callers of `from_row()`
- `src/stream_of_worship/app/db/read_client.py` — all callers of `from_row()`
- `src/stream_of_worship/app/services/catalog.py` — JOIN query callers

**Problem:** Position-based `from_row()` (`row[0]`, `row[1]`, ...) silently mis-maps fields whenever physical column order differs from the expected order. This affects:
- `SELECT *` queries (admin client `list_recordings_with_songs` uses `r.*`)
- Explicit column list queries (app catalog service uses `RECORDING_COLUMNS_FOR_JOIN`)
- Any DB at a different schema version (fewer columns = shifted indices)

**Solution:** Replace positional mapping with column-name-based mapping using `cursor.description`. This makes `from_row()` resilient to column reordering, addition, or removal.

```python
# In models.py

# Define column-to-field mapping (order-independent)
_RECORDING_COLUMN_MAP = {
    "content_hash": "content_hash",
    "hash_prefix": "hash_prefix",
    "song_id": "song_id",
    "original_filename": "original_filename",
    "file_size_bytes": "file_size_bytes",
    "imported_at": "imported_at",
    "r2_audio_url": "r2_audio_url",
    "r2_stems_url": "r2_stems_url",
    "r2_lrc_url": "r2_lrc_url",
    "duration_seconds": "duration_seconds",
    "tempo_bpm": "tempo_bpm",
    "musical_key": "musical_key",
    "musical_mode": "musical_mode",
    "key_confidence": "key_confidence",
    "loudness_db": "loudness_db",
    "beats": "beats",
    "downbeats": "downbeats",
    "sections": "sections",
    "embeddings_shape": "embeddings_shape",
    "analysis_status": "analysis_status",
    "analysis_job_id": "analysis_job_id",
    "lrc_status": "lrc_status",
    "lrc_job_id": "lrc_job_id",
    "created_at": "created_at",
    "updated_at": "updated_at",
    "youtube_url": "youtube_url",
    "visibility_status": "visibility_status",
    "deleted_at": "deleted_at",
    "download_status": "download_status",
}


@classmethod
def from_row(cls, row: tuple, description: Optional[tuple] = None) -> "Recording":
    """Create a Recording from a database row tuple.

    Args:
        row: Database row tuple.
        description: Column descriptions from cursor.description.
            If provided, maps columns by name (order-independent).
            If None, falls back to positional mapping (legacy).

    Returns:
        Recording instance
    """
    if description is not None:
        col_names = [desc[0] for desc in description]
        values = dict(zip(col_names, row))
        return cls(
            content_hash=values.get("content_hash", ""),
            hash_prefix=values.get("hash_prefix", ""),
            song_id=values.get("song_id"),
            original_filename=values.get("original_filename", ""),
            file_size_bytes=values.get("file_size_bytes", 0),
            imported_at=values.get("imported_at", ""),
            r2_audio_url=values.get("r2_audio_url"),
            r2_stems_url=values.get("r2_stems_url"),
            r2_lrc_url=values.get("r2_lrc_url"),
            duration_seconds=values.get("duration_seconds"),
            tempo_bpm=values.get("tempo_bpm"),
            musical_key=values.get("musical_key"),
            musical_mode=values.get("musical_mode"),
            key_confidence=values.get("key_confidence"),
            loudness_db=values.get("loudness_db"),
            beats=values.get("beats"),
            downbeats=values.get("downbeats"),
            sections=values.get("sections"),
            embeddings_shape=values.get("embeddings_shape"),
            analysis_status=values.get("analysis_status", "pending"),
            analysis_job_id=values.get("analysis_job_id"),
            lrc_status=values.get("lrc_status", "pending"),
            lrc_job_id=values.get("lrc_job_id"),
            youtube_url=values.get("youtube_url"),
            visibility_status=values.get("visibility_status"),
            deleted_at=values.get("deleted_at"),
            download_status=values.get("download_status", "pending"),
            created_at=values.get("created_at"),
            updated_at=values.get("updated_at"),
        )
    else:
        # Legacy positional fallback for callers that don't provide description
        return cls._from_row_positional(row)
```

**Caller updates:** Every `from_row()` call site must pass `cursor.description` after executing a query. Helper function to reduce boilerplate:

```python
# In client.py and read_client.py
def _query_models(self, cursor, model_cls, query, params=None):
    """Execute query and return model instances with column-name mapping."""
    cursor.execute(query, params or [])
    description = cursor.description
    return [model_cls.from_row(tuple(row), description) for row in cursor.fetchall()]
```

**Catalog service JOIN queries:** After the dict-based refactor, `RECORDING_COLUMNS_FOR_JOIN` ordering no longer matters — columns can be in any order since mapping is by name, not position. `SONG_COLUMN_COUNT` and `RECORDING_COLUMN_COUNT` are still needed for slicing the combined row tuple, but the `from_row()` calls pass `description` so position within the slice doesn't matter.

**Benefits:**
- Eliminates the `RECORDING_COLUMNS_FOR_JOIN` column-order bug class entirely
- Makes `SELECT *` queries safe (column order doesn't matter)
- Removes the need to update `from_row()` positional indices when adding columns
- Reduces the future column addition checklist from 6 steps to 3

## Files to Modify

| File | Change |
|------|--------|
| `src/.../admin/db/schema.py` | Add `COLUMN_MIGRATIONS` + `apply_column_migrations()`. Fix `RECORDING_COLUMNS_FOR_JOIN` column order. |
| `src/.../admin/db/models.py` | Add dict-based `from_row()` for `Song` and `Recording` with `description` parameter. Keep positional fallback for backward compat. Add `_RECORDING_COLUMN_MAP` / `_SONG_COLUMN_MAP`. |
| `src/.../admin/db/client.py` | Refactor `initialize_schema()` to use `apply_column_migrations()`. Add two-phase sync + integrity check + post-sync validation in `sync()`. Update all `from_row()` callers to pass `cursor.description`. |
| `src/.../admin/commands/db.py` | Remove `turso-bootstrap` and `tokens` commands. Keep `init`, `sync`, `status`, `reset`, `path`. |
| `src/.../app/db/read_client.py` | Refactor `_migrate_schema()` to use `apply_column_migrations()`. Update all `from_row()` callers to pass `cursor.description`. |
| `src/.../app/services/catalog.py` | Update `from_row()` callers to pass `cursor.description`. |
| `src/.../admin/services/sync.py` | Add safe auto-recovery in `_execute_sync_with_recovery()` with backup + Turso health check. |
| `scripts/turso-init.py` | New file — one-time Turso provisioning (moved from `turso-bootstrap` + `--seed`). Add `--force` confirmation prompt. |

## Future Column Addition Checklist

When adding a new column in the future:

1. Add the column to `CREATE_TABLE` in `schema.py` (for fresh DBs)
2. Add entry to `COLUMN_MIGRATIONS` in `schema.py` (for existing DBs) — **must append, not reorder**
3. Add the field to `_RECORDING_COLUMN_MAP` / `_SONG_COLUMN_MAP` in `models.py`
4. Done — `db sync` auto-applies the new column to both local and Turso

**No longer needed** (eliminated by dict-based `from_row()`):
- ~~Add the column to `RECORDING_COLUMNS_FOR_JOIN` / `SONG_COLUMNS_FOR_JOIN` in the correct position~~
- ~~Update `RECORDING_COLUMN_COUNT` / `SONG_COLUMN_COUNT`~~ (still used for JOIN slicing but doesn't need updating on column addition)
- ~~Update `from_row()` positional mapping~~

## Verification

1. **Migration centralization test:** Add a temporary new column entry to `COLUMN_MIGRATIONS`. Run `sow_admin db sync` and verify it appears in both local DB and Turso.

2. **Column order test:** Run the app's catalog service and verify `Recording.from_row()` returns correct values for `created_at`, `updated_at`, `youtube_url`, `visibility_status`, `deleted_at`, `download_status` (no silent swaps).

3. **Dict-based from_row test:** Execute a `SELECT *` query with columns in a shuffled order (via explicit column list). Verify `Recording.from_row(row, cursor.description)` correctly maps fields regardless of column order.

4. **Integrity check test:** Corrupt the local WAL file manually, then run `sow_admin db sync` and verify it detects corruption and suggests recovery.

5. **Safe auto-recovery test:** Simulate a malformed DB, run `sow_admin db sync` (via SyncService), and verify:
   - Timestamped backup is created in `{db_path}.bak-{timestamp}/`
   - Turso health is checked before deletion
   - Local DB is deleted and re-synced from Turso
   - `apply_column_migrations()` runs on next sync

6. **Two-phase sync test:** Start with a local DB that has one more column than Turso. Run `db sync` and verify:
   - Schema is committed to local DB before sync
   - First sync pushes schema to Turso
   - Second sync pulls data from Turso
   - No "malformed" error occurs

7. **Post-sync validation test:** Simulate a scenario where Turso has fewer columns than expected after sync. Verify `SyncError` is raised with schema mismatch message.

8. **Full recovery test (immediate fix for current corruption):**
   ```bash
   rm -f ~/.config/sow-admin/db/sow.db ~/.config/sow-admin/db/sow.db-*
   sow_admin db init
   sow_admin db sync
   sow_admin audio list    # should work without "malformed" error
   sow_admin catalog list  # should work without "malformed" error
   ```

9. **turso-init script test:**
   ```bash
   python scripts/turso-init.py --seed  # one-time setup
   sow_admin db sync                     # subsequent sync works
   ```

10. **Existing tests:**
    ```bash
    PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ \
      --ignore=tests/services/analysis \
      --ignore=services/qwen3/tests \
      --ignore=services/analysis/tests -v
    ```
