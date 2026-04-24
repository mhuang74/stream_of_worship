# Phase 7: Turso Sync — Detailed Implementation Plan

## Overview

Add bidirectional Turso cloud sync to the existing local SQLite database using **embedded replicas**. When Turso is configured, `DatabaseClient` uses `libsql.connect()` (drop-in for `sqlite3.connect()` with sync support). When unconfigured, plain `sqlite3` is used — zero breaking changes.

**User decisions:** Single admin, embedded replicas, no auto-backup, explicit sync only.

---

## Files to Modify/Create

| File | Action | Description |
|------|--------|-------------|
| `pyproject.toml` | Modify | Add `turso` optional dependency group |
| `src/stream_of_worship/admin/db/client.py` | Modify | Conditional libsql/sqlite3 backend, `sync()` method |
| `src/stream_of_worship/admin/db/models.py` | Modify | Add sync fields to `DatabaseStats` |
| `src/stream_of_worship/admin/services/sync.py` | **Create** | `SyncService` orchestrator |
| `src/stream_of_worship/admin/commands/db.py` | Modify | Add `db sync` command, enhance `db status` |
| `tests/admin/services/test_sync.py` | **Create** | SyncService unit tests |
| `tests/admin/commands/test_db_commands.py` | **Create** | db sync + enhanced status command tests |
| `tests/admin/test_client.py` | Modify | Add sync-related DatabaseClient tests |

---

## Step 1: `pyproject.toml` — Add turso dependency

Add new optional dependency group:

```toml
turso = [
    "libsql>=0.1.0",
]
```

**Note:** `libsql` is the stable Turso Python SDK (replaces deprecated `libsql-experimental`). Keep it separate from `admin` extra so users without Turso don't need to install it.

---

## Step 2: `db/client.py` — Conditional Backend + Sync

### 2a. Add `SyncError` exception

```python
class SyncError(Exception):
    """Error during Turso sync operations."""
    pass
```

### 2b. Update `__init__` signature

```python
def __init__(self, db_path: Path, turso_url: str = "", turso_token: str = ""):
    self.db_path = db_path
    self._turso_url = turso_url
    self._turso_token = turso_token
    self._connection = None
    self._use_libsql = bool(turso_url and turso_token)
```

Change `_connection` type hint from `Optional[sqlite3.Connection]` to `Optional[Any]`.

### 2c. Update `connection` property

```python
@property
def connection(self):
    if self._connection is None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        if self._use_libsql:
            try:
                import libsql
            except ImportError:
                raise ImportError(
                    "The 'libsql' package is required for Turso sync. "
                    "Install with: pip install libsql"
                )
            self._connection = libsql.connect(
                str(self.db_path),
                sync_url=self._turso_url,
                auth_token=self._turso_token,
            )
        else:
            self._connection = sqlite3.connect(
                self.db_path, detect_types=sqlite3.PARSE_DECLTYPES,
            )
            self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
    return self._connection
```

**Compatibility note:** `libsql` does NOT support `row_factory`. This is safe because:
- All `from_row()` calls use `tuple(row)` — works on both `sqlite3.Row` and native tuples
- All stats queries use `row[0]`, `row[1]` index access — works on both
- `for (table_name,) in tables:` tuple unpacking — works on both

### 2d. Add `is_turso_enabled` property

```python
@property
def is_turso_enabled(self) -> bool:
    return self._use_libsql
```

### 2e. Add `sync()` method

```python
def sync(self) -> None:
    if not self._use_libsql:
        raise SyncError("Turso sync is not configured")
    self.connection.sync()
```

### 2f. Update `get_stats()` to read more sync_metadata

Read `sync_version` and `local_device_id` from sync_metadata. Populate new `DatabaseStats` fields.

### 2g. Add `update_sync_metadata()` method

```python
def update_sync_metadata(self, key: str, value: str) -> None:
    """Update a sync_metadata entry."""
    self.connection.execute(
        "UPDATE sync_metadata SET value = ?, updated_at = datetime('now') WHERE key = ?",
        (value, key),
    )
    self.connection.commit()
```

---

## Step 3: `db/models.py` — Extend DatabaseStats

Add fields:

```python
@dataclass
class DatabaseStats:
    table_counts: dict[str, int] = field(default_factory=dict)
    integrity_ok: bool = True
    foreign_keys_enabled: bool = False
    last_sync_at: Optional[str] = None
    sync_version: Optional[str] = None       # NEW
    local_device_id: Optional[str] = None    # NEW
    turso_configured: bool = False            # NEW
```

---

## Step 4: `services/sync.py` — New SyncService

### Dataclasses

```python
@dataclass
class SyncStatus:
    is_configured: bool = False
    turso_url: str = ""             # Masked for display
    has_auth_token: bool = False
    last_sync_at: Optional[str] = None
    sync_version: Optional[str] = None
    local_device_id: Optional[str] = None
    local_songs: int = 0
    local_recordings: int = 0

@dataclass
class SyncResult:
    success: bool
    sync_timestamp: str
    error_message: Optional[str] = None
    songs_after: int = 0
    recordings_after: int = 0
```

### Exceptions

```python
class SyncConfigError(Exception):
    """Turso sync configuration is missing or invalid."""

class SyncNetworkError(Exception):
    """Network error during Turso sync."""
```

### SyncService class

```python
class SyncService:
    def __init__(self, client: DatabaseClient):
        self.client = client

    def get_sync_status(self) -> SyncStatus:
        """Read sync_metadata + row counts. Safe to call even if Turso unconfigured."""

    def validate_config(self) -> None:
        """Check client.is_turso_enabled. Raise SyncConfigError if not."""

    def execute_sync(self) -> SyncResult:
        """
        1. validate_config()
        2. Get pre-sync row counts
        3. client.sync()  (bidirectional via libsql)
        4. Get post-sync row counts
        5. Update sync_metadata: last_sync_at = now(), ensure device_id
        6. Return SyncResult
        Catch network exceptions → wrap in SyncNetworkError
        """

    def _ensure_device_id(self) -> str:
        """Read local_device_id from sync_metadata. If empty, generate uuid4 and store."""

    def _mask_url(self, url: str) -> str:
        """Mask URL for display: libsql://my-db-org.turso.io → libsql://my-*****.turso.io"""
```

---

## Step 5: `commands/db.py` — Add `sync` Command + Enhance `status`

### 5a. Update `get_db_client` to pass Turso config

```python
def get_db_client(config: AdminConfig) -> DatabaseClient:
    turso_url = config.turso_database_url
    turso_token = get_secret("turso.auth_token") or ""
    return DatabaseClient(
        db_path=config.db_path,
        turso_url=turso_url,
        turso_token=turso_token,
    )
```

`get_secret("turso.auth_token")` maps to env var `SOW_TURSO_AUTH_TOKEN` via existing `get_env_var_name()`.

### 5b. New `db sync` command

```python
@app.command("sync")
def sync_db(
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate config without syncing"),
    config_path: Path = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
```

Flow:
1. Load config, verify DB exists
2. Create `DatabaseClient` via `get_db_client(config)`
3. Create `SyncService(client)`
4. **dry-run:** `get_sync_status()` → display table with config, counts, last sync
5. **real sync:** `validate_config()` → `execute_sync()` → display result
6. Error handling: `SyncConfigError` → red message + exit(1), `SyncNetworkError` → red message + retry hint + exit(1)

### 5c. Enhance `show_status`

After existing stats table, add a "Sync" section:
- Turso URL (masked or "Not configured")
- Auth Token ("Set" or "Not Set")
- Last Sync timestamp
- Device ID

Use `get_db_client(config)` instead of `DatabaseClient(db_path)` for Turso awareness.

---

## Step 6: Tests

### 6a. `tests/admin/services/test_sync.py` (~20 tests)

**SyncStatus tests (4):**
- get_sync_status unconfigured / configured / with last_sync / with counts

**validate_config tests (3):**
- success / no URL / no token

**execute_sync tests (8):**
- success / updates last_sync_at / generates device_id / preserves existing device_id
- network error wrapping / auth error wrapping / not configured error
- reports correct before/after counts

**_ensure_device_id tests (3):**
- creates new UUID / returns existing / valid UUID format

**_mask_url tests (2):**
- masks middle of URL / handles edge cases

**Mocking approach:** Use real SQLite for local DB operations. Mock `client.sync()` (the libsql sync call) to avoid network. Create `DatabaseClient` with `turso_url`/`turso_token` but mock the `connection` property to return a regular sqlite3 connection with an added `sync` method.

### 6b. `tests/admin/commands/test_db_commands.py` (~17 tests)

**db sync tests (10):**
- sync not configured → error message
- sync no auth token → error message
- sync dry-run unconfigured → shows status
- sync dry-run configured → shows full status without syncing
- sync success → shows result
- sync network error → shows error
- sync after success → db status shows updated last_sync_at
- sync libsql not installed → shows import error
- sync without database → fails cleanly
- sync without config → fails cleanly

**db status enhanced tests (5):**
- status shows "Not configured" when no Turso URL
- status shows masked Turso URL
- status shows auth token Set/Not Set
- status shows last sync timestamp
- status shows device ID

**db init + reset still work (2):**
- init still works (backward compat)
- reset still works (backward compat)

### 6c. `tests/admin/test_client.py` additions (~8 tests)

- default uses sqlite3 (is_turso_enabled = False)
- turso_enabled true with both params
- sync() raises SyncError when not configured
- sync() calls connection.sync() when configured (mock libsql)
- constructor stores turso params
- get_stats returns new fields (sync_version, device_id, turso_configured)
- update_sync_metadata works
- connection uses libsql when configured (mock import)

### Total new tests: ~45

---

## Error Handling Summary

| Scenario | Exception | User sees |
|----------|-----------|-----------|
| No `turso_database_url` in config | `SyncConfigError` | "Turso sync not configured. Set [turso] database_url in config.toml" |
| No `SOW_TURSO_AUTH_TOKEN` env var | `SyncConfigError` | "SOW_TURSO_AUTH_TOKEN environment variable not set" |
| `libsql` package not installed | `ImportError` | "Install with: pip install libsql" |
| Network unreachable | `SyncNetworkError` | "Cannot connect to Turso. Check network and URL." |
| Auth failure | `SyncNetworkError` | "Authentication failed. Check SOW_TURSO_AUTH_TOKEN." |
| Generic sync error | `Exception` | "Sync failed: {message}" |

---

## Migration Path (Phase 6 → Phase 7)

Zero breaking changes:
1. All existing commands work without Turso config (plain sqlite3 fallback)
2. No schema migration needed — `sync_metadata` table already exists with correct keys
3. First `conn.sync()` bootstraps the Turso cloud replica from local data

**To enable sync:**
```bash
pip install libsql  # or: uv add libsql
sow-admin config set turso_database_url libsql://your-db.turso.io
export SOW_TURSO_AUTH_TOKEN=your-token
sow-admin db sync
```

---

## Implementation Order

1. `pyproject.toml` — add `turso` extra
2. `db/models.py` — add `DatabaseStats` fields
3. `db/client.py` — `SyncError`, updated `__init__`/`connection`, `sync()`, `is_turso_enabled`, `update_sync_metadata`, updated `get_stats()`
4. `services/sync.py` — new file with `SyncService`, `SyncStatus`, `SyncResult`
5. `commands/db.py` — updated `get_db_client`, new `sync` command, enhanced `status`
6. `tests/admin/test_client.py` — new sync-related tests
7. `tests/admin/services/test_sync.py` — new file
8. `tests/admin/commands/test_db_commands.py` — new file
9. Run all tests, verify 295 existing + ~45 new = ~340 total
10. Update `report/current_impl_status.md` and `MEMORY.md`

---

## Verification

```bash
# Run all existing tests (must still pass)
PYTHONPATH=src uv run --extra admin --extra test pytest tests/admin/ -v

# Run new sync tests
PYTHONPATH=src uv run --extra admin --extra test pytest tests/admin/services/test_sync.py tests/admin/commands/test_db_commands.py -v

# Manual smoke tests (without Turso — backward compat)
sow-admin db init --config /tmp/test.toml
sow-admin db status --config /tmp/test.toml
sow-admin db sync --config /tmp/test.toml        # Should show "not configured" error
sow-admin db sync --dry-run --config /tmp/test.toml  # Should show status

# Manual smoke tests (with Turso — requires real credentials)
export SOW_TURSO_AUTH_TOKEN=<token>
sow-admin config set turso_database_url libsql://<db>.turso.io
sow-admin db sync --dry-run    # Should show configured status
sow-admin db sync              # Should sync successfully
sow-admin db status            # Should show last sync time
```

---

## Key Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| `libsql` doesn't support `row_factory` | All code uses `tuple(row)` or index access — already compatible |
| `libsql` not available on all platforms | Optional dependency; falls back to sqlite3 when unconfigured |
| First sync requires network | Clear error message if network unreachable |
| `--dry-run` can't preview what will sync | Document that dry-run validates config + shows state only |
| Turso free tier may not support embedded replicas | Document in error message; suggest upgrading plan |
