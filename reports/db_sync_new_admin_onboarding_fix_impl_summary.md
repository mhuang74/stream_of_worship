# DB Sync New Admin Onboarding Fix - Implementation Summary

**Date:** 2026-05-03
**Status:** Completed

## Goal

Enable new admin users to create a local replica from Turso master with a single command: `sow_admin db sync`

## Problem

Five interacting bugs prevented `db sync` from working for new or recovering users:

1. `client.sync()` didn't catch `libsql.Error` from integrity check
2. `validate_config()` rejected missing DB even when Turso was configured
3. `_execute_sync_with_recovery()` didn't ensure DB directory exists
4. `sync_db()` command errors if config.toml doesn't exist
5. `show_status()` crashes on vanilla SQLite after `db init`

## Solution

### Fix 1: Catch libsql.Error in client.sync() and wrap as SyncError

**File:** `src/stream_of_worship/admin/db/client.py`

- Added `_LIBSQL_ERROR` tuple for conditional libsql error handling
- Added `libsql.Error` handling in integrity check block to catch metadata file errors
- Added `*_LIBSQL_ERROR` to `_validate_schema()` except clause

### Fix 2: Relax validate_config() when Turso is configured

**File:** `src/stream_of_worship/admin/services/sync.py`

- Changed "Database not found" check to only error when Turso URL is not configured
- When `turso_url` is set, `libsql.connect()` can create the DB from scratch

### Fix 3: Ensure DB directory exists in _execute_sync_with_recovery()

**File:** `src/stream_of_worship/admin/services/sync.py`

- Added `self.db_path.parent.mkdir(parents=True, exist_ok=True)` before creating DatabaseClient

### Fix 4: Create default config in sync_db() when missing

**File:** `src/stream_of_worship/admin/commands/db.py`

- Instead of erroring when config.toml is missing, creates default config
- User can then configure Turso URL and proceed with sync

### Fix 5: Fix show_status() crash on vanilla SQLite

**File:** `src/stream_of_worship/admin/commands/db.py`

- Changed `get_db_client(config)` to `DatabaseClient(db_path)` (sqlite3-only client)
- Avoids libsql metadata error when local DB is a vanilla SQLite file

## Files Modified

| File | Changes |
|------|---------|
| `src/stream_of_worship/admin/db/client.py` | +23 lines |
| `src/stream_of_worship/admin/services/sync.py` | +7 lines |
| `src/stream_of_worship/admin/commands/db.py` | +10 lines |

## Verification

### Test 1: New user (no config, no DB)
```bash
rm -rf ~/.config/sow-admin
# Configure Turso URL in config.toml
export SOW_TURSO_TOKEN=...
sow_admin db sync
# Expected: creates config, creates libsql replica, syncs from Turso
```

### Test 2: Existing user with vanilla SQLite (after `db init`)
```bash
rm -rf ~/.config/sow-admin/db
mkdir -p ~/.config/sow-admin/db
sow_admin db init      # creates vanilla SQLite (no -info sidecar)
sow_admin db sync      # should auto-recover and sync from Turso
```

### Test 3: Existing user with working replica
```bash
sow_admin db sync      # already synced
# Expected: no-op sync, "Sync completed successfully"
```

### Test 4: `db init` doesn't crash on `show_status()`
```bash
rm -rf ~/.config/sow-admin/db
mkdir -p ~/.config/sow-admin/db
sow_admin db init
# Expected: initializes DB, shows status without libsql error
```

### Test 5: `db sync` without Turso URL configured
```bash
rm -rf ~/.config/sow-admin
sow_admin db sync
# Expected: creates default config, prints "Turso database URL not configured"
```

## Usage Flow (Post-Fix)

### Option A: `db sync` only (recommended for new users)
```bash
# 1. Install with Turso support
uv sync --extra turso

# 2. Configure Turso URL
cat >> ~/.config/sow-admin/config.toml << 'EOF'
[turso]
database_url = "libsql://sow-catalog-<org>.aws-us-west-2.turso.io"
EOF

# 3. Set Turso auth token
export SOW_TURSO_TOKEN=<your-full-access-token>

# 4. Sync from Turso
sow_admin db sync
```

### Option B: `db init` + `db sync` (works, but auto-recovery deletes vanilla DB)
```bash
# 1-3. Same as above
# 4. Initialize local database (optional)
sow_admin db init

# 5. Sync from Turso
sow_admin db sync
```