# Fix Schema Sync Corruption & Centralize Column Migrations (v3)

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

1. **`db sync` auto-migrates with sync + post-validation** — applies column migrations to local DB, commits, syncs with Turso, then validates schema. Post-sync validation is the primary defense against schema drift.
2. **`turso-bootstrap` → `sow_admin infra turso-init`** — one-time infrastructure provisioning under a separate CLI subgroup. Separated from daily-use `db` commands to prevent accidental re-runs. Includes `--seed` flag for initial data migration.
3. **Centralized `COLUMN_MIGRATIONS`** in `schema.py` — single source of truth, referenced by all code paths.
4. **`db sync` integrity check** — refuses to sync if local DB is corrupted, suggests recovery.
5. **Dict-based `from_row()`** — eliminates column-order fragility by mapping column names to fields instead of relying on positional indices. `description` parameter is **required** (no positional fallback). Prevents the entire class of silent data corruption bugs.
6. **Post-sync schema validation** — verifies column counts match after sync to detect mismatch before it causes corruption. Expected counts derived from schema definitions.
7. **Safe auto-recovery** — creates timestamped backup (copy, not move) before deleting local DB; verifies Turso health including row counts before nuking local data; calls `initialize_schema()` before retry sync.

## Changes from v2

| # | Change | Reason |
|---|--------|--------|
| 1 | Two-phase sync → single sync + post-validation | libsql `conn.sync()` is bidirectional; calling it twice doesn't guarantee push-before-pull. Post-sync validation is the real defense. |
| 2 | `scripts/turso-init.py` → `sow_admin infra turso-init` | Stays in CLI config/test ecosystem, discoverable via `--help`, won't drift from codebase. |
| 3 | `_verify_turso_health()` checks row counts | Prevents data loss when Turso has correct schema but 0 rows (e.g., re-bootstrapped without `--seed`). |
| 4 | Recovery retry calls `initialize_schema()` before sync | After deleting local DB, tables don't exist. `apply_column_migrations()` ALTERs silently fail on nonexistent tables. |
| 5 | `_backup_local_db()` uses `copy2` not `move` | Crash-safe: if process dies mid-backup, original files remain intact. |
| 6 | `from_row()` `description` is required, no positional fallback | Eliminates latent bug class where callers forget `description` and silently get fragile positional path. |
| 7 | `list_recordings_with_songs()` uses `RECORDING_COLUMN_COUNT` for slicing | `r.*` + hardcoded `[:-3]` breaks on next column addition. Explicit slice boundary is future-proof. |
| 8 | Column count constants must be updated on column addition | Post-sync validation and JOIN slicing depend on accurate counts. Added back to future checklist. |
| 9 | Recovery error messages include backup location | User can manually restore if Turso sync fails after recovery. |

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
    for table, column, col_type in COLUMN_MIGRATIONS:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        except sqlite3.OperationalError:
            pass
```

Any future column addition only needs one entry here. All code paths call `apply_column_migrations()`.

**Ordering constraint:** Entries MUST be in the same order the columns were historically added. Reordering entries changes the physical column position in SQLite (since `ALTER TABLE ADD COLUMN` appends columns). After the dict-based `from_row()` refactor (step 9), ordering no longer affects correctness but should still be maintained for consistency.

### 2. Fix `RECORDING_COLUMNS_FOR_JOIN` column order (immediate fix)

**File:** `src/stream_of_worship/admin/db/schema.py:191-199`

The current explicit column list puts `youtube_url`, `visibility_status`, `download_status` before `created_at`, `updated_at`, `deleted_at`, but `Recording.from_row()` (models.py:214-220) expects them after.

**Current (broken) order:**
```
r.youtube_url, r.visibility_status, r.download_status, r.created_at, r.updated_at, r.deleted_at
```

**Fixed order (matches DDL + ALTER TABLE append order):**
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

### 5. Make `db sync` self-healing (integrity check + migrate + sync + post-validation)

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

4. conn.sync()
   → bidirectional sync with Turso
   → pushes local schema changes + pulls remote data

5. Post-sync validation: PRAGMA table_info
   → verify local column count matches expected count
   ├─ MATCH → continue
   └─ MISMATCH → raise SyncError (schema drift detected after sync)

6. Update sync_metadata timestamp
```

**Why single sync, not two-phase?** libsql's `conn.sync()` is bidirectional — it pushes local WAL frames and pulls remote WAL frames in one call. Calling it twice doesn't guarantee push-before-pull sequencing. Instead, we rely on: (a) committing schema changes locally before sync ensures the local WAL includes the DDL, and (b) post-sync validation catches any schema drift that slipped through.

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
    self.connection.commit()

    # Sync with Turso (bidirectional: pushes local changes, pulls remote changes)
    try:
        self.connection.sync()
    except Exception as e:
        raise SyncError(f"Sync failed: {e}", cause=e)

    # Post-sync validation: verify schema matches expected column counts
    self._validate_schema(cursor)

    self.update_sync_metadata("last_sync_at", datetime.now().isoformat())


def _validate_schema(self, cursor) -> None:
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

Extend `_execute_sync_with_recovery()` to handle "malformed" errors with safe recovery.

**Key safety measures (new in v3):**
- `_verify_turso_health()` checks row counts, not just schema existence
- `_backup_local_db()` uses `shutil.copy2` (crash-safe), not `shutil.move`
- Recovery retry calls `initialize_schema()` before sync (tables must exist for migrations)
- Error messages include backup location for manual restore

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

            # Step 1: Verify Turso is healthy AND has data before nuking local
            if not self._verify_turso_health():
                raise SyncNetworkError(
                    "Cannot auto-recover: Turso remote appears unhealthy or empty. "
                    "Manual intervention required. "
                    f"Original error: {e}"
                )

            # Step 2: Create timestamped backup (copy, not move — crash-safe)
            backup_dir = self._backup_local_db()

            # Step 3: Delete local DB and sidecar files
            self._delete_local_db()

            # Step 4: Initialize schema before retry (tables must exist for migrations)
            try:
                init_client = DatabaseClient(
                    self.db_path,
                    turso_url=self.turso_url,
                    turso_token=self.turso_token or os.environ.get("SOW_TURSO_TOKEN"),
                )
                init_client.initialize_schema()
                init_client.close()
            except Exception as init_err:
                raise SyncNetworkError(
                    f"Recovery failed during schema initialization. "
                    f"Backup saved at: {backup_dir}. "
                    f"To restore: cp {backup_dir}/sow.db {self.db_path}. "
                    f"Error: {init_err}"
                )

            # Step 5: Retry sync
            try:
                return self._execute_sync_with_recovery(
                    attempt=attempt + 1, max_attempts=max_attempts
                )
            except Exception as retry_err:
                raise SyncNetworkError(
                    f"Recovery sync failed. "
                    f"Backup saved at: {backup_dir}. "
                    f"To restore: cp {backup_dir}/sow.db {self.db_path}. "
                    f"Error: {retry_err}"
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
    """Check that Turso remote is reachable, has valid schema, and has data.

    Returns True only if Turso has a recordings table with columns AND
    at least one song row. This prevents auto-recovery from nuking local
    data when Turso is empty (e.g., re-bootstrapped without --seed).
    """
    try:
        conn = libsql.connect(
            ":memory:",
            sync_url=self.turso_url,
            auth_token=self.turso_token or os.environ.get("SOW_TURSO_TOKEN", ""),
        )
        conn.sync()
        cursor = conn.cursor()

        # Check schema exists
        cursor.execute("PRAGMA table_info(recordings)")
        columns = cursor.fetchall()
        if len(columns) == 0:
            return False

        # Check data exists — refuse recovery if Turso is empty
        cursor.execute("SELECT COUNT(*) FROM songs")
        song_count = cursor.fetchone()[0]
        if song_count == 0:
            return False

        conn.close()
        return True
    except Exception:
        return False


def _backup_local_db(self) -> Path:
    """Create timestamped backup of local DB before recovery.

    Uses copy (not move) so a crash during backup leaves originals intact.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    backup_dir = self.db_path.parent / f"{self.db_path.name}.bak-{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    # Copy db file and all sidecar files into backup
    if self.db_path.exists():
        shutil.copy2(self.db_path, backup_dir / self.db_path.name)
    for sibling in self.db_path.parent.glob(f"{self.db_path.name}-*"):
        shutil.copy2(sibling, backup_dir / sibling.name)

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

### 7. Move `turso-bootstrap` to `sow_admin infra turso-init`

**Modify:** `src/stream_of_worship/admin/commands/db.py`

Remove the `turso_bootstrap` command and the `tokens` command from the `db` CLI group. The `db` subgroup becomes:

- `db init` — first-time local DB creation
- `db sync` — routine sync (auto-migrates columns)
- `db status` — show DB info
- `db reset` — destructive reset
- `db path` — show DB path

**New CLI subgroup:** `sow_admin infra`

Add a new `infra` command group in a new file `src/stream_of_worship/admin/commands/infra.py`. Register it in the main CLI entry point.

- `infra turso-init` — one-time Turso provisioning (ported from `turso-bootstrap`)
  - `--seed` — copy local data to Turso after schema creation
  - `--force` — overwrite existing remote data (with `--seed`). Requires confirmation prompt.

Implementation (ported from `turso-bootstrap` command, db.py:399-610):

1. Load admin config (via existing `AdminConfig`)
2. Connect to Turso via libsql (embedded replica)
3. Create schema: tables, indexes, triggers (via `ALL_SCHEMA_STATEMENTS`)
4. Run `apply_column_migrations(cursor)` — adds all migration columns including `download_status`
5. `conn.commit()` — persist schema
6. If `--seed`: copy local data to Turso (batched, transactional)
7. `conn.sync()` → push to remote
8. Print success/failure

**Safety guard:** When `--force` is used with existing remote data, require interactive confirmation (typing the database name), matching the `db reset --confirm` pattern.

Benefits over standalone script:
- Uses existing `AdminConfig` for config loading
- Stays in CLI test ecosystem (`tests/admin/commands/`)
- Discoverable via `sow_admin infra --help`
- Won't drift from codebase after refactors

### 8. Recover the corrupted local DB

**Prerequisites:** Deploy code fixes (steps 1-7) first. Verify Turso schema is correct via `sow_admin infra turso-init` (which will apply `download_status` migration to Turso).

**Steps:**

```bash
# 0. PRESERVE sow_backup.db as safety net — do NOT delete it
#    It has 685 songs, 73 recordings (28 cols, no download_status)

# 1. Ensure Turso has correct schema (adds download_status if missing)
sow_admin infra turso-init

# 2. Delete the corrupted local DB + sidecar files
rm -f ~/.config/sow-admin/db/sow.db ~/.config/sow-admin/db/sow.db-*

# 3. Re-init (creates fresh local schema with all 29 columns)
sow_admin db init

# 4. Re-sync (auto-migrates + syncs from Turso)
sow_admin db sync

# 5. Verify
sow_admin db status
sow_admin audio list
sow_admin catalog list

# 6. If sync failed in step 4 (network/auth error), restore from backup:
#    cp ~/.config/sow-admin/db/sow_backup.db ~/.config/sow-admin/db/sow.db
#    sow_admin db init    # applies download_status migration
#    sow_admin db sync    # retry sync
```

**Why not use `--seed`?** Turso already has all 685 songs and 73 recordings from the prior successful sync. Seeding from the backup is unnecessary and risks introducing `download_status = NULL` for existing rows (since the backup has only 28 columns).

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

**Solution:** Replace positional mapping with column-name-based mapping using `cursor.description`. The `description` parameter is **required** — no positional fallback. This eliminates the entire bug class rather than leaving it latent behind a default `None`.

```python
# In models.py

@classmethod
def from_row(cls, row: tuple, description: tuple) -> "Recording":
    """Create a Recording from a database row tuple.

    Args:
        row: Database row tuple.
        description: Column descriptions from cursor.description. Required.
    """
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
```

Same pattern for `Song.from_row()`:

```python
@classmethod
def from_row(cls, row: tuple, description: tuple) -> "Song":
    col_names = [desc[0] for desc in description]
    values = dict(zip(col_names, row))
    return cls(
        id=values.get("id", ""),
        title=values.get("title", ""),
        title_pinyin=values.get("title_pinyin"),
        composer=values.get("composer"),
        lyricist=values.get("lyricist"),
        album_name=values.get("album_name"),
        album_series=values.get("album_series"),
        musical_key=values.get("musical_key"),
        lyrics_raw=values.get("lyrics_raw"),
        lyrics_lines=values.get("lyrics_lines"),
        sections=values.get("sections"),
        source_url=values.get("source_url", ""),
        table_row_number=values.get("table_row_number"),
        scraped_at=values.get("scraped_at", ""),
        created_at=values.get("created_at"),
        updated_at=values.get("updated_at"),
        deleted_at=values.get("deleted_at"),
    )
```

**Caller updates:** Every `from_row()` call site must pass `cursor.description` after executing a query.

**Admin client helper** (client.py) to reduce boilerplate:

```python
def _query_models(self, cursor, model_cls, query, params=None):
    cursor.execute(query, params or [])
    description = cursor.description
    return [model_cls.from_row(tuple(row), description) for row in cursor.fetchall()]
```

**Admin client `list_recordings_with_songs()`** (client.py:660-748) — fix `r.*` + hardcoded slice:

Replace `row_tuple[:-3]` / `row_tuple[-3:]` with explicit slicing using `RECORDING_COLUMN_COUNT`:

```python
# Before (fragile — breaks when column count changes):
recording_cols = row_tuple[:-3]
song_title = row_tuple[-3]

# After (robust — uses constant for slice boundary):
recording_cols = row_tuple[:RECORDING_COLUMN_COUNT]
# Remaining columns are song_title, album_name, album_series
song_title = row_tuple[RECORDING_COLUMN_COUNT]
album_name = row_tuple[RECORDING_COLUMN_COUNT + 1]
album_series_val = row_tuple[RECORDING_COLUMN_COUNT + 2]
```

Since `r.*` returns columns in physical order, we need to construct a `description` for the recording slice. The cursor's `description` covers all columns including the song joins, so slice it:

```python
rec_description = cursor.description[:RECORDING_COLUMN_COUNT]
recording = Recording.from_row(recording_cols, rec_description)
```

**Catalog service JOIN queries** (catalog.py:199-360):

After the dict-based refactor, `RECORDING_COLUMNS_FOR_JOIN` ordering no longer matters — columns can be in any order since mapping is by name, not position. Update callers to pass description slices:

```python
cursor.execute(query, params)
description = cursor.description
song_desc = description[:SONG_COLUMN_COUNT]
rec_desc = description[SONG_COLUMN_COUNT:]

for row in cursor.fetchall():
    row_tuple = tuple(row)
    song = Song.from_row(row_tuple[:SONG_COLUMN_COUNT], song_desc)
    recording = Recording.from_row(row_tuple[SONG_COLUMN_COUNT:], rec_desc)
    result.append(SongWithRecording(song=song, recording=recording))
```

**Benefits:**
- Eliminates the `RECORDING_COLUMNS_FOR_JOIN` column-order bug class entirely
- Makes `SELECT *` queries safe (column order doesn't matter)
- Removes the need to update `from_row()` positional indices when adding columns
- No latent positional fallback — compile-time errors if `description` is missing

## Files to Modify

| File | Change |
|------|--------|
| `src/.../admin/db/schema.py` | Add `COLUMN_MIGRATIONS` + `apply_column_migrations()`. Fix `RECORDING_COLUMNS_FOR_JOIN` column order. |
| `src/.../admin/db/models.py` | Dict-based `from_row()` for `Song` and `Recording` with required `description` parameter. Remove positional mapping. |
| `src/.../admin/db/client.py` | Refactor `initialize_schema()` to use `apply_column_migrations()`. Add integrity check + post-sync validation in `sync()`. Update all `from_row()` callers. Fix `list_recordings_with_songs()` slice to use `RECORDING_COLUMN_COUNT`. |
| `src/.../admin/commands/db.py` | Remove `turso-bootstrap` and `tokens` commands. Keep `init`, `sync`, `status`, `reset`, `path`. |
| `src/.../admin/commands/infra.py` | **New file** — `infra` CLI subgroup with `turso-init` command (ported from `turso-bootstrap`). |
| `src/.../app/db/read_client.py` | Refactor `_migrate_schema()` to use `apply_column_migrations()`. Update all `from_row()` callers to pass `cursor.description`. |
| `src/.../app/services/catalog.py` | Update `from_row()` callers to pass description slices. |
| `src/.../admin/services/sync.py` | Add safe auto-recovery with copy-based backup, row-count health check, `initialize_schema()` before retry, backup location in error messages. |

## Future Column Addition Checklist

When adding a new column in the future:

1. Add the column to `CREATE_TABLE` in `schema.py` (for fresh DBs)
2. Add entry to `COLUMN_MIGRATIONS` in `schema.py` (for existing DBs) — **must append, not reorder**
3. Add the field to `from_row()` dict mapping in `models.py`
4. Update `RECORDING_COLUMN_COUNT` or `SONG_COLUMN_COUNT` in `schema.py`
5. Done — `db sync` auto-applies the new column to both local and Turso

## Verification

1. **Migration centralization test:** Add a temporary new column entry to `COLUMN_MIGRATIONS`. Run `sow_admin db sync` and verify it appears in both local DB and Turso.

2. **Column order test:** Run the app's catalog service and verify `Recording.from_row()` returns correct values for `created_at`, `updated_at`, `youtube_url`, `visibility_status`, `deleted_at`, `download_status` (no silent swaps).

3. **Dict-based from_row test:** Execute a `SELECT *` query with columns in a shuffled order (via explicit column list). Verify `Recording.from_row(row, cursor.description)` correctly maps fields regardless of column order.

4. **Integrity check test:** Corrupt the local WAL file manually, then run `sow_admin db sync` and verify it detects corruption and suggests recovery.

5. **Safe auto-recovery test:** Simulate a malformed DB, run `sow_admin db sync` (via SyncService), and verify:
   - Turso health check validates both schema AND row counts
   - Timestamped backup is created via `copy2` (not `move`) in `{db_path}.bak-{timestamp}/`
   - `initialize_schema()` runs before retry sync
   - Error messages include backup location if retry fails

6. **Post-sync validation test:** Simulate a scenario where Turso has fewer columns than expected after sync. Verify `SyncError` is raised with schema mismatch message.

7. **Full recovery test (immediate fix for current corruption):**
   ```bash
   sow_admin infra turso-init   # ensure Turso has download_status
   rm -f ~/.config/sow-admin/db/sow.db ~/.config/sow-admin/db/sow.db-*
   sow_admin db init
   sow_admin db sync
   sow_admin audio list    # should work without "malformed" error
   sow_admin catalog list  # should work without "malformed" error
   ```

8. **Infra turso-init test:**
   ```bash
   sow_admin infra turso-init --seed    # one-time setup
   sow_admin db sync                     # subsequent sync works
   ```

9. **from_row description required test:** Verify that calling `Recording.from_row(row)` without `description` raises `TypeError` (missing required argument). Ensures no caller silently falls back to positional mapping.

10. **list_recordings_with_songs slice test:** Add a column to `COLUMN_MIGRATIONS`, run `list_recordings_with_songs()`, verify the song columns (title, album_name, album_series) are correctly extracted regardless of recording column count.

11. **Existing tests:**
    ```bash
    PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ \
      --ignore=tests/services/analysis \
      --ignore=services/qwen3/tests \
      --ignore=services/analysis/tests -v
    ```
