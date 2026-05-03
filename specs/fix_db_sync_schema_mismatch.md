# Fix: db sync fails for new admin onboarding (schema mismatch + Hrana duplicate column)

**Date:** 2026-05-03
**Status:** Spec
**Branch:** `robust_schema_updates`

## Problem

After `db init --force` + `db sync` on a new machine, sync fails with one of:
```
Schema mismatch after sync: recordings has 28 columns, expected 30.
```
or (after partial fix):
```
Unexpected error: Hrana: `stream error: `Error { message: "SQLite error: duplicate column name: youtube_url", code: "SQLITE_UNKNOWN" }``
```

## Root Causes

### 1. `RECORDING_COLUMN_COUNT = 30` is wrong

The recordings DDL defines exactly **29** columns (verified by counting DDL + `RECORDING_COLUMNS_FOR_JOIN`). The constant should be 29. The validation always fails.

### 2. Turso remote has only 28 columns

Turso was never migrated to add `download_status` (the 29th column). After `conn.sync()` pulls from remote, local gets 28 columns. Post-sync migration is needed.

### 3. `apply_column_migrations` uses catch-error approach that breaks libsql replication

**This is the critical bug.** Current approach:
```python
cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
```
If column exists → `sqlite3.OperationalError` is caught locally → no-op.

**But with libsql (Turso-connected):** The ALTER TABLE still gets recorded in libsql's WAL even when it errors locally. When `conn.sync()` pushes to Turso, Turso also has the column → Hrana raises "duplicate column name" as a generic `Exception` that isn't caught.

**Fix:** Check column existence via `PRAGMA table_info` BEFORE issuing ALTER TABLE. Only issue ALTER TABLE for columns genuinely missing. This way no duplicate ALTER TABLE gets pushed to Turso.

### 4. Metadata recovery condition too narrow

`sync.py` line 289 checks for `"metadata file does not"` but the SyncError message says `"metadata is missing or invalid"`. Recovery doesn't trigger reliably.

## Solution

### Fix 1: Rewrite `apply_column_migrations` to check-then-alter

**File:** `src/stream_of_worship/admin/db/schema.py`

```python
def apply_column_migrations(cursor) -> None:
    """Apply column migrations only for columns that don't exist yet.

    Uses PRAGMA table_info to check existence first, avoiding ALTER TABLE
    on columns that already exist. This is critical for libsql connections
    where a caught ALTER TABLE error still gets replicated to Turso via Hrana,
    causing "duplicate column name" errors on sync.

    Args:
        cursor: Database cursor to execute migrations on.
    """
    # Build set of existing columns per table
    existing_columns: dict[str, set[str]] = {}
    tables_needed = {table for table, _, _ in COLUMN_MIGRATIONS}
    for table in tables_needed:
        try:
            cursor.execute(f"PRAGMA table_info({table})")
            existing_columns[table] = {row[1] for row in cursor.fetchall()}
        except (sqlite3.OperationalError, *_LIBSQL_ERROR):
            existing_columns[table] = set()  # Table doesn't exist yet

    # Only ALTER TABLE for genuinely missing columns
    for table, column, col_type in COLUMN_MIGRATIONS:
        if column not in existing_columns.get(table, set()):
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            except (sqlite3.OperationalError, *_LIBSQL_ERROR):
                pass  # Race condition safety net
```

### Fix 2: Correct `RECORDING_COLUMN_COUNT`

**File:** `src/stream_of_worship/admin/db/schema.py` (line 233)

```python
RECORDING_COLUMN_COUNT = 29
```

### Fix 3: Restore `COLUMN_MIGRATIONS`

**File:** `src/stream_of_worship/admin/db/schema.py`

Keep the current restored list (already done):
```python
COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    ("recordings", "youtube_url", "TEXT"),
    ("recordings", "visibility_status", "TEXT"),
    ("songs", "deleted_at", "TIMESTAMP"),
    ("recordings", "deleted_at", "TIMESTAMP"),
    ("recordings", "download_status", "TEXT DEFAULT 'pending'"),
]
```

### Fix 4: Broaden metadata recovery condition

**File:** `src/stream_of_worship/admin/services/sync.py` (line 289)

```python
# Before:
if "metadata file does not" in error_msg.lower() and attempt < max_attempts:

# After:
if ("metadata file does not" in error_msg.lower() or "metadata is missing" in error_msg.lower()) and attempt < max_attempts:
```

### Fix 5: Handle "duplicate column" in second sync (defensive)

**File:** `src/stream_of_worship/admin/db/client.py` (line 218-223, the post-sync second `conn.sync()`)

The second `conn.sync()` already has `except Exception: pass`, which handles the case where the ALTER TABLE for the genuinely-missing column gets pushed but Turso has some issue. This is fine as-is — the check-then-alter approach in Fix 1 eliminates the duplicate column scenario.

## Sync Flow After Fix

```
client.sync():
  1. PRAGMA integrity_check
  2. apply_column_migrations(cursor)  ← checks PRAGMA table_info first, only ALTERs missing cols
  3. conn.commit()                    ← only commits genuinely new ALTER TABLEs (if any)
  4. conn.sync()                      ← pushes new columns to Turso (if any), pulls remote data
  5. apply_column_migrations(cursor)  ← post-sync: adds columns missing from remote schema
  6. conn.commit()
  7. conn.sync()                      ← pushes post-sync column additions to Turso
  8. _validate_schema()               ← checks against RECORDING_COLUMN_COUNT=29
  9. update sync_metadata timestamp
```

## Files to Modify

| File | Change |
|------|--------|
| `src/stream_of_worship/admin/db/schema.py` | Rewrite `apply_column_migrations` to check-then-alter; fix `RECORDING_COLUMN_COUNT` to 29 |
| `src/stream_of_worship/admin/services/sync.py` | Broaden metadata recovery condition |

## Verification

```bash
# 1. Simulate new machine
rm -rf ~/.config/sow-admin/db

# 2. Init (creates vanilla SQLite with 29 columns from DDL)
sow_admin db init --force

# 3. Sync (metadata recovery → libsql reconnect → sync from Turso → post-sync migrate → push back)
sow_admin db sync

# 4. Verify
sow_admin db status
sow_admin catalog list

# 5. Verify idempotent re-sync (no duplicate column errors)
sow_admin db sync
```

### Edge cases:
- Turso already has 29 columns → no ALTER TABLE issued, sync is clean
- Turso has 28 columns → post-sync ALTER adds `download_status`, second sync pushes to Turso
- Local has 29, Turso has 28 → pre-sync ALTER is no-op (local already has all), first sync pulls 28-col data, post-sync ALTER adds missing col, second sync pushes

## Why check-then-alter instead of catch-error

With libsql embedded replicas, there's no way to "catch and swallow" a failed ALTER TABLE purely locally. The libsql client records ALL SQL in its WAL for replication, including statements that will fail on the remote. The only safe approach is to never issue the ALTER TABLE in the first place when the column already exists.
