# Turso SDK Analysis: libsql vs pyturso for Stream of Worship

**Date:** 2026-05-05
**Status:** Research Report

## Executive Summary

The project currently uses `libsql` v0.1.11 (legacy) with bidirectional `conn.sync()`, which has caused WalConflict errors and requires workarounds (WAL checkpoint hacks, delete-and-re-sync recovery). Turso has released a new Python SDK — `pyturso` (v0.5.1 stable, v0.6.0rc14 pre-release) — built on a ground-up database engine rewrite that provides explicit `push()` and `pull()` methods. This eliminates the entire class of WAL-based sync conflicts and natively achieves the goals of both existing specs (`fix_walconflict_sync_error_opus.md` and `simplify_turso_sync_publish_update.md`).

**Recommendation:** Migrate to `pyturso` for both Admin CLI (sync) and User App (sync). The benefits — elimination of WAL conflicts, explicit directional sync, built-in checkpoint/stats, and conflict resolution — clearly outweigh the migration cost for this project's use case. However, pyturso is in Beta (Alpha per PyPI), so the migration should be phased: User App first (lower risk, read-only), Admin CLI second.

---

## Turso Python SDK Landscape (2026-05-05)

| | `libsql` (current) | `pyturso` (recommended) |
|---|---|---|
| **Status** | Legacy, "fully supported and battle-tested" | Recommended for new projects, BETA |
| **Version** | 0.1.11 | 0.5.1 (stable), 0.6.0rc14 (pre-release) |
| **Engine** | libSQL (SQLite fork) | Turso Database (ground-up rewrite) |
| **Sync API** | `conn.sync()` — bidirectional, no parameters | `conn.push()` + `conn.pull()` — explicit direction |
| **Write model** | Embedded replica: writes go to remote | True local-first: writes are local, push when ready |
| **Conflict resolution** | WAL frame injection → WalConflict on divergence | Logical CDC statements, "last push wins" |
| **Checkpoint** | Manual `PRAGMA wal_checkpoint(TRUNCATE)` | Built-in `conn.checkpoint()` |
| **Stats** | None | Built-in `conn.stats()` (WAL size, network bytes, revision, last push/pull times) |
| **Partial sync** | Not supported | Yes — lazy page fetching, prefix/query bootstrap |
| **Offline-first** | `offline: true` flag (fragile) | Native — `bootstrap_if_empty=False`, write locally, push when online |
| **Concurrent writes** | Not supported | MVCC |
| **Async** | No | Yes (`turso.aio`) |
| **Python** | >=3.8 | >=3.9 (project uses 3.11 — compatible) |
| **sqlite3 compat** | Full | Follows sqlite3 interface, but different engine |
| **ORM support** | SQLAlchemy, Drizzle, Prisma (via libsql) | SQLAlchemy dialect available (v0.5.0+) |

### Turso SDK Package Guide (from official docs)

| Use case | Python package |
|---|---|
| **Local database** (embedded, offline) | `pyturso` |
| **Local database + cloud sync** (push/pull) | `pyturso` (with `turso.sync`) |
| **Remote access** (over-the-wire, no local file) | `libsql` |
| **Legacy (libSQL)** — ORM support | `libsql` |

> "Starting a new project? Use pyturso. Need sync? Use Turso Sync for local reads and writes with explicit push/pull to Turso Cloud."

---

## How pyturso's Sync Model Works

### Connection

```python
import turso.sync

conn = turso.sync.connect(
    path="./app.db",
    remote_url="libsql://...",
    remote_auth_token=os.environ["TURSO_AUTH_TOKEN"],
    # bootstrap_if_empty=False,  # optional: start offline
)
```

On first connect, the local database is automatically bootstrapped from the remote. If `bootstrap_if_empty=False`, the local DB starts empty and can be hydrated later via `pull()`.

### Push (Admin CLI use case)

```python
conn.execute("INSERT INTO songs (...) VALUES (...)")
conn.commit()
conn.push()  # Sends logical statements to Turso Cloud
```

Push sends local changes as **logical CDC statements**, not WAL frames. Conflict resolution is "last push wins."

### Pull (User App use case)

```python
changed = conn.pull()  # Returns True if anything changed locally
```

Pull fetches remote changes and applies them locally. If you have unpushed local changes during a pull, the engine:
1. Rolls back local DB to last synced state
2. Applies remote changes
3. **Replays** your unpushed local changes on top

This rollback-and-replay is atomic — if anything fails, the DB remains in its previous state.

### Checkpoint

```python
conn.checkpoint()  # Compacts WAL, bounds disk usage
```

Auto-checkpoint is **disabled** for sync databases — you must call `checkpoint()` explicitly. Recommended after bulk inserts or on a schedule.

### Stats

```python
s = conn.stats()
print(s.main_wal_size, s.revert_wal_size)
print(s.network_received_bytes, s.network_sent_bytes)
print(s.last_push_unix_time, s.last_pull_unix_time)
print(s.revision)
```

---

## Fit Analysis: pyturso vs Project Use Cases

### Use Case 1: Local Read Cache for User App

**Current (libsql):**
- `ReadOnlyClient` connects via `libsql.connect()` with read-only token
- `conn.sync()` is bidirectional — the "push" half is rejected by the server (read-only token), but the replicator still attempts it and can fail
- Auto-recovery requires deleting local DB files and re-syncing
- No insight into sync state (no stats)

**With pyturso:**
- `turso.sync.connect()` with read-only token
- `conn.pull()` — explicitly pull-only, no push attempt, no WAL conflict possible
- Built-in `stats()` for monitoring sync freshness
- `bootstrap_if_empty=False` enables true offline-first startup
- Partial sync option for faster cold starts on large databases

**Verdict:** **Excellent fit.** pyturso's pull-only model perfectly matches the User App's read-only consumer role. Eliminates all WAL-related sync failures.

### Use Case 2: Database Distribution (Admin → Turso → App)

**Current (libsql):**
- Admin writes locally, then `conn.sync()` tries to push AND pull simultaneously
- WAL frames from local writes conflict with remote frame injection → WalConflict
- Requires WAL checkpoint before sync as workaround
- Requires delete-and-re-sync for recovery
- No visibility into what was pushed/pulled

**With pyturso:**
- Admin writes locally, then `conn.push()` — explicitly push-only
- Logical CDC statements (not WAL frames) — no WalConflict possible
- "Last push wins" conflict resolution
- Built-in `stats()` shows last push time, network bytes, revision
- `checkpoint()` for WAL compaction after bulk operations

**Verdict:** **Excellent fit.** pyturso's push model natively implements the "publish" concept from `specs/simplify_turso_sync_publish_update.md` without any WAL hacks.

### Use Case 3: Future Multi-Admin

**Current (libsql):** WalConflict would become frequent with multiple admins writing independently between syncs.

**With pyturso:** "Last push wins" conflict resolution + rollback-and-replay during pull handles concurrent writes gracefully. However, there's no merge semantics — the last push wins, potentially overwriting another admin's changes. For this project's data model (song catalog), this is acceptable (catalog entries are append-mostly, and admins rarely edit the same row simultaneously).

**Verdict:** **Good fit.** Better than libsql for multi-admin, but no automatic merge. Acceptable for catalog data.

---

## Impact on Existing Specs

### `specs/fix_walconflict_sync_error_opus.md`

**6 changes proposed** (WalConflict recovery, remove pre-sync migrations, WAL checkpoint, CLI tip, app-side recovery).

**With pyturso:** All 6 changes become unnecessary. The WalConflict error is a libsql-specific artifact of WAL frame injection. pyturso uses logical CDC statements for sync, completely sidestepping WAL-level conflicts.

**Verdict:** Do not implement. Migrate to pyturso instead.

### `specs/simplify_turso_sync_publish_update.md`

**Proposes** replacing `db sync` with `db publish` (push) and `db update` (pull), using WAL checkpoint and delete-and-re-sync as pre-conditions to achieve directionality with libsql's bidirectional `conn.sync()`.

**With pyturso:** The goals are natively achieved:
- `db publish` → `conn.push()` (built-in, no WAL checkpoint needed)
- `db update` → `conn.pull()` after delete-and-reconnect (or simply `conn.pull()` if starting fresh)
- No WAL checkpoint before push (logical statements, not WAL frames)
- Delete-and-re-sync for recovery is simpler (just delete the .db file, pull will bootstrap)

**Verdict:** The spec's *goals* remain valid (explicit publish/update commands). The *implementation* changes — no WAL hacks, just call `push()` or `pull()`. The CLI UX design (publish/update commands, deprecation of sync) should still be implemented.

---

## Migration Assessment

### What Changes

| Component | Current | After Migration |
|---|---|---|
| **pyproject.toml** | `libsql>=0.1.0` | `pyturso>=0.5.1` |
| **Admin DB client** | `libsql.connect()` + `conn.sync()` | `turso.sync.connect()` + `conn.push()` / `conn.pull()` |
| **App DB client** | `libsql.connect()` + `conn.sync()` | `turso.sync.connect()` + `conn.pull()` |
| **Admin CLI** | `db sync` (bidirectional) | `db publish` (`push()`) + `db update` (`pull()`) |
| **App TUI** | `Shift+S` = `conn.sync()` | `Shift+S` = `conn.pull()` |
| **WAL checkpoint** | `PRAGMA wal_checkpoint(TRUNCATE)` before sync | `conn.checkpoint()` after bulk writes |
| **Recovery** | Delete DB + sidecar files + retry sync | Delete DB + `conn.pull()` (automatic bootstrap) |
| **Sync stats** | Manual `sync_metadata` table | Built-in `conn.stats()` |
| **Offline fallback** | `sqlite3` when `libsql` not available | `turso.connect()` (no sync) or `sqlite3` for non-sync DBs |

### What Stays the Same

| Component | Reason |
|---|---|
| **Schema (DDL)** | pyturso is SQLite-compatible |
| **SQL queries** | pyturso follows sqlite3 API |
| **Data models** | Unchanged |
| **songsets.db** | Local-only, stays on `sqlite3` |
| **Turso URL/auth tokens** | Same |
| **R2 storage** | Unrelated to DB sync |

### Migration Risks

| Risk | Severity | Mitigation |
|---|---|---|
| **pyturso is Beta** | Medium | Turso officially recommends it; rapid release cycle suggests active maintenance. Phase migration: App first (lower risk). |
| **File format incompatibility** | Low-Medium | pyturso claims SQLite file format support. However, existing libsql `.db` files with `.db-info` metadata sidecars cannot be reused — must re-bootstrap from Turso. This is equivalent to a fresh `db update`. |
| **API gaps vs sqlite3** | Low | pyturso follows sqlite3 interface. Missing: `row_factory` (need to verify). Admin CLI currently uses `sqlite3.Row` — check if pyturso supports it. |
| **Admin CLI sqlite3 fallback** | Low | When libsql is unavailable, admin falls back to sqlite3 (no sync). With pyturso, the fallback would be `turso.connect()` (no sync). This is a simpler fallback — no need for sqlite3 at all for the admin DB. |
| **No offline sync without pyturso** | Low | If pyturso can't be imported, no sync is possible (same as current libsql situation). Fallback is `turso.connect()` for local-only mode. |
| **SQL compatibility gaps** | Low-Medium | pyturso is a rewrite — some SQLite features may not yet be supported. Project uses basic SQL (CRUD, ALTER TABLE ADD COLUMN, PRAGMA). Check [compat status](https://github.com/tursodatabase/turso/blob/main/COMPAT.md). |

### Compatibility Concerns to Verify

1. **`row_factory = sqlite3.Row`**: Does pyturso support `row_factory`? The docs show standard tuple results. May need to use dict-like access or implement a wrapper.
2. **`PRAGMA table_info`**: Used by `apply_column_migrations()`. Verify pyturso supports this.
3. **`PRAGMA foreign_keys = ON`**: Verify support.
4. **`PRAGMA wal_checkpoint(TRUNCATE)`**: No longer needed for sync, but verify it still works for general WAL management.
5. **`PRAGMA integrity_check`**: Used by `db status`. Verify support.
6. **`ALTER TABLE ADD COLUMN`**: Core of schema migration. Verify support.
7. **`sqlite3.connect()` fallback**: Admin CLI needs to work without pyturso installed (for `admin` extra only). Options:
   - Keep `sqlite3` as fallback for local-only admin mode
   - Make pyturso required for admin sync features
   - Use `turso.connect()` (no sync) as fallback

---

## Proposed Migration Plan

### Phase 1: User App Migration (Lower Risk)

The User App is read-only and only needs `pull()`. This is the safest migration target.

1. Replace `libsql` with `pyturso` in `app` extra
2. Update `ReadOnlyClient` to use `turso.sync.connect()` + `conn.pull()`
3. Remove WAL conflict recovery code (not needed with pull)
4. Add `conn.checkpoint()` after bulk pull
5. Add `conn.stats()` integration for sync status display
6. Test with existing Turso database (fresh bootstrap)

**Expected code changes:** ~50-80 lines in `app/db/read_client.py` and `app/services/sync.py`

### Phase 2: Admin CLI Migration

The Admin CLI is the writer and needs `push()`. More complex due to:
- Current `sync()` flow with pre/post sync migrations
- WAL checkpoint workaround
- Recovery logic
- `db publish` / `db update` command restructure (per existing spec)

1. Replace `libsql` with `pyturso` in `app` extra (already done in Phase 1)
2. Keep `sqlite3` as fallback for `admin` extra (no sync mode)
3. Implement `DatabaseClient.publish()` using `conn.push()`
4. Implement `DatabaseClient.update()` using delete + `conn.pull()`
5. Remove `conn.sync()`, WAL checkpoint workaround, and pre-sync migration
6. Add `conn.checkpoint()` after bulk writes
7. Add `conn.stats()` for `db status` command
8. Deprecate `db sync` alias

**Expected code changes:** ~100-150 lines across `admin/db/client.py`, `admin/services/sync.py`, `admin/commands/db.py`

### Phase 3: Cleanup

1. Remove `libsql` dependency entirely from pyproject.toml
2. Remove WalConflict recovery code from both admin and app
3. Remove `.db-info` sidecar file handling (pyturso uses different metadata)
4. Update `apply_column_migrations()` to run only at init or post-pull (not pre-push)
5. Update AGENTS.md and documentation

---

## Cost-Benefit Summary

| Factor | Stay on libsql | Migrate to pyturso |
|---|---|---|
| **WAL conflict risk** | Ongoing (workarounds needed) | Eliminated |
| **Sync model** | Bidirectional (misaligned with architecture) | Explicit push/pull (aligned) |
| **Recovery complexity** | Delete + re-sync + WAL checkpoint | Delete + pull (simpler) |
| **Monitoring** | Manual metadata table | Built-in stats() |
| **Offline-first** | Fragile (offline: true) | Native (bootstrap_if_empty=False) |
| **Partial sync** | Not available | Available for faster cold starts |
| **Concurrent writes** | Not supported | MVCC |
| **Spec simplification** | Need both specs' workarounds | Specs' goals natively achieved |
| **SDK maturity** | Battle-tested | Beta (Alpha per PyPI) |
| **Migration effort** | None | ~2-3 days phased work |
| **Future multi-admin** | WAL conflicts worsen | "Last push wins" handles gracefully |

**Net assessment:** The migration is justified. The project's unidirectional data flow (Admin writes → Turso distributes → App reads) is fundamentally misaligned with libsql's bidirectional sync model. pyturso's explicit push/pull is the correct architectural fit. The Beta status is the primary risk, but it's mitigated by:
- Turso's official recommendation for new projects
- Active development (0.5.1 → 0.6.0 in RC)
- Phased migration (App first, Admin second)
- The project's data is re-creatable from Turso (if pyturso has a bug, re-bootstrap)
