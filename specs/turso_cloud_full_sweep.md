# Full Sweep: 100% Turso Cloud Architecture

**Date:** 2026-05-05
**Predecessor:** `specs/fix_scrape_save_songs_error.md`, `specs/remote_write_local_read_impl_plan_v2.md`
**Scope:** Remove all local sqlite3 write paths from Admin CLI; enforce Turso Cloud as sole write backend; fix all discovered inconsistencies across Admin CLI and User App.

---

## Design Decisions (Confirmed)

| Decision | Choice |
|---|---|
| Local sqlite3 fallback in admin writes | **Remove entirely** — all writes go to Turso Cloud via HTTP |
| `sync_metadata` storage | **New separate `metadata.db`** (sqlite3, local-only, no Turso) |
| `local_device_id` | **Remove** (no longer meaningful in 100% Turso model) |
| `reset_database()` | **Destroy local replica + re-pull from Turso master** (no remote DDL) |
| App `ReadOnlyClient` sqlite3 fallback | **Keep** (reads don't cause the transaction bugs, test convenience) |
| App `_migrate_schema()` on Turso path | **Remove** (schema comes from remote via `sync()`) |
| `bulk_insert_recordings()` | **Add** (symmetric with `bulk_insert_songs()`) |
| TOCTOU races in bool-returning methods | **Fix now** (single pipeline SELECT+UPDATE) |
| `is_turso_enabled` / constructor | **Fail fast** if Turso credentials missing |
| Scraper error on bulk failure | **Just report, no retry** |
| `audio.py` raw SQL / missing Turso | **Fix now** (proper methods + Turso-enabled constructors) |
| `infra.py` `auth_token` bug | **Fix now** (one-line fix) |
| Test strategy | **Mock Turso HTTP calls** for write tests |

---

## Root Cause Summary

The fundamental problem is **dual-path ambiguity**: the codebase has a local sqlite3 path and a Turso remote path in every write method, selected by `if self.is_turso_enabled`. This creates:

1. **`transaction()` + remote write conflict** — `scraper.save_songs()` wraps remote-write `insert_song()` calls in a local `transaction()` context manager, causing "stream not found" Hrana errors
2. **`sync_metadata` local-write leak** — `update_sync_metadata()` always writes locally via `transaction()`, even when Turso is enabled, violating the "no DML writes go to the replica" principle
3. **`reset_database()` is a no-op with Turso** — drops local tables but data reappears on next `sync()`
4. **TOCTOU races** — bool-returning methods do SELECT then UPDATE as two separate HTTP calls
5. **Missing `bulk_insert_recordings()`** — recordings can only be inserted one at a time over HTTP
6. **App `_migrate_schema()` on replica** — ALTER TABLE on embedded replica conflicts with Hrana sync
7. **Inconsistent Turso enablement** — `audio.py` commands create `DatabaseClient` without Turso params
8. **Raw cursor SQL bypass** — `audio.py` executes raw SQL via `db_client.connection.cursor()`, bypassing Turso remote-write path
9. **`infra.py` keyword bug** — `auth_token=turso_token` should be `turso_token=turso_token`

---

## Phase 1: Core `client.py` Refactoring

**File:** `src/stream_of_worship/admin/db/client.py`

### 1.1 Constructor: Fail-fast on missing Turso credentials

**Current:**
```python
def __init__(self, db_path: Path, turso_url: Optional[str] = None, turso_token: Optional[str] = None):
    self.db_path = db_path
    self.turso_url = turso_url
    self.turso_token = turso_token or os.environ.get("SOW_TURSO_TOKEN")
```

**Target:**
```python
def __init__(self, db_path: Path, turso_url: str, turso_token: str, metadata_client: MetadataClient):
    if not turso_url:
        raise ValueError("turso_url is required for Turso Cloud connection")
    if not turso_token:
        raise ValueError("turso_token is required (set SOW_TURSO_TOKEN env var or pass explicitly)")
    self.db_path = db_path
    self.turso_url = turso_url
    self.turso_token = turso_token
    self._metadata_client = metadata_client
```

- Require `turso_url` and `turso_token` — no Optional
- Accept `MetadataClient` instance for sync metadata storage
- Remove `os.environ.get("SOW_TURSO_TOKEN")` fallback from constructor; caller is responsible (factory helper handles it)
- `is_turso_enabled` property simplifies to always return `True`, or is removed entirely
- Remove `LIBSQL_AVAILABLE` conditional; fail at import time with clear error if `libsql` not available

### 1.2 Remove `transaction()` context manager

Delete the `transaction()` method entirely (lines 410-423):

```python
# DELETE this method:
@contextmanager
def transaction(self) -> Generator[sqlite3.Connection, None, None]:
    conn = self.connection
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
```

Rationale: The only valid use of `transaction()` was for local sqlite3 writes. Since all writes now go to Turso via HTTP, this method is dead code. Its existence invites future bugs where someone wraps remote-write calls in a local transaction.

### 1.3 Remove local sqlite3 connection path

**Current `connection` property has dual path** (lines 148-184):
- Turso path: `libsql.connect()` with embedded replica
- Local path: `sqlite3.connect()` with `row_factory = sqlite3.Row`

**Target:** Only the libsql embedded replica path remains:

```python
@property
def connection(self):
    if self._connection is None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._connection = libsql.connect(
                str(self.db_path),
                sync_url=self.turso_url,
                auth_token=self.turso_token or "",
            )
            self._connection.sync()  # Pull schema + data on first connect
        except (*_LIBSQL_ERROR, ValueError) as e:
            # ... error handling (same as current) ...
    return self._connection
```

- Remove `sqlite3.connect()` branch entirely
- Add `sync()` on initial connection to ensure schema is fresh
- Remove `row_factory = sqlite3.Row` (not needed with libsql; all `from_row()` uses `tuple(row)`)

### 1.4 Extract `sync_metadata` to separate `MetadataClient`

**New file:** `src/stream_of_worship/admin/db/metadata_client.py`

```python
class MetadataClient:
    """Local-only metadata storage for sync tracking.
    
    Uses a separate SQLite file (metadata.db) to avoid writing
    to the Turso embedded replica. This client is NOT a Turso client —
    it's a plain sqlite3 database for device-local metadata only.
    """
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._connection: Optional[sqlite3.Connection] = None
    
    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(str(self.db_path))
            self._connection.execute("""
                CREATE TABLE IF NOT EXISTS sync_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT DEFAULT (datetime('now'))
                )
            """)
            self._connection.commit()
        return self._connection
    
    def get_last_sync_at(self) -> Optional[str]:
        cursor = self.connection.cursor()
        cursor.execute("SELECT value FROM sync_metadata WHERE key = 'last_sync_at'")
        row = cursor.fetchone()
        return row[0] if row else None
    
    def set_last_sync_at(self, value: str) -> None:
        self.connection.execute(
            "INSERT OR REPLACE INTO sync_metadata (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            ("last_sync_at", value),
        )
        self.connection.commit()
    
    def close(self) -> None:
        if self._connection:
            self._connection.close()
            self._connection = None
```

Key properties:
- Pure `sqlite3`, no libsql/Turso dependency
- Separate file from catalog replica (`metadata.db` vs `sow.db`)
- Only stores `last_sync_at` (no `local_device_id`, no `sync_version`)
- No `transaction()` context manager — simple `commit()` after each write

### 1.5 Rewrite `_sync_replica()`

**Current** (lines 315-337): Calls `self.update_sync_metadata("last_sync_at", ...)` which writes to the catalog replica via `transaction()`.

**Target:**
```python
def _sync_replica(self, fatal: bool = False) -> None:
    """Pull remote changes to local embedded replica."""
    if self._connection is None:
        return
    try:
        self._connection.sync()
        self._metadata_client.set_last_sync_at(datetime.now().isoformat())
    except Exception as e:
        if fatal:
            raise SyncError(
                f"Replica sync failed: {e}. Run 'db pull' to recover.",
                cause=e,
            )
        logger.warning(f"Replica sync after write failed (non-fatal): {e}")
```

- Replaces `self.update_sync_metadata()` with `self._metadata_client.set_last_sync_at()`
- No writes to the catalog replica

### 1.6 Remove `update_sync_metadata()` method

Delete entirely (lines 385-400). No longer needed — `MetadataClient` handles sync metadata.

### 1.7 Simplify `initialize_schema()` — remote only

**Current:** Has `if self.is_turso_enabled:` and `else:` branches (lines 425-487).

**Target:** Remove the `else` branch entirely. Only the remote DDL path remains:

```python
def initialize_schema(self) -> None:
    """Initialize the database schema on Turso Cloud."""
    for stmt in [CREATE_SONGS_TABLE, CREATE_RECORDINGS_TABLE]:
        try:
            self._execute_remote(stmt)
        except SyncError as e:
            if "already exists" not in str(e).lower():
                raise
    apply_column_migrations_remote(self)
    try:
        self._execute_remote(
            "UPDATE recordings SET visibility_status = 'published' "
            "WHERE lrc_status = 'completed' AND visibility_status IS NULL"
        )
    except SyncError:
        pass
    for stmt in CREATE_INDEXES:
        try:
            self._execute_remote(stmt)
        except SyncError as e:
            if "already exists" not in str(e).lower():
                raise
    for stmt in [CREATE_SONGS_UPDATE_TRIGGER, CREATE_RECORDINGS_UPDATE_TRIGGER]:
        try:
            self._execute_remote(stmt)
        except SyncError as e:
            if "already exists" not in str(e).lower():
                raise
    self._sync_replica(fatal=False)
```

Changes:
- Remove `CREATE_SYNC_METADATA_TABLE` (metadata is in separate DB)
- Remove `DEFAULT_SYNC_METADATA` insertion (not needed — sync_metadata is in MetadataClient)
- Remove local `sqlite3` path
- Remove `else` branch

### 1.8 Rewrite `reset_database()` — destroy local replica + re-pull

**Current** (lines 489-507): Drops local tables via `transaction()`, then calls `initialize_schema()`.

**Target:**
```python
def reset_database(self) -> None:
    """Reset the local embedded replica by destroying and re-pulling from Turso.

    The remote Turso database is NOT modified. This deletes the local
    replica file(s) and re-syncs from Turso Cloud.
    """
    # Close existing connection
    self.close()
    
    # Delete local replica files
    for suffix in ["", "-wal", "-shm", "-info", "-client_wal_index"]:
        p = Path(str(self.db_path) + suffix)
        if p.exists():
            p.unlink()
    
    # Re-create connection (triggers libsql.connect + sync on first access)
    self._connection = None
    _ = self.connection  # Force re-connect + sync
    
    # Ensure remote schema is up to date
    self.initialize_schema()
```

Key behaviors:
- Local replica files are deleted
- Remote Turso database is untouched
- After reset, `connection` property re-creates libsql connection and syncs
- `initialize_schema()` ensures remote schema is current
- This effectively gives you a fresh local replica from the Turso master

### 1.9 Simplify all write methods — remove `else` branches

Each of the following methods loses its `else` (local sqlite3) branch. The Turso remote-write path becomes the only path. For each method, the pattern is:

**Before (dual-path):**
```python
def method(self, ...):
    sql = "UPDATE ..."
    params = (...)
    if self.is_turso_enabled:
        self._execute_remote(sql, params)
        self._sync_replica(fatal=False)
    else:
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(sql, params)
```

**After (single-path):**
```python
def method(self, ...):
    sql = "UPDATE ..."
    params = (...)
    self._execute_remote(sql, params)
    self._sync_replica(fatal=False)
```

Methods to simplify:
- `insert_song()` (lines 558-598)
- `bulk_insert_songs()` (lines 600-667) — remove local `transaction()` loop
- `insert_recording()` (lines 824-877)
- `update_recording_status()` (lines 1069-1121)
- `update_recording_analysis()` (lines 1152-1220)
- `update_recording_lrc()` (lines 1222-1252)
- `update_recording_download()` (lines 1254-1288)
- `delete_recording()` (lines 1337-1351) — also change to return `bool` (see 1.13)

### 1.10 Fix TOCTOU races in bool-returning methods

Four methods currently do SELECT then UPDATE as two separate HTTP calls:

- `update_recording_visibility()` (lines 1290-1335)
- `soft_delete_song()` (lines 1353-1378)
- `restore_song()` (lines 1410-1435)
- `restore_recording()` (lines 1437-1462)

**Current pattern:**
```python
if self.is_turso_enabled:
    result = self._execute_remote(f"SELECT 1 FROM ... WHERE id = ?", (id,))
    exists = len(result.get("rows", [])) > 0
    if not exists:
        return False
    self._execute_remote(update_sql, update_params)
    self._sync_replica(fatal=False)
    return True
```

**Target pattern:** Single pipeline sends SELECT + UPDATE atomically:

```python
def soft_delete_song(self, song_id: str) -> bool:
    select_sql = "SELECT 1 FROM songs WHERE id = ? AND deleted_at IS NULL LIMIT 1"
    update_sql = "UPDATE songs SET deleted_at = datetime('now') WHERE id = ?"
    params = (song_id,)
    
    # Single pipeline: SELECT checks existence, UPDATE runs regardless.
    # If the row doesn't exist, UPDATE affects 0 rows — same as current behavior.
    pipeline = [
        {"type": "execute", "stmt": {"sql": select_sql, "args": [_format_param(p) for p in params]}},
        {"type": "execute", "stmt": {"sql": update_sql, "args": [_format_param(p) for p in params]}},
        {"type": "close"},
    ]
    results = self._execute_remote_pipeline(pipeline)
    self._check_pipeline_results(results)
    
    # Extract SELECT result to determine existence
    for r in results:
        if r.get("type") == "ok":
            resp = r.get("response", {})
            if resp.get("type") == "execute":
                rows = resp.get("result", {}).get("rows", [])
                exists = len(rows) > 0
                if not exists:
                    return False
    
    self._sync_replica(fatal=False)
    return True
```

Race window eliminated: SELECT and UPDATE are in the same pipeline request, executed atomically by Turso.

### 1.11 Add `bulk_insert_recordings()`

Mirror `bulk_insert_songs()` pattern for recordings:

```python
def bulk_insert_recordings(self, recordings: list[Recording]) -> None:
    """Insert multiple recordings in a single remote transaction."""
    statements = []
    for rec in recordings:
        sql = """
            INSERT OR REPLACE INTO recordings (
                hash_prefix, song_id, source_url, duration_seconds,
                download_status, analysis_status, lrc_status, 
                visibility_status, imported_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
        """
        params = (
            rec.hash_prefix, rec.song_id, rec.source_url, rec.duration_seconds,
            rec.download_status or "pending", rec.analysis_status or "pending",
            rec.lrc_status or "pending", rec.visibility_status or "review",
        )
        statements.append((sql, params))
    
    if not statements:
        return
    
    self._execute_remote_transaction(statements)
    self._sync_replica(fatal=False)
```

Note: The exact column list should match the current `insert_recording()` method (28 columns). The abbreviated version above is for illustration.

### 1.12 Simplify `get_stats()`

**Current** (lines 509-554): Reads `sync_metadata` from catalog DB, writes `local_device_id` via `update_sync_metadata()`.

**Target:**
```python
def get_stats(self) -> DatabaseStats:
    cursor = self.connection.cursor()
    cursor.execute(ROW_COUNT_QUERY)
    table_counts = {row[0]: row[1] for row in cursor.fetchall()}
    cursor.execute(ACTIVE_ROW_COUNT_QUERY)
    active_counts = {row[0]: row[1] for row in cursor.fetchall()}
    cursor.execute(INTEGRITY_CHECK_QUERY)
    integrity_result = cursor.fetchone()
    integrity_ok = integrity_result[0] == "ok" if integrity_result else False
    cursor.execute(FOREIGN_KEYS_QUERY)
    fk_result = cursor.fetchone()
    foreign_keys_enabled = bool(fk_result[0]) if fk_result else False
    
    last_sync_at = self._metadata_client.get_last_sync_at()
    
    return DatabaseStats(
        table_counts=table_counts,
        active_counts=active_counts,
        integrity_ok=integrity_ok,
        foreign_keys_enabled=foreign_keys_enabled,
        last_sync_at=last_sync_at,
        turso_configured=True,  # Always true now
    )
```

Changes:
- Remove `local_device_id` from `DatabaseStats` dataclass (and all references)
- Remove `sync_version` from `DatabaseStats`
- Remove `self.update_sync_metadata("local_device_id", ...)` write side-effect
- Get `last_sync_at` from `MetadataClient` instead of querying catalog DB

### 1.13 Make `delete_recording()` return `bool`

Consistent with `soft_delete_song()`. Use TOCTOU-safe pipeline (section 1.10 pattern).

**Current:** Returns `None`.
**Target:** Returns `bool` — `True` if recording existed and was deleted, `False` if not found.

---

## Phase 2: Fix `scraper.py`

**File:** `src/stream_of_worship/admin/services/scraper.py`

### 2.1 Rewrite `save_songs()`

**Current (buggy):**
```python
def save_songs(self, songs):
    with self.db_client.transaction():       # ← local transaction wrapping remote writes
        for song in songs:
            self.db_client.insert_song(song)  # ← writes remotely via HTTP
```

**Target:**
```python
def save_songs(self, songs: list[Song]) -> int:
    if not self.db_client:
        logger.warning("No database client configured, songs not saved")
        return 0

    if not songs:
        logger.info("No songs to save")
        return 0

    logger.info(f"Saving {len(songs)} songs to database")

    try:
        self.db_client.bulk_insert_songs(songs)
        saved_count = len(songs)
    except Exception as e:
        logger.error(f"Bulk insert failed: {e}")
        saved_count = 0

    logger.info(f"Successfully saved {saved_count}/{len(songs)} songs")
    return saved_count
```

- No `transaction()` wrapper
- Single `bulk_insert_songs()` call (one HTTP pipeline request)
- On failure, log error and return 0 (no per-song retry)

---

## Phase 3: Fix `audio.py`

**File:** `src/stream_of_worship/admin/commands/audio.py`

### 3.1 Unify `DatabaseClient` constructors

**Problem:** Most commands create `DatabaseClient(config.db_path)` without Turso params. Only the `status` command creates a Turso-enabled client.

**Fix:** Add a module-level `get_db_client(config)` helper:

```python
def get_db_client(config: AdminConfig) -> DatabaseClient:
    return DatabaseClient(
        db_path=config.db_path,
        turso_url=config.effective_turso_url,
        turso_token=os.environ.get("SOW_TURSO_TOKEN", ""),
        metadata_client=MetadataClient(config.metadata_db_path),
    )
```

Replace all `DatabaseClient(config.db_path)` calls with `get_db_client(config)`.

**Sites to update** (all `DatabaseClient(config.db_path)` calls in audio.py):
- Line 685, 897, 1052, 1161, 1267, 1321, 1517, 1609, 2525, 2610, 2745, 2906, 3123
- Line 1838 already passes Turso params — convert to `get_db_client(config)`

### 3.2 Replace raw cursor queries with proper DB client methods

Three sites access `db_client.connection.cursor()` directly:

#### Site 1: LRC pending query (line 2088-2092)

```python
# CURRENT:
cursor = db_client.connection.cursor()
cursor.execute("SELECT hash_prefix FROM recordings WHERE lrc_status IN ('pending', 'processing')")
lrc_pending_hashes = [row[0] for row in cursor.fetchall()]

# TARGET: Add new method to DatabaseClient
hashes = db_client.list_hash_prefixes_by_lrc_status(["pending", "processing"])
```

New method on `DatabaseClient`:
```python
def list_hash_prefixes_by_lrc_status(self, statuses: list[str]) -> list[str]:
    placeholders = ", ".join(["?" for _ in statuses])
    sql = f"SELECT hash_prefix FROM recordings WHERE lrc_status IN ({placeholders})"
    cursor = self.connection.cursor()
    cursor.execute(sql, tuple(statuses))
    return [row[0] for row in cursor.fetchall()]
```

This is a local read from the replica (eventual consistency is fine for status polling).

#### Site 2: Pending recordings with song titles JOIN (line 2187-2194)

```python
# CURRENT:
cursor = db_client.connection.cursor()
cursor.execute("""
    SELECT r.*, s.title as song_title
    FROM recordings r
    LEFT JOIN songs s ON r.song_id = s.id
    WHERE r.analysis_status != 'completed' OR r.lrc_status != 'completed'
    ORDER BY r.imported_at DESC
""")
```

New method on `DatabaseClient`:
```python
def list_pending_recordings_with_songs(self) -> list[tuple]:
    """List non-completed recordings with song titles via JOIN."""
    cursor = self.connection.cursor()
    cursor.execute("""
        SELECT r.*, s.title as song_title
        FROM recordings r
        LEFT JOIN songs s ON r.song_id = s.id
        WHERE r.analysis_status != 'completed' OR r.lrc_status != 'completed'
        ORDER BY r.imported_at DESC
    """)
    return cursor.fetchall(), cursor.description
```

This is a local read from the replica. The JOIN doesn't need to be remotely consistent.

#### Site 3: Force sync all pending (line 2303-2308)

```python
# CURRENT:
cursor = db_client.connection.cursor()
cursor.execute("""
    SELECT * FROM recordings
    WHERE analysis_status IN ('pending', 'processing', 'failed')
       OR lrc_status IN ('pending', 'processing', 'failed')
""")
```

New method on `DatabaseClient`:
```python
def list_non_completed_recordings(self) -> list[Recording]:
    cursor = self.connection.cursor()
    cursor.execute("""
        SELECT * FROM recordings
        WHERE analysis_status IN ('pending', 'processing', 'failed')
           OR lrc_status IN ('pending', 'processing', 'failed')
    """)
    description = cursor.description
    return [Recording.from_row(tuple(row), description) for row in cursor.fetchall()]
```

---

## Phase 4: Fix `infra.py`

**File:** `src/stream_of_worship/admin/commands/infra.py`

### 4.1 Fix `auth_token` keyword bug

**Line 102:**
```python
# BUG:
auth_token=turso_token,
# FIX:
turso_token=turso_token,
```

### 4.2 Update seed flow for new constructor

The seed flow (lines 131-172) reads from local sqlite3 and writes to Turso via `client._execute_remote_transaction()`. The local read side using `sqlite3.connect(config.db_path)` is acceptable here — it's reading from a source DB to seed the remote. But we need to create the `DatabaseClient` with the new required params:

```python
metadata_client = MetadataClient(config.metadata_db_path)
client = DatabaseClient(
    db_path=config.db_path,
    turso_url=effective_url,
    turso_token=turso_token,
    metadata_client=metadata_client,
)
```

---

## Phase 5: Fix `db.py` commands

**File:** `src/stream_of_worship/admin/commands/db.py`

### 5.1 Update `get_db_client()` factory

```python
def get_db_client(config: AdminConfig) -> DatabaseClient:
    return DatabaseClient(
        db_path=config.db_path,
        turso_url=config.effective_turso_url,
        turso_token=os.environ.get("SOW_TURSO_TOKEN", ""),
        metadata_client=MetadataClient(config.metadata_db_path),
    )
```

### 5.2 Update `reset_database()` callers

The `db reset` command now destroys the local replica and re-pulls. Update any confirmation messages or command documentation.

### 5.3 Update `get_stats()` return value

Remove `local_device_id` and `sync_version` from stats display. Add `last_sync_at` display from `MetadataClient`.

### 5.4 `_get_turso_counts()` — keep as-is

This function creates an in-memory libsql connection for verification. It's independent of the main `DatabaseClient` and doesn't need changes.

---

## Phase 6: Fix App `ReadOnlyClient`

**File:** `src/stream_of_worship/app/db/read_client.py`

### 6.1 Remove `_migrate_schema()` from Turso path

**Current `connection` property** (lines 82-103): Calls `self._migrate_schema()` on line 101 in both paths.

**Target:**
```python
@property
def connection(self):
    if self._connection is None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self.is_turso_enabled:
            self._connection = libsql.connect(
                str(self.db_path),
                sync_url=self.turso_url,
                auth_token=self.turso_token or "",
            )
            self._connection.sync()  # Schema comes from remote
            # NO _migrate_schema() — schema is managed by admin via Turso
        else:
            self._connection = sqlite3.connect(
                self.db_path, detect_types=sqlite3.PARSE_DECLTYPES
            )
            self._connection.row_factory = sqlite3.Row
            self._execute_sqlite_pragmas()
            self._migrate_schema()  # Only for sqlite3 fallback
    return self._connection
```

- When Turso is enabled, `sync()` pulls the schema. No local DDL.
- When using sqlite3 fallback, migrations run as before.

### 6.2 Fix post-recovery ordering in `sync()`

**Current recovery path** (lines 132-136):
1. `_recover_replica()` → deletes local DB, closes connection
2. `self.connection.sync()` → re-creates connection (triggers `_migrate_schema()`) → sync

**Problem:** `_migrate_schema()` runs on an empty DB before sync.

**Target:** After recovery, the `connection` property will call `sync()` before any reads (as per 6.1 above). The `sync()` method's recovery path becomes:

```python
def sync(self) -> None:
    if not self.is_turso_enabled:
        raise SyncError("Turso sync is not configured")
    try:
        self.connection.sync()
    except Exception as e:
        error_msg = str(e).lower()
        if any(kw in error_msg for kw in ("walconflict", "wal", "metadata", "malformed", "corrupt")):
            self._recover_replica()
            self.connection.sync()  # Re-creates connection via property → syncs from remote
        else:
            raise SyncError(f"Sync failed: {e}", cause=e)
```

After `_recover_replica()` closes the connection, `self.connection` re-creates it. In the Turso path (6.1), the `connection` property calls `sync()` during connection creation. So the second `self.connection.sync()` is redundant but harmless (idempotent). More importantly, `_migrate_schema()` is no longer called in the Turso path.

### 6.3 Remove `sync_metadata` reads from catalog replica

The app's `ReadOnlyClient` currently queries `sync_metadata` in the catalog DB for `last_sync_at` (used in `SyncStatus`). Since admin's `sync_metadata` is moving to a separate `metadata.db`, the catalog replica's `sync_metadata` table will no longer be populated.

**Options:**
- The app's `SyncStatus` should use `SongsetClient`'s `_sync_metadata` table (already stores `last_sync_at` for local sync tracking)
- Or accept that `last_sync_at` in the app context means "last time the catalog replica was synced" and store it in `songsets.db`'s `_sync_metadata` table

**Recommendation:** Use `SongsetClient`'s existing `_sync_metadata` table for the app's sync status tracking. The ` readOnlyclient` no longer exposes `sync_metadata` queries.

### 6.4 Keep sqlite3 fallback for reads

As agreed: `ReadOnlyClient` keeps its sqlite3 fallback path. Reads don't cause the transaction/write conflict bugs, and this allows tests and offline development to work without Turso credentials.

---

## Phase 7: Schema Changes

**File:** `src/stream_of_worship/admin/db/schema.py`

### 7.1 Remove `CREATE_SYNC_METADATA_TABLE`

Remove `CREATE_SYNC_METADATA_TABLE` from schema constants. The `sync_metadata` table is no longer part of the catalog schema.

### 7.2 Remove `DEFAULT_SYNC_METADATA`

Remove the `DEFAULT_SYNC_METADATA` dictionary. Default metadata insertion is now handled by `MetadataClient`.

### 7.3 Update `initialize_schema()` references

In `client.py`'s `initialize_schema()`, remove `CREATE_SYNC_METADATA_TABLE` from the DDL list sent to Turso.

### 7.4 Keep `apply_column_migrations()` and `apply_column_migrations_remote()`

These are still needed for evolving schema (adding columns to existing tables). But they should no longer include `sync_metadata` table columns (there are none currently, so no change needed).

---

## Phase 8: Update Tests

### 8.1 Admin `test_client.py`

**Major rewrite needed.**

- **Remove** tests for `transaction()` context manager (method deleted)
- **Remove** tests for local sqlite3 write paths (all `else` branches deleted)
- **Add** mock-based tests for all write methods:
  ```python
  @pytest.fixture
  def db_client(tmp_path):
      metadata_client = MetadataClient(tmp_path / "metadata.db")
      client = DatabaseClient(
          db_path=tmp_path / "test.db",
          turso_url="https://test.turso.io",
          turso_token="test-token",
          metadata_client=metadata_client,
      )
      # Patch HTTP calls
      with patch.object(client, "_execute_remote") as mock_exec, \
           patch.object(client, "_execute_remote_transaction") as mock_txn, \
           patch.object(client, "_sync_replica"):
          client._mock_exec = mock_exec
          client._mock_txn = mock_txn
          yield client
  ```
- **Add** test for constructor fail-fast (missing turso_url or turso_token raises `ValueError`)
- **Add** test for `bulk_insert_recordings()` (new method)
- **Update** `get_stats()` tests (no `local_device_id`, `last_sync_at` from MetadataClient)
- **Add** test for `reset_database()` new behavior (verify local DB files deleted, connection re-created)
- **Add** tests for TOCTOU-safe bool methods (verify single pipeline call)

### 8.2 Scraper tests

- Update `save_songs()` test to verify `bulk_insert_songs()` is called (not `insert_song()` in a loop)
- Verify `transaction()` is never called (method no longer exists)
- Verify bulk failure returns 0

### 8.3 App `test_read_client.py` / `test_read_client_libsql.py`

- These test the sqlite3 path of `ReadOnlyClient` — since we're keeping the sqlite3 fallback, most tests survive
- **Add** test that `_migrate_schema()` is skipped when Turso is enabled (mock libsql)
- **Update** tests that reference `sync_metadata` table in catalog DB
- **Add** test for post-recovery sync ordering (no _migrate_schema before sync in Turso path)

### 8.4 App `test_navigation.py` and other integration tests

- Tests creating `SowApp(config)` with real sqlite3 need adjustment
- Since `ReadOnlyClient` keeps sqlite3 fallback, these should still work
- But `SowApp` constructor may need to handle the case where Turso is not configured (use sqlite3 fallback for reads)

### 8.5 App `test_catalog_cross_db.py`

- Uses `ReadOnlyClient(catalog_db)` without Turso
- Since we keep sqlite3 fallback for reads, these tests should survive unchanged
- Update if `sync_metadata` queries are removed from `ReadOnlyClient`

---

## Phase 9: Config & Wiring

### 9.1 `AdminConfig` updates

**File:** `src/stream_of_worship/admin/config.py`

- Add `metadata_db_path` property:
  ```python
  @property
  def metadata_db_path(self) -> Path:
      return self.data_dir / "metadata.db"
  ```
- `effective_turso_url` and `SOW_TURSO_TOKEN` env var remain as-is (they're already the source of truth)

### 9.2 Centralize `get_db_client()` factory

Create a shared helper (use the existing one in `commands/db.py` or `commands/catalog.py`) that all commands use:

```python
def get_db_client(config: AdminConfig) -> DatabaseClient:
    """Create a Turso-enabled DatabaseClient from config."""
    turso_token = os.environ.get("SOW_TURSO_TOKEN", "")
    return DatabaseClient(
        db_path=config.db_path,
        turso_url=config.effective_turso_url,
        turso_token=turso_token,
        metadata_client=MetadataClient(config.metadata_db_path),
    )
```

**Move to** `src/stream_of_worship/admin/db/__init__.py` or a new `admin/db/factory.py` so all command modules import the same factory.

**Caller updates:**
- `commands/catalog.py`: Replace local `get_db_client()` with shared factory
- `commands/db.py`: Replace local `get_db_client()` with shared factory
- `commands/audio.py`: Replace all `DatabaseClient(...)` constructors with shared factory
- `commands/infra.py`: Replace `DatabaseClient(...)` constructor with shared factory
- `services/scraper.py`: Receives `DatabaseClient` via injection (no change needed)
- `services/sync.py`: Replace `DatabaseClient(...)` constructor with shared factory

### 9.3 `DatabaseStats` dataclass updates

**File:** `src/stream_of_worship/admin/db/models.py`

Remove fields:
- `local_device_id`
- `sync_version`

Update field:
- `turso_configured` is always `True` (consider removing or changing to a constant)

---

## Summary of Files Changed

| File | Change Type | Phase |
|---|---|---|
| `admin/db/client.py` | **Major rewrite** — remove sqlite3, remove transaction(), fix TOCTOU, add bulk_recordings, inject MetadataClient | 1 |
| `admin/db/metadata_client.py` | **NEW** — local-only MetadataClient with sync_metadata in separate DB | 1 |
| `admin/db/schema.py` | Remove sync_metadata table/defaults from catalog schema | 7 |
| `admin/db/models.py` | Remove local_device_id, sync_version from DatabaseStats | 9 |
| `admin/db/__init__.py` | Export MetadataClient, add shared get_db_client() factory | 9 |
| `admin/services/scraper.py` | Rewrite save_songs() to use bulk_insert_songs() | 2 |
| `admin/commands/audio.py` | Unify constructors via factory, replace raw SQL with proper methods | 3 |
| `admin/commands/infra.py` | Fix auth_token bug, update for new constructor | 4 |
| `admin/commands/catalog.py` | Use shared get_db_client() factory | 9 |
| `admin/commands/db.py` | Update reset_database, stats, use shared factory | 5 |
| `admin/services/sync.py` | Use shared factory, update MetadataClient usage | 9 |
| `admin/config.py` | Add metadata_db_path property | 9 |
| `app/db/read_client.py` | Skip _migrate_schema() on Turso, remove sync_metadata reads | 6 |
| `app/services/sync.py` | Use SongsetClient._sync_metadata for SyncStatus | 6 |
| `tests/admin/test_client.py` | Major rewrite (mock-based) | 8 |
| `tests/app/db/test_read_client.py` | Minor updates for sync_metadata removal | 8 |
| `tests/app/db/test_read_client_libsql.py` | Add test for schema migration skip | 8 |
| `tests/app/test_navigation.py` | Adapt for ReadOnlyClient changes | 8 |

---

## Migration Path / Backward Compatibility

### Breaking changes

1. **`DatabaseClient.__init__` signature change** — `turso_url` and `turso_token` are now required. Any code creating `DatabaseClient(config.db_path)` without Turso params will fail with `ValueError`.
2. **`transaction()` removed** — Any code using `with db_client.transaction():` will fail with `AttributeError`.
3. **`update_sync_metadata()` removed** — Any code calling this method will fail with `AttributeError`.
4. **`DatabaseStats` fields removed** — Code accessing `stats.local_device_id` or `stats.sync_version` will fail with `AttributeError`.
5. **`delete_recording()` now returns `bool`** — Previously returned `None`. Code that doesn't check the return value is unaffected.
6. **`sync_metadata` table gone from catalog DB** — Code querying it directly will get empty results.

### Migration for existing deployments

Users with existing local `sow.db` files:
1. The `sow.db` file becomes the local embedded replica path (same as current Turso path)
2. A new `metadata.db` file is created automatically by `MetadataClient`
3. The `sync_metadata` table in `sow.db` is no longer used; existing data is ignored
4. `db reset` now destroys the local replica and re-pulls from Turso (safe operation)

---

## Implementation Order

Execute phases in this order to minimize broken intermediate states:

1. **Phase 1.4** — Create `MetadataClient` (additive, no breakage)
2. **Phase 9.1** — Add `metadata_db_path` to `AdminConfig` (additive)
3. **Phase 1.1** — Change `DatabaseClient.__init__` signature (breaks all callers)
4. **Phase 9.2** — Create shared `get_db_client()` factory (fixes callers)
5. **Phase 3.1** — Unify `audio.py` constructors (uses factory)
6. **Phase 4** — Fix `infra.py` auth_token bug + constructor
7. **Phase 5** — Update `db.py` commands
8. **Phase 1.2** — Remove `transaction()` (breaks `scraper.py`)
9. **Phase 2** — Fix `scraper.py` (removes `transaction()` usage)
10. **Phase 1.3** — Remove local sqlite3 connection path
11. **Phase 1.5** — Rewrite `_sync_replica()` with `MetadataClient`
12. **Phase 1.6** — Remove `update_sync_metadata()`
13. **Phase 1.7** — Simplify `initialize_schema()`
14. **Phase 1.8** — Rewrite `reset_database()`
15. **Phase 1.9** — Simplify all write methods (remove else branches)
16. **Phase 1.10** — Fix TOCTOU races
17. **Phase 1.11** — Add `bulk_insert_recordings()`
18. **Phase 1.12** — Simplify `get_stats()`
19. **Phase 1.13** — `delete_recording()` returns `bool`
20. **Phase 3.2** — Add new methods for `audio.py` raw SQL replacements
21. **Phase 7** — Schema changes
22. **Phase 6** — Fix App `ReadOnlyClient`
23. **Phase 9.3** — Update `DatabaseStats` and remaining models
24. **Phase 8** — Update tests

Steps 1-2 are safe (additive). Steps 3-7 fix constructors. Steps 8-9 fix the core bug. Steps 10-20 complete the client rewrite. Steps 21-23 handle schema and models. Step 24 is tests last.
