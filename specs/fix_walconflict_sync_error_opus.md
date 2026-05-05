# Fix WalConflict Error During Turso Sync

**Date:** 2026-05-05
**Status:** Spec
**Related:** `specs/fix_walconflict_sync_error.md` (prior spec)

## Library

- **Python package:** `libsql` v0.1.11 (declared as `libsql>=0.1.0` in pyproject.toml)
- **Rust crate:** `libsql-0.9.22` (compiled to `libsql.cpython-311-darwin.so`)
- **API:** `libsql.connect(path, sync_url=..., auth_token=...)` returns a `Connection` with a `.sync()` method for bidirectional embedded replica replication
- **Error type:** `libsql.Error` (inherits from `Exception`)

## Problem

`sow_admin db sync` fails with:

```
2026-05-04T12:52:18.059857Z ERROR libsql::sync: insert error (frame=152) : WalConflict
Unexpected error: WAL frame insert conflict
```

The error originates from libsql's Rust replication injector (`libsql-0.9.22/src/sync.rs`). When the embedded replica calls `conn.sync()`, the Rust `Replicator` downloads WAL frames from the Turso primary and the `InjectorWal` inserts them into the local SQLite WAL. `WalConflict` means the injection failed because the local WAL's page state conflicts with the incoming remote frames.

### Error propagation chain (verified from native binary strings)

1. Rust `InjectorWal::insert_frames()` fails → returns `LIBSQL_INJECT_FATAL` (error code 200)
2. Rust raises `WalConflict` enum variant → formatted as `"sync error: WAL frame insert conflict"`
3. Python `conn.sync()` raises `libsql.Error` with that message
4. `client.py:214` catches via `except Exception as e` → `SyncError(f"Sync failed: {e}")`
5. Final error string: `"Sync failed: sync error: WAL frame insert conflict"`
6. `sync.py:237` catches `SyncError`, checks `error_msg` — **no match on any recovery condition**

## Root Cause Analysis

Three contributing factors, ordered by likelihood:

### Factor 1: Pre-sync `apply_column_migrations()` writes WAL frames outside the replication protocol

**File:** `src/stream_of_worship/admin/db/client.py:206-208`

```python
if tables_exist:
    apply_column_migrations(cursor)
    self.connection.commit()
```

This ALTER TABLE + commit creates local WAL frames that are **not part of Turso's replication log**. When `conn.sync()` then tries to inject remote frames, the local WAL has pages (from the ALTER TABLE) that conflict with the remote's expected page state. The replicator detects the mismatch and raises WalConflict.

### Factor 2: Accumulated un-checkpointed WAL frames from local writes

The admin CLI modifies data (inserts songs, updates recordings) between syncs, generating local WAL frames. When the remote also has changes to overlapping B-tree pages, the frame injection at sync time finds conflicting page versions. The libsql replicator's `InjectorWal` expects to apply remote frames in sequence — if local un-checkpointed frames modify the same pages, reconciliation fails.

### Factor 3: Local WAL divergence from any source

More generally, any situation where local writes occur without syncing, then the remote changes independently, creates divergent frame histories. The next `conn.sync()` push attempt conflicts because the server has frames at positions the client is also trying to write to.

## Current Recovery Gaps

`SyncService._execute_sync_with_recovery()` in `sync.py:203-287` handles two error patterns:

1. **Line 241:** `"malformed"` — backs up local DB → verifies Turso health → deletes local DB + sidecars → retries sync
2. **Line 275:** `"metadata file does not"` / `"metadata is missing"` / `"not a database"` — deletes local DB + sidecars → retries sync

**Neither condition matches WalConflict.** The error falls through to line 285: `raise SyncNetworkError(f"Sync failed: {e}")` — no auto-recovery. The user gets a cryptic error.

## Plan

### Fix 1: Add WalConflict to auto-recovery conditions (sync.py)

**File:** `src/stream_of_worship/admin/services/sync.py`
**Location:** `_execute_sync_with_recovery()`, insert new block after the "malformed" recovery (line 272) and before the "metadata" recovery (line 274)

```python
# Auto-recovery for WAL frame conflict (local WAL diverged from remote)
if (
    "wal" in error_msg.lower() and "conflict" in error_msg.lower()
) and attempt < max_attempts:
    client.close()

    if not self._verify_turso_health():
        raise SyncNetworkError(
            "Cannot auto-recover from WAL conflict: Turso remote appears unhealthy or empty. "
            "Manual intervention required. "
            f"Original error: {e}"
        )

    backup_dir = self._backup_local_db()
    self._delete_local_db()

    try:
        return self._execute_sync_with_recovery(
            attempt=attempt + 1, max_attempts=max_attempts
        )
    except Exception as retry_err:
        raise SyncNetworkError(
            f"WAL conflict recovery sync failed. "
            f"Backup saved at: {backup_dir}. "
            f"To restore: cp {backup_dir}/sow.db {self.db_path}. "
            f"Error: {retry_err}"
        )
```

**String matching rationale:** The known error string after wrapping is `"Sync failed: sync error: WAL frame insert conflict"`. Checking for both `"wal"` and `"conflict"` as substrings (case-insensitive) matches this and also covers the Rust enum name `"WalConflict"` if it appears in future error formats. The dual-substring check avoids false positives — no other libsql error contains both words (verified by scanning all error variants in the native binary: `SqliteFailure`, `DatabaseBusy`, `DatabaseCorrupt`, `ConstraintViolation`, etc.).

**Why separate block (not merged with "malformed"):** Different root causes deserve distinct error messages for diagnostics, and the "malformed" block is already tested — touching it adds risk for no gain.

### Fix 2: Remove pre-sync `apply_column_migrations()` (client.py)

**File:** `src/stream_of_worship/admin/db/client.py`
**Location:** Lines 206-208

Remove the pre-sync migration call that writes WAL frames before `conn.sync()`:

**Current flow:**
```python
if tables_exist:
    # integrity check...
    apply_column_migrations(cursor)  # ← writes WAL frames, causes conflict
    self.connection.commit()

conn.sync()

apply_column_migrations(cursor)  # post-sync (already exists)
self.connection.commit()
```

**New flow:**
```python
if tables_exist:
    # integrity check only — NO schema writes before sync

conn.sync()

apply_column_migrations(cursor)  # post-sync handles all migrations
self.connection.commit()
```

**Why this is safe:** `apply_column_migrations()` uses `ALTER TABLE ADD COLUMN` which is idempotent (checks `PRAGMA table_info` before altering). Running it only post-sync is equivalent — the column values are NULL for existing rows regardless of when the ALTER runs. Remote frames that reference new columns work correctly because frame injection and column migration are independent operations.

### Fix 3: Add WAL checkpoint before sync (client.py)

**File:** `src/stream_of_worship/admin/db/client.py`
**Location:** Before the `conn.sync()` call (line 212)

```python
try:
    cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
except Exception:
    pass  # Non-fatal: may fail if DB locked by another process
```

**Why TRUNCATE:** Writes all WAL frames into the main DB file and resets the WAL to empty. The replicator's `InjectorWal` starts with a clean slate — no conflicting pages. PASSIVE only checkpoints non-conflicting frames, leaving potential conflicts behind.

**Why non-fatal:** If another process (e.g., the TUI app) has the DB open, checkpoint returns BUSY. Sync proceeds with the existing WAL; Fix 1 provides fallback recovery.

### Fix 4: Add WalConflict-specific CLI error tip (db.py)

**File:** `src/stream_of_worship/admin/commands/db.py`
**Location:** Error handler in `sync_db` (around lines 540-554)

Add a WalConflict-specific hint alongside existing tips for permission errors and metadata errors:

```python
elif "wal" in error_msg and "conflict" in error_msg:
    console.print("\n[yellow]Tip: Local WAL has conflicting changes with Turso.[/yellow]")
    console.print("[dim]Try running 'sow-admin db sync' again (auto-recovery will fix it).[/dim]")
    console.print("[dim]If it persists, reset: rm -rf ~/.config/sow-admin/db/sow.db* && sow-admin db sync[/dim]")
```

### Fix 5: Add WalConflict recovery to app-side sync

**File:** `src/stream_of_worship/app/db/read_client.py`
**Location:** `sync()` method (lines 116-129)

The app's `ReadOnlyClient` has no recovery logic — any sync exception raises `SyncError`. For WalConflict, the app should delete local DB files and retry, similar to the admin recovery pattern.

**File:** `src/stream_of_worship/app/services/sync.py`
**Location:** `AppSyncService.execute_sync()` (lines 212-257)

Add WalConflict detection to the app-side sync error handling with the same backup-delete-retry pattern.

## Files to Modify

| File | Changes |
|------|---------|
| `src/stream_of_worship/admin/services/sync.py` | Add WalConflict recovery block (~20 lines) |
| `src/stream_of_worship/admin/db/client.py` | Remove pre-sync migrations, add WAL checkpoint (~5 lines net) |
| `src/stream_of_worship/admin/commands/db.py` | Add WalConflict CLI error tip (~5 lines) |
| `src/stream_of_worship/app/services/sync.py` | Add WalConflict recovery (~15 lines) |
| `src/stream_of_worship/app/db/read_client.py` | Add WalConflict recovery in sync (~10 lines) |
| `tests/admin/services/test_sync.py` | Add WalConflict recovery tests (~80 lines) |

## Files NOT Modified

| File | Why |
|------|-----|
| `src/stream_of_worship/admin/db/schema.py` | `apply_column_migrations()` is unchanged — the fix is *when* it's called, not what it does |

## Implementation Order

1. **Fix 1** (WalConflict recovery in sync.py) — highest impact, prevents user-facing failure
2. **Fix 2** (remove pre-sync migrations) — eliminates the primary WAL conflict trigger
3. **Fix 3** (WAL checkpoint) — defense in depth
4. **Fix 4** (CLI error tip) — UX improvement
5. **Fix 5** (app-side recovery) — same pattern applied to the app

Fixes 1-3 form the core solution and should be implemented together. Fixes 4-5 can follow.

## Test Plan

### Unit Tests (`tests/admin/services/test_sync.py`)

| Test | What it verifies |
|------|-----------------|
| WalConflict triggers recovery, retry succeeds | Mock `client.sync()` → raise `SyncError("Sync failed: WAL frame insert conflict")` first call, succeed second. Assert backup + delete + retry called. |
| WalConflict with unhealthy Turso aborts recovery | Mock `_verify_turso_health` → `False`. Assert raises `SyncNetworkError("Cannot auto-recover")`, no backup/delete. |
| WalConflict retry also fails | Both sync calls raise WAL error. Assert error message includes backup path. |
| Case-insensitive "WalConflict" matches | Mock error as `"WalConflict"` (no spaces). Assert recovery triggers — both "wal" and "conflict" are substrings. |

Follow existing patterns: `@patch.object(DatabaseClient, "sync")`, `@patch.object(SyncService, "_verify_turso_health")`, etc.

### Manual Verification

```bash
# Run sync service tests
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/services/test_sync.py -v

# Run all admin tests for regressions
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/ -v

# End-to-end: trigger and recover from WalConflict
uv run --extra admin sow-admin db sync
```

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Post-sync migration misses columns remote frames reference | Can't happen: remote frames reference columns in remote schema, which matches our DDL. Frame injection modifies row data; column migration modifies schema — independent operations. |
| WAL TRUNCATE checkpoint fails if DB locked | `try/except` makes it non-fatal. Fix 1 provides fallback recovery. |
| Auto-recovery deletes local DB with unpushed changes | `_backup_local_db()` creates timestamped backup first. `_verify_turso_health()` confirms Turso has data before deletion. Both safeguards already exist. |
