# Fix `apply_column_migrations` libsql Error + Redundant COLUMN_MIGRATIONS

## Problem

Two interacting bugs in `apply_column_migrations()` cause `sow_admin infra turso-init` and `sow_admin db sync` (via auto-recovery) to crash with `duplicate column name: youtube_url` when using libsql connections.

### Bug 1: Redundant COLUMN_MIGRATIONS entries

Every entry in `COLUMN_MIGRATIONS` (schema.py:185-191) already exists in the corresponding `CREATE TABLE` DDL:

| COLUMN_MIGRATIONS entry | Already in DDL? | DDL location |
|---|---|---|
| `("recordings", "youtube_url", "TEXT")` | Yes | schema.py:69 |
| `("recordings", "visibility_status", "TEXT")` | Yes | schema.py:70 |
| `("songs", "deleted_at", "TIMESTAMP")` | Yes | schema.py:30 |
| `("recordings", "deleted_at", "TIMESTAMP")` | Yes | schema.py:71 |
| `("recordings", "download_status", "TEXT DEFAULT 'pending'")` | Yes | schema.py:72 |

This means `ALTER TABLE ADD COLUMN` always fails because the column already exists. For sqlite3 connections, this is harmlessly caught. For libsql connections, it's a crash (see Bug 2).

### Bug 2: `apply_column_migrations` only catches `sqlite3.OperationalError`, not `libsql.Error`

**File:** `src/stream_of_worship/admin/db/schema.py:194-208`

```python
def apply_column_migrations(cursor) -> None:
    for table, column, col_type in COLUMN_MIGRATIONS:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        except sqlite3.OperationalError:  # <-- ONLY catches sqlite3 errors
            pass
```

`libsql.Error` is **not** a subclass of `sqlite3.OperationalError`. When the cursor is from a libsql connection, the "duplicate column name" error raises `libsql.Error`, which propagates uncaught.

This affects three code paths that use libsql connections:

1. **`infra turso-init`** (commands/infra.py:114-118) — runs `ALL_SCHEMA_STATEMENTS` (which includes CREATE TABLE with the columns) then `apply_column_migrations(cursor)` on a libsql connection. Crash at first migration entry (`youtube_url`).

2. **`db sync` auto-recovery** (services/sync.py:257-263) — calls `init_client.initialize_schema()` which calls `apply_column_migrations()`. The `DatabaseClient` is created with Turso args, so it uses a libsql connection. Crash.

3. **`app/db/read_client.py:111-114`** — calls `apply_column_migrations(cursor)` on what may be a libsql connection (when `is_turso_enabled`). Crash on fresh app DB.

### Why it works with sqlite3

`db init` creates a `DatabaseClient(db_path)` with no Turso args, so `connection` property uses `sqlite3.connect()`. When `apply_column_migrations` tries `ALTER TABLE ADD COLUMN` for an existing column, `sqlite3.OperationalError` is raised and caught. No crash.

## Impact on Recovery Flows

The auto-recovery in `SyncService._execute_sync_with_recovery()` (sync.py:203-298) is currently broken for corrupted local DBs because:

1. Sync detects "malformed" → backs up local DB → deletes local DB
2. Creates `DatabaseClient(db_path, turso_url=..., turso_token=...)` → libsql connection
3. Calls `init_client.initialize_schema()` → calls `apply_column_migrations(cursor)` → crashes with `libsql.Error`

This means the **documented recovery path** (`db init` then `db sync`) only works if the user manually runs `db init` first (which uses sqlite3, avoiding the bug). The auto-recovery path within `db sync` itself is broken.

## Correct Manual Recovery Sequence

Given the current code (pre-fix):

```bash
# 1. Remove corrupted DB + sidecar files
rm -rf ~/.config/sow-admin/db
mkdir -p ~/.config/sow-admin/db

# 2. Create fresh local schema via sqlite3 (NOT libsql) — avoids the libsql.Error bug
sow_admin db init

# 3. Sync from Turso — libsql pulls remote data into the existing local DB
sow_admin db sync
```

**Why NOT `turso-init`?** `turso-init` is for one-time Turso remote provisioning (creating tables on the remote). If the Turso remote already has tables and data, you don't need it. `db init` + `db sync` is sufficient.

**Why `turso-init` fails even for its intended purpose:** Because it runs `ALL_SCHEMA_STATEMENTS` (includes CREATE TABLE with all columns) then `apply_column_migrations` on a libsql connection. The duplicate column error crashes it before it can do anything useful.

## Plan

### Fix 1: Remove redundant COLUMN_MIGRATIONS entries

**File:** `src/stream_of_worship/admin/db/schema.py:185-191`

Since all 5 current entries already exist in the `CREATE TABLE` DDL, they serve no purpose for fresh databases. For existing databases that were created before these columns were added to the DDL, these migrations were already applied successfully (because those DBs were using sqlite3 when `db init` was run).

Remove all entries:

```python
COLUMN_MIGRATIONS = [
    # ("recordings", "youtube_url", "TEXT"),         — already in CREATE_RECORDINGS_TABLE (line 69)
    # ("recordings", "visibility_status", "TEXT"),   — already in CREATE_RECORDINGS_TABLE (line 70)
    # ("songs", "deleted_at", "TIMESTAMP"),          — already in CREATE_SONGS_TABLE (line 30)
    # ("recordings", "deleted_at", "TIMESTAMP"),     — already in CREATE_RECORDINGS_TABLE (line 71)
    # ("recordings", "download_status", "TEXT DEFAULT 'pending'"),  — already in CREATE_RECORDINGS_TABLE (line 72)
]
```

Set `COLUMN_MIGRATIONS = []` (keep the list structure for future migrations).

**Safety argument:** These migrations were only needed for databases created before the columns were added to the CREATE TABLE DDL. Any such database has already been migrated (the columns exist in the DDL now, meaning `db init` on a fresh DB creates them, and `apply_column_migrations` on existing DBs applied them via sqlite3 which catches the duplicate error). The migrations are now dead code.

### Fix 2: Broaden exception handling in `apply_column_migrations` for libsql compatibility

**File:** `src/stream_of_worship/admin/db/schema.py:194-208`

Even with Fix 1 removing the current redundant entries, **future** column additions will add entries to `COLUMN_MIGRATIONS` that need idempotent `ALTER TABLE ADD COLUMN`. When these run on libsql connections, `libsql.Error` must be caught.

Change the except clause to catch both `sqlite3.OperationalError` and `libsql.Error`:

```python
try:
    import libsql as _libsql_module
    _LIBSQL_ERROR: tuple = (_libsql_module.Error,)
except ImportError:
    _LIBSQL_ERROR = ()


def apply_column_migrations(cursor) -> None:
    """Apply all column migrations to a database cursor.

    Idempotent: silently skips columns that already exist, regardless of
    whether the cursor is from sqlite3 or libsql.
    """
    for table, column, col_type in COLUMN_MIGRATIONS:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        except (sqlite3.OperationalError, *_LIBSQL_ERROR):
            pass
```

**Why conditional import?** The `admin` extra doesn't include `libsql`. The `turso` extra does. Using a conditional import with a tuple fallback means:
- When `libsql` is installed: `except (sqlite3.OperationalError, libsql.Error)` — catches both
- When `libsql` is not installed: `except (sqlite3.OperationalError,)` — catches sqlite3 only (no libsql connections possible anyway)

**Why not just `except Exception`?** Too broad — would silently swallow real errors like permission denied, disk full, etc. Catching only the specific "column already exists" errors from both backends is safer.

### Fix 3: Fix the auto-recovery path in SyncService

**File:** `src/stream_of_worship/admin/services/sync.py:257-263`

The recovery path creates a `DatabaseClient` with Turso args (libsql connection) then calls `initialize_schema()`. With Fix 2 applied, `apply_column_migrations` inside `initialize_schema` will properly catch `libsql.Error`. No code change needed here — it's fixed transitively.

However, there's a subtle issue: the recovery `DatabaseClient` connects to Turso to run `initialize_schema()`, but the local DB file doesn't exist yet (it was just deleted). The `libsql.connect()` call at `client.py:104-108` will create the file, but `CREATE TABLE IF NOT EXISTS` followed by `apply_column_migrations` will hit the same redundancy. With Fixes 1+2, this is handled correctly (empty migrations list + broadened exception catching).

**No code change needed in sync.py** — it's fixed transitively by Fixes 1+2.

### Fix 4: Fix the `turso-init` command

**File:** `src/stream_of_worship/admin/commands/infra.py:109-120`

Currently runs both `ALL_SCHEMA_STATEMENTS` (includes CREATE TABLE with all columns) and `apply_column_migrations()`. With Fix 1 (empty migrations list), the `apply_column_migrations()` call becomes a no-op. With Fix 2, any future migration entries will be handled idempotently.

**No code change needed in infra.py** — it's fixed transitively by Fixes 1+2.

### Fix 5: Fix `app/db/read_client.py`

**File:** `src/stream_of_worship/app/db/read_client.py:111-114`

Calls `apply_column_migrations(cursor)` on what may be a libsql connection. With Fixes 1+2 applied, this works correctly.

**No code change needed in read_client.py** — it's fixed transitively by Fixes 1+2.

## Correct Recovery Sequence (Post-Fix)

After applying Fixes 1+2:

```bash
# Option A: Manual recovery (recommended — clearest)
rm -rf ~/.config/sow-admin/db
mkdir -p ~/.config/sow-admin/db
sow_admin db init    # Creates fresh local schema via sqlite3
sow_admin db sync    # Pulls data from Turso

# Option B: Auto-recovery via db sync (now works after fix)
# If local DB is corrupted, db sync detects "malformed", auto-recovers:
#   1. Verifies Turso health
#   2. Backs up corrupted DB
#   3. Deletes local DB
#   4. Runs initialize_schema() (libsql connection — now works with Fix 2)
#   5. Retries sync
sow_admin db sync

# Option C: Fresh Turso setup (only for brand-new Turso databases)
sow_admin infra turso-init          # Creates schema on Turso remote
sow_admin infra turso-init --seed   # Also seeds data from local DB
```

### Why NOT `turso-init` for schema updates?

`turso-init` is for **one-time** Turso remote provisioning (creating tables on an empty remote). For ongoing schema updates:

- `db sync` calls `apply_column_migrations(cursor)` on the libsql embedded replica connection
- libsql embedded replicas forward **writes** (including DDL) to the Turso remote
- Therefore, `db sync` automatically pushes new column additions to Turso

The workflow for adding a new column becomes:

1. Add column to `CREATE_TABLE` DDL in `schema.py` (for fresh DBs)
2. Append entry to `COLUMN_MIGRATIONS` in `schema.py` (for existing DBs)
3. Update `from_row()` in `models.py`
4. Update `SONG_COLUMN_COUNT` or `RECORDING_COLUMN_COUNT` in `schema.py`
5. `db sync` applies the migration to both local and Turso automatically

`turso-init` is only needed when creating a brand-new Turso database that has no tables at all.

## Files to Modify

| File | Change | Reason |
|------|--------|--------|
| `src/.../admin/db/schema.py` | 1. Set `COLUMN_MIGRATIONS = []` (remove 5 redundant entries). 2. Add conditional `_LIBSQL_ERROR` import. 3. Broaden `apply_column_migrations` except clause. | Primary fix for both bugs |

**No other files need changes** — all other affected code paths (infra.py, sync.py, read_client.py, client.py) are fixed transitively.

## Verification

### 1. `turso-init` no longer crashes

```bash
sow_admin infra turso-init
# Should print "Schema created successfully!" instead of
# "duplicate column name: youtube_url"
```

### 2. `db sync` auto-recovery works with libsql

```bash
# Corrupt the local DB to trigger auto-recovery
rm -rf ~/.config/sow-admin/db
mkdir -p ~/.config/sow-admin/db
# Create a dummy corrupted DB
echo "not a database" > ~/.config/sow-admin/db/sow.db

sow_admin db sync
# Should auto-recover: detect corruption, verify Turso health,
# backup, delete, re-init schema (libsql), re-sync
```

### 3. `db init` still works (no regression)

```bash
rm -rf ~/.config/sow-admin/db
mkdir -p ~/.config/sow-admin/db
sow_admin db init
# Should print "Database initialized successfully!"
```

### 4. Future migration is idempotent on both sqlite3 and libsql

Add a temporary test migration:
```python
COLUMN_MIGRATIONS = [
    ("songs", "test_column", "TEXT"),
]
```

```bash
# First run: adds column
sow_admin db init    # sqlite3 — should add column, no crash
sow_admin db sync    # libsql — should add column to Turso, no crash

# Second run: column already exists — idempotent
sow_admin db init    # sqlite3 — OperationalError caught, no crash
sow_admin db sync    # libsql — libsql.Error caught, no crash
```

Remove the test migration entry afterward.

### 5. Existing tests pass

```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ \
  --ignore=tests/services/analysis \
  --ignore=services/qwen3/tests \
  --ignore=services/analysis/tests -v
```
