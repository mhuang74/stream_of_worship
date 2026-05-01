# Research: Admin CLI Database & Turso Sync Architecture

## Turso Database Connection Setup

**Connection architecture:** The system uses `libsql` (the Turso SDK) as an embedded replica that maintains a local SQLite file synced with the Turso cloud database. The `libsql` package is an optional dependency.

**Key file: `src/stream_of_worship/admin/db/client.py`**

- Lines 30-36: `libsql` is imported with a try/except. If unavailable, `LIBSQL_AVAILABLE = False` and the client falls back to standard `sqlite3`.
- Lines 61-66: `DatabaseClient.__init__` accepts `db_path`, `turso_url`, and `turso_token`. The token falls back to the `SOW_TURSO_TOKEN` environment variable (line 76).
- Lines 79-86: `is_turso_enabled` property returns `True` only when both `turso_url` is set AND `libsql` is importable.
- Lines 89-117: The `connection` property lazily creates the connection:
  - **With Turso**: calls `libsql.connect(str(self.db_path), sync_url=self.turso_url, auth_token=self.turso_token)` -- this creates an embedded replica at the local path that can sync with the remote.
  - **Without Turso**: uses standard `sqlite3.connect()` with row factory and foreign keys enabled.

**Key file: `src/stream_of_worship/admin/config.py`**

- Lines 20-50: `AdminConfig` dataclass holds `turso_database_url` (default empty string) and `sync_on_startup` (default `True`).
- Lines 87-91: Turso config loaded from the `[turso]` section of the TOML config file (`database_url` and `sync_on_startup`).
- Lines 248, 255: Config path is `~/.config/sow-admin/config.toml`; default DB path is `~/.config/sow-admin/db/sow.db`.

**Key file: `src/stream_of_worship/admin/commands/db.py`**

- Lines 38-51: `get_db_client()` helper constructs `DatabaseClient` using config values and `SOW_TURSO_TOKEN` from the environment.

**Environment variables for credentials:**

| Variable | Purpose | Used by |
|---|---|---|
| `SOW_TURSO_TOKEN` | Full-access Turso token | Admin CLI |
| `SOW_TURSO_READONLY_TOKEN` | Read-only Turso token | User App |
| `SOW_R2_ACCESS_KEY_ID` | R2 access key | Admin CLI |
| `SOW_R2_SECRET_ACCESS_KEY` | R2 secret key | Admin CLI |

**Example config file: `examples/sow-admin-config.toml`**
```toml
[turso]
database_url = "https://your-db.turso.io"

[database]
path = "/Users/you/.local/share/sow-admin/sow.db"
```

**pyproject.toml dependency extras:**
- `turso` extra: `libsql>=0.1.0` (deprecated, now included in `app` extra)
- `app` extra includes `libsql>=0.1.0` directly
- `admin` extra does NOT include `libsql` -- you must install `turso` or `app` extra separately

## How `audio list --lrc incomplete` and `audio lrc` Commands Work

### `sow_admin audio list --lrc incomplete`

**File: `src/stream_of_worship/admin/commands/audio.py`**

- Lines 972-1005: The `list` command accepts `--lrc` option with valid values: `pending`, `processing`, `completed`, `failed`, `incomplete`.
- Lines 1049-1059: Creates a `DatabaseClient` (plain sqlite3, no Turso URL passed) and calls `db_client.list_recordings_with_songs(lrc_status=lrc, ...)`.

**The `incomplete` magic filter** is in `DatabaseClient.list_recordings_with_songs()` (client.py, lines 696-705):
```python
if lrc_status == "incomplete":
    query += " AND r.lrc_status IN ('pending', 'processing', 'failed')"
```
This means `--lrc incomplete` matches recordings where `lrc_status` is any of `pending`, `processing`, or `failed`.

- Lines 1076-1078: With `--format ids`, it outputs one `song_id` per line, making it pipeable to `audio lrc --stdin`.

### `sow_admin audio lrc`

**File: `src/stream_of_worship/admin/commands/audio.py`**

- Lines 1456-1557: The `lrc` command accepts `song_id` (or `--stdin`), `--force`, `--model`, `--lang`, `--wait`, etc.

**Single song flow** (`_submit_lrc_single`, lines 284-419):
1. Looks up the `Recording` by `song_id`
2. Looks up the `Song` for lyrics
3. Validates `r2_audio_url` exists
4. Checks if already completed (skips unless `--force`)
5. Submits to the Analysis Service via `analysis_client.submit_lrc()`
6. Updates local DB to `lrc_status = "processing"` with the job ID
7. With `--wait`: polls the Analysis Service (30s interval, 600s timeout), then updates DB with result URL

**Batch flow** (`_submit_lrc_batch`, lines 422-500):
- Reads song IDs from stdin, iterates over each, submitting jobs without waiting
- Skips already-completed or in-progress recordings

## Sync Mechanisms and Sync Frequency

### A. Turso Embedded Replica Sync (libsql `.sync()`)

**Admin CLI sync service: `src/stream_of_worship/admin/services/sync.py`**

- `SyncService` class (lines 70-311) wraps `DatabaseClient.sync()` with validation, error handling, and automatic recovery from metadata corruption.
- `execute_sync()` (line 174): Validates config, then calls `_execute_sync_with_recovery()`.
- `_execute_sync_with_recovery()` (lines 193-239): Calls `client.sync()`, which calls `conn.sync()` on the libsql connection. If metadata is corrupted, it auto-recovers by deleting the local DB files and retrying.

**IMPORTANT: The Admin CLI has NO automatic/periodic sync.** The `sync_on_startup` config field exists but is never consumed by the Admin CLI -- it is only used by the User App. The Admin CLI requires manual `sow-admin db sync` invocation.

### B. Analysis Service Job Status Sync

**`sow-admin audio status --sync`** (audio.py lines 1918-2020):
1. Finds all recordings with `analysis_status` or `lrc_status` in (`pending`, `processing`)
2. For each, queries the Analysis Service API for the job status
3. Updates the local DB if the job has completed or failed

This is also entirely manual.

### C. User App Sync (for reference)

- On app mount, if `sync_on_startup` is `True` and Turso is configured, runs sync in a background worker.
- Manual sync action bound to `Shift+S` in the TUI.
- No periodic timer -- the V2 spec explicitly says: "No periodic timer."

## Key Files Reference

| File | Role |
|---|---|
| `src/stream_of_worship/admin/db/client.py` | DatabaseClient with libsql/sqlite3 dual-mode connection and sync |
| `src/stream_of_worship/admin/config.py` | Admin TOML config with `turso_database_url` and `sync_on_startup` |
| `src/stream_of_worship/admin/commands/db.py` | `db sync`, `db init`, `db status`, `db turso-bootstrap` commands |
| `src/stream_of_worship/admin/commands/audio.py` | `audio list`, `audio lrc`, `audio status` commands |
| `src/stream_of_worship/admin/services/sync.py` | SyncService with validation, execution, and metadata recovery |
| `src/stream_of_worship/admin/db/schema.py` | SQL schema including sync_metadata table |
| `src/stream_of_worship/admin/db/models.py` | Song, Recording, DatabaseStats dataclasses |
| `src/stream_of_worship/app/db/read_client.py` | App-side read-only client with Turso sync |
| `src/stream_of_worship/app/services/sync.py` | App-side AppSyncService with pre-sync snapshots |
