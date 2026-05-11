# PostgreSQL on Neon — Database Layer Architecture

**Date:** 2026-05-11  
**Status:** Current  
**Audience:** Agents / developers working with the Stream of Worship database layer

---

## 1. Overview

The Stream of Worship database layer uses **PostgreSQL hosted on Neon** with the **psycopg v3** driver. All data (catalog and user songsets) resides in a single Neon database, with access controlled via Postgres roles:

| Role | Catalog Tables | Songset Tables |
|------|---------------|----------------|
| `sow_admin_rw` | Full CRUD | Full CRUD |
| `sow_app` | SELECT only | Full CRUD |

**Key design principles:**
- **Single shared connection** via `ConnectionProvider` — one `psycopg.Connection` shared by all clients in the app
- **Password separation** — DSN in TOML config (no password), password from `SOW_DATABASE_PASSWORD` env var
- **Cold-start resilience** — Neon suspends compute after 5 min idle; `ConnectionProvider` retries with exponential backoff
- **Pooled connections** — Always use Neon's `-pooler` hostname for transaction-mode connection pooling
- **No destructive operations in app code** — Database reset is infrastructure-level only (Neon branching, `pg_dump`/`psql`)

---

## 2. Architecture

### 2.1 Component Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              Configuration                                   │
│  ┌─────────────────────┐    ┌─────────────────────┐                        │
│  │   AdminConfig       │    │    AppConfig        │                        │
│  │  (admin/config.py)  │    │  (app/config.py)    │                        │
│  │                     │    │                     │                        │
│  │  database_url ──────┼────┼── database_url      │                        │
│  │  (password-less)    │    │  (password-less)    │                        │
│  └─────────┬───────────┘    └─────────┬───────────┘                        │
│            │                          │                                     │
│            │  get_connection_url()    │  get_connection_url()              │
│            │  + SOW_DATABASE_PASSWORD │  + SOW_DATABASE_PASSWORD           │
│            ▼                          ▼                                     │
└─────────────────────────────────────────────────────────────────────────────┘
             │                          │
             │                          │
             ▼                          ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Connection Layer                                     │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                    ConnectionProvider                                │   │
│  │                  (db/connection.py)                                  │   │
│  │                                                                      │   │
│  │  - Holds single psycopg.Connection (lazy init)                       │   │
│  │  - Thread-safe auto-reconnect on .closed                             │   │
│  │  - Cold-start retry: MAX_RETRIES=2, exponential backoff              │   │
│  │  - connect_timeout=10, sslmode="prefer"                              │   │
│  └──────────────────────────────┬───────────────────────────────────────┘   │
│                                 │                                            │
│                                 │ get_connection()                           │
│                                 ▼                                            │
│                    ┌────────────────────────┐                               │
│                    │   psycopg.Connection   │                               │
│                    │   (single shared)      │                               │
│                    └────────────────────────┘                               │
└─────────────────────────────────────────────────────────────────────────────┘
                                 │
         ┌───────────────────────┼───────────────────────┐
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
│ DatabaseClient  │   │ ReadOnlyClient  │   │ SongsetClient   │
│ (admin/db/)     │   │ (app/db/)       │   │ (app/db/)       │
│                 │   │                 │   │                 │
│ songs           │   │ songs           │   │ songsets        │
│ recordings      │   │ recordings      │   │ songset_items   │
│ (full CRUD)     │   │ (SELECT only)   │   │ (full CRUD)     │
└─────────────────┘   └─────────────────┘   └─────────────────┘
         │                       │                       │
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Neon PostgreSQL                                      │
│                                                                              │
│  Tables: songs, recordings, songsets, songset_items                         │
│  Roles: sow_admin_rw (admin), sow_app (app)                                 │
│  Host: ep-xxx-pooler.us-east-1.aws.neon.tech (pooled DSN)                  │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Connection Flow

```
1. Config loads TOML → database_url (password-less DSN)
2. Config.get_connection_url() → injects password from SOW_DATABASE_PASSWORD env var
3. ConnectionProvider(database_url) created
4. Client calls connection_provider.get_connection()
5. ConnectionProvider lazily connects with retry on cold start
6. psycopg.Connection returned (cached for reuse)
7. Client executes queries via connection.cursor() or connection.execute()
```

---

## 3. Connection Management

### 3.1 ConnectionProvider

**Location:** `src/stream_of_worship/db/connection.py`

**Responsibilities:**
- Manage a single `psycopg.Connection` with lazy initialization
- Auto-reconnect if connection is closed
- Retry on cold-start failures (Neon compute resume latency)
- Thread-safe connection access

**Key attributes:**
```python
MAX_RETRIES = 2
RETRY_DELAY_SECONDS = 1.0  # Exponential backoff: 1s, 2s
connect_timeout = 10
sslmode = "prefer"
```

**Usage pattern:**
```python
provider = ConnectionProvider(config.get_connection_url())
client = DatabaseClient(provider)  # or ReadOnlyClient, SongsetClient
```

### 3.2 DSN Assembly

The DSN is split between TOML config (no password) and environment variable:

**TOML (`~/.config/sow-admin/config.toml` or `~/.config/sow/config.toml`):**
```toml
[database]
url = "postgresql://sow_admin_rw@ep-xxx-pooler.us-east-1.aws.neon.tech/sow?sslmode=require"
```

**Environment variable:**
```bash
export SOW_DATABASE_PASSWORD="your-password-here"
```

**Runtime assembly (in `AdminConfig.get_connection_url()` / `AppConfig.get_connection_url()`):**
```python
def get_connection_url(self) -> str:
    url = os.environ.get("SOW_DATABASE_URL", self.database_url)
    password = os.environ.get("SOW_DATABASE_PASSWORD", "")
    if password and "@" in url:
        # Inject password into DSN
        ...
    return url
```

### 3.3 Cold-Start Handling

Neon suspends compute after 5 minutes of inactivity. Cold starts take ~300ms–1s. `ConnectionProvider._connect_with_retry()` handles this:

```python
def _connect_with_retry(self) -> psycopg.Connection:
    for attempt in range(self.MAX_RETRIES + 1):
        try:
            conn = psycopg.connect(self.database_url, connect_timeout=10)
            conn.execute("SELECT 1")  # Force connection establishment
            return conn
        except Exception:
            if attempt == self.MAX_RETRIES:
                raise
            time.sleep(self.RETRY_DELAY_SECONDS * (attempt + 1))
```

---

## 4. Schema

### 4.1 Tables

**Catalog tables (admin + app read):**

| Table | Primary Key | Key Columns | Notes |
|-------|-------------|-------------|-------|
| `songs` | `id` (TEXT) | `title`, `album_name`, `title_pinyin`, `deleted_at` | Soft delete via `deleted_at` |
| `recordings` | `content_hash` (TEXT) | `song_id` → `songs.id`, `hash_prefix` (UNIQUE), `analysis_status`, `visibility_status`, `deleted_at` | Soft delete via `deleted_at` |

**User data tables (app only):**

| Table | Primary Key | Key Columns | Notes |
|-------|-------------|-------------|-------|
| `songsets` | `id` (TEXT) | `name`, `description` | User-created song collections |
| `songset_items` | `id` (TEXT) | `songset_id` → `songsets.id` (CASCADE), `song_id`, `recording_hash_prefix`, `position` | Items within a songset |

### 4.2 Timestamp Column Design

| Column | Type | Set By | Rationale |
|--------|------|--------|-----------|
| `created_at` | `timestamptz` | SQL `DEFAULT NOW()` | Server-side, consistent timezone handling |
| `updated_at` | `timestamptz` | SQL trigger | Auto-updated on every row modification |
| `deleted_at` | `timestamptz` | Python | Soft delete timestamp |
| `scraped_at` | `TEXT` | Python | Set once at scrape time, never modified by SQL |
| `imported_at` | `TEXT` | Python | Set once at import time, never modified by SQL |

**Why `timestamptz` for SQL-managed columns:**
- psycopg3 auto-converts to Python `datetime` with `tzinfo=timezone.utc`
- Server-side timestamp arithmetic and ordering work correctly
- Consistent format regardless of client timezone

**Why `TEXT` for Python-managed columns:**
- Set by `datetime.now().isoformat()` in Python code
- Never modified by SQL triggers or defaults
- Avoids reformatting historical data from migration

### 4.3 JSON Columns

JSON-like columns (`lyrics_lines`, `sections`, `beats`, `downbeats`, `embeddings_shape`) are stored as `TEXT`. All data is valid JSON (verified during migration). Conversion to `jsonb` is a future optimization.

### 4.4 Indexes

**Catalog indexes (8 total):**
```sql
CREATE INDEX idx_recordings_song_id ON recordings(song_id);
CREATE INDEX idx_recordings_analysis_status ON recordings(analysis_status);
CREATE INDEX idx_recordings_hash_prefix ON recordings(hash_prefix);
CREATE INDEX idx_songs_album ON songs(album_name);
CREATE INDEX idx_songs_title_pinyin ON songs(title_pinyin);
CREATE INDEX idx_recordings_visibility_status ON recordings(visibility_status);
CREATE INDEX idx_songs_deleted_at ON songs(deleted_at);
CREATE INDEX idx_recordings_deleted_at ON recordings(deleted_at);
```

**Songset indexes (3 total):**
```sql
CREATE INDEX idx_songset_items_songset_id ON songset_items(songset_id);
CREATE INDEX idx_songset_items_position ON songset_items(songset_id, position);
CREATE INDEX idx_songset_items_song_id ON songset_items(song_id);
```

### 4.5 Updated At Trigger

A single trigger function updates `updated_at` on all tables:

```sql
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

-- Applied to: songs, recordings, songsets
CREATE TRIGGER trg_<table>_updated_at
    BEFORE UPDATE ON <table>
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
```

### 4.6 Schema Files

| File | Contents |
|------|----------|
| `admin/db/schema.py` | `ALL_SCHEMA_STATEMENTS` — songs, recordings, indexes, triggers |
| `app/db/schema.py` | `ALL_APP_SCHEMA_STATEMENTS` — songsets, songset_items, indexes, trigger |
| `db/postgres_schema.py` | `ALL_SCHEMA_STATEMENTS` — unified re-export of both |

---

## 5. Database Clients

### 5.1 DatabaseClient (Admin)

**Location:** `src/stream_of_worship/admin/db/client.py`

**Role:** Full CRUD on `songs` and `recordings` tables. Used by admin CLI.

**Constructor:**
```python
def __init__(self, connection_provider: ConnectionProvider):
    self.connection_provider = connection_provider
```

**Key patterns:**
- `@property connection` → `connection_provider.get_connection()`
- `transaction()` context manager for write operations
- All queries use `%s` placeholders

**Method categories:**
- **Schema:** `initialize_schema()`, `get_stats()`
- **Songs:** `insert_song()`, `get_song()`, `list_songs()`, `search_songs()`, `list_albums()`, `soft_delete_song()`, `list_deleted_songs()`, `restore_song()`
- **Recordings:** `insert_recording()`, `get_recording_by_hash()`, `get_recording_by_song_id()`, `list_recordings()`, `list_recordings_with_songs()`, `update_recording_status()`, `update_recording_analysis()`, `update_recording_lrc()`, `update_recording_download()`, `update_recording_visibility()`, `delete_recording()`, `list_deleted_recordings()`, `restore_recording()`

### 5.2 ReadOnlyClient (App)

**Location:** `src/stream_of_worship/app/db/read_client.py`

**Role:** Read-only access to `songs` and `recordings`. Used by app TUI. Write restriction enforced by Postgres role (`sow_app` has SELECT only on catalog tables).

**Constructor:**
```python
def __init__(self, connection_provider: ConnectionProvider):
    self.connection_provider = connection_provider
```

**Method categories:**
- **Connection:** `check_connection()`
- **Songs:** `get_song()`, `get_song_including_deleted()`, `list_songs()`, `search_songs()`, `list_albums()`, `list_keys()`
- **Recordings:** `get_recording_by_hash()`, `get_recording_by_song_id()`, `list_recordings()`
- **Stats:** `get_song_count()`, `get_recording_count()`, `get_analyzed_recording_count()`, `get_lrc_ready_count()`

### 5.3 SongsetClient (App)

**Location:** `src/stream_of_worship/app/db/songset_client.py`

**Role:** Full CRUD on `songsets` and `songset_items`. Used by app TUI.

**Constructor:**
```python
def __init__(self, connection_provider: ConnectionProvider):
    self.connection_provider = connection_provider
```

**Key patterns:**
- `transaction()` context manager for multi-row operations
- `validate_recording_exists()` before adding items
- Raises `MissingReferenceError` for invalid recording references

**Method categories:**
- **Schema:** `initialize_schema()`
- **Songsets:** `create_songset()`, `get_songset()`, `list_songsets()`, `update_songset()`, `delete_songset()`
- **Items:** `add_item()`, `get_items()`, `get_items_raw()`, `update_item()`, `remove_item()`, `reorder_item()`, `get_item_count()`

### 5.4 Shared Connection in App

In `app/app.py`, both clients share the same `ConnectionProvider`:

```python
provider = ConnectionProvider(config.get_connection_url())
self.read_client = ReadOnlyClient(provider)
self.songset_client = SongsetClient(provider)
```

This ensures one connection to Neon, with privilege separation at the Postgres role level.

---

## 6. Data Models

### 6.1 Model Classes

| Class | File | Fields | Notes |
|-------|------|--------|-------|
| `Song` | `admin/db/models.py` | 17 fields | `from_row()`, `to_dict()`, `lyrics_list` property |
| `Recording` | `admin/db/models.py` | 29 fields | `from_row()` with schema version handling, `has_analysis`, `has_lrc`, `is_published` properties |
| `DatabaseStats` | `admin/db/models.py` | 4 fields | `table_counts`, `is_healthy`, `last_sync_at`, `sync_version="3"` |
| `Songset` | `app/db/models.py` | 5 fields | `from_row()`, `to_dict()`, `generate_id()` |
| `SongsetItem` | `app/db/models.py` | 11 base + 9 joined fields | `from_row(detailed=True)` for joined data, `formatted_duration`, `display_key` properties |

### 6.2 Timestamp Coercion Pattern

Since `timestamptz` columns return `datetime` objects via psycopg, `from_row()` methods coerce to ISO 8601 strings:

**Helper function (in `db/helpers.py`):**
```python
def to_str(val) -> Optional[str]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)
```

**Usage in `from_row()`:**
```python
@classmethod
def from_row(cls, row: tuple) -> "Song":
    return cls(
        # ... other fields ...
        created_at=to_str(row[14]),
        updated_at=to_str(row[15]),
        deleted_at=to_str(row[16]) if len(row) > 16 else None,
    )
```

This preserves the `Optional[str]` type on dataclass fields while correctly converting `datetime` → ISO 8601 string at the boundary.

---

## 7. Configuration

### 7.1 TOML Structure

**Admin (`~/.config/sow-admin/config.toml`):**
```toml
[database]
url = "postgresql://sow_admin_rw@ep-xxx-pooler.us-east-1.aws.neon.tech/sow?sslmode=require"

[r2]
bucket_name = "..."
account_id = "..."
access_key_id = "..."
# ...

[analysis]
url = "http://localhost:8001"
```

**App (`~/.config/sow/config.toml`):**
```toml
[database]
url = "postgresql://sow_app@ep-xxx-pooler.us-east-1.aws.neon.tech/sow?sslmode=require"

[r2]
# ... same as admin ...

[playback]
default_volume = 0.8

[export]
output_dir = "~/sow-output"

[video]
# ...
```

### 7.2 Environment Variables

| Variable | Purpose | Required |
|----------|---------|----------|
| `SOW_DATABASE_URL` | Override TOML `database.url` | Optional |
| `SOW_DATABASE_PASSWORD` | Password for DSN | Required |

### 7.3 Backward Compatibility

Config loaders silently ignore old `[turso]` sections (logged as warning). This allows existing config files to work without manual editing.

---

## 8. SQL Dialect Conventions

All SQL uses PostgreSQL-specific syntax:

| Pattern | Example |
|---------|---------|
| Placeholders | `%s` (not `?`) |
| Current timestamp | `NOW()` (not `datetime('now')`) |
| Upsert | `INSERT ... ON CONFLICT DO UPDATE` (not `INSERT OR REPLACE`) |
| Foreign keys | Enforced automatically (no `PRAGMA foreign_keys`) |
| Null ordering | `NULLS LAST` (native in Postgres) |

**Example query:**
```python
cursor.execute(
    "SELECT * FROM songs WHERE id = %s AND deleted_at IS NULL",
    (song_id,)
)
```

---

## 9. Testing

### 9.1 Testcontainers

Integration tests use `testcontainers[postgres]` to spin up ephemeral Postgres instances:

```python
from testcontainers.postgres import PostgresContainer

@pytest.fixture(scope="session")
def postgres_url():
    with PostgresContainer("postgres:16-alpine") as postgres:
        yield postgres.get_connection_url()
```

### 9.2 Test Markers

Tests requiring Docker are marked with `@pytest.mark.integration`:

```python
@pytest.mark.integration
def test_database_client_with_real_postgres(postgres_url):
    provider = ConnectionProvider(postgres_url)
    client = DatabaseClient(provider)
    # ...
```

**Running tests:**
```bash
# Unit tests only (no Docker)
pytest tests/ -m "not integration"

# All tests including integration (requires Docker)
pytest tests/
```

### 9.3 Test Files

| File | Scope |
|------|-------|
| `tests/admin/commands/test_db_commands.py` | Admin CLI commands |
| `tests/app/db/test_songset_client.py` | SongsetClient operations |
| `tests/app/services/test_catalog_cross_db.py` | Catalog service (unified DB) |
| `tests/db/test_postgres_clients.py` | Integration tests for psycopg clients |
| `tests/db/test_role_permissions.py` | Verify app role restrictions |
| `tests/db/test_connection_provider.py` | ConnectionProvider retry logic |
| `tests/db/test_model_coercion.py` | `to_str()` datetime coercion |

---

## 10. Neon Operational Notes

### 10.1 Pooled DSN

Always use the pooled connection hostname (`-pooler` suffix):

| Type | Hostname |
|------|----------|
| Direct | `ep-xxx.us-east-1.aws.neon.tech` |
| **Pooled (required)** | `ep-xxx-pooler.us-east-1.aws.neon.tech` |

Pooled connections use Neon's built-in PgBouncer (transaction mode), which:
- Handles up to 10,000 concurrent connections
- Returns connections to pool after each transaction
- Mitigates cold-start latency

### 10.2 Cold-Start Behavior

- Neon suspends compute after 5 minutes of inactivity (default)
- Cold start latency: ~300ms to ~1s
- `ConnectionProvider._connect_with_retry()` handles this automatically

### 10.3 Connection Limits

| Tier | Direct | Pooled |
|------|--------|--------|
| Free | ~5 | ~100 |
| Pro | ~100 | ~10,000 |

The pooled DSN mitigates connection limit concerns for a single-operator TUI application.

### 10.4 Database Reset

**No destructive operations in application code.** To reset the database:

1. **Neon branching:** Create a fresh branch from empty state, promote it
2. **Manual:** `pg_dump` / `psql` to recreate schema
3. **New database:** Create new Neon database, point `database_url` to it, run `sow-admin db init`

### 10.5 Point-in-Time Restore

Neon provides PITR on paid plans. To restore:
1. Use Neon console to create a branch from a specific timestamp
2. Point `database_url` to the new branch

### 10.6 Monitoring

- `sow-admin db status` — connection health, row counts
- `sow-app db check` — app-role connectivity verification
- Neon console — query metrics, connection counts, compute status

---

## 11. Key Files Reference

| Category | File | Purpose |
|----------|------|---------|
| **Connection** | `db/connection.py` | `ConnectionProvider`, `check_database_connection()` |
| **Helpers** | `db/helpers.py` | `to_str()` for datetime coercion |
| **Schema** | `admin/db/schema.py` | Catalog DDL |
| | `app/db/schema.py` | Songset DDL |
| | `db/postgres_schema.py` | Unified DDL re-export |
| **Clients** | `admin/db/client.py` | `DatabaseClient` |
| | `app/db/read_client.py` | `ReadOnlyClient` |
| | `app/db/songset_client.py` | `SongsetClient` |
| **Models** | `admin/db/models.py` | `Song`, `Recording`, `DatabaseStats` |
| | `app/db/models.py` | `Songset`, `SongsetItem` |
| **Config** | `admin/config.py` | `AdminConfig` |
| | `app/config.py` | `AppConfig` |
| **CLI** | `admin/commands/db.py` | `sow-admin db init/status/url` |
| | `app/main.py` | `sow-app db check` |
