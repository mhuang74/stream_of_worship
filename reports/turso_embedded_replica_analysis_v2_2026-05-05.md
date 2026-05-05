# Turso Embedded Replica Analysis v2: Remote-Write + Local-Read Architecture

**Date:** 2026-05-05
**Status:** Analysis Report
**Supersedes:** `reports/turso_embedded_replica_analysis_2026-05-05.md`, `reports/turso_sdk_analysis_2026-05-05.md`

---

## Executive Summary

The project currently uses `libsql` v0.1.11 embedded replicas with **local writes and bidirectional `conn.sync()`**, causing recurring WalConflict errors. Analysis of Turso's documentation and current SDK offerings reveals:

1. **The project IS using embedded replicas via libsql** — but in a way that contradicts the documented write model. The TypeScript/libSQL docs state "writes go to the remote primary," but the Python `libsql` package (v0.1.11) writes locally by default. This mismatch is the root cause of WalConflict errors.

2. **Turso now offers two sync systems**: the legacy `libsql` embedded replicas (current) and the new `pyturso` package with explicit `push()`/`pull()`. The pyturso package is the ideal long-term solution but is currently in Beta (v0.5.1, PyPI classifier: Alpha).

3. **The recommended architecture is a hybrid remote-write + local-read model** using the existing `libsql` package. All writes go directly to Turso Cloud (via HTTP API `/v2/pipeline`), while all reads come from the local embedded replica. This eliminates WalConflict at the root cause.

4. **Three implementation options for remote writes exist**: (a) `libsql.remote_connect()`, (b) Turso HTTP API (`/v2/pipeline`), (c) `pyturso` sync driver. Options (a) and (b) use the existing `libsql` package or `urllib`/`httpx`. Option (c) requires the beta `pyturso` package.

**Decision**: Pivot to remote-write + local-read architecture. Admin is always online, so remote writes are acceptable. Not adopting `pyturso` at this time. Implement defensive fixes for replica sync recovery.

---

## 1. Is the Project Using Embedded Replicas via libSQL?

**Yes.** Both the Admin CLI and User App use `libsql.connect()` with `sync_url` to create embedded replica connections that maintain a local SQLite file synced with Turso Cloud.

### Admin CLI (`src/stream_of_worship/admin/db/client.py:108-112`)

```python
self._connection = libsql.connect(
    str(self.db_path),           # Local file path
    sync_url=self.turso_url,     # Turso cloud URL
    auth_token=self.turso_token or "",
)
```

### User App (`src/stream_of_worship/app/db/read_client.py:88-91`)

```python
self._connection = libsql.connect(
    str(self.db_path),
    sync_url=self.turso_url,
    auth_token=self.turso_token or "",
)
```

When `libsql` is unavailable, both fall back to standard `sqlite3` (no sync capability).

### Current Data Flow (Problematic)

```
Admin CLI (RW)                      Turso Cloud                     User App (RO)
──────────────                      ───────────                     ─────────────
1. Write locally (INSERT/UPDATE)
2. ALTER TABLE locally (DDL)
3. conn.sync() ←── push+pull ───→ Turso primary ←── pull-only ──→ conn.sync()
4. WAL frames accumulate           (distribution hub)               (read-only token
5. WalConflict on sync                                               rejects push)
```

---

## 2. Key Finding: Python libsql Writes Locally, Not Remotely

### 2.1 The Documentation Gap

Turso's Embedded Replicas documentation states:

> "Writes are sent to the remote primary database by default. They are NOT written to the local file first."

This is **true for the TypeScript `@libsql/client` SDK** but **NOT true for the Python `libsql` package (v0.1.11)**. The Python package writes locally and uses `conn.sync()` for bidirectional replication.

### 2.2 Evidence

1. **WAL frame accumulation**: INSERT/UPDATE/ALTER TABLE operations generate local WAL frames. If writes went to the remote, local WAL would not accumulate from DML operations.

2. **WalConflict errors**: `conn.sync()` pushes local WAL frames to Turso AND pulls remote frames. When local and remote frame histories diverge, the Rust replicator's `InjectorWal` detects page-level conflicts and raises `WalConflict`.

3. **Pre-sync migration writes WAL frames**: `apply_column_migrations()` (ALTER TABLE ADD COLUMN) before `conn.sync()` creates WAL frames that conflict with remote frame injection.

4. **The `offline=True` flag exists**: If writes went to remote by default, there would be no need for an `offline` flag to enable local writes.

### 2.3 libsql Sidecar Files

| File | Purpose |
|---|---|
| `.db` | Main SQLite database |
| `.db-wal` | Write-ahead log (local writes) |
| `.db-shm` | Shared memory (WAL index) |
| `.db-info` | libsql replication metadata (frame counter) |

All sidecar files must be deleted together when resetting local state.

---

## 3. Turso's Two Sync Systems

### 3.1 Legacy: libsql Embedded Replicas (Current)

| Feature | Behavior |
|---|---|
| **Package** | `libsql` v0.1.11 |
| **Connection** | `libsql.connect(path, sync_url=..., auth_token=...)` |
| **Write model** | Local writes + bidirectional `conn.sync()` |
| **Sync API** | `conn.sync()` — bidirectional, no parameters, no push-only/pull-only |
| **Conflict** | WAL frame injection → `WalConflict` on divergence |
| **Offline** | `offline=True` flag (fragile) |
| **Checkpoint** | Manual `PRAGMA wal_checkpoint(TRUNCATE)` |
| **Status** | Legacy, marked deprecated in Turso docs |
| **Read-your-writes** | Via `conn.sync()` after local write |

Turso docs now display a warning on the embedded replicas page:

> "Embedded Replicas are a legacy Turso Cloud feature built on libSQL. Writes are sent to the remote primary database, not stored locally. For new projects that need sync, use Turso Sync."

### 3.2 New: Turso Sync via pyturso (Not Adopting Now)

| Feature | Behavior |
|---|---|
| **Package** | `pyturso` v0.5.1 (Beta/Alpha) |
| **Connection** | `turso.sync.connect(path, remote_url=..., remote_auth_token=...)` |
| **Write model** | True local-first: writes stored locally, push explicitly |
| **Sync API** | `conn.push()` + `conn.pull()` — explicit direction |
| **Conflict** | Logical CDC statements, "last push wins" |
| **Offline** | Native — `bootstrap_if_empty=False` |
| **Checkpoint** | Built-in `conn.checkpoint()` |
| **Stats** | Built-in `conn.stats()` |
| **Partial sync** | `PartialSyncOpts` for lazy page fetching |
| **Status** | Beta (Alpha per PyPI classifier: "3 - Alpha") |
| **Read-your-writes** | Immediate (local reads are authoritative) |

**Decision**: Not adopting `pyturso` now due to Beta status. Staying with `libsql` and refactoring to remote-write + local-read.

### 3.3 pyturso API Comparison (Informative)

The `pyturso` sync driver provides exactly the API the project needs:

```python
import turso.sync

conn = turso.sync.connect(
    path="./app.db",
    remote_url="libsql://...",
    remote_auth_token=os.environ["TURSO_AUTH_TOKEN"],
)

# Read locally
rows = conn.execute("SELECT * FROM songs").fetchall()

# Write locally
conn.execute("INSERT INTO songs VALUES (...)")
conn.commit()

# Push to remote (explicit)
conn.push()

# Pull from remote (explicit)
changed = conn.pull()

# Checkpoint WAL
conn.checkpoint()

# Stats
s = conn.stats()
```

This is the target API for when `pyturso` reaches stable. The current refactor should make migration straightforward.

### 3.4 Python SDK Reference — Official Comparison

Per the Turso Python SDK reference (`/sdk/python/reference`):

| | `pyturso` | `libsql` (legacy) |
|---|---|---|
| Use case | Local/embedded, sync | Legacy — embedded replicas |
| Engine | Turso Database (rewrite) | libSQL (SQLite fork) |
| Concurrent writes | Yes (MVCC) | Not supported |
| Sync | push/pull (true local-first) | Embedded replicas (writes go to remote) |
| API | Python `sqlite3`-compatible | Python `sqlite3`-compatible |

> "Starting a new project? Use pyturso — it is built on the Turso Database engine with better performance, concurrent writes, and true local-first sync."

---

## 4. Confirmed Design Decisions (From Interview)

| Decision | Choice | Rationale |
|---|---|---|
| Admin connectivity | Always online | Admin CLI requires network for Turso writes |
| Offline-first writes | Not needed | Admin is always online; app is read-only |
| App push capability | No, stays read-only | App only reads catalog data; local songsets stay separate |
| pyturso adoption | Not now | Beta risk too high; refactor libsql first |
| Architecture | Remote-write + local-read | Eliminates WalConflict at root cause |
| Migration strategy | Pivot to remote-write first | Defensive recovery fixes for replica sync |
| Remote write method | HTTP API preferred | More explicit than libsql remote connection; no dependency on libsql remote-mode behavior |

---

## 5. Hybrid Architecture: Remote Writes + Local Reads

### 5.1 Design Overview

The admin CLI uses **two connections**:

1. **Write connection** (remote via HTTP API): All DML (INSERT/UPDATE/DELETE) and DDL (ALTER TABLE) go directly to Turso Cloud via HTTP `/v2/pipeline`
2. **Read connection** (embedded replica via libsql): All reads are served from the local SQLite file for speed

After writes, the read connection is synced to pull changes.

```
Admin CLI
─────────
                   ┌──────────────────────┐
                   │  Turso Cloud          │
                   │  (Primary DB)         │
                   │                       │
  WRITE ──HTTP────→│  INSERT/UPDATE/DELETE │
  (http_client)    │  ALTER TABLE          │
                   │                       │
  PULL ────HTTP────→│  Replication frames   │
  (read_conn.sync) │                       │
                   └──────────────────────┘
                          ↑
                          │ push (no-op: no local DML writes on replica)
                          │
  READ ←──local───  [Local .db file]
  (read_conn)       [Embedded replica]
```

### 5.2 Remote Write Options

There are two viable approaches for remote writes, both achieving the same goal:

#### Option A: HTTP API (`/v2/pipeline`) — Recommended

Use Python's `urllib.request` or `httpx` to send SQL directly to Turso Cloud over HTTP. No additional library needed beyond what's already available.

**Pros**:
- No dependency on libsql remote connection mode behavior
- Uses well-documented, versioned Turso API (`/v2/pipeline`)
- Explicit request/response format; easy to debug
- Supports transactions via baton mechanism
- Can use any HTTP client (urllib, httpx, requests)
- Works even if libsql is not installed (admin extra without libsql)

**Cons**:
- Must format SQL as JSON payload (minor boilerplate)
- Must parse JSON response (minor boilerplate)
- Connection management is manual (baton for interactive sessions)

**HTTP API details**:
- Endpoint: `POST https://<db>-<org>.turso.io/v2/pipeline`
- Auth: `Authorization: Bearer <token>`
- Request body: `{"requests": [{"type": "execute", "stmt": {"sql": "...", "args": [...]}}, {"type": "close"}]}`
- Response: `{"results": [{"type": "ok", "response": {"type": "execute", "result": {...}}}]}`
- Transaction timeout: 5 seconds; Connection idle timeout: 10 seconds
- Interactive queries: baton mechanism for multi-request connections

#### Option B: libsql Remote Connection

Use `libsql.connect(database="libsql://...", auth_token=...)` to create a remote-only connection.

**Pros**:
- Same `execute/commit/cursor` API as embedded replica
- Less boilerplate than HTTP API
- Automatic connection management

**Cons**:
- Less transparent — relies on libsql's internal HTTP handling
- Behavior of remote connection mode is less documented than HTTP API
- Requires libsql to be installed (can't be used in admin-only mode without libsql)
- Debugging is harder (no raw HTTP to inspect)

**Recommendation**: **Option A (HTTP API)** — it's more explicit, has no hidden behavior, and aligns with the principle of understanding what's happening at the protocol level. The admin CLI already uses various HTTP clients (for Analysis Service, R2), so this is consistent.

### 5.3 Connection Management

```python
class DatabaseClient:
    def __init__(self, db_path, turso_url, turso_token):
        self.db_path = db_path
        self.turso_url = turso_url
        self.turso_token = turso_token
        self._read_connection = None
        self._http_url = None  # Derived from turso_url

    @property
    def http_url(self) -> str:
        """Convert libsql:// URL to https:// URL for HTTP API."""
        if self._http_url is None and self.turso_url:
            self._http_url = self.turso_url.replace("libsql://", "https://").replace(
                "http://", "https://"
            ) + "/v2/pipeline"
        return self._http_url

    @property
    def connection(self):
        """Local embedded replica for reads."""
        if self._read_connection is None:
            if self.is_turso_enabled:
                self._read_connection = libsql.connect(
                    str(self.db_path),
                    sync_url=self.turso_url,
                    auth_token=self.turso_token or "",
                )
            else:
                self._read_connection = sqlite3.connect(str(self.db_path))
                self._read_connection.row_factory = sqlite3.Row
                self._read_connection.execute("PRAGMA foreign_keys = ON")
        return self._read_connection
```

### 5.4 Write Operations (Go to Remote via HTTP)

All write operations send SQL to Turso Cloud via the HTTP API:

```python
def _execute_remote(self, sql: str, params: tuple = ()) -> dict:
    """Execute a SQL statement on Turso Cloud via HTTP API."""
    import json
    import urllib.request

    payload = {
        "requests": [
            {
                "type": "execute",
                "stmt": {"sql": sql, "args": [_format_param(p) for p in params]},
            },
            {"type": "close"},
        ]
    }

    req = urllib.request.Request(
        self.http_url,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {self.turso_token}",
            "Content-Type": "application/json",
        },
    )

    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())

    # Check for errors
    for r in result.get("results", []):
        if r.get("type") == "ok":
            execute_result = r.get("response", {}).get("result", {})
            if execute_result.get("cols") is not None:
                return execute_result
        else:
            raise SyncError(f"Remote execute failed: {r.get('error', 'unknown')}")

    return {}

def insert_song(self, song: Song) -> None:
    if self.is_turso_enabled:
        self._execute_remote(
            "INSERT OR REPLACE INTO songs (...) VALUES (...)",
            (song.id, song.title, ...),
        )
        self._sync_replica()
    else:
        # sqlite3 fallback (no Turso)
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(...)
```

### 5.5 HTTP API Parameter Formatting

The HTTP API requires parameters in a specific JSON format:

```python
def _format_param(value) -> dict:
    """Format a Python value for the Turso HTTP API."""
    if value is None:
        return {"type": "null"}
    elif isinstance(value, bool):
        return {"type": "integer", "value": "1" if value else "0"}
    elif isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    elif isinstance(value, float):
        return {"type": "float", "value": str(value)}
    elif isinstance(value, str):
        return {"type": "text", "value": value}
    elif isinstance(value, bytes):
        import base64
        return {"type": "blob", "base64": base64.b64encode(value).decode()}
    else:
        return {"type": "text", "value": str(value)}
```

### 5.6 Read Operations (From Local Replica)

Unchanged — all reads use the embedded replica:

```python
def get_song(self, song_id: str) -> Optional[Song]:
    cursor = self.connection.cursor()
    cursor.execute("SELECT * FROM songs WHERE id = ?", (song_id,))
    row = cursor.fetchone()
    if row:
        return Song.from_row(tuple(row), cursor.description)
    return None
```

### 5.7 Sync After Writes

After writing to the remote, the local replica is synced (pull-only operation in this model):

```python
def _sync_replica(self) -> None:
    """Pull remote changes to local replica."""
    if self.is_turso_enabled and self._read_connection:
        try:
            self._read_connection.sync()
        except Exception as e:
            # Non-fatal: the write succeeded on the remote;
            # the replica will catch up on next sync
            logger.warning(f"Replica sync after write failed: {e}")
```

Since the replica has no local DML writes (all writes go to the remote), `conn.sync()` on the replica is effectively pull-only. The push half is a no-op because there's nothing to push. This eliminates the WAL conflict at the root cause.

### 5.8 DDL Operations (Go to Remote via HTTP)

Schema changes go directly to Turso Cloud:

```python
def apply_column_migrations_remote(self) -> None:
    """Run ALTER TABLE on the remote."""
    if self.is_turso_enabled:
        for migration_sql in get_migration_sqls():
            self._execute_remote(migration_sql)
        self._sync_replica()
    else:
        # sqlite3 fallback
        cursor = self.connection.cursor()
        apply_column_migrations(cursor)
        self.connection.commit()
```

This eliminates the pre-sync migration WAL conflict entirely — schema changes go to the remote, and the replica pulls them cleanly.

### 5.9 Bulk Operations

For batch operations (scrape 200 songs), use the HTTP API's pipeline mechanism for transactional writes:

```python
def bulk_insert_songs(self, songs: list[Song]) -> None:
    if self.is_turso_enabled:
        # Send all INSERTs in a single HTTP pipeline request
        requests = [{"type": "execute", "stmt": {"sql": "BEGIN"}}]
        for song in songs:
            requests.append({
                "type": "execute",
                "stmt": {
                    "sql": "INSERT OR REPLACE INTO songs (...) VALUES (...)",
                    "args": [...],
                },
            })
        requests.append({"type": "execute", "stmt": {"sql": "COMMIT"}})
        requests.append({"type": "close"})

        self._execute_remote_pipeline(requests)
        self._sync_replica()
```

---

## 6. Detailed Implementation Considerations

### 6.1 Read-After-Write Consistency

**Problem**: After writing to the remote, the local replica doesn't have the new data until `_sync_replica()` is called.

**Mitigations**:
- **Immediate sync after write**: Call `_sync_replica()` after every write. Acceptable for admin CLI (not latency-sensitive).
- **Read from remote for immediate consistency**: For write-then-read patterns, query the remote via HTTP instead of the local replica.
- **Accept stale reads**: For fire-and-forget writes, skip sync and let the user call `db pull` later.

**Recommended**: Call `_sync_replica()` after every write operation. One sync per write ensures consistency. For bulk operations, sync once after the entire batch.

### 6.2 Transaction Handling: Read-Then-Write Patterns

Many admin operations read a row, check conditions, then update. In the hybrid model:

```python
def reconcile_recording(self, hash_prefix: str):
    # Read from local replica (fast)
    read_cursor = self.connection.cursor()
    read_cursor.execute("SELECT * FROM recordings WHERE hash_prefix = ?", (hash_prefix,))
    row = read_cursor.fetchone()

    # ... process row ...

    # Write to remote
    self._execute_remote("UPDATE recordings SET ... WHERE hash_prefix = ?", (...))
    self._sync_replica()
```

This is safe because the admin is the single writer — no race condition between the local read and the remote write.

### 6.3 libsql `row_factory` Not Supported

The `libsql.Connection` object does NOT support `row_factory = sqlite3.Row`. However, `Song.from_row()` and `Recording.from_row()` already handle plain tuples by using `cursor.description` to map column names to values. This is not a blocker.

For the sqlite3 fallback path: `row_factory` is still set. No change needed.

### 6.4 Non-Turso Mode (sqlite3 Fallback)

When Turso is not configured, the admin CLI falls back to `sqlite3.connect()`. This path is unchanged — all reads and writes use the same local `sqlite3` connection. The dual-path model (HTTP for remote, local reads from replica) only applies when Turso is enabled.

### 6.5 Connection Lifecycle

| Connection | When Created | When Closed |
|---|---|---|
| HTTP client (remote writes) | On first write or DDL operation | Per-request (stateless) |
| `connection` (replica reads) | On first read or sync | On `close()` or context exit |
| sqlite3 fallback | On first access | On `close()` or context exit |

Both connections are lazily created. The HTTP client is stateless — each write creates a new HTTP request.

### 6.6 Recovery Scenarios

| Scenario | Current (local writes) | Hybrid (remote writes) |
|---|---|---|
| **WAL conflict on sync** | Common. Delete DB + retry sync. | Rare (only from replica-side writes like `_migrate_schema()`). Same recovery: delete DB + sync. |
| **Metadata corruption** | Delete DB + sidecars + retry sync. | Same: delete replica files + sync. |
| **Remote write failure** | Not applicable (writes are local). | Write fails immediately. Retry or handle error. No local data loss. |
| **Network outage during write** | Not applicable. | Write fails. No data written locally. User must retry. |
| **Stale replica** | `conn.sync()` pulls changes. | Same: `_sync_replica()` pulls changes. |
| **HTTP API error** | Not applicable. | Parse error response, raise SyncError with details. |

### 6.7 App-Side (ReadOnlyClient) Changes

The User App is **largely unchanged**. It already uses a read-only token and only pulls. The key improvements:

1. **Add auto-recovery**: If `conn.sync()` fails with WAL/metadata errors, delete local DB + sidecars + retry sync (from existing spec).
2. **No dual-connection needed**: The app only reads, so it only needs the embedded replica connection.
3. **No HTTP API needed**: The app never writes to Turso.

---

## 7. Impact on Existing Specs

### 7.1 `specs/fix_walconflict_sync_error_opus.md`

**6 fixes proposed** (WalConflict recovery, remove pre-sync migrations, WAL checkpoint, CLI tip, app-side recovery).

**Impact**:
- **Fix 1 (WalConflict recovery)**: Still needed but becomes rare. DDL on the remote eliminates the primary WAL conflict trigger. Recovery logic still needed for edge cases.
- **Fix 2 (remove pre-sync migrations)**: **Eliminated**. Migrations run on the remote, not on the replica before sync.
- **Fix 3 (WAL checkpoint)**: **Not needed** before sync. The replica has no local DML writes, so `sync()` is effectively pull-only.
- **Fix 4 (CLI error tip)**: Still useful but less frequently needed.
- **Fix 5 (app-side recovery)**: Still needed. Same implementation.

### 7.2 `specs/simplify_turso_sync_publish_update.md`

**Proposes** replacing `db sync` with `db publish` and `db update`.

**Impact**: The spec's goals remain valid, but the implementation changes:

| Spec Element | Current Spec (libsql local writes) | Hybrid (remote writes) |
|---|---|---|
| `db publish` | WAL checkpoint + `conn.sync()` | Writes already on remote. `_sync_replica()` to update local. No-op if no pending writes. |
| `db update` | Delete local DB + `conn.sync()` | Same: delete local DB + `conn.sync()` on replica. |
| `db sync` deprecation | Alias for `db publish` | `db sync` = `_sync_replica()` (pull-only). Better name: `db pull`. |
| Pre-publish migration | Run migrations before sync | Migrations run on remote (already done during writes). |
| Auto-recovery | WAL checkpoint + retry | `_sync_replica()` with auto-recovery for replica-side errors. |

**Recommended command structure**:
- `db publish` — confirms writes are on Turso (no-op in remote-write model), syncs the local replica.
- `db pull` — sync the local replica from Turso (for manual refresh or after network outage).
- `db update` — delete local replica + re-pull from Turso (for recovery or fresh setup).
- `db sync` — deprecated alias for `db pull`.

---

## 8. Turso Sync Docs: Conflict Resolution and Checkpoint

### 8.1 Conflict Resolution (New Turso Sync)

The new Turso Sync system uses **"last push wins"** conflict resolution:

> When two clients modify the same data and push, the last push determines the final state on the remote.

During pull, if there are unpushed local changes:
1. Local database is rolled back to last synced state
2. Remote changes are applied
3. Unpushed local changes are **replayed** on top

This rollback-and-replay is atomic — if anything fails, the database remains in its previous state.

This is the conflict resolution model available via `pyturso`, not the legacy `libsql` embedded replicas. The libsql model has no conflict resolution — it fails with WalConflict.

### 8.2 Checkpoint (New Turso Sync)

The new sync system has a built-in `checkpoint()` that compacts the local WAL:

- Auto-checkpoint is **disabled** for sync databases
- Must call `checkpoint()` explicitly
- Call after bulk inserts, on a schedule, or when WAL size is large (via `stats()`)
- Compacts WAL by transferring committed frames into the main database file

For the current `libsql` embedded replica, the equivalent is `PRAGMA wal_checkpoint(TRUNCATE)`. In the hybrid model (no local DML writes on replica), checkpoint is rarely needed — WAL only grows from `conn.sync()` pulling remote frames.

---

## 9. Future pyturso Migration Timeline

The hybrid `libsql` + HTTP API approach is the **right architecture for now**. When `pyturso` reaches stable (likely when v0.6.0 or v1.0.0 is released), the migration path is:

1. **Phase 1**: Migrate User App to `turso.sync.connect()` + `conn.pull()`. Low risk, read-only.
2. **Phase 2**: Migrate Admin CLI. Replace HTTP API + `libsql` replica with single `turso.sync.connect()` (local-first + `push()`/`pull()`). Single connection instead of two.
3. **Phase 3**: Remove `libsql` dependency entirely.

**Benefits of eventual migration**:
- Single connection instead of two
- Native push/pull with "last push wins" conflict resolution
- Built-in checkpoint/stats
- Partial sync for faster cold starts
- Offline-first writes if ever needed
- MVCC concurrent writes (SQLite only allows one writer at a time)

**Trigger for migration**: When `pyturso` v1.0.0 is released, or when the project needs offline-first writes.

---

## 10. Risks and Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| **Remote write latency** | Medium | Batch writes in transactions. Admin CLI is not latency-sensitive. HTTP pipeline supports batch requests. |
| **Network outage during write** | Medium | Write fails immediately. User retries. No partial data. Better than current: no local WAL corruption. |
| **HTTP API boilerplate** | Low | Create `_execute_remote()` helper. Parameter formatting is mechanical. |
| **Dual path complexity** | Low | Clear separation: HTTP for writes, libsql for reads. sqlite3 fallback when no Turso. |
| **Read-after-write stale reads** | Low | `_sync_replica()` after each write. Optional: query remote for immediate consistency. |
| **DDL on remote then sync** | Low | ALTER TABLE on remote is atomic. Replica sync picks up schema changes. No WAL conflict. |
| **App-side sync errors** | Low | Same as current. Add auto-recovery (delete + re-pull). |
| **Replica sync still writes locally** | Low | `conn.sync()` on replica writes WAL frames from remote. But no DML writes = minimal frame conflict risk. `_migrate_schema()` on app creates small WAL frames — only runs on first connection. |
| **HTTP API versioning** | Low | Using `/v2/pipeline` which is the current version. Monitor Turso changelog. |
| **Transaction timeout on HTTP API** | Low | 5-second transaction timeout. For large batches, use baton mechanism or split into smaller transactions. |
| **pyturso API divergence** | Low | Monitor pyturso releases. Migration path is straightforward — replace HTTP + replica with single `turso.sync.connect()`. |

---

## 11. Files to Modify (For Future Implementation)

| File | Changes |
|---|---|
| `src/stream_of_worship/admin/db/client.py` | Add `_execute_remote()` HTTP helper, refactor write methods to use it, add `_sync_replica()`, remove pre-sync migrations, add `write_connection` property (if using libsql remote as alternative) |
| `src/stream_of_worship/admin/services/sync.py` | Update publish/update to use replica sync only, add auto-recovery for WAL/metadata errors, simplify (no WAL checkpoint needed) |
| `src/stream_of_worship/admin/commands/db.py` | Update `db sync` → `db pull`, add `db publish` (confirms writes on remote), add `db update` (recovery), add WalConflict recovery tips |
| `src/stream_of_worship/app/db/read_client.py` | Add auto-recovery in `sync()` for WAL/metadata/corruption errors |
| `src/stream_of_worship/app/services/sync.py` | Minimal changes (recovery in read_client) |
| `tests/admin/services/test_sync.py` | Add tests for remote-write + replica-sync flow, auto-recovery, HTTP API parameter formatting |

---

## 12. Summary of Architectural Decision

**Decision**: Adopt hybrid remote-write + local-read architecture. Writes go to Turso Cloud via HTTP API. Reads come from local embedded replica via libsql. Admin is always online.

**Rationale**:
1. Eliminates WalConflict at the root cause (no local WAL from DML writes)
2. Conforms to Turso's documented design intent (writes go to remote primary)
3. No dependency on a beta package (`pyturso`)
4. DDL operations (ALTER TABLE) also go to remote, eliminating pre-sync migration conflict
5. Local reads remain fast (embedded replica with microsecond-level access)
6. HTTP API is explicit, debuggable, and doesn't depend on libsql remote-mode behavior
7. Clear migration path to `pyturso` when it reaches stable

**Tradeoffs accepted**:
- Network latency for writes (acceptable for admin CLI)
- HTTP API boilerplate for parameter formatting
- Need to sync replica after writes (one sync per write/batch)
- Replica sync still writes WAL frames from remote DML — but no conflict since no local DML WAL frames coexist

**Why not pyturso now**:
- Beta/Alpha status — too risky for production database operations
- The hybrid approach solves the immediate problem without new dependencies
- When pyturso is stable, migration from HTTP API + libsql replica to `turso.sync.connect()` is straightforward

**Why HTTP API over libsql remote connection**:
- More explicit — no hidden behavior in libsql's remote connection mode
- Uses well-documented, versioned Turso API (`/v2/pipeline`)
- No dependency on libsql's internal HTTP handling for the write path
- Consistent with project's use of HTTP clients elsewhere (Analysis Service, R2)
- Can work even without libsql installed (future: admin extra without libsql)
