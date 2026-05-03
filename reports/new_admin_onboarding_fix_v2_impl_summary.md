# New Admin Onboarding Fix v2 - Implementation Summary

**Date:** 2026-05-03
**Status:** Completed

## Goal

Enable new admin users to successfully onboard on a new host and sync from Turso without losing configuration or hitting metadata file errors.

## Problems Fixed

### 1. Config Loss on Overwrite
**Problem:** When `AdminConfig.load()` raised `FileNotFoundError`, the fallback created a blank `AdminConfig()` and called `config.save()`, overwriting any previous Turso URL.

**Solution:** Added automatic backup before overwriting config in `AdminConfig.save()` - creates `config.toml.bak-{timestamp}` backup file.

**File:** `src/stream_of_worship/admin/config.py`

### 2. No Alternative Way to Provide Turso URL
**Problem:** Turso URL had to be in config file. No env var or CLI flag support.

**Solution:** 
- Added `effective_turso_url` property that checks `SOW_TURSO_URL` env var first
- Added `--turso-url` CLI flag to `db sync` command
- Priority: `--turso-url` flag > `SOW_TURSO_URL` env var > config file

**Files:** 
- `src/stream_of_worship/admin/config.py` - `effective_turso_url` property
- `src/stream_of_worship/admin/commands/db.py` - `--turso-url` flag
- `src/stream_of_worship/admin/services/sync.py` - uses `effective_turso_url`

### 3. Poor Onboarding UX
**Problem:** On fresh host, `db sync` created blank config then errored with "Turso not configured", leaving user stuck.

**Solution:** Changed `sync_db()` to:
- Detect missing config + missing URL
- Show clear 3-step guidance with examples
- Only create config when URL is provided (via flag or env var)
- Auto-save URL to config when provided

**File:** `src/stream_of_worship/admin/commands/db.py`

### 4. turso-init Required Existing Config
**Problem:** `infra turso-init` required config to exist first.

**Solution:** 
- Creates default config if missing
- Added `--url` flag to accept Turso URL
- Saves URL to config when provided

**File:** `src/stream_of_worship/admin/commands/infra.py`

### 5. Wrong Default R2 Bucket
**Problem:** Default R2 bucket was "sow-audio", should be "stream-of-worship".

**Solution:** Changed default from `"sow-audio"` to `"stream-of-worship"`.

**File:** `src/stream_of_worship/admin/config.py`

### 6. Metadata File Error Not Triggering Recovery
**Problem:** When a vanilla SQLite database was created by `db init` and then synced, libsql raised `ValueError: sync error: invalid local state: db file exists but metadata file does not` during connection creation. This wasn't being caught as `SyncError`, so recovery logic didn't trigger.

**Solution:** Wrapped `libsql.connect()` call in `connection` property with try-except to catch `ValueError` (and `libsql.Error`) and convert to `SyncError` with helpful message for recovery.

**File:** `src/stream_of_worship/admin/db/client.py`

## Files Modified

| File | Changes |
|------|---------|
| `admin/config.py` | +backup on save, +`effective_turso_url`, default R2 bucket |
| `admin/commands/db.py` | +`--turso-url` flag, improved onboarding guidance |
| `admin/commands/infra.py` | +`--url` flag, create config if missing |
| `admin/services/sync.py` | Use `effective_turso_url` |
| `admin/main.py` | Use `effective_turso_url` in config show |
| `admin/db/client.py` | +catch ValueError during libsql.connect(), convert to SyncError |

## New Onboarding Flow

### Option 1: CLI Flag (Recommended)
```bash
# New host - no config exists
export SOW_TURSO_TOKEN="your-token"
sow-admin db sync --turso-url libsql://your-db.turso.io
# Creates config, saves URL, syncs from Turso
```

### Option 2: Environment Variable
```bash
export SOW_TURSO_TOKEN="your-token"
export SOW_TURSO_URL="libsql://your-db.turso.io"
sow-admin db sync
# Uses env var, creates config, syncs
```

### Option 3: Interactive Setup
```bash
sow-admin db init
sow-admin config set turso.database_url libsql://your-db.turso.io
export SOW_TURSO_TOKEN="your-token"
sow-admin db sync
```

### Option 4: turso-init (for first-time Turso setup)
```bash
export SOW_TURSO_TOKEN="your-token"
sow-admin infra turso-init --url libsql://your-db.turso.io
```

## Verification

Tested scenarios:
- ✅ Config backup created on overwrite
- ✅ `SOW_TURSO_URL` env var overrides config
- ✅ `--turso-url` flag saves URL to config
- ✅ New admin onboarding shows clear guidance
- ✅ `turso-init` creates config with `--url` flag
- ✅ Default R2 bucket is "stream-of-worship"
- ✅ ValueError from libsql.connect() converted to SyncError for recovery

## Post-Deployment

For users with lost configs:
```bash
# Option A: Use CLI flag (config will be created with URL)
export SOW_TURSO_TOKEN="your-token"
sow-admin db sync --turso-url libsql://your-db.turso.io

# Option B: Check for config backups
ls ~/.config/sow-admin/config.toml.bak-*
# Restore: cp ~/.config/sow-admin/config.toml.bak-xxx ~/.config/sow-admin/config.toml
```
