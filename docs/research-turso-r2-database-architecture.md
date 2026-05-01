# Research: Turso Sync & R2 Database Architecture

## Turso Configuration

The project uses Turso (a hosted libSQL service) as the cloud database for the song catalog. There are two distinct roles with separate tokens:

**Admin CLI (`sow-admin`)** -- read-write access:
- Config file: `~/.config/sow-admin/config.toml` (TOML `[turso]` section)
- Config class: `src/stream_of_worship/admin/config.py` -- `AdminConfig.turso_database_url` (line 43)
- Auth token: `SOW_TURSO_TOKEN` environment variable (full-access / read-write token)
- Database client: `src/stream_of_worship/admin/db/client.py` -- `DatabaseClient` class
- Sync service: `src/stream_of_worship/admin/services/sync.py` -- `SyncService` class

**User App (`sow-app`)** -- read-only access:
- Config file: `~/.config/sow/config.toml` (TOML `[turso]` section)
- Config class: `src/stream_of_worship/app/config.py` -- `AppConfig.turso_database_url` (line 112) and `AppConfig.turso_readonly_token` property (line 264-271)
- Auth token: `SOW_TURSO_READONLY_TOKEN` environment variable (read-only token)
- Database client: `src/stream_of_worship/app/db/read_client.py` -- `ReadOnlyClient` class
- Sync service: `src/stream_of_worship/app/services/sync.py` -- `AppSyncService` class

**Turso URL format**: `libsql://<database-name>-<username>.turso.io`

**Token generation commands** (shown in `src/stream_of_worship/admin/commands/db.py` lines 617-629):
- Admin: `turso db tokens create sow-catalog --read-write`
- User app: `turso db tokens create sow-catalog --read-only`

## Database Schema

**Schema definition**: `src/stream_of_worship/admin/db/schema.py`

Three tables:
- **`songs`** -- scraped catalog entries (id, title, title_pinyin, composer, lyricist, album_name, album_series, musical_key, lyrics_raw, lyrics_lines, sections, source_url, table_row_number, scraped_at, created_at, updated_at, deleted_at)
- **`recordings`** -- audio recordings linked to songs via `song_id` FK (content_hash PK, hash_prefix UNIQUE, song_id, original_filename, file_size_bytes, imported_at, r2_audio_url, r2_stems_url, r2_lrc_url, analysis metadata, processing status fields, youtube_url, visibility_status, deleted_at)
- **`sync_metadata`** -- key/value metadata for sync tracking (key PK, value, updated_at). Default keys: `last_sync_at`, `sync_version` ("2"), `local_device_id`

**Indexes** (8 total): on `recordings.song_id`, `recordings.analysis_status`, `recordings.hash_prefix`, `songs.album_name`, `songs.title_pinyin`, `recordings.visibility_status`, `songs.deleted_at`, `recordings.deleted_at`

**Triggers**: auto-update `updated_at` on both `songs` and `recordings` tables.

## Local Replica Architecture and Sync

The architecture follows an embedded-replica pattern:

```
Admin machine (RW)  -->  Turso master DB  <--  User machines (RO)
  sow-admin writes          cloud            sow-app reads
  local embedded replica                     local embedded replica
```

**How libsql embedded replicas work:**

Both the admin and user app connect using `libsql.connect()` with a local file path AND a `sync_url` + `auth_token`:

- **Admin client** (`admin/db/client.py` lines 99-105):
  ```python
  self._connection = libsql.connect(
      str(self.db_path),
      sync_url=self.turso_url,
      auth_token=self.turso_token or "",
  )
  ```

- **User app client** (`app/db/read_client.py` lines 85-91): identical pattern.

**Sync call sites:**

1. **Admin sync command** (`admin/commands/db.py` line 294): Manual CLI command `sow-admin db sync`
2. **Admin sync service** with recovery (`admin/services/sync.py` lines 193-238): auto-recovery from metadata corruption
3. **User app background sync** (`app/app.py` lines 112-125): On TUI startup if `sync_on_startup=True`
4. **User app manual sync** (`app/app.py` lines 127-140): `Shift+S` keybinding
5. **User app CLI sync**: `sow-app db sync` command

**Turso bootstrap** (`admin/commands/db.py` line 399): One-time `sow-admin db turso-bootstrap` command that initializes the Turso cloud database, creates schema, and optionally seeds data.

## Database File Locations

- **Admin catalog DB**: `~/.config/sow-admin/db/sow.db`
- **User app catalog DB** (Turso replica): `~/.config/sow/db/sow.db`
- **User app songsets DB** (local-only, plain SQLite): `~/.config/sow/db/songsets.db`

## R2 (Cloudflare) Storage Setup

**R2 Client**: `src/stream_of_worship/admin/services/r2.py` -- `R2Client` class

**Configuration**:
- Admin config (`admin/config.py` lines 37-40): `r2_bucket` (default "sow-audio"), `r2_endpoint_url`, `r2_region` (default "auto")
- App config (`app/config.py` lines 116-118): same fields
- TOML section: `[r2]` with keys `bucket`, `endpoint_url`, `region`

**Credentials** (env vars only, never in config files):
- `SOW_R2_ACCESS_KEY_ID`
- `SOW_R2_SECRET_ACCESS_KEY`

**R2 uses boto3 S3-compatible client** (line 52-58).

**Storage layout in R2** (all keyed by `hash_prefix`, the first 12 chars of the audio file's SHA-256):
- Audio: `{hash_prefix}/audio.mp3`
- LRC lyrics: `{hash_prefix}/lyrics.lrc`
- Stems: `{hash_prefix}/stems/{stem_name}.flac`

**R2 URLs stored in DB**: The `recordings` table stores `r2_audio_url`, `r2_stems_url`, and `r2_lrc_url` as `s3://sow-audio/{hash_prefix}/...` format strings.

## Cache Invalidation and Data Freshness

**There is NO automated cache invalidation mechanism.** Here is what currently exists:

**Database sync freshness**:
- `sync_on_startup` config option (default `True`) triggers one sync when the app starts
- Manual `Shift+S` keybinding in TUI or `sow-app db sync` CLI command
- The example config file mentions `sync_interval = 30`, but this is NOT implemented in code
- The V2 spec explicitly says: "No periodic timer."
- `last_sync_at` timestamp is tracked in the songsets database `_sync_metadata` table

**R2 asset cache freshness**:
- The `AssetCache` has NO TTL, ETag, or last-modified checking
- Files are cached indefinitely once downloaded
- Only invalidation is via `force=True` parameter (explicit re-download)
- `clear_cache(older_than_days=N)` can remove old cached files but must be called explicitly

**Soft-delete freshness**: Both `songs` and `recordings` use `deleted_at` for soft deletes. User app filters `WHERE deleted_at IS NULL`.

## Key Environment Variables

| Variable | Used By | Purpose |
|---|---|---|
| `SOW_TURSO_TOKEN` | Admin CLI | Full-access Turso auth token (read-write) |
| `SOW_TURSO_READONLY_TOKEN` | User App | Read-only Turso auth token |
| `SOW_R2_ACCESS_KEY_ID` | Admin CLI + App | R2 access key |
| `SOW_R2_SECRET_ACCESS_KEY` | Admin CLI + App | R2 secret key |
| `SOW_ANALYSIS_API_KEY` | Admin CLI | Analysis service API key |
