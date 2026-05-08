# PostgreSQL on Neon Migration — Application Layer Implementation Plan v2

**Date:** 2026-05-08  
**Runbook:** `specs/sqlite_turso_to_neon_migration_runbook_v4.md`  
**Status:** Draft / Review  
**Supersedes:** `specs/postgres_neon_migration_implementation_plan.md` (v1)

### v1 → v2 Changelog

| Area | v1 | v2 | Rationale |
|------|----|----|-----------|
| Timestamp columns | `TEXT` with `CURRENT_TIMESTAMP` default | `timestamptz` with ISO 8601 trigger | v1 trigger produced `2026-05-08 15:57:51.971703+00` format, breaking `fromisoformat()` callers; `timestamptz` is idiomatic Postgres and psycopg auto-converts to `datetime` |
| Destructive DB actions | `reset_database()` with `DROP TABLE CASCADE` | **Removed.** No `reset` CLI command. No `DROP TABLE` in application code. | Remote destructive operations on shared Postgres are irreversible; infrastructure-level reset via Neon branching/`pg_dump` is safer |
| Connection strategy | Ambiguous Option A vs B for SongsetClient | `ConnectionProvider` wrapper (shared connection, decoupled lifecycle) | Resolves v1's unresolved Option A/B; enables future swap to connection pool |
| Secret handling | `database_url` (with password) in TOML config | Split: `database_url` (no password) in TOML + `SOW_DATABASE_PASSWORD` env var | v1 regressed from current env-var-only token pattern; DSN in plaintext config exposes credentials |
| Neon cold-start | Not addressed | Pooled DSN (`-pooler`), `connect_timeout=10`, retry with backoff, TUI "Connecting..." indicator | Neon suspends compute after 5 min idle; TUI users hit ~300ms-1s+ cold starts |
| Songset data migration | Not addressed | Pre-cutover `sow-app songsets export-all` + post-cutover `sow-app songsets import` | Users with existing `songsets.db` data would lose it without a migration path |
| Data validation | Not addressed | Row-count comparison + spot-check queries between SQLite and Postgres before cutover | v1 only checked connectivity/permissions, not data parity |
| Testcontainers-only tests | All tests require Docker | `@pytest.mark.integration` marker; `-m "not integration"` skips Docker-dependent tests | Developers/CI without Docker cannot run v1's full test suite |
| `DatabaseStats.integrity_ok` | Hardcoded `True` | Runtime `pg_is_in_recovery()` check + row-count consistency | v1 lost all diagnostic capability |
| `snapshot_db()` replacement | "Add export function later" | Replace with `sow-app songsets export-all` as documented pre-cutover step | v1 removed backup with no replacement; users need a safety net |

---

## 1. Executive Summary

Migrate the Stream of Worship database layer from SQLite + Turso/libSQL embedded replicas to a unified PostgreSQL database hosted on Neon. This is a **hard cutover with a validation gate**: all libsql/Turso code is removed after data parity is verified. Both the catalog (`songs`, `recordings`) and user data (`songsets`, `songset_items`) live in the same Neon database. Admin CLI uses a read-write role; User App uses a role with `SELECT` on catalog tables and full CRUD on songset tables.

**Key difference from v1:** No destructive database operations (`DROP TABLE`, `reset`) exist in application code. Database reset/recreation is an infrastructure-level operation performed via Neon console or `pg_dump`/`psql`. Timestamps use native `timestamptz` columns. Connection handling uses a `ConnectionProvider` pattern.

---

## 2. Scope & Assumptions

**In scope:**
- Replace `sqlite3`/`libsql` with `psycopg` (v3) across all database clients
- Migrate catalog schema (`songs`, `recordings`) to Postgres with `timestamptz` columns
- Migrate app schema (`songsets`, `songset_items`) to Postgres with `timestamptz` columns
- Remove all Turso sync infrastructure (sync services, CLI commands, config)
- Update configuration layer for Postgres DSNs (split password into env var)
- Update all raw SQL for Postgres dialect
- Update tests with `testcontainers` for Postgres + `@pytest.mark.integration` for Docker-dependent tests
- Add data validation step before cutover
- Add songset data migration path (export → import)
- Data loading is handled by the existing v4 runbook (`02_load_data.py`)

**Out of scope:**
- Data migration execution (covered by v4 runbook)
- R2 object migration
- Feature additions beyond what's required for the database swap
- `jsonb` column conversion (Phase 2)
- SQL JOIN optimization between songset_items → songs/recordings (Phase 2)

**Assumptions:**
- Neon roles are pre-created: `sow_admin_rw` (catalog CRUD), `sow_app` (catalog SELECT + songset CRUD)
- Both roles connect to the same database; access is controlled via Postgres privileges
- `sync_metadata` table is excluded from Postgres (per checklist recommendation)
- Neon project is configured with pooled connection string (`-pooler` hostname)

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
| Admin catalog access | `psycopg` → Neon Postgres (pooled DSN) | `postgresql://...@ep-xxx-pooler.neon.tech/sow` |
| App catalog access | `psycopg` → Neon Postgres (read-only role, pooled DSN) | Same DB, restricted privileges |
| App songsets access | `psycopg` → Neon Postgres (same `ConnectionProvider`) | Same DB, songset tables |

**App connection strategy:** The app uses a **single `ConnectionProvider`** that manages one Postgres connection with a role that has:
- `SELECT` on `songs`, `recordings`
- `INSERT`, `UPDATE`, `DELETE` on `songsets`, `songset_items`
- No write access to catalog tables (enforced at Postgres privilege layer)

This eliminates the cross-DB lookup complexity currently handled by `CatalogService`. Since everything is in one database, true SQL JOINs between `songset_items` → `songs`/`recordings` become possible. However, to minimize scope, Phase 1 keeps the existing two-step Python lookup pattern. Phase 2 can optimize to JOINs.

### 3.3 ConnectionProvider Pattern

Both `ReadOnlyClient` and `SongsetClient` accept a `ConnectionProvider` instead of managing their own connections:

```python
class ConnectionProvider:
    """Manages a single psycopg connection with auto-reconnect and cold-start retry."""

    MAX_RETRIES = 2
    RETRY_DELAY_SECONDS = 1.0

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._connection: Optional[psycopg.Connection] = None

    def get_connection(self) -> psycopg.Connection:
        if self._connection is None or self._connection.closed:
            self._connection = self._connect_with_retry()
        return self._connection

    def _connect_with_retry(self) -> psycopg.Connection:
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                conn = psycopg.connect(
                    self.database_url,
                    connect_timeout=10,
                )
                conn.execute("SELECT 1")
                return conn
            except Exception:
                if attempt == self.MAX_RETRIES:
                    raise
                time.sleep(self.RETRY_DELAY_SECONDS * (attempt + 1))

    def close(self) -> None:
        if self._connection and not self._connection.closed:
            self._connection.close()
```

**Benefits over v1's Option A/B:**
- Shared connection (Option A benefit) — one connection to Neon, not two
- Decoupled lifecycle (Option B benefit) — clients don't own connection creation
- Built-in cold-start retry — handles Neon compute resume latency
- Easy swap to `psycopg_pool.ConnectionPool` in the future

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
    "testcontainers[postgres]>=4.0.0",
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
- `database_url: str = ""` — Postgres DSN **without password** (e.g. `postgresql://sow_admin_rw@ep-xxx-pooler.us-east-1.aws.neon.tech/sow?sslmode=require`)
- `neon_admin_role: str = "sow_admin_rw"` — informational, not secret

**TOML section rename:** `[turso]` → `[database]`

**Environment variables:**
- `SOW_DATABASE_URL` — overrides TOML `database_url` (replaces `SOW_TURSO_TOKEN`)
- `SOW_DATABASE_PASSWORD` — password for the DSN (always env-var, never in TOML)

**DSN assembly at runtime:**

```python
def get_connection_url(self) -> str:
    url = os.environ.get("SOW_DATABASE_URL", self.database_url)
    password = os.environ.get("SOW_DATABASE_PASSWORD", "")
    if password and "@" in url:
        user_host = url.split("://", 1)[1]
        url = url.replace(f"://{user_host.split('@', 1)[0]}@", f"://{user_host.split('@', 1)[0]}:{password}@")
    return url
```

```toml
# Before
[turso]
database_url = "libsql://..."
sync_on_startup = true

# After
[database]
url = "postgresql://sow_admin_rw@ep-xxx-pooler.us-east-1.aws.neon.tech/sow?sslmode=require"
# Password from SOW_DATABASE_PASSWORD env var
```

### 5.2 App Config (`src/stream_of_worship/app/config.py`)

**Remove:**
- `turso_database_url`
- `sync_on_startup`
- `turso_readonly_token` property
- `is_turso_configured` property
- `db_path` (catalog no longer local file)
- `get_default_db_path()`
- `songsets_db_path` (songsets now in same DB)

**Add:**
- `database_url: str = ""` — Postgres DSN for app role (without password)
- Same `get_connection_url()` method as admin config
- Same `SOW_DATABASE_PASSWORD` env var support

**TOML section rename:** `[turso]` → `[database]`

**Environment variables:**
- `SOW_DATABASE_URL` (replaces `SOW_TURSO_READONLY_TOKEN`)
- `SOW_DATABASE_PASSWORD`

### 5.3 Backward Compatibility

Config loader should **ignore** old `[turso]` sections silently (or log a warning). Provide a migration hint to users.

### 5.4 Neon Pooled DSN

The `database_url` in TOML should use the **pooled connection hostname** (`-pooler` suffix):

| Type | Hostname pattern |
|------|-----------------|
| Direct | `ep-xxx.us-east-1.aws.neon.tech` |
| **Pooled (recommended)** | `ep-xxx-pooler.us-east-1.aws.neon.tech` |

Pooled connections use Neon's built-in PgBouncer (transaction mode), which:
- Handles up to 10,000 concurrent connections
- Returns connections to pool after each transaction (allows scale-to-zero)
- Mitigates cold-start latency (pooler manages connection lifecycle)

---

## 6. Schema Migration (`src/stream_of_worship/admin/db/schema.py` + `app/db/schema.py`)

Create unified Postgres DDL in a new file: `src/stream_of_worship/db/postgres_schema.py` (or update existing schema files).

### 6.1 Timestamp Design: `timestamptz` (v2 change)

**v1 used `TEXT` columns with `CURRENT_TIMESTAMP` defaults.** This caused a format mismatch bug:
- Python `datetime.now().isoformat()` → `2026-05-08T15:57:51.971703` (ISO 8601, no TZ)
- SQLite `datetime('now')` → `2026-05-08 15:57:51` (non-ISO space separator)
- Postgres `CURRENT_TIMESTAMP` cast to TEXT → `2026-05-08 15:57:51.971703+00` (different from both)

**v2 decision:** Use native `timestamptz` columns. psycopg3 auto-converts `timestamptz` values to Python `datetime` objects with `timezone.utc`. This requires updating `from_row()` methods to accept both `str` and `datetime` (see Section 10).

**Rationale for changing from v1:**
- The "keep TEXT to minimize model changes" rationale doesn't hold because the format is changing anyway (Postgres `CURRENT_TIMESTAMP` produces a different string format than SQLite `datetime('now')`)
- `timestamptz` enables server-side timestamp arithmetic, ordering, and timezone-aware comparisons
- The `audio.py:3408-3413` staleness check already parses `updated_at` with `fromisoformat()` and checks `tzinfo` — `timestamptz` makes this work correctly instead of assuming UTC on a naive datetime

### 6.2 Catalog Tables (from `admin/db/schema.py`)

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
    created_at timestamptz DEFAULT NOW(),
    updated_at timestamptz DEFAULT NOW(),
    deleted_at timestamptz
);
```

**Key decisions:**
- `scraped_at` and `imported_at` remain `TEXT` — these are set by Python (`datetime.now().isoformat()`) and never updated by SQL; keeping them as TEXT avoids reformatting historical data from the runbook load
- `created_at`, `updated_at`, `deleted_at` use `timestamptz` — these are set/updated by SQL (defaults, triggers, UPDATE statements) and need consistent server-side behavior
- Keep JSON-like columns (`lyrics_lines`, `sections`) as `TEXT` (runbook v4 JSON scan confirms all rows are valid JSON; `jsonb` conversion is Phase 2 optimization)

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
    created_at timestamptz DEFAULT NOW(),
    updated_at timestamptz DEFAULT NOW(),
    youtube_url TEXT,
    visibility_status TEXT DEFAULT NULL,
    deleted_at timestamptz
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

**`updated_at` trigger (Postgres syntax, ISO 8601 output):**

```sql
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
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

Since the column is `timestamptz`, `NOW()` returns a proper `timestamptz` value. When psycopg reads it, it arrives as a `datetime` object with `tzinfo=timezone.utc`, which `from_row()` handles per Section 10.

### 6.3 App Tables (from `app/db/schema.py`)

**`songsets` table:**

```sql
CREATE TABLE IF NOT EXISTS songsets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    created_at timestamptz DEFAULT NOW(),
    updated_at timestamptz DEFAULT NOW()
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
    created_at timestamptz DEFAULT NOW()
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

### 6.4 Deprecated Tables

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
def __init__(self, connection_provider: ConnectionProvider):
```

**Connection access:**

```python
@property
def connection(self) -> psycopg.Connection:
    return self.connection_provider.get_connection()
```

**Remove entirely:**
- `is_turso_enabled`
- `turso_url`, `turso_token`
- `sync()` method
- `update_sync_metadata()` (or repurpose for generic metadata)
- `sqlite_master` queries
- `PRAGMA` queries
- SQLite file path handling
- `reset_database()` method — **removed in v2** (no destructive DB operations in app code)

**SQL dialect changes (all methods):**
- `?` placeholders → `%s`
- `datetime('now')` → `NOW()`
- `INSERT OR REPLACE` → `INSERT ... ON CONFLICT DO UPDATE`
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
        with conn.transaction():
            yield conn
    except Exception:
        raise
```

**`get_stats()` refactoring:**

Remove SQLite-specific checks. Replace with Postgres-compatible stats:

```python
def get_stats(self) -> DatabaseStats:
    cursor = self.connection.cursor()

    cursor.execute(
        "SELECT 'songs', COUNT(*) FROM songs "
        "UNION ALL SELECT 'recordings', COUNT(*) FROM recordings"
    )
    table_counts = {row[0]: row[1] for row in cursor.fetchall()}

    cursor.execute("SELECT pg_is_in_recovery()")
    is_healthy = not cursor.fetchone()[0]

    return DatabaseStats(
        table_counts=table_counts,
        is_healthy=is_healthy,
        last_sync_at=None,
        sync_version="3",
    )
```

**`reset_database()` — REMOVED in v2**

Database reset is an infrastructure-level operation. To reset the database:
1. Use Neon console to create a fresh branch
2. Or use `pg_dump` / `psql` to recreate schema
3. Or use `sow-admin db init` on a new empty database

The admin CLI `db reset` command is also removed (see Section 9).

### 7.2 App `ReadOnlyClient` (`src/stream_of_worship/app/db/read_client.py`)

**Major change:** This client no longer needs to be "read-only" in code — Postgres privileges enforce read-only on catalog tables. But to minimize changes, keep the class name and methods.

**Constructor:**

```python
def __init__(self, connection_provider: ConnectionProvider):
```

**Remove:**
- `turso_url`, `turso_token`
- `is_turso_enabled`
- `sync()` → replace with `check_connection()` that verifies liveness
- `_migrate_schema()` → no longer needed (no schema migrations on connect)

**SQL changes:** Same as admin client (`?` → `%s`, `datetime('now')` → `NOW()`, etc.)

### 7.3 App `SongsetClient` (`src/stream_of_worship/app/db/songset_client.py`)

**Constructor change:**

```python
# Before
def __init__(self, db_path: Path):

# After
def __init__(self, connection_provider: ConnectionProvider):
```

Both `ReadOnlyClient` and `SongsetClient` now accept the same `ConnectionProvider` instance, sharing a single underlying connection.

**Remove:**
- `snapshot_db()` method (SQLite-specific backup API)
- `_sync_metadata` table operations (replace or remove)
- All SQLite-specific path handling

**SQL changes:** Same dialect updates (`?` → `%s`, `datetime('now')` → `NOW()`)

### 7.4 Unified Connection Strategy in `app.py`

**Before:**

```python
self.read_client = ReadOnlyClient(config.db_path, turso_url=..., turso_token=...)
self.songset_client = SongsetClient(config.songsets_db_path)
```

**After:**

```python
from stream_of_worship.db.connection import ConnectionProvider

provider = ConnectionProvider(config.get_connection_url())
self.read_client = ReadOnlyClient(provider)
self.songset_client = SongsetClient(provider)
```

Both clients share the same underlying `psycopg.Connection` via the `ConnectionProvider`. The privilege restriction is at the Postgres role level, not connection level.

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

**Replacement:** The `ConnectionProvider._connect_with_retry()` method handles connection health. For CLI-level checks, a simple function:

```python
def check_database_connection(database_url: str) -> bool:
    try:
        with psycopg.connect(database_url, connect_timeout=10) as conn:
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

**Replacement:** If the app still wants to show "last refresh" time, store it in `~/.config/sow/last_refresh.txt` or a lightweight local file.

**Note:** The `Shift+S` keybinding in the TUI currently triggers sync. Replace with a "Reconnect" action that calls `ConnectionProvider._connect_with_retry()`.

---

## 9. CLI Command Changes

### 9.1 Admin DB Commands (`src/stream_of_worship/admin/commands/db.py`)

| Command | Action |
|---------|--------|
| `init` | Keep. Connect to Postgres and run schema creation |
| `status` | Update. Show Postgres connection status, row counts, table sizes, health check |
| `reset` | **REMOVED in v2** — no destructive DB operations in application code |
| `path` | Replace with `url` (masked DSN, password redacted) |
| `sync` | **Remove** |
| `turso-bootstrap` | **Remove** |
| `tokens` | **Remove** |

**`init` command update:**

```python
def init_db(config_path=None):
    config = AdminConfig.load(config_path)
    provider = ConnectionProvider(config.get_connection_url())
    client = DatabaseClient(provider)
    client.initialize_schema()
    console.print("[green]Postgres schema initialized successfully![/green]")
```

**`url` command (replaces `path`):**

```python
def show_url(config_path=None):
    config = AdminConfig.load(config_path)
    url = config.database_url
    masked = re.sub(r'://([^:]+):([^@]+)@', r'://\1:****@', url)
    console.print(f"Database URL (masked): {masked}")
    console.print("Password: loaded from SOW_DATABASE_PASSWORD env var" if os.environ.get("SOW_DATABASE_PASSWORD") else "Password: NOT SET")
```

### 9.2 App Main (`src/stream_of_worship/app/main.py`)

**Remove:**
- `db sync` CLI command
- `db turso-bootstrap` CLI command (if any)
- All references to `SOW_TURSO_READONLY_TOKEN`

**Add:**
- `db check` CLI command — verifies Neon connection is alive

**Note:** The app no longer needs a separate catalog DB path and songsets DB path. All data is in one Neon database.

---

## 10. Model Changes

### 10.1 `Song.from_row()` / `Recording.from_row()` — Handle `timestamptz` (v2 change)

With `timestamptz` columns, psycopg3 returns `datetime` objects (with `tzinfo=timezone.utc`) instead of strings. The `from_row()` methods currently type these fields as `Optional[str]` and pass them through without parsing.

**Two options:**

**Option A: Coerce at the model layer** — Update `from_row()` to accept both `str` and `datetime`:

```python
@classmethod
def from_row(cls, row: tuple) -> "Song":
    def _to_str(val) -> Optional[str]:
        if val is None:
            return None
        if isinstance(val, datetime):
            return val.isoformat()
        return str(val)

    return cls(
        # ... other fields ...
        created_at=_to_str(row[14]),
        updated_at=_to_str(row[15]),
        deleted_at=_to_str(row[16]) if len(row) > 16 else None,
    )
```

This preserves the `Optional[str]` type on the dataclass fields (minimal model changes) while correctly converting `datetime` → ISO 8601 string at the boundary.

**Option B: Use `row_factory=dict_row`** — Switch to dict-based rows. Requires rewriting all `from_row()` methods to access by column name instead of index. More invasive but more robust for schema evolution.

**Recommendation: Option A** (coerce at model layer). It's the minimum viable change and produces consistent ISO 8601 strings from both `timestamptz` and `TEXT` columns.

**Note on `scraped_at` and `imported_at`:** These remain `TEXT` columns. If they were loaded from the v4 runbook as ISO 8601 strings, they'll still be strings. `_to_str()` handles this correctly (passes strings through, converts `datetime` objects).

### 10.2 `DatabaseStats`

Remove fields that are SQLite-specific:

```python
@dataclass
class DatabaseStats:
    table_counts: Dict[str, int]
    is_healthy: bool          # was integrity_ok; now runtime pg_is_in_recovery() check
    last_sync_at: Optional[str] = None   # unused, kept for API compat
    sync_version: str = "3"              # bumped to indicate Postgres
```

**Removed fields:**
- `foreign_keys_enabled` → always `True` in Postgres
- `local_device_id` → meaningless without Turso
- `turso_configured` → always `False`
- `integrity_ok` → replaced by `is_healthy` (runtime check)

### 10.3 `Songset.from_row()` / `SongsetItem.from_row()`

Same `_to_str()` coercion pattern for `created_at` and `updated_at` fields.

---

## 11. Testing Strategy

### 11.1 Add `testcontainers` for Integration Tests

Replace file-based SQLite tests with Postgres containers. Mark Docker-dependent tests with `@pytest.mark.integration`:

```python
import pytest
from testcontainers.postgres import PostgresContainer

@pytest.fixture(scope="session")
def postgres_url():
    with PostgresContainer("postgres:16-alpine") as postgres:
        yield postgres.get_connection_url()
```

### 11.2 Test Markers

```python
# tests/conftest.py
import pytest

def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires Docker (testcontainers)")

def pytest_collection_modifyitems(config, items):
    if config.getoption("-m") == "not integration":
        # Default: skip integration tests unless explicitly selected
        pass
```

**Running tests:**

```bash
# Unit tests only (no Docker required)
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ \
  -m "not integration" \
  --ignore=tests/services/analysis \
  --ignore=services/qwen3/tests \
  --ignore=services/analysis/tests -v

# All tests including integration (requires Docker)
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ \
  --ignore=tests/services/analysis \
  --ignore=services/qwen3/tests \
  --ignore=services/analysis/tests -v
```

### 11.3 Update Existing Tests

**`tests/admin/commands/test_db_commands.py`:**
- Change `temp_db_path` fixture to `postgres_url` fixture
- Update `get_db_client(config)` to pass `ConnectionProvider` instead of `db_path`
- Remove tests for `turso_url`, `turso_token`, `libsql`
- Remove/rewrite tests for `sync` command
- **Remove tests for `reset` command** (no longer exists)
- Update `show_status` tests to expect Postgres output format

**`tests/app/services/test_catalog_cross_db.py`:**
- Currently creates two SQLite DBs (catalog + songsets)
- Now creates **one** Postgres DB with all tables
- `CatalogService` tests should still work with a unified connection
- The "cross-DB" nature goes away; rename test file or adjust comments

**`tests/app/db/test_songset_client.py`:**
- Remove `snapshot_db()` tests (method removed)
- Add `ConnectionProvider`-based setup

### 11.4 New Tests to Add

**Integration (requires Docker):**
- Postgres connection health check
- Role permission test: app role cannot INSERT into songs
- Role permission test: app role CAN INSERT into songsets
- Schema initialization idempotency (run `initialize_schema()` twice)
- `timestamptz` column reads return `datetime` objects

**Unit (no Docker):**
- `_to_str()` coercion in `Song.from_row()` handles both `str` and `datetime`
- `ConnectionProvider` retry logic (mock `psycopg.connect`)
- DSN password assembly from env var
- Config backward compatibility (old `[turso]` section ignored)

---

## 12. File-by-File Implementation Checklist

### 12.1 Configuration Layer
- [ ] `pyproject.toml` — remove `libsql`, add `psycopg`, `testcontainers`
- [ ] `src/stream_of_worship/admin/config.py` — remove Turso, add `database_url`, `get_connection_url()`, `SOW_DATABASE_PASSWORD` support
- [ ] `src/stream_of_worship/app/config.py` — remove Turso, add `database_url`, `get_connection_url()`, remove `db_path`, `songsets_db_path`

### 12.2 Schema Layer
- [ ] `src/stream_of_worship/admin/db/schema.py` — rewrite ALL_SCHEMA_STATEMENTS for Postgres with `timestamptz`
- [ ] `src/stream_of_worship/app/db/schema.py` — rewrite for Postgres with `timestamptz`
- [ ] **New:** `src/stream_of_worship/db/postgres_schema.py` (optional unified schema file)

### 12.3 Database Clients
- [ ] **New:** `src/stream_of_worship/db/connection.py` — `ConnectionProvider` class
- [ ] `src/stream_of_worship/admin/db/client.py` — replace sqlite3/libsql with psycopg via `ConnectionProvider`, update SQL dialect, **remove `reset_database()`**
- [ ] `src/stream_of_worship/app/db/read_client.py` — replace sqlite3/libsql with psycopg via `ConnectionProvider`, update SQL dialect
- [ ] `src/stream_of_worship/app/db/songset_client.py` — replace sqlite3 with psycopg via `ConnectionProvider`, update SQL dialect, remove `snapshot_db()`
- [ ] `src/stream_of_worship/admin/db/models.py` — update `DatabaseStats`, add `_to_str()` coercion in `from_row()` methods
- [ ] `src/stream_of_worship/app/db/models.py` — add `_to_str()` coercion in `from_row()` methods

### 12.4 Services
- [ ] `src/stream_of_worship/admin/services/sync.py` — **DELETE** or replace with `check_database_connection()`
- [ ] `src/stream_of_worship/app/services/sync.py` — **DELETE** or replace with connection checker

### 12.5 CLI Commands
- [ ] `src/stream_of_worship/admin/commands/db.py` — remove sync/turso-bootstrap/tokens/**reset** commands, update init/status, add `url` command
- [ ] `src/stream_of_worship/app/main.py` — remove sync CLI command, add `db check` command

### 12.6 Application Layer
- [ ] `src/stream_of_worship/app/app.py` — update client initialization with `ConnectionProvider`, remove sync service setup
- [ ] `src/stream_of_worship/app/services/catalog.py` — update imports, connection handling
- [ ] All app screens using `SongsetClient` — verify constructor signature change (now accepts `ConnectionProvider`)
- [ ] `src/stream_of_worship/app/screens/songset_list.py` — verify `fromisoformat()` works with `timestamptz`-derived strings

### 12.7 Tests
- [ ] `tests/conftest.py` — add `postgres_url` fixture, add `@pytest.mark.integration` marker
- [ ] `tests/admin/commands/test_db_commands.py` — rewrite for Postgres/testcontainers, remove reset tests
- [ ] `tests/app/services/test_catalog_cross_db.py` — rewrite for unified Postgres DB
- [ ] `tests/app/db/test_songset_client.py` — remove `snapshot_db()` tests, update for `ConnectionProvider`
- [ ] **New:** `tests/db/test_postgres_clients.py` — integration tests for psycopg clients
- [ ] **New:** `tests/db/test_role_permissions.py` — verify app role restrictions
- [ ] **New:** `tests/db/test_connection_provider.py` — unit tests for ConnectionProvider retry logic
- [ ] **New:** `tests/db/test_model_coercion.py` — unit tests for `_to_str()` datetime→string coercion

---

## 13. Critical Design Decisions

### 13.1 Single Connection via `ConnectionProvider` (v2 change)

**Decision:** The app uses a **single `ConnectionProvider`** shared by both `ReadOnlyClient` and `SongsetClient`. This gives shared-connection simplicity while decoupling clients from connection lifecycle management.

**Why not two separate connections:** Two connections to the same database with the same role doubles connection overhead on Neon (which has connection limits) and provides no privilege separation benefit (privileges are role-level, not connection-level).

**Why not raw `psycopg.connect()` in each client:** Tightly couples clients to connection creation, making it hard to add retry logic, connection pooling, or testing with mock connections.

### 13.2 Timestamp Columns as `timestamptz` (v2 change)

**Decision:** Use `timestamptz` for `created_at`, `updated_at`, `deleted_at`. Keep `scraped_at` and `imported_at` as `TEXT` (they're set by Python and never modified by SQL).

**Why changed from v1:** v1's `TEXT` approach with `CURRENT_TIMESTAMP` would produce format `2026-05-08 15:57:51.971703+00` when implicitly cast to text, which differs from both the Python ISO 8601 format and the SQLite `datetime('now')` format. This would break `fromisoformat()` callers and produce inconsistent data. Using `timestamptz` is idiomatic Postgres and makes the format issue explicit at the model boundary via `_to_str()`.

**Impact:** `_to_str()` coercion added to `from_row()` methods. This is a small, well-contained change. The alternative (keeping TEXT with a custom `to_char()` format in triggers) would be fragile and non-standard.

### 13.3 JSON Columns as TEXT

**Decision:** Keep JSON-like columns as `TEXT`. The v4 runbook confirmed all rows are valid JSON. Conversion to `jsonb` is a Phase 2 optimization.

### 13.4 `SongsetClient.snapshot_db()` Replacement (v2 change)

**Decision:** Remove `snapshot_db()`. Instead, document the pre-cutover safety step:

```bash
# Before cutover: export all songsets as JSON
sow-app songsets export-all --output-dir ~/sow-songset-backup

# After cutover: re-import if needed
sow-app songsets import ~/sow-songset-backup/<songset>.json --on-conflict rename
```

This leverages the existing JSON export/import CLI (`src/stream_of_worship/app/services/songset_io.py`) instead of the SQLite-specific backup API.

### 13.5 `_sync_metadata` Table

**Decision:** Do not migrate `_sync_metadata`. The concept of "last sync" is meaningless without Turso. Replace with simple app-level tracking if needed.

### 13.6 No Destructive DB Operations in Application Code (v2 change)

**Decision:** Remove `reset_database()` method and `db reset` CLI command. Database reset/recreation is an infrastructure-level operation performed via:
1. Neon console: create a fresh branch, then promote it
2. `pg_dump` / `psql`: manual schema recreation
3. `sow-admin db init` on a new empty Neon database

**Why:** Running `DROP TABLE CASCADE` on a remote shared Postgres database is irreversible. Unlike SQLite file deletion (which is local and recoverable from backup), a remote DROP cannot be undone. The existing `--confirm` flag is a non-interactive boolean, not a "type the database name" safety prompt. Removing this capability from app code eliminates the risk entirely.

---

## 14. Data Validation Gate (v2 addition)

Before cutover, run a validation step comparing SQLite source data with Postgres destination data:

### 14.1 Row Count Comparison

```sql
-- Postgres
SELECT 'songs', COUNT(*) FROM songs
UNION ALL SELECT 'recordings', COUNT(*) FROM recordings;

-- SQLite (before decommissioning)
SELECT 'songs', COUNT(*) FROM songs
UNION ALL SELECT 'recordings', COUNT(*) FROM recordings;
```

### 14.2 Spot-Check Queries

Verify 10 random rows from each table match:

```sql
-- Compare a specific song by ID
SELECT id, title, scraped_at FROM songs WHERE id = '<known_id>';
```

### 14.3 Timestamp Format Verification

After data load, verify that `timestamptz` columns contain valid values:

```sql
SELECT id, created_at, updated_at FROM songs LIMIT 5;
SELECT content_hash, created_at, updated_at FROM recordings LIMIT 5;
```

Confirm psycopg reads these as `datetime` objects and `_to_str()` produces consistent ISO 8601 strings.

### 14.4 Songset Data Migration

Existing user songset data in `~/.config/sow/db/songsets.db` must be exported before cutover:

```bash
# Step 1: Export all songsets from local SQLite
sow-app songsets export-all --output-dir ~/sow-songset-backup

# Step 2: After Postgres cutover, import into Neon
sow-app songsets import ~/sow-songset-backup/<songset>.json --on-conflict rename
```

The existing JSON export/import service (`src/stream_of_worship/app/services/songset_io.py`) handles this. No new migration code needed.

---

## 15. Rollback Plan (Code-Level)

If the application changes need to be reverted before cutover:
1. Revert to the previous git commit (before the migration PR)
2. Restore `~/.config/sow-admin/config.toml` and `~/.config/sow/config.toml` from backups
3. Legacy SQLite/Turso files remain untouched during the code migration
4. The v4 runbook handles data-level rollback independently
5. Re-import songsets from the JSON backup created in Section 14.4

---

## 16. Pre-Implementation Checklist

Before starting implementation:
- [ ] This plan is approved by operator (mhuang)
- [ ] Neon roles `sow_admin_rw` and `sow_app` are created with correct privileges
- [ ] Neon pooled connection hostname (`-pooler`) is identified and verified
- [ ] v4 runbook data migration is ready (`01_schema.sql`, `02_load_data.py`, etc.)
- [ ] A staging branch in Neon is available for testing
- [ ] `specs/migration/checklists/cutover_checklist.md` is up to date

---

## 17. Post-Implementation Verification

After all code changes but before data cutover:
- [ ] `sow-admin db init` connects to staging Neon and creates schema
- [ ] `sow-admin db status` shows connection health, row counts
- [ ] `sow-app` can browse catalog via staging Neon
- [ ] `sow-app` can create/edit songsets via staging Neon
- [ ] Integration tests pass with `testcontainers`
- [ ] Unit tests pass without Docker (`-m "not integration"`)
- [ ] App role cannot write to catalog tables (permission denied)
- [ ] Admin role can write to all tables
- [ ] `timestamptz` columns return `datetime` objects via psycopg
- [ ] `_to_str()` coercion produces consistent ISO 8601 strings
- [ ] `ConnectionProvider` retry works on cold start (test with idle Neon compute)
- [ ] Data validation gate (Section 14) passes: row counts match, spot-checks pass
- [ ] Songset export/import round-trip works

---

## 18. Neon Operational Runbook (v2 addition)

### 18.1 Connection Parameters

```python
psycopg.connect(
    database_url,
    connect_timeout=10,     # accommodate cold-start latency
    sslmode="require",      # Neon requires SSL
)
```

### 18.2 Cold-Start Behavior

- Neon suspends compute after 5 minutes of inactivity (default on free tier)
- Cold start latency: ~300ms to ~1s (compute activation + SSL handshake)
- `ConnectionProvider._connect_with_retry()` handles this with 2 retries and exponential backoff

### 18.3 Connection Pooling

- Always use **pooled DSN** (`-pooler` hostname) for application connections
- Neon's built-in PgBouncer runs in transaction mode
- Limitations of transaction mode: no `SET`/`RESET`, no `LISTEN`/`NOTIFY`, no temp tables with `PRESERVE`/`DELETE ROWS`
- The application does not use any of these features

### 18.4 Database Reset (Infrastructure Level)

To reset the database without application-level destructive operations:

1. **Via Neon branching:** Create a fresh branch from an empty state, then promote it
2. **Via psql:** Drop and recreate tables manually (outside application code)
3. **Via new database:** Create a new Neon database, point `database_url` to it, run `sow-admin db init`

### 18.5 Point-in-Time Restore

- Neon provides point-in-time restore (PITR) on paid plans
- To restore: use Neon console to create a branch from a specific timestamp
- Test PITR procedure on staging before relying on it in production

### 18.6 Monitoring

- `sow-admin db status` shows connection health and row counts
- `sow-app db check` verifies app-role connectivity
- Neon console provides query metrics, connection counts, and compute status

### 18.7 Connection Limits

| Tier | Max concurrent connections |
|------|---------------------------|
| Free | ~5 (direct) / 100 (pooled) |
| Pro | ~100 (direct) / 10,000 (pooled) |

The pooled DSN mitigates connection limit concerns for a single-operator TUI application.
