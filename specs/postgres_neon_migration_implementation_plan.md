# PostgreSQL on Neon Migration — Application Layer Implementation Plan

**Date:** 2026-05-08  
**Runbook:** `specs/sqlite_turso_to_neon_migration_runbook_v4.md`  
**Status:** Draft / Review

---

## 1. Executive Summary

Migrate the Stream of Worship database layer from SQLite + Turso/libSQL embedded replicas to a unified PostgreSQL database hosted on Neon. This is a **hard cutover**: all libsql/Turso code is removed. Both the catalog (`songs`, `recordings`) and user data (`songsets`, `songset_items`) live in the same Neon database. Admin CLI uses a read-write role; User App uses a role with `SELECT` on catalog tables and full CRUD on songset tables.

---

## 2. Scope & Assumptions

**In scope:**
- Replace `sqlite3`/`libsql` with `psycopg` (v3) across all database clients
- Migrate catalog schema (`songs`, `recordings`) to Postgres
- Migrate app schema (`songsets`, `songset_items`, `_sync_metadata`) to Postgres
- Remove all Turso sync infrastructure (sync services, CLI commands, config)
- Update configuration layer for Postgres DSNs
- Update all raw SQL for Postgres dialect
- Update tests to use `testcontainers` with Postgres
- Data loading is handled by the existing v4 runbook (`02_load_data.py`)

**Out of scope:**
- Data migration execution (covered by v4 runbook)
- R2 object migration
- Feature additions beyond what's required for the database swap

**Assumptions:**
- Neon roles are pre-created: `sow_admin_rw` (catalog CRUD), `sow_app` (catalog SELECT + songset CRUD)
- Both roles connect to the same database; access is controlled via Postgres privileges
- `sync_metadata` table is excluded from Postgres (per checklist recommendation)

---

## 3. Architecture Changes

### 3.1 Before (Current)

| Component | Technology | Location |
|-----------|-----------|----------|
| Admin catalog DB | SQLite + libsql/Turso embedded replica | `~/.config/sow-admin/db/sow.db` |
| App catalog DB | SQLite + libsql/Turso embedded replica | `~/.config/sow/db/sow.db` |
| App songsets DB | Plain SQLite | `~/.config/sow/db/songsets.db` |
| Sync mechanism | `libsql.connect(sync_url=...).sync()` | Both admin + app |

### 3.2 After (Target)

| Component | Technology | Location |
|-----------|-----------|----------|
| Admin catalog access | `psycopg` -> Neon Postgres | `postgresql://...@neon.tech/sow` |
| App catalog access | `psycopg` -> Neon Postgres (read-only role) | Same DB, restricted privileges |
| App songsets access | `psycopg` -> Neon Postgres (same connection) | Same DB, songset tables |

**App connection strategy:** The app uses a **single Postgres connection** with a role that has:
- `SELECT` on `songs`, `recordings`
- `INSERT`, `UPDATE`, `DELETE` on `songsets`, `songset_items`
- No write access to catalog tables (enforced at Postgres privilege layer)

This eliminates the cross-DB lookup complexity currently handled by `CatalogService`. Since everything is in one database, true SQL JOINs between `songset_items` -> `songs`/`recordings` become possible. However, to minimize scope, Phase 1 keeps the existing two-step Python lookup pattern. Phase 2 can optimize to JOINs.

---

## 4. Dependency Changes (`pyproject.toml`)

### 4.1 Add

```toml
postgres = [
    "psycopg[binary]>=3.2.0",
]
test = [
    "pytest>=7.4.0",
    "pytest-mock>=3.12.0",
    "pytest-asyncio>=0.23.0",
    "fastapi>=0.109.0",
    "httpx>=0.26.0",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
    "aiosqlite>=0.19.0",
    "testcontainers[postgres]>=4.0.0",  # NEW
]
```

### 4.2 Remove

- `libsql` dependency (currently in `[project.optional-dependencies] turso` and `app`)
- Remove `"libsql>=0.1.0"` from `app` extra

### 4.3 Update extras

- `admin` extra should include `psycopg` (or depend on `postgres` extra)
- `app` extra should include `psycopg`

---

## 5. Configuration Changes

### 5.1 Admin Config (`src/stream_of_worship/admin/config.py`)

**Remove:**
- `turso_database_url`
- `sync_on_startup`

**Add:**
- `database_url: str = ""`  # Postgres DSN (e.g. `postgresql://user:pass@host/db`)
- `neon_admin_role: str = "sow_admin_rw"`  # informational, not secret

**TOML section rename:** `[turso]` -> `[database]`

**Environment variable:** `SOW_DATABASE_URL` (replaces `SOW_TURSO_TOKEN`)

```python
# Before
[turso]
database_url = "libsql://..."
sync_on_startup = true

# After
[database]
url = "postgresql://sow_admin_rw:...@ep-xxx.us-east-1.aws.neon.tech/sow"
```

### 5.2 App Config (`src/stream_of_worship/app/config.py`)

**Remove:**
- `turso_database_url`
- `sync_on_startup`
- `turso_readonly_token` property
- `is_turso_configured` property
- `db_path` (catalog no longer local file)
- `get_default_db_path()`

**Add:**
- `database_url: str = ""`  # Postgres DSN for app role
- `songsets_db_path` -> **removed** (songsets now in same DB; but keep if we want backward compat)

**TOML section rename:** `[turso]` -> `[database]`

**Environment variable:** `SOW_DATABASE_URL` (replaces `SOW_TURSO_READONLY_TOKEN`)

**Note:** `songsets_db_path` becomes obsolete. The `_sync_metadata` table tracked in songsets.db should be replaced with a simpler in-memory or config-based last-sync timestamp, or migrated to a small `app_metadata` table in Postgres.

### 5.3 Backward Compatibility

Config loader should **ignore** old `[turso]` sections silently (or log a warning). Provide a migration hint to users.

---

## 6. Schema Migration (`src/stream_of_worship/admin/db/schema.py` + `app/db/schema.py`)

Create unified Postgres DDL in a new file: `src/stream_of_worship/db/postgres_schema.py` (or update existing schema files).

### 6.1 Catalog Tables (from `admin/db/schema.py`)

**`songs` table:**

```sql
CREATE TABLE IF NOT EXISTS songs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    title_pinyin TEXT,
    composer TEXT,
    lyricist TEXT,
    album_name TEXT,
    album_series TEXT,
    musical_key TEXT,
    lyrics_raw TEXT,
    lyrics_lines TEXT,
    sections TEXT,
    source_url TEXT NOT NULL,
    table_row_number INTEGER,
    scraped_at TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    deleted_at TEXT  -- keep as TEXT to minimize model changes
);
```

**Key decisions:**
- Keep timestamps as `TEXT` (ISO strings) to avoid changing `Song.from_row()`/`Recording.from_row()`
- Keep JSON-like columns (`lyrics_lines`, `sections`, `beats`, etc.) as `TEXT` (runbook v4 JSON scan confirms all rows are valid JSON; `jsonb` conversion is Phase 2 optimization)
- `deleted_at` as TEXT to match current model

**`recordings` table:**

```sql
CREATE TABLE IF NOT EXISTS recordings (
    content_hash TEXT PRIMARY KEY,
    hash_prefix TEXT NOT NULL UNIQUE,
    song_id TEXT REFERENCES songs(id),
    original_filename TEXT NOT NULL,
    file_size_bytes INTEGER NOT NULL,
    imported_at TEXT NOT NULL,
    r2_audio_url TEXT,
    r2_stems_url TEXT,
    r2_lrc_url TEXT,
    duration_seconds REAL,
    tempo_bpm REAL,
    musical_key TEXT,
    musical_mode TEXT,
    key_confidence REAL,
    loudness_db REAL,
    beats TEXT,
    downbeats TEXT,
    sections TEXT,
    embeddings_shape TEXT,
    analysis_status TEXT DEFAULT 'pending',
    analysis_job_id TEXT,
    lrc_status TEXT DEFAULT 'pending',
    lrc_job_id TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    youtube_url TEXT,
    visibility_status TEXT DEFAULT NULL,
    deleted_at TEXT
);
```

**Indexes (8 total, same as current):**

```sql
CREATE INDEX IF NOT EXISTS idx_recordings_song_id ON recordings(song_id);
CREATE INDEX IF NOT EXISTS idx_recordings_analysis_status ON recordings(analysis_status);
CREATE INDEX IF NOT EXISTS idx_recordings_hash_prefix ON recordings(hash_prefix);
CREATE INDEX IF NOT EXISTS idx_songs_album ON songs(album_name);
CREATE INDEX IF NOT EXISTS idx_songs_title_pinyin ON songs(title_pinyin);
CREATE INDEX IF NOT EXISTS idx_recordings_visibility_status ON recordings(visibility_status);
CREATE INDEX IF NOT EXISTS idx_songs_deleted_at ON songs(deleted_at);
CREATE INDEX IF NOT EXISTS idx_recordings_deleted_at ON recordings(deleted_at);
```

**`updated_at` trigger (Postgres syntax):**

```sql
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER trg_songs_updated_at
    BEFORE UPDATE ON songs
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER trg_recordings_updated_at
    BEFORE UPDATE ON recordings
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
```

### 6.2 App Tables (from `app/db/schema.py`)

**`songsets` table:**

```sql
CREATE TABLE IF NOT EXISTS songsets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

**`songset_items` table:**

```sql
CREATE TABLE IF NOT EXISTS songset_items (
    id TEXT PRIMARY KEY,
    songset_id TEXT NOT NULL REFERENCES songsets(id) ON DELETE CASCADE,
    song_id TEXT NOT NULL,
    recording_hash_prefix TEXT,
    position INTEGER NOT NULL,
    gap_beats REAL DEFAULT 2.0,
    crossfade_enabled INTEGER DEFAULT 0,
    crossfade_duration_seconds REAL,
    key_shift_semitones INTEGER DEFAULT 0,
    tempo_ratio REAL DEFAULT 1.0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

**Indexes:**

```sql
CREATE INDEX IF NOT EXISTS idx_songset_items_songset_id ON songset_items(songset_id);
CREATE INDEX IF NOT EXISTS idx_songset_items_position ON songset_items(songset_id, position);
CREATE INDEX IF NOT EXISTS idx_songset_items_song_id ON songset_items(song_id);
```

**`updated_at` trigger:**

```sql
CREATE TRIGGER trg_songsets_updated_at
    BEFORE UPDATE ON songsets
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
```

### 6.3 Deprecated Tables

**Do NOT migrate:**
- `sync_metadata` (Turso-specific state tracking)
- `_sync_metadata` (replace with in-app tracking or new `app_metadata` table)

---

## 7. Database Client Refactoring

### 7.1 Admin `DatabaseClient` (`src/stream_of_worship/admin/db/client.py`)

**Constructor change:**

```python
# Before
def __init__(self, db_path: Path, turso_url: Optional[str] = None, turso_token: Optional[str] = None):

# After
def __init__(self, database_url: str):
```

**Connection property:**

```python
@property
def connection(self) -> psycopg.Connection:
    if self._connection is None:
        self._connection = psycopg.connect(self.database_url)
    return self._connection
```

**Remove entirely:**
- `is_turso_enabled`
- `turso_url`, `turso_token`
- `sync()` method
- `update_sync_metadata()` (or repurpose for generic metadata)
- `sqlite_master` queries
- `PRAGMA` queries
- SQLite file path handling

**SQL dialect changes (all methods):**
- `?` placeholders -> `%s`
- `datetime('now')` -> `CURRENT_TIMESTAMP` (or `NOW()`)
- `INSERT OR REPLACE` -> `INSERT ... ON CONFLICT DO UPDATE`
- `IF NOT EXISTS` on indexes/tables works in Postgres too
- `LIMIT {limit}` syntax is the same
- `NULLS LAST` is native in Postgres (no changes needed)
- Remove `PRAGMA foreign_keys = ON` (Postgres enforces declared FKs)

**Transaction context manager:**

```python
@contextmanager
def transaction(self) -> Generator[psycopg.Connection, None, None]:
    conn = self.connection
    try:
        with conn.transaction():  # psycopg handles transaction via context manager
            yield conn
    except Exception:
        # psycopg auto-rolls back on exception in transaction block
        raise
```

**`get_stats()` refactoring:**

Remove SQLite-specific checks. Replace with Postgres-compatible stats:

```python
def get_stats(self) -> DatabaseStats:
    cursor = self.connection.cursor()

    # Get row counts
    cursor.execute("SELECT 'songs', COUNT(*) FROM songs UNION ALL SELECT 'recordings', COUNT(*) FROM recordings")
    table_counts = {row[0]: row[1] for row in cursor.fetchall()}

    # Postgres doesn't have PRAGMA integrity_check; skip or use pg_verify

    return DatabaseStats(
        table_counts=table_counts,
        integrity_ok=True,  # Postgres doesn't expose this via SQL easily
        foreign_keys_enabled=True,  # Always true if declared
        last_sync_at=None,  # Remove Turso sync tracking
        sync_version="3",  # Bump to indicate Postgres
        local_device_id="",
        turso_configured=False,
    )
```

**`reset_database()` refactoring:**

Postgres doesn't support dropping tables inside a transaction easily with FK constraints. Use:

```sql
DO $$ 
BEGIN
    DROP TABLE IF EXISTS songset_items CASCADE;
    DROP TABLE IF EXISTS songsets CASCADE;
    DROP TABLE IF EXISTS recordings CASCADE;
    DROP TABLE IF EXISTS songs CASCADE;
END $$;
```

Then re-run schema creation.

### 7.2 App `ReadOnlyClient` (`src/stream_of_worship/app/db/read_client.py`)

**Major change:** This client no longer needs to be "read-only" in code — Postgres privileges enforce read-only on catalog tables. But to minimize changes, keep the class name and methods.

**Constructor:**

```python
def __init__(self, database_url: str):
```

**Remove:**
- `turso_url`, `turso_token`
- `is_turso_enabled`
- `sync()` -> replace with no-op or connection health check
- `_migrate_schema()` -> no longer needed (no schema migrations on connect)

**SQL changes:** Same as admin client (`?` -> `%s`, etc.)

### 7.3 App `SongsetClient` (`src/stream_of_worship/app/db/songset_client.py`)

**Major change:** Previously this was a separate SQLite client for a local file. Now it must use the same Postgres connection as `ReadOnlyClient` (or have its own connection to the same database).

**Two options:**

**Option A: Pass connection object**
- `SongsetClient` accepts an existing `psycopg.Connection` instead of `db_path: Path`
- App creates ONE connection and shares it with both clients

**Option B: Separate DSN**
- `SongsetClient` takes its own `database_url` (same database, same role)
- Manages its own connection lifecycle

**Recommendation: Option A** (pass connection)

```python
class SongsetClient:
    def __init__(self, connection: psycopg.Connection):
        self.connection = connection
```

But this breaks existing usage where `SongsetClient(config.songsets_db_path)` is called. To minimize changes, Option B is safer:

```python
class SongsetClient:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self._connection = None
```

**Remove:**
- `snapshot_db()` method (SQLite-specific backup API)
- `_sync_metadata` table operations (replace or remove)

**SQL changes:** Same dialect updates (`?` -> `%s`, `datetime('now')` -> `CURRENT_TIMESTAMP`)

### 7.4 Unified Connection Strategy in `app.py`

**Before:**

```python
self.read_client = ReadOnlyClient(config.db_path, turso_url=..., turso_token=...)
self.songset_client = SongsetClient(config.songsets_db_path)
```

**After (Option B):**

```python
self.db_conn = psycopg.connect(config.database_url)  # single shared connection
self.read_client = ReadOnlyClient(self.db_conn)      # catalog reads
self.songset_client = SongsetClient(self.db_conn)    # songset writes (same conn)
```

Or if clients manage their own connections:

```python
self.read_client = ReadOnlyClient(config.database_url)
self.songset_client = SongsetClient(config.database_url)
```

**Note:** With Postgres, a single connection can handle both SELECT on catalog and INSERT/UPDATE on songsets. The privilege restriction is at the role level, not connection level.

---

## 8. Sync Service Removal

### 8.1 Admin Sync Service (`src/stream_of_worship/admin/services/sync.py`)

**Action:** Remove the entire file or reduce to a connection health checker.

**Remove:**
- `SyncService` class
- `SyncStatus`, `SyncResult` dataclasses
- `SyncConfigError`, `SyncNetworkError`
- `get_sync_service_from_config()`
- All Turso-specific validation and recovery logic

**Replacement:** Simple function:

```python
def check_database_connection(database_url: str) -> bool:
    """Verify Postgres connection is alive."""
    try:
        with psycopg.connect(database_url) as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False
```

### 8.2 App Sync Service (`src/stream_of_worship/app/services/sync.py`)

**Action:** Remove Turso sync entirely.

**Remove:**
- `AppSyncService` class
- `TursoNotConfiguredError`, `SyncAuthError`
- `last_sync_at` tracking via `_sync_metadata`

**Replacement:** If the app still wants to show "last refresh" time, store it in `~/.config/sow/last_refresh.txt` or a lightweight local file. Or add an `app_metadata` table in Postgres.

**Note:** The `Shift+S` keybinding in the TUI currently triggers sync. It should be removed or replaced with a no-op/reconnect action.

---

## 9. CLI Command Changes

### 9.1 Admin DB Commands (`src/stream_of_worship/admin/commands/db.py`)

| Command | Action |
|---------|--------|
| `init` | Keep. Connect to Postgres and run schema creation |
| `status` | Update. Show Postgres connection status, row counts, table sizes |
| `reset` | Update. Use Postgres `DROP TABLE CASCADE` |
| `path` | Remove or replace with `url` (masked DSN) |
| `sync` | **Remove** |
| `turso-bootstrap` | **Remove** |
| `tokens` | **Remove** |

**`init` command update:**

```python
def init_db(config_path=None):
    config = AdminConfig.load(config_path)
    client = DatabaseClient(database_url=config.database_url)
    client.initialize_schema()
    console.print("[green]Postgres schema initialized successfully![/green]")
```

### 9.2 App Main (`src/stream_of_worship/app/main.py`)

**Remove:**
- `db sync` CLI command
- `db turso-bootstrap` CLI command (if any)
- All references to `SOW_TURSO_READONLY_TOKEN`

**Note:** The app no longer needs a separate catalog DB path and songsets DB path. All data is in one Neon database.

---

## 10. Model Changes

### 10.1 `Song.from_row()` / `Recording.from_row()`

These methods currently accept `tuple` and access by index. With `psycopg`, `cursor.fetchone()` still returns a tuple by default. **No changes needed** if we keep tuple return.

If we switch to dict rows (`cursor(row_factory=dict_row)`), we'd need to rewrite all `from_row()` methods. **Recommendation:** Keep tuple return for minimal change.

### 10.2 `DatabaseStats`

Remove fields that are SQLite-specific:
- `integrity_ok` -> always `True` (or remove)
- `foreign_keys_enabled` -> always `True`
- `last_sync_at` -> remove or replace with `last_connected_at`
- `sync_version` -> bump to "3"
- `local_device_id` -> remove
- `turso_configured` -> remove

---

## 11. Testing Strategy

### 11.1 Add `testcontainers` for Integration Tests

Replace file-based SQLite tests with Postgres containers:

```python
# tests/conftest.py or test module
import pytest
from testcontainers.postgres import PostgresContainer

@pytest.fixture(scope="session")
def postgres_url():
    with PostgresContainer("postgres:16-alpine") as postgres:
        yield postgres.get_connection_url()
```

### 11.2 Update Existing Tests

**`tests/admin/commands/test_db_commands.py`:**
- Change `temp_db_path` fixture to `postgres_url` fixture
- Update `get_db_client(config)` to pass `database_url` instead of `db_path`
- Remove tests for `turso_url`, `turso_token`, `libsql`
- Remove/rewrite tests for `sync` command
- Update `show_status` tests to expect Postgres output format

**`tests/app/services/test_catalog_cross_db.py`:**
- Currently creates two SQLite DBs (catalog + songsets)
- Now creates **one** Postgres DB with all tables
- `CatalogService` tests should still work with a unified connection
- The "cross-DB" nature goes away; rename test file or adjust comments

### 11.3 New Tests to Add

- Postgres connection health check
- Role permission test: app role cannot INSERT into songs
- Role permission test: app role CAN INSERT into songsets
- Schema initialization idempotency (run `initialize_schema()` twice)
- `reset_database()` in Postgres

### 11.4 Running Tests

```bash
# Start testcontainers postgres automatically via fixture
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ \
  --ignore=tests/services/analysis \
  --ignore=services/qwen3/tests \
  --ignore=services/analysis/tests -v
```

---

## 12. File-by-File Implementation Checklist

### 12.1 Configuration Layer
- [ ] `pyproject.toml` — remove `libsql`, add `psycopg`, `testcontainers`
- [ ] `src/stream_of_worship/admin/config.py` — remove Turso, add `database_url`
- [ ] `src/stream_of_worship/app/config.py` — remove Turso, add `database_url`, remove `db_path`, `songsets_db_path`

### 12.2 Schema Layer
- [ ] `src/stream_of_worship/admin/db/schema.py` — rewrite ALL_SCHEMA_STATEMENTS for Postgres
- [ ] `src/stream_of_worship/app/db/schema.py` — rewrite for Postgres
- [ ] **New:** `src/stream_of_worship/db/postgres_schema.py` (optional unified schema file)

### 12.3 Database Clients
- [ ] `src/stream_of_worship/admin/db/client.py` — replace sqlite3/libsql with psycopg, update SQL dialect
- [ ] `src/stream_of_worship/app/db/read_client.py` — replace sqlite3/libsql with psycopg, update SQL dialect
- [ ] `src/stream_of_worship/app/db/songset_client.py` — replace sqlite3 with psycopg, update SQL dialect, remove `snapshot_db()`
- [ ] `src/stream_of_worship/admin/db/models.py` — update `DatabaseStats`

### 12.4 Services
- [ ] `src/stream_of_worship/admin/services/sync.py` — **DELETE** or replace with connection checker
- [ ] `src/stream_of_worship/app/services/sync.py` — **DELETE** or replace with connection checker

### 12.5 CLI Commands
- [ ] `src/stream_of_worship/admin/commands/db.py` — remove sync/turso-bootstrap/tokens commands, update init/status/reset
- [ ] `src/stream_of_worship/app/main.py` — remove sync CLI command

### 12.6 Application Layer
- [ ] `src/stream_of_worship/app/app.py` — update client initialization, remove sync service setup
- [ ] `src/stream_of_worship/app/services/catalog.py` — update imports, connection handling
- [ ] All app screens using `SongsetClient` — verify constructor signature change

### 12.7 Tests
- [ ] `tests/admin/commands/test_db_commands.py` — rewrite for Postgres/testcontainers
- [ ] `tests/app/services/test_catalog_cross_db.py` — rewrite for unified Postgres DB
- [ ] **New:** `tests/db/test_postgres_clients.py` — integration tests for psycopg clients
- [ ] **New:** `tests/db/test_role_permissions.py` — verify app role restrictions

---

## 13. Critical Design Decisions

### 13.1 Single Connection vs. Two Connections for App

**Decision:** The app uses a **single Postgres connection** with a role that has mixed privileges (SELECT on catalog, CRUD on songsets). This is simpler and appropriate for a single-operator system.

**Alternative:** Two separate connections (one read-only for catalog, one read-write for songsets) would more closely match the current architecture but adds complexity.

### 13.2 Timestamp Columns as TEXT

**Decision:** Keep timestamp columns as `TEXT` in Postgres (storing ISO strings) to avoid changing `Song.from_row()`, `Recording.from_row()`, and all datetime handling in the app layer.

**Alternative:** Convert to `timestamptz` and update models to use `datetime` objects. Cleaner for Postgres but requires touching every timestamp consumer.

### 13.3 JSON Columns as TEXT

**Decision:** Keep JSON-like columns as `TEXT`. The v4 runbook confirmed all rows are valid JSON. Conversion to `jsonb` is a Phase 2 optimization.

### 13.4 `SongsetClient.snapshot_db()`

**Decision:** Remove the SQLite backup API method. Postgres backups are handled at the infrastructure level (Neon point-in-time restore, `pg_dump`). If local export is needed, add an export function later.

### 13.5 `_sync_metadata` Table

**Decision:** Do not migrate `_sync_metadata`. The concept of "last sync" is meaningless without Turso. Replace with simple app-level tracking if needed.

---

## 14. Rollback Plan (Code-Level)

If the application changes need to be reverted before cutover:
1. Revert to the previous git commit (before the migration PR)
2. Restore `~/.config/sow-admin/config.toml` and `~/.config/sow/config.toml` from backups
3. Legacy SQLite/Turso files remain untouched during the code migration
4. The v4 runbook handles data-level rollback independently

---

## 15. Pre-Implementation Checklist

Before starting implementation:
- [ ] This plan is approved by operator (mhuang)
- [ ] Neon roles `sow_admin_rw` and `sow_app` are created with correct privileges
- [ ] v4 runbook data migration is ready (`01_schema.sql`, `02_load_data.py`, etc.)
- [ ] A staging branch in Neon is available for testing
- [ ] `specs/migration/checklists/cutover_checklist.md` is up to date

---

## 16. Post-Implementation Verification

After all code changes but before data cutover:
- [ ] `sow-admin` can connect to staging Neon and initialize schema
- [ ] `sow-app` can browse catalog via staging Neon
- [ ] `sow-app` can create/edit songsets via staging Neon
- [ ] Unit tests pass with `testcontainers`
- [ ] App role cannot write to catalog tables (permission denied)
- [ ] Admin role can write to all tables
