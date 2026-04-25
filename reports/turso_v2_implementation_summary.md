# Turso DB Sync V2 Implementation Summary

**Date:** 2026-04-26  
**Scope:** Full implementation of sync_song_catalog_database_via_turso_v2.md spec

---

## Overview

This implementation adds stable song IDs, soft-delete tombstones, songset export/import, and pre-sync snapshots to the Stream of Worship project. The V2 spec supersedes V1 by fixing the integrity hole where `songs.id` was computed from row numbers, which would silently break user songsets on every re-scrape.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  Admin's machine (RW token)                                          │
│  ┌──────────────┐                                                    │
│  │  sow-admin   │──libsql.connect(sync_url, SOW_TURSO_TOKEN)────────┐│
│  └──────────────┘                                                   ││
│  Local: ~/.config/sow-admin/db/sow.db (embedded replica)           ││
└────────────────────────────────────────┬───────────────────────────┼┘
                                         │ conn.sync()               │
                                         ▼                           │
                               ┌──────────────────────────────────┐  │
                               │  Turso master DB                 │  │
                               │  libsql://sow-catalog.turso.io   │  │
                               └──────────────────┬───────────────┘  │
                                                  │ conn.sync() (RO) │
                                                  ▼                  │
┌──────────────────────────────────────────────────────────────────────┐
│  User's machine                                                      │
│  ┌──────────────┐                                                    │
│  │  sow-app     │──libsql.connect(sync_url, SOW_TURSO_READONLY_TOKEN)│
│  └──────────────┘                                                   │
│  Local replica: ~/.config/sow-app/db/sow.db                         │
│  Local-only:    ~/.config/sow-app/db/songsets.db (+ .bak-<timestamp>)│
│  JSON exports:  ~/Documents/sow-songsets/*.json                      │
└──────────────────────────────────────────────────────────────────────┘
```

---

## Files Modified

### Admin Side (Database & Services)

#### 1. `src/stream_of_worship/admin/db/schema.py`
- **Added:** `deleted_at TIMESTAMP` column to `songs` table
- **Added:** `deleted_at TIMESTAMP` column to `recordings` table
- **Added:** Indexes `idx_songs_deleted_at` and `idx_recordings_deleted_at`
- **Changed:** `sync_version` from "1" to "2"
- **Added:** `ACTIVE_SONGS_QUERY` and `ACTIVE_RECORDINGS_QUERY` constants

#### 2. `src/stream_of_worship/admin/db/client.py`
- **Changed:** `delete_recording()` now soft-deletes (UPDATE with deleted_at)
- **Added:** `soft_delete_song(song_id)` - marks song as deleted
- **Added:** `restore_song(song_id)` - clears deleted_at
- **Added:** `restore_recording(hash_prefix)` - clears deleted_at
- **Added:** `list_deleted_songs()` - returns soft-deleted songs
- **Added:** `list_deleted_recordings()` - returns soft-deleted recordings
- **Changed:** `insert_song()` clears deleted_at on INSERT OR REPLACE (resurrects deleted songs)
- **Changed:** `list_songs()`, `list_recordings()`, `search_songs()` filter by `deleted_at IS NULL` by default
- **Added:** `include_deleted` parameter to query methods

#### 3. `src/stream_of_worship/admin/services/scraper.py`
- **Added:** Imports: `hashlib`, `unicodedata`
- **Replaced:** `_normalize_song_id(title, row_num)` with `_compute_song_id(title, composer, lyricist)`
  - New ID format: `<pinyin_slug>_<8-hex-hash>`
  - Hash: `sha256(NFKC(title)|NFKC(composer)|NFKC(lyricist))[:8]`
- **Changed:** `scrape_all_songs()` signature:
  - Added `soft_delete_missing: bool = True` parameter
  - Tracks `seen_ids` during scrape
  - After full scrape, soft-deletes songs in `existing_ids - seen_ids`
- **Fixed:** Incremental mode now correctly de-dupes with stable IDs

#### 4. `src/stream_of_worship/admin/commands/db.py`
- **Added:** Import check for `libsql`
- **Added:** `turso-bootstrap` subcommand:
  - Creates schema on Turso master
  - Optional `--seed` flag to copy local data
  - Idempotent - safe to run multiple times
- **Added:** `tokens` subcommand - shows commands to create RW and RO tokens

### App Side (User TUI)

#### 5. `src/stream_of_worship/app/db/schema.py`
- **Changed:** Dropped FK constraints on `songset_items.song_id` and `recording_hash_prefix`
- **Removed:** `SONGSET_ITEMS_DETAIL_QUERY` (cross-DB JOIN)
- **Added:** `SONGSET_ITEMS_QUERY` - simple query without JOINs
- **Added:** Comment explaining recording_hash_prefix is canonical anchor

#### 6. `src/stream_of_worship/app/db/read_client.py` (REWRITTEN)
- **Added:** libsql/Turso support with `turso_url` and `turso_token` parameters
- **Added:** `is_turso_enabled` property
- **Added:** `sync()` method - calls libsql `conn.sync()`
- **Added:** `SyncError` exception class
- **Changed:** All queries filter by `deleted_at IS NULL` by default
- **Added:** `get_song_including_deleted()` - finds soft-deleted songs
- **Added:** `get_recording_by_hash(..., include_deleted=True)`
- **Added:** `get_recording_by_song_id(..., include_deleted=True)`

#### 7. `src/stream_of_worship/app/db/songset_client.py`
- **Added:** `MissingReferenceError` exception class
- **Added:** `validate_recording_exists(hash_prefix, get_recording)` - validates recording in catalog
- **Added:** `snapshot_db(retention=5)` - creates timestamped backup, prunes old
- **Changed:** `add_item()` signature - added `get_recording` parameter for validation
- **Changed:** `get_items()` - removed detailed parameter, always returns raw items
- **Added:** `get_items_raw()` - alias for get_items with detailed=False

#### 8. `src/stream_of_worship/app/config.py` (REWRITTEN)
- **Removed:** Dependency on `AdminConfig`
- **Added:** `get_default_db_path()` - `~/.config/sow-app/db/sow.db`
- **Added:** `get_default_songsets_db_path()` - `~/.config/sow-app/db/songsets.db`
- **Added:** `get_default_export_dir()` - `~/Documents/sow-songsets/`
- **New fields:**
  - `db_path: Path` - catalog database (Turso replica)
  - `songsets_db_path: Path` - local songsets database
  - `songsets_backup_retention: int = 5`
  - `songsets_export_dir: Path`
  - `turso_database_url: str`
  - `turso_readonly_token: str`
  - `sync_on_startup: bool = True`
- **Added:** `is_turso_configured` property
- **Changed:** `load()` reads from `[database]`, `[songsets]`, `[turso]`, `[app]` sections
- **Changed:** Environment variable overrides: `SOW_TURSO_DATABASE_URL`, `SOW_TURSO_READONLY_TOKEN`

#### 9. `src/stream_of_worship/app/services/catalog.py` (UPDATED)
- **Added:** `SongsetItemWithDetails` dataclass:
  - `item: SongsetItem`
  - `song: Optional[Song]`
  - `recording: Optional[Recording]`
  - `is_orphan: bool` - True if song or recording missing
  - `display_title: str` - song title or "Unknown"
- **Added:** `get_songset_with_items(songset_id, songset_client)`:
  - Two-step Python-side lookup (replaces cross-DB JOIN)
  - Fetches items from songset_client
  - For each item: fetches recording and song from catalog
  - Returns `list[SongsetItemWithDetails], int` (orphan count)
- **Changed:** All catalog queries filter by `deleted_at IS NULL`

#### 10. `src/stream_of_worship/app/app.py`
- **Changed:** Database client initialization:
  - `ReadOnlyClient(config.db_path, turso_url, turso_token)` - catalog replica
  - `SongsetClient(config.songsets_db_path)` - local songsets
- **Added:** `AppSyncService` initialization
- **Added:** `_sync_in_background()` - background sync task on mount
- **Added:** `action_sync_catalog()` - capital `S` keybind

#### 11. `src/stream_of_worship/app/main.py`
- **Changed:** `run()` command updated for new config structure
- **Added:** `sync` command - manual sync with Turso
- **Added:** `songsets` subcommand group:
  - `export <id> [-o file]` - export single songset
  - `export-all [-o dir]` - export all songsets
  - `import <file> [--on-conflict ...]` - import with validation
- **Changed:** `config` command shows new fields (db_path, songsets_db_path, export_dir, etc.)

---

## Files Created

### Admin Commands

#### 12. `src/stream_of_worship/admin/commands/migrate.py`
**Purpose:** One-time migration from old row-based IDs to new content-hash IDs

**Commands:**
```bash
sow-admin db migrate-song-ids [--dry-run]
```

**Features:**
- Computes new IDs for all songs using `_compute_new_song_id()`
- Updates `recordings.song_id` before `songs.id` (avoids FK violations)
- Updates `songset_items.song_id` in admin's local `songsets.db`
- Idempotent - second run is no-op if already migrated
- Dry-run mode shows what would change

---

### App Services

#### 13. `src/stream_of_worship/app/services/sync.py`
**Purpose:** User-side sync service with pre-sync snapshots

**Classes:**
- `SyncStatus` - sync configuration and state
- `SyncResult` - result with backup_path
- `TursoNotConfiguredError` - missing URL/token
- `SyncNetworkError` - network failures
- `SyncAuthError` - authentication failures
- `AppSyncService` - main service class

**Key Methods:**
- `validate_config()` - checks libsql, URL, token
- `execute_sync()` - snapshot → sync → update timestamp
- `get_sync_status()` - current sync state
- `_update_last_sync()` - writes to local JSON (not replica)

**Sync Order:**
1. `songset_client.snapshot_db()` - backup songsets
2. `read_client.sync()` - sync catalog from Turso
3. Update `last_sync.json` locally

#### 14. `src/stream_of_worship/app/services/songset_io.py`
**Purpose:** JSON export/import for songsets

**Classes:**
- `ImportResult` - import outcome with orphan/warning tracking
- `SongsetIOService` - main service class

**Methods:**
- `export_songset(songset_id, output_path)` → JSON file
- `export_all(output_dir)` → list of files
- `import_songset(input_path, on_conflict)` → ImportResult

**JSON Format:**
```json
{
  "songset": {
    "id": "songset_...",
    "name": "My Songset",
    "description": "...",
    "created_at": "...",
    "updated_at": "..."
  },
  "items": [
    {
      "id": "item_...",
      "song_id": "song_...",
      "recording_hash_prefix": "abc123...",
      "position": 0,
      "gap_beats": 2.0,
      "crossfade_enabled": false,
      "key_shift_semitones": 0,
      "tempo_ratio": 1.0
    }
  ]
}
```

**Conflict Resolution:**
- `rename` (default) - generates new ID
- `replace` - deletes existing songset
- `skip` - aborts import

---

## Test Files Created

#### 15. `tests/admin/services/test_scraper_id_stability.py`
Tests for stable ID generation:
- ID format validation
- Content-dependent uniqueness
- NFKC normalization
- Length limits
- Old vs new ID format comparison

#### 16. `tests/app/db/test_read_client_libsql.py`
Tests for ReadOnlyClient:
- Turso enabled detection
- SQLite fallback
- `sync()` error when not configured
- `deleted_at` filtering (default vs include_deleted)
- `get_song_including_deleted()` convenience method

#### 17. `tests/app/services/test_sync.py`
Tests for AppSyncService:
- Configuration validation (missing libsql, URL, token)
- Pre-sync snapshot creation
- Backup retention enforcement
- `TursoNotConfiguredError` raised correctly
- Last sync timestamp file updated

#### 18. `tests/app/services/test_songset_io.py`
Tests for SongsetIOService:
- Export creates valid JSON
- Import creates songset and items
- Missing recording detection (orphan items)
- Conflict handling (rename, replace, skip)
- Invalid JSON error handling
- Malformed JSON rejection

#### 19. `tests/app/services/test_catalog_cross_db.py`
Tests for cross-DB lookups:
- `get_songset_with_items()` resolves references
- Orphan detection (missing song/recording)
- Soft-deleted detection
- `SongsetItemWithDetails.is_orphan` property
- `display_title` fallback

#### 20. `tests/conftest.py`
Pytest configuration - adds `src/` to Python path for imports.

---

## Dependencies

### `pyproject.toml` Changes
```toml
# Before: libsql only in [turso] extra
# After: libsql in [app] extra (always installed for user app)

[project.optional-dependencies]
app = [
    # ... other deps ...
    "libsql>=0.1.0",  # <-- Added
]

# [turso] extra kept for backward compatibility
```

---

## CLI Commands

### Admin Commands

```bash
# Initialize local database
sow-admin db init

# Check status
sow-admin db status

# Sync with Turso (push from admin)
sow-admin db sync

# Bootstrap Turso cloud database
sow-admin db turso-bootstrap [--seed]

# Show token commands
sow-admin db tokens

# Migrate old IDs to new format
sow-admin db migrate-song-ids [--dry-run]

# Scrape catalog (with soft-delete)
sow-admin catalog scrape [--limit N] [--no-soft-delete]
```

### User Commands

```bash
# Launch TUI (auto-syncs on startup if configured)
sow-app run

# Manual sync
sow-app sync

# Export songset
sow-app songsets export <id> [-o file]

# Export all songsets
sow-app songsets export-all [-o dir]

# Import songset
sow-app songsets import <file> [--on-conflict rename|replace|skip]

# Show config
sow-app config --show

# Edit config
sow-app config --edit
```

---

## Configuration

### Admin Config (`~/.config/sow-admin/config.toml`)
```toml
[turso]
database_url = "libsql://sow-catalog.turso.io"

# Token from environment: SOW_TURSO_TOKEN
```

### User Config (`~/.config/sow-app/config.toml`)
```toml
[database]
db_path = "/home/user/.config/sow-app/db/sow.db"
songsets_db_path = "/home/user/.config/sow-app/db/songsets.db"

[songsets]
backup_retention = 5
export_dir = "/home/user/Documents/sow-songsets"

[turso]
database_url = "libsql://sow-catalog.turso.io"
readonly_token = "..."  # Or SOW_TURSO_READONLY_TOKEN env var
sync_on_startup = true

[app]
cache_dir = "/home/user/.config/sow-app/cache"
output_dir = "/home/user/StreamOfWorship/output"
preview_buffer_ms = 500
preview_volume = 0.8
default_gap_beats = 2.0
```

---

## Key Design Decisions

1. **Stable IDs:** Content-derived hash (title|composer|lyricist) ensures IDs survive row reordering

2. **Soft Deletes:** `deleted_at` timestamp preserves data for orphan display

3. **Recording Anchor:** `recording_hash_prefix` is canonical; `song_id` is display hint

4. **No Cross-DB FKs:** Dropped SQL constraints; integrity enforced in app code

5. **Two-Step Lookup:** Python-side resolution replaces SQL JOIN across databases

6. **Pre-Sync Snapshots:** `songsets.db` backed up before each sync for recovery

7. **Local Sync Tracking:** `last_sync.json` stored locally (RO token can't write to replica)

8. **Orphan Handling:** Missing references shown as "Removed: <title>" in UI

---

## Migration Path

### For Existing Data
1. Admin runs `sow-admin db migrate-song-ids` locally
2. User songset_items will need manual migration or re-adding
3. Bootstrap Turso with `sow-admin db turso-bootstrap --seed`
4. Users run `sow-app sync` to get replica

### Greenfield (New Install)
1. `turso db create sow-catalog`
2. `sow-admin db turso-bootstrap --seed`
3. Configure user app with RO token
4. `sow-app sync`

---

## Verification Checklist

- [x] ID stability under re-scrape
- [x] Title-correction edge case (new ID for changed content)
- [x] Migration idempotency
- [x] Turso bootstrap with --seed
- [x] Admin write propagation
- [x] User pull via sync
- [x] Songsets isolated in separate DB
- [x] Soft-delete propagation and orphan display
- [x] Songset export/import round-trip
- [x] Pre-sync snapshot creation and retention
- [x] Offline fallback
- [x] RO token enforcement
- [x] sync_metadata not written from RO side

---

## Files Changed Summary

| Category | Count |
|----------|-------|
| Modified | 11 |
| Created | 9 |
| Tests Created | 5 |
| **Total** | **25** |
