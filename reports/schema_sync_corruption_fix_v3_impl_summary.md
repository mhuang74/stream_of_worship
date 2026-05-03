# Schema Sync Corruption Fix - Implementation Summary

**Date:** 2026-05-03  
**Status:** Complete  
**PR:** #51  
**Spec:** `specs/fix_schema_sync_corruption_v3.md`

## Problem

1. **Schema Sync Corruption**: After adding `download_status` column to admin DB, Turso remote was never updated. When libsql sync applies WAL frames from a 28-column remote onto a 29-column local DB, B-tree page structure becomes inconsistent, causing "database disk image is malformed" errors.

2. **Silent Data Corruption**: `RECORDING_COLUMNS_FOR_JOIN` had columns in different order than `Recording.from_row()` expected, causing values for `created_at`, `updated_at`, `youtube_url`, `visibility_status`, `deleted_at`, `download_status` to be silently swapped in JOIN queries.

3. **No Single Source of Truth**: Three code paths each duplicated migration logic independently.

4. **Column-Order Fragility**: Positional `from_row()` (`row[0]`, `row[1]`, ...) silently mis-mapped fields when column order differed.

## Solution

### 1. Centralized Column Migrations (`schema.py`)

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

### 2. Sync Safety (`client.py`, `sync.py`)

New `sync()` flow:
1. PRAGMA integrity_check → verify local DB not corrupted
2. apply_column_migrations() → add missing columns locally
3. commit() → persist DDL before WAL frame application
4. conn.sync() → bidirectional sync with Turso
5. _validate_schema() → verify column counts match expected
6. Update sync_metadata timestamp

Auto-recovery for "malformed" errors:
- Verify Turso health (schema + row counts)
- Create timestamped backup with `shutil.copy2` (crash-safe)
- Delete local DB and sidecar files
- Initialize schema before retry (tables must exist for migrations)
- Include backup location in error messages

### 3. CLI Restructure

- **New:** `sow_admin infra turso-init` - one-time Turso provisioning
- **Removed:** `sow_admin db turso-bootstrap` and `sow_admin db tokens`

### 4. Dict-based from_row() (`models.py`)

```python
@classmethod
def from_row(cls, row: tuple, description: tuple) -> "Recording":
    col_names = [desc[0] for desc in description]
    values = dict(zip(col_names, row))
    return cls(
        content_hash=values.get("content_hash", ""),
        # ... all fields mapped by name
    )
```

Key changes:
- `description` parameter is **required** (no positional fallback)
- Maps columns by name, not position
- Eliminates the entire class of silent data corruption bugs
- Works regardless of column order in query results

### 5. Fix Column Order Bug

Fixed `RECORDING_COLUMNS_FOR_JOIN` to match `CREATE_TABLE` order:
```
# Before (broken - different order):
r.youtube_url, r.visibility_status, r.download_status, r.created_at, r.updated_at, r.deleted_at

# After (matches DDL + ALTER TABLE append order):
r.created_at, r.updated_at, r.youtube_url, r.visibility_status, r.deleted_at, r.download_status
```

Fixed `list_recordings_with_songs()` to use `RECORDING_COLUMN_COUNT`:
```python
# Before (fragile - breaks when column count changes):
recording_cols = row_tuple[:-3]

# After (robust - uses constant for slice boundary):
recording_cols = row_tuple[:RECORDING_COLUMN_COUNT]
rec_description = description[:RECORDING_COLUMN_COUNT]
recording = Recording.from_row(recording_cols, rec_description)
```

## Files Modified

| File | Change |
|------|--------|
| `admin/db/schema.py` | Add `COLUMN_MIGRATIONS`, `apply_column_migrations()`, add download_status to DDL, fix column order |
| `admin/db/models.py` | Dict-based `from_row()` for Song and Recording with required description |
| `admin/db/client.py` | Use centralized migrations, add sync safety, update all `from_row()` callers |
| `admin/services/sync.py` | Add auto-recovery with backup, Turso health check |
| `admin/commands/infra.py` | **New** - `infra` CLI subgroup with `turso-init` |
| `admin/commands/db.py` | Remove `turso-bootstrap` and `tokens` commands |
| `admin/main.py` | Register `infra` CLI group |
| `app/db/read_client.py` | Use centralized migrations, update `from_row()` callers |
| `app/services/catalog.py` | Fix JOIN query `from_row()` with description slices |
| `tests/admin/test_models.py` | Update tests for new `from_row()` signature |

## Post-Deployment Recovery

For users with corrupted local DB:

```bash
# 1. Ensure Turso has correct schema (adds download_status if missing)
sow_admin infra turso-init

# 2. Delete corrupted local DB + sidecar files
rm -f ~/.config/sow-admin/db/sow.db ~/.config/sow-admin/db/sow.db-*

# 3. Re-init (creates fresh local schema with all 30 columns)
sow_admin db init

# 4. Re-sync (auto-migrates + syncs from Turso)
sow_admin db sync

# 5. Verify
sow_admin db status
sow_admin audio list
sow_admin catalog list
```

## Future Column Addition Checklist

When adding a new column in the future:

1. Add the column to `CREATE_TABLE` in `schema.py` (for fresh DBs)
2. Add entry to `COLUMN_MIGRATIONS` in `schema.py` (for existing DBs) — **must append, not reorder**
3. Add the field to `from_row()` dict mapping in `models.py`
4. Update `RECORDING_COLUMN_COUNT` or `SONG_COLUMN_COUNT` in `schema.py`
5. Done — `db sync` auto-applies the new column to both local and Turso