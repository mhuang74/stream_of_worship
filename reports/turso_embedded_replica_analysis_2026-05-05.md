# Turso Embedded Replica Analysis: libsql Remote-Write + Local-Read Architecture

**Date:** 2026-05-05
**Status:** Analysis Report
**Supersedes:** `reports/turso_sdk_analysis_2026-05-05.md`

## Executive Summary

The project currently uses `libsql` v0.1.11 embedded replicas with local writes and bidirectional `conn.sync()`, causing WalConflict errors. Analysis reveals that the Python `libsql` package writes locally by default (contrary to Turso's TypeScript SDK behavior where writes go remote). The recommended architecture is a **hybrid remote-write + local-read** model: use a remote `libsql` connection (URL-only) for all writes and DDL, and an embedded replica connection (local file + sync_url) for reads. This eliminates WAL conflicts at the root cause, conforms to libsql's documented design intent, and avoids migrating to the beta `pyturso` package.

---

## 1. Current Usage Analysis

### 1.1 The Project IS Using Embedded Replicas via libsql

Both the Admin CLI and User App create `libsql` embedded replica connections:

**Admin CLI** (`src/stream_of_worship/admin/db/client.py:108-112`):
```python
self._connection = libsql.connect(
    str(self.db_path),
    sync_url=self.turso_url,
    auth_token=self.turso_token or "",
)
```

**User App** (`src/stream_of_worship/app/db/read_client.py:88-91`):
```python
self._connection = libsql.connect(
    str(self.db_path),
    sync_url=self.turso_url,
    auth_token=self.turso_token or "",
)
```

When `libsql` is unavailable, both fall back to standard `sqlite3` (no sync capability).

### 1.2 Current Data Flow

```
Admin CLI (RW)                      Turso Cloud                     User App (RO)
──────────────                      ───────────                     ─────────────
1. Write locally (INSERT/UPDATE)
2. ALTER TABLE locally (DDL)
3. conn.sync() ←── push+pull ───→ Turso primary ←── pull-only ──→ conn.sync()
4. WAL frames accumulate           (distribution hub)               (read-only token
5. WalConflict on sync                                               rejects push)
```

### 1.3 Sidecar Files Created by libsql Embedded Replicas

| File | Purpose |
|---|---|
| `.db` | Main SQLite database |
| `.db-wal` | Write-ahead log (local writes) |
| `.db-shm` | Shared memory (WAL index) |
| `.db-info` | libsql replication metadata (frame counter) |

---

## 2. Key Finding: Python libsql Writes Locally, Not Remotely

### 2.1 The Documentation Gap

Turso's Embedded Replicas documentation states:

> "Writes are sent to the remote primary database by default. They are NOT written to the local file first."

This is **true for the TypeScript `@libsql/client` SDK** but **NOT true for the Python `libsql` package (v0.1.11)**. The Python package writes locally and uses `conn.sync()` for bidirectional replication.

### 2.2 Evidence

1. **WAL frame accumulation**: INSERT/UPDATE/ALTER TABLE operations generate local WAL frames. If writes went to the remote, local WAL would not accumulate from DML operations.

2. **WalConflict errors**: The `conn.sync()` call pushes local WAL frames to Turso AND pulls remote frames. When local and remote frame histories diverge, the Rust replicator's `InjectorWal` detects page-level conflicts and raises `WalConflict`.

3. **Pre-sync migration writes WAL frames**: `apply_column_migrations()` (ALTER TABLE ADD COLUMN) before `conn.sync()` creates WAL frames that conflict with remote frame injection — this would be impossible if writes went to the remote.

4. **Empirical test**: `libsql.connect('https://...', auth_token=...)` creates a separate remote connection type. The embedded replica connection (`libsql.connect('/path/to/db', sync_url=...)`) does not redirect writes to the remote.

### 2.3 Why This Matters

The project's architecture assumes:
- Admin writes locally → syncs to push changes to Turso
- `conn.sync()` is the push mechanism

This is a **local-first write model** overlaid on a **replication protocol** that expects minimal local divergence. The mismatch causes WalConflict whenever local writes and remote changes touch overlapping B-tree pages.

---

## 3. Turso's Two Sync Systems

### 3.1 Legacy: libsql Embedded Replicas (Current)

| Feature | Behavior |
|---|---|
| **Package** | `libsql` v0.1.11 |
| **Connection** | `libsql.connect(path, sync_url=..., auth_token=...)` |
| **Write model** | Local writes + bidirectional `conn.sync()` |
| **Sync API** | `conn.sync()` — bidirectional, no parameters |
| **Conflict** | WAL frame injection → `WalConflict` on divergence |
| **Offline** | `offline=True` flag (fragile) |
| **Checkpoint** | Manual `PRAGMA wal_checkpoint(TRUNCATE)` |
| **Status** | Legacy, "fully supported" per docs |

### 3.2 New: Turso Sync via pyturso (Not Adopting)

| Feature | Behavior |
|---|---|
| **Package** | `pyturso` v0.5.1 (Beta/Alpha) |
| **Connection** | `turso.sync.connect(path, remote_url=..., auth_token=...)` |
| **Write model** | True local-first: writes stored locally, push explicitly |
| **Sync API** | `conn.push()` + `conn.pull()` — explicit direction |
| **Conflict** | Logical CDC statements, "last push wins" |
| **Offline** | Native — `bootstrap_if_empty=False` |
| **Checkpoint** | Built-in `conn.checkpoint()` |
| **Stats** | Built-in `conn.stats()` |
| **Status** | Beta (Alpha per PyPI classifier) |

**Decision**: Not adopting pyturso now due to Beta status. Staying with `libsql` and refactoring to use its remote connection mode for writes.

### 3.3 Remote Connection Mode (The Hybrid Solution)

The `libsql` Python package supports a **third** connection mode beyond local-only and embedded replica:

```python
# Remote connection: queries go to Turso Cloud over HTTP
remote_conn = libsql.connect(
    database="libsql://sow-catalog-mhuang.aws-us-west-2.turso.io",
    auth_token=SOW_TURSO_TOKEN,
)
```

When `database` is a URL (not a file path) and `sync_url` is not set, the connection operates in **remote mode**: all queries (reads AND writes) go to the Turso Cloud server over HTTP. No local file is created.

**Verified**: This connection type works with the current `libsql` v0.1.11 package and exposes the same `execute/commit/cursor` API as the embedded replica connection.

---

## 4. Hybrid Architecture: Remote Writes + Local Reads

### 4.1 Design

The admin CLI uses **two libsql connections**:

1. **Write connection** (remote): All DML (INSERT/UPDATE/DELETE) and DDL (ALTER TABLE) go directly to Turso Cloud
2. **Read connection** (embedded replica): All reads are served from the local SQLite file for speed

After writes, the read connection is synced to pull changes.

```
Admin CLI
─────────
                   ┌──────────────────────┐
                   │  Turso Cloud          │
                   │  (Primary DB)         │
                   │                       │
  WRITE ──HTTP───→│  INSERT/UPDATE/DELETE │
  (remote_conn)   │  ALTER TABLE          │
                   │                       │
  PULL ────HTTP───→│  Replication frames   │
  (read_conn.sync)│                       │
                   └──────────────────────┘
                          ↑
                          │ push (no-op: no local writes on replica)
                          │
  READ ←──local───  [Local .db file]
  (read_conn)       [Embedded replica]
```

### 4.2 Connection Management

```python
class DatabaseClient:
    def __init__(self, db_path, turso_url, turso_token):
        self.db_path = db_path
        self.turso_url = turso_url
        self.turso_token = turso_token
        self._read_connection = None
        self._write_connection = None

    @property
    def write_connection(self):
        """Remote connection to Turso Cloud for writes."""
        if self._write_connection is None and self.is_turso_enabled:
            self._write_connection = libsql.connect(
                database=self.turso_url,
                auth_token=self.turso_token or "",
            )
        return self._write_connection

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

### 4.3 Write Operations (Go to Remote)

All write operations use `write_connection`:

```python
def insert_song(self, song: Song) -> None:
    conn = self.write_connection  # Remote connection
    cursor = conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO songs (...) VALUES (...)", (...))
    conn.commit()  # Sends to Turso Cloud over HTTP

    # Pull changes to local replica
    self._sync_replica()
```

### 4.4 Read Operations (From Local Replica)

All read operations use `connection` (the embedded replica):

```python
def get_song(self, song_id: str) -> Optional[Song]:
    cursor = self.connection.cursor()  # Local replica
    cursor.execute("SELECT * FROM songs WHERE id = ?", (song_id,))
    row = cursor.fetchone()
    if row:
        return Song.from_row(tuple(row), cursor.description)
    return None
```

### 4.5 Sync After Writes

After writing to the remote, the local replica needs to be synced:

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

### 4.6 DDL Operations (Go to Remote)

Schema changes go directly to Turso Cloud:

```python
def apply_column_migrations_remote(self) -> None:
    """Run ALTER TABLE on the remote connection."""
    write_cursor = self.write_connection.cursor()
    apply_column_migrations(write_cursor)
    self.write_connection.commit()

    # Pull schema changes to local replica
    self._sync_replica()
```

This eliminates the pre-sync migration WAL conflict entirely — schema changes go to the remote, and the replica pulls them cleanly.

### 4.7 Bulk Operations

For batch operations (scrape 200 songs), use transactions on the remote connection:

```python
def bulk_insert_songs(self, songs: list[Song]) -> None:
    conn = self.write_connection  # Remote
    cursor = conn.cursor()
    for song in songs:
        cursor.execute("INSERT OR REPLACE INTO songs (...) VALUES (...)", song_params)
    conn.commit()  # Single HTTP request with all INSERTs

    # One sync to pull all changes to replica
    self._sync_replica()
```

---

## 5. Detailed Implementation Considerations

### 5.1 Read-After-Write Consistency

**Problem**: After writing to the remote connection, the local replica doesn't have the new data until `_sync_replica()` is called.

**Mitigations**:
- **Immediate sync after write**: Call `_sync_replica()` after every write operation. For the admin CLI (single-user, not latency-sensitive), this is acceptable.
- **Read from write connection for immediate consistency**: For operations that write and then immediately read back (e.g., insert a recording, then check its status), read from `write_connection` instead of `connection`.
- **Accept stale reads**: For operations where the write is fire-and-forget (e.g., batch LRC submissions), skip the sync and let the user call `db sync` / `Shift+S` later.

**Recommended**: Call `_sync_replica()` after every write operation. The admin CLI is not performance-sensitive, and a single sync after each write ensures consistency. For bulk operations, sync once after the entire batch.

### 5.2 Transaction Handling

**Write transactions** that also read pose a challenge:

```python
# Problem: This reads from the remote (slow) and writes to the remote
def reconcile_recording(self, hash_prefix: str):
    cursor = self.write_connection.cursor()
    cursor.execute("SELECT * FROM recordings WHERE hash_prefix = ?", (hash_prefix,))
    row = cursor.fetchone()  # Remote read (slow)
    # ... process row ...
    cursor.execute("UPDATE recordings SET ... WHERE hash_prefix = ?", (...))
    self.write_connection.commit()
```

**Mitigation**: For read-then-write patterns, read from the local replica and write to the remote:

```python
def reconcile_recording(self, hash_prefix: str):
    # Read from local replica (fast)
    read_cursor = self.connection.cursor()
    read_cursor.execute("SELECT * FROM recordings WHERE hash_prefix = ?", (hash_prefix,))
    row = read_cursor.fetchone()

    # Write to remote
    write_cursor = self.write_connection.cursor()
    write_cursor.execute("UPDATE recordings SET ... WHERE hash_prefix = ?", (...))
    self.write_connection.commit()

    # Sync replica
    self._sync_replica()
```

This works because the admin is the single writer — no race condition between the read and the write.

### 5.3 libsql `row_factory` Not Supported

The `libsql.Connection` object does NOT support `row_factory = sqlite3.Row`. However, `Song.from_row()` and `Recording.from_row()` already handle plain tuples by using `cursor.description` to map column names to values. This is not a blocker.

**For the sqlite3 fallback path** (when libsql is not available): `row_factory` is still set. No change needed.

### 5.4 Non-Turso Mode (sqlite3 Fallback)

When Turso is not configured, the admin CLI falls back to `sqlite3.connect()`. This path is unchanged — all reads and writes use the same local `sqlite3` connection. The dual-connection model only applies when Turso is enabled.

### 5.5 Connection Lifecycle

| Connection | When Created | When Closed |
|---|---|---|
| `write_connection` (remote) | On first write or DDL operation | On `close()` or context exit |
| `connection` (replica) | On first read or sync | On `close()` or context exit |
| sqlite3 fallback | On first access | On `close()` or context exit |

Both connections are lazily created. The remote connection can be `None` when Turso is not configured.

### 5.6 Recovery Scenarios

| Scenario | Current (local writes) | Hybrid (remote writes) |
|---|---|---|
| **WAL conflict on sync** | Delete DB + retry sync | Rare (only from replica-side writes). Same recovery: delete DB + sync. |
| **Metadata corruption** | Delete DB + sidecars + retry sync | Same: delete replica files + sync. |
| **Remote write failure** | Not applicable (writes are local) | Write fails immediately. Retry or handle error. No local data loss. |
| **Network outage during write** | Not applicable | Write fails. No data written locally. User must retry. |
| **Stale replica** | `conn.sync()` pulls changes | Same: `_sync_replica()` pulls changes. |

### 5.7 App-Side (ReadOnlyClient) Changes

The User App is **largely unchanged**. It already uses a read-only token and only pulls. The key improvements:

1. **Add auto-recovery**: If `conn.sync()` fails with WAL/metadata errors, delete local DB + sidecars + retry sync (from `simplify_turso_sync_publish_update.md` spec).
2. **No dual-connection needed**: The app only reads, so it only needs the embedded replica connection.

---

## 6. Impact on Existing Specs

### 6.1 `specs/fix_walconflict_sync_error_opus.md`

**6 fixes proposed** (WalConflict recovery, remove pre-sync migrations, WAL checkpoint, CLI tip, app-side recovery).

**Impact**:
- **Fix 1 (WalConflict recovery)**: Still needed but becomes rare. DDL on the remote eliminates the primary WAL conflict trigger. Recovery logic still needed for edge cases (replica-side writes from schema migration, or app-side sync errors).
- **Fix 2 (remove pre-sync migrations)**: Eliminated. Migrations run on the remote connection, not on the replica before sync.
- **Fix 3 (WAL checkpoint)**: No longer needed before sync. The replica connection has no local DML writes, so `sync()` is effectively pull-only. WAL checkpoint is only needed for DDL on the replica (if any).
- **Fix 4 (CLI error tip)**: Still useful but less frequently needed.
- **Fix 5 (app-side recovery)**: Still needed. Same implementation.

### 6.2 `specs/simplify_turso_sync_publish_update.md`

**Proposes** replacing `db sync` with `db publish` and `db update`.

**Impact**: The spec's goals remain valid, but the implementation changes:

| Spec Element | Current Spec (libsql local writes) | Hybrid (remote writes) |
|---|---|---|
| `db publish` | WAL checkpoint + `conn.sync()` | Writes already on remote. `_sync_replica()` to update local. |
| `db update` | Delete local DB + `conn.sync()` | Same: delete local DB + `conn.sync()` on replica. |
| `db sync` deprecation | Alias for `db publish` | `db sync` = `_sync_replica()` (pull-only). Rename to `db pull`. |
| Pre-publish migration | Run migrations before sync | Run migrations on remote connection (already done during writes). |
| Auto-recovery | WAL checkpoint + retry | `_sync_replica()` with auto-recovery for replica-side errors. |

**Recommended command structure**:
- `db publish` — confirms writes are on Turso (already done from remote writes), syncs the local replica.
- `db update` / `db pull` — delete local replica + re-pull from Turso. Used for recovery or fresh setup.
- `db sync` — deprecated alias for `db pull` (since the replica is read-only in the hybrid model).

### 6.3 `reports/turso_sdk_analysis_2026-05-05.md`

This earlier report recommended migrating to `pyturso`. The current analysis supersedes it with the hybrid libsql approach. The pyturso migration remains a future option when the package reaches stable.

---

## 7. Future pyturso Migration Timeline

The hybrid libsql approach is the **right architecture for now**. When `pyturso` reaches stable (likely when v0.6.0 or v1.0.0 is released), the migration path is:

1. **Phase 1**: Migrate User App to `turso.sync.connect()` + `conn.pull()`. Low risk, read-only.
2. **Phase 2**: Migrate Admin CLI. Replace `libsql.connect()` (remote) + `libsql.connect()` (replica) with `turso.sync.connect()` (local-first + `push()`/`pull()`). Single connection instead of two.
3. **Phase 3**: Remove `libsql` dependency entirely.

**Benefits of eventual migration**: Single connection instead of two, native push/pull, built-in checkpoint/stats, partial sync for faster cold starts.

**Trigger for migration**: When `pyturso` v1.0.0 is released, or when the project needs offline-first writes.

---

## 8. Risks and Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| **Remote write latency** | Medium | Batch writes in transactions. Admin CLI is not latency-sensitive. |
| **Network outage during write** | Medium | Write fails immediately. User retries. No partial data. Better than current: no local WAL corruption. |
| **libsql remote connection bugs** | Low-Medium | Same libsql package, different connection mode. Well-tested path in the TypeScript SDK. |
| **Dual connection complexity** | Low | Both connections are `libsql.Connection` with same API. Clear separation: write_conn for writes, connection for reads. |
| **Read-after-write stale reads** | Low | `_sync_replica()` after each write. Optional: read from write_connection for immediate consistency. |
| **DDL on remote then sync** | Low | ALTER TABLE on remote is atomic. Replica sync picks up schema changes. No WAL conflict. |
| **App-side sync errors** | Low | Same as current. Add auto-recovery (delete + re-pull). |

---

## 9. Files to Modify (For Future Implementation)

| File | Changes |
|---|---|
| `src/stream_of_worship/admin/db/client.py` | Add `write_connection` property (remote), refactor write methods to use it, add `_sync_replica()`, remove pre-sync migrations |
| `src/stream_of_worship/admin/services/sync.py` | Update publish/update to use replica sync only, add auto-recovery for WAL/metadata errors |
| `src/stream_of_worship/admin/commands/db.py` | Update `db sync` -> `db pull`, add `db publish` (writes already remote), add WalConflict recovery tips |
| `src/stream_of_worship/app/db/read_client.py` | Add auto-recovery in `sync()` for WAL/metadata/corruption errors |
| `src/stream_of_worship/app/services/sync.py` | Minimal changes (recovery in read_client) |
| `tests/admin/services/test_sync.py` | Add tests for remote-write + replica-sync flow, auto-recovery |

---

## 10. Summary of Architectural Decision

**Decision**: Adopt hybrid remote-write + local-read architecture using the existing `libsql` v0.1.11 package.

**Rationale**:
1. Eliminates WalConflict at the root cause (no local WAL from DML writes)
2. Conforms to libsql's documented design (writes go to remote primary)
3. No dependency on a beta package (`pyturso`)
4. DDL operations (ALTER TABLE) also go to remote, eliminating the pre-sync migration conflict
5. Local reads remain fast (embedded replica with microsecond-level access)
6. Clear migration path to `pyturso` when it reaches stable

**Tradeoffs accepted**:
- Network latency for writes (acceptable for admin CLI)
- Dual connection management (complexity, but clean separation)
- Need to sync replica after writes (one sync per write/batch)
