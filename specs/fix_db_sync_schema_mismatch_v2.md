# Fix: db sync onboarding — schema mismatch, Hrana duplicate column, empty replica

**Date:** 2026-05-03
**Status:** Spec
**Branch:** `robust_schema_updates`

## Problem

After `db init --force` + `db sync` on a new machine, multiple failures:

1. **Schema mismatch**: `recordings has 28 columns, expected 30`
2. **Hrana duplicate column**: `duplicate column name: youtube_url`
3. **Empty replica**: sync "succeeds" but local has 0 songs

## Root Causes

### Bug 1: `RECORDING_COLUMN_COUNT = 30` is wrong

DDL defines **29** columns. Constant should be 29.

### Bug 2: `apply_column_migrations` uses catch-error approach incompatible with libsql

libsql replicates ALTER TABLE to Turso even when it errors locally. Must check column existence via PRAGMA first.

### Bug 3: `initialize_schema()` before sync prevents data pull (THE CRITICAL BUG)

**Verified experimentally:**
- Fresh `libsql.connect()` on non-existent path + `conn.sync()` → pulls 685 songs from Turso ✓
- `libsql.connect()` + `initialize_schema()` (creates empty tables) + `conn.sync()` → 0 songs ✗

**Why:** libsql embedded replicas treat locally-created tables as authoritative. If tables exist locally before the first sync, `conn.sync()` sees them as "local state" and doesn't overwrite with remote data. The sync is essentially a no-op for existing tables.

This affects:
- **`db init --force`** → creates tables via `DatabaseClient(db_path)` (sqlite3) → then `db sync` does metadata recovery → retry calls `initialize_schema()` → sync pulls 0 rows
- **"malformed" recovery** in `sync.py` line 258-273 → explicitly calls `init_client.initialize_schema()` before retry sync

### Bug 4: Turso remote missing `download_status` column (28 vs 29)

Turso has 28 columns. DDL has 29 (`download_status` was added to DDL but never pushed to Turso since `COLUMN_MIGRATIONS` was emptied).

### Bug 5: Metadata recovery condition too narrow

`sync.py` checks `"metadata file does not"` but SyncError says `"metadata is missing or invalid"`.

## Solution

### Fix 1: NEVER call `initialize_schema()` before first sync with Turso

The correct flow for syncing from Turso:
1. Delete local DB + sidecar files (clean slate)
2. `libsql.connect(path, sync_url=..., auth_token=...)` — creates empty file
3. `conn.sync()` — pulls ALL schema + data from Turso
4. `apply_column_migrations(cursor)` — adds any columns Turso is missing
5. `conn.commit()` + `conn.sync()` — pushes new columns back to Turso

**Files to change:**
- `src/stream_of_worship/admin/services/sync.py`: Remove `initialize_schema()` from "malformed" recovery path (lines 258-273)
- `src/stream_of_worship/admin/services/sync.py`: In metadata recovery retry, do NOT initialize schema
- `src/stream_of_worship/admin/db/client.py`: In `sync()`, remove or guard pre-sync migrations when DB is empty (no tables exist yet — let sync pull them first)

**New `sync()` flow in `client.py`:**
```python
def sync(self) -> None:
    cursor = self.connection.cursor()

    # Check if tables exist (determines if this is first sync or subsequent)
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='songs'")
    tables_exist = cursor.fetchone() is not None

    if tables_exist:
        # Existing replica: integrity check + pre-sync migrations
        # ... integrity check ...
        apply_column_migrations(cursor)
        self.connection.commit()

    # Sync with Turso
    conn = self.connection
    conn.sync()

    # Post-sync: apply migrations (remote may have fewer columns)
    apply_column_migrations(cursor)
    self.connection.commit()

    # Push migrations to Turso
    try:
        conn.sync()
    except Exception:
        pass  # Non-fatal

    self._validate_schema(cursor)
    self.update_sync_metadata("last_sync_at", datetime.now().isoformat())
```

### Fix 2: Rewrite `apply_column_migrations` to check-then-alter

**File:** `src/stream_of_worship/admin/db/schema.py`

```python
def apply_column_migrations(cursor) -> None:
    """Apply column migrations only for columns that don't exist yet.

    Uses PRAGMA table_info to check existence first. Critical for libsql
    where ALTER TABLE gets replicated to Turso even when it errors locally.
    """
    existing_columns: dict[str, set[str]] = {}
    tables_needed = {table for table, _, _ in COLUMN_MIGRATIONS}
    for table in tables_needed:
        try:
            cursor.execute(f"PRAGMA table_info({table})")
            existing_columns[table] = {row[1] for row in cursor.fetchall()}
        except (sqlite3.OperationalError, *_LIBSQL_ERROR):
            existing_columns[table] = set()

    for table, column, col_type in COLUMN_MIGRATIONS:
        if column not in existing_columns.get(table, set()):
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            except (sqlite3.OperationalError, *_LIBSQL_ERROR):
                pass
```

### Fix 3: Fix `RECORDING_COLUMN_COUNT`

**File:** `src/stream_of_worship/admin/db/schema.py`

```python
RECORDING_COLUMN_COUNT = 29
```

### Fix 4: Keep `COLUMN_MIGRATIONS` populated

Already restored. Must never be emptied again.

### Fix 5: Broaden metadata recovery condition

**File:** `src/stream_of_worship/admin/services/sync.py`

```python
if ("metadata file does not" in error_msg.lower() or "metadata is missing" in error_msg.lower()) and attempt < max_attempts:
```

### Fix 6: Remove `initialize_schema()` from recovery paths

**File:** `src/stream_of_worship/admin/services/sync.py`

In the "malformed" recovery (lines 258-273), remove the `init_client.initialize_schema()` step. Just delete files and retry — libsql will pull schema from Turso.

## Files to Modify

| File | Change |
|------|--------|
| `src/stream_of_worship/admin/db/schema.py` | Rewrite `apply_column_migrations` (check-then-alter), fix `RECORDING_COLUMN_COUNT` to 29 |
| `src/stream_of_worship/admin/db/client.py` | Guard pre-sync migrations: skip when no tables exist (first sync), keep post-sync migrations |
| `src/stream_of_worship/admin/services/sync.py` | Remove `initialize_schema()` from recovery paths, broaden metadata condition |

## Verification

```bash
# Full clean onboarding (the failing scenario)
rm -rf ~/.config/sow-admin/db
sow_admin db init --force
sow_admin db sync
sow_admin db status      # should show 685 songs, 73 recordings

# Alternative: sync without init (recommended path for new users)
rm -rf ~/.config/sow-admin/db
sow_admin db sync        # should recover + pull data in one step

# Idempotent re-sync (no errors, no data loss)
sow_admin db sync
sow_admin db sync

# Verify Turso now has 29 columns (download_status added)
# (check via turso-init or in-memory replica)
```

## Key Insight

**libsql embedded replicas have a fundamental constraint:** if tables exist locally before the first sync, `conn.sync()` treats local as authoritative and won't pull remote data. For onboarding, the ONLY correct approach is:

1. Clean local (delete everything)
2. `libsql.connect()` (creates empty file, no tables)
3. `conn.sync()` (pulls schema + data from Turso)
4. Then apply any post-sync migrations

Never create tables locally before the first Turso sync.
