# Fix WalConflict Error During Turso Sync

**Date:** 2026-05-04
**Status:** Spec
**Predecessor:** `specs/fix_schema_sync_corruption_v3.md`, `specs/fix_apply_column_migrations_libsql_error.md`, `specs/fix_db_init_turso_compliant.md`

## Problem

`sow_admin db sync` fails with:

```
2026-05-04T12:52:18.059857Z ERROR libsql::sync: insert error (frame=152) : WalConflict
Unexpected error: WAL frame insert conflict
```

The error originates from libsql's replication injector. When the embedded replica calls `conn.sync()`, the Rust replicator downloads WAL frames from the Turso primary and injects them into the local SQLite WAL. `WalConflict` means the injection failed because the local WAL's page state conflicts with the incoming remote frames.

This project uses **libSQL** (Python package `libsql` v0.1.11, a SQLite-fork with embedded replica support compiled as a native `.so` module). It does NOT use the new Rust-based Turso Database, which is a separate ground-up rewrite that supports concurrent writes natively.

## Root Cause Analysis

There are **three contributing factors**, each sufficient to cause WalConflict on its own. In practice, they compound.

### Factor 1: Pre-sync `apply_column_migrations()` writes local WAL frames outside the replication protocol

**File:** `src/stream_of_worship/admin/db/client.py:206-208`

```python
if tables_exist:
    apply_column_migrations(cursor)
    self.connection.commit()
```

This ALTER TABLE + commit creates local WAL frames that are **not part of Turso's replication log**. When `conn.sync()` then tries to inject remote frames, the local WAL has pages (from the ALTER TABLE) that conflict with the remote's expected page state. The replicator detects the mismatch and raises WalConflict.

This is the **most likely trigger** for the observed error. The admin CLI modifies data (inserts songs, updates recordings, etc.), generating local WAL frames. Then `apply_column_migrations()` adds more WAL frames. When sync pulls remote frames that touch the same B-tree pages, the conflict surfaces.

### Factor 2: Accumulated un-checkpointed WAL frames

**Evidence:** `PRAGMA wal_checkpoint(PASSIVE)` returned `(0, 183, 183)` — 183 un-checkpointed frames, meaning significant local changes since last sync ~2 hours prior.

The WAL had grown to 4.1MB. Each local write (INSERT, UPDATE) creates WAL frames that modify B-tree pages. When the remote also has changes to overlapping pages (e.g., both admin and another client updated the same table), the frame injection at sync time finds conflicting page versions.

The libsql replicator uses a custom WAL implementation (`InjectorWal`) that intercepts SQLite's WAL write path. It expects to apply remote frames in sequence. If the local WAL already has uncommitted/non-checkpointed frames for the same pages, the injector cannot reconcile them and raises WalConflict.

### Factor 3: `sync_version` mismatch — local "1" vs code default "2"

**Evidence:** Local `sync_metadata` table has `sync_version: 1`, but `DEFAULT_SYNC_METADATA` in schema.py specifies `"sync_version": "2"`.

While the `sync_version` value is not directly read by the sync protocol itself, the mismatch indicates the local DB was created by an older `db init` flow that never had `sync_version` properly updated. This is a symptom of the broader metadata drift problem — the local DB's replication state may be inconsistent with what Turso expects, contributing to WAL frame ordering issues during replication.

## How libsql WalConflict Works (Internal)

The error originates in the Rust libsql replication pipeline:

1. **`Replicator`** (`libsql-replication/src/replicator.rs`) downloads WAL frames from the primary
2. **`SqliteInjector`** (`libsql-replication/src/injector/sqlite_injector/mod.rs`) passes frames to the custom WAL
3. **`InjectorWal`** (`libsql-replication/src/injector/sqlite_injector/injector_wal.rs`) implements `insert_frames()` — intercepts normal SQLite WAL writes to insert replicated frames instead
4. If the injection fails (conflicting page, sequence mismatch, or concurrent writer), `InjectorWal` returns `LIBSQL_INJECT_FATAL` (error code 200)
5. This surfaces as `WalConflict` at the Python API level

SQLite's single-writer model means the replicator and application **cannot both write simultaneously**. If the application has pending writes in the WAL when the replicator tries to inject frames, the conflict is inevitable.

## Current Recovery Gaps

The `SyncService._execute_sync_with_recovery()` method in `sync.py:203-288` has two recovery conditions:

1. **Line 241:** `"malformed" in error_msg.lower()` — handles corrupted local DB
2. **Line 275:** `"metadata file does not" in error_msg.lower()` — handles missing libsql metadata

**Neither condition matches `WalConflict` or `"WAL frame insert conflict"`.** The error propagates as an unhandled `SyncNetworkError` with no auto-recovery. The user gets a cryptic error message and must manually delete their local DB.

Additionally, the CLI error handler in `db.py:540-554` only provides helpful tips for write-permission errors and metadata errors — not for WalConflict.

## Immediate Workaround (Already Applied)

A WAL checkpoint clears the accumulated frames:

```python
import sqlite3
conn = sqlite3.connect('/path/to/sow.db')
conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
conn.close()
```

After checkpointing, `sow_admin db sync` succeeds because the local WAL is empty — no conflicting pages for the replicator to encounter.

However, this is only a temporary fix. The next sync will accumulate WAL frames again, and if pre-sync migrations run or local writes overlap with remote changes, WalConflict recurs.

## Plan

### Fix 1: Add WalConflict to auto-recovery conditions

**File:** `src/stream_of_worship/admin/services/sync.py`

In `_execute_sync_with_recovery()`, expand the "malformed" recovery condition to also match WalConflict errors:

**Current (line 241):**
```python
if "malformed" in error_msg.lower() and attempt < max_attempts:
```

**New:**
```python
if (
    "malformed" in error_msg.lower()
    or "walconflict" in error_msg.replace(" ", "").lower()
    or "wal frame insert conflict" in error_msg.lower()
) and attempt < max_attempts:
```

The same recovery logic applies: verify Turso health → backup local DB → delete local DB → retry sync from scratch. When the local WAL is in a conflicting state, nuking and re-pulling from Turso is the correct recovery.

**Why `replace(" ", "")`:** The error string from libsql may be `"WalConflict"` (no space) or `"WAL frame insert conflict"` (with spaces). Normalizing whitespace handles both.

### Fix 2: Move `apply_column_migrations()` to post-sync only

**File:** `src/stream_of_worship/admin/db/client.py`

The pre-sync `apply_column_migrations()` at line 206-208 writes ALTER TABLE WAL frames that conflict with the replication protocol. Move this to **post-sync only** (it already exists post-sync at line 218-219):

**Current flow (lines 164-226):**
```python
if tables_exist:
    # PRE-SYNC: integrity check + migrations ← PROBLEM: writes WAL frames
    cursor.execute("PRAGMA integrity_check")
    ...
    apply_column_migrations(cursor)
    self.connection.commit()

# Sync with Turso
conn.sync()

# POST-SYNC: re-apply migrations + push back
apply_column_migrations(cursor)
self.connection.commit()
conn.sync()  # push new columns back to Turso
```

**New flow:**
```python
if tables_exist:
    # PRE-SYNC: integrity check only (NO schema writes)
    cursor.execute("PRAGMA integrity_check")
    ...
    # Do NOT apply_column_migrations here — it writes WAL frames
    # that conflict with the replication protocol

# Checkpoint WAL before sync to reduce page conflicts
try:
    cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
except Exception:
    pass

# Sync with Turso (no conflicting local WAL frames)
conn.sync()

# POST-SYNC: apply migrations + validate + push back
apply_column_migrations(cursor)
self.connection.commit()
self._validate_schema(cursor)

# Update sync version to current code version
current_version = DEFAULT_SYNC_METADATA.get("sync_version", "2")
cursor.execute(
    "UPDATE sync_metadata SET value = ? WHERE key = 'sync_version' AND value != ?",
    (current_version, current_version),
)

# Push new columns back to Turso so remote schema stays current
try:
    conn.sync()
except Exception:
    pass  # Non-fatal: local is correct, remote will get updated next sync

# Update last sync timestamp
self.update_sync_metadata("last_sync_at", datetime.now().isoformat())
```

**Why this is safe:** The `apply_column_migrations()` function uses `ALTER TABLE ADD COLUMN` which is idempotent (catches `OperationalError` / `libsql.Error` for duplicate columns). Running it post-sync is equivalent to running it pre-sync — the only difference is it doesn't create conflicting WAL frames before the replication injection.

**Risk assessment:** If a migration is needed for data that Turso will send during sync, the column won't exist locally when the remote frames reference it. However, SQLite ALTER TABLE ADD COLUMN **only adds columns to the schema**, not data — the column values are NULL for existing rows. Remote INSERT/UPDATE frames that reference the new column will work correctly as long as the column exists in the schema. Since migrations only add columns that are already in the remote schema (or should be), running them post-sync is safe.

**Edge case: first sync (no tables).** This path skips pre-sync entirely (`tables_exist` is False), syncs from Turso (pulls schema), then runs post-sync migrations. This already works correctly.

### Fix 3: Add WAL checkpoint before sync

**File:** `src/stream_of_worship/admin/db/client.py`

Before calling `conn.sync()`, checkpoint the WAL to minimize the chance of page conflicts between local and remote frames:

```python
try:
    cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
except Exception:
    pass  # Non-fatal: checkpoint may fail if there are active readers
```

**Why:** A full TRUNCATE checkpoint writes all WAL frames into the main DB file and resets the WAL. This means the replicator's `InjectorWal` starts with a clean slate — the local WAL is empty, so there are no conflicting pages. The tradeoff is that checkpointing takes time proportional to the WAL size, but for a ~4MB WAL, this is negligible (<100ms).

**Why TRUNCATE vs PASSIVE:** PASSIVE only checkpoints frames that don't conflict with active readers, leaving some frames in the WAL. TRUNCATE ensures the WAL is fully cleared, eliminating all possible frame conflicts. Since `db sync` is an explicit user action (not real-time), the delay is acceptable.

**Risk:** If another process has the DB open (e.g., the TUI app), the checkpoint will return BUSY. The `try/except` handles this gracefully — sync proceeds with the existing WAL. The WalConflict recovery (Fix 1) provides a fallback.

### Fix 4: Add WalConflict-specific error message in CLI

**File:** `src/stream_of_worship/admin/commands/db.py`

In the `sync_db` command's error handler (lines 540-554), add a WalConflict-specific tip:

```python
except SyncNetworkError as e:
    error_msg = str(e).lower()
    console.print(f"\n[red]Network error: {e}[/red]")
    if e.status_code:
        console.print(f"Status code: {e.status_code}")

    if "write" in error_msg and ("forbidden" in error_msg or "blocked" in error_msg or "permission" in error_msg):
        # ... existing tip ...
    elif "wal conflict" in error_msg or "walconflict" in error_msg.replace(" ", ""):
        console.print("\n[yellow]Tip: Local database WAL has conflicting changes with Turso.[/yellow]")
        console.print("[yellow]This happens when local writes overlap with remote changes.[/yellow]")
        console.print("[dim]Try running 'sow-admin db sync' again (auto-recovery will attempt to fix it).[/dim]")
        console.print("[dim]If it persists, reset the local database:[/dim]")
        console.print("  [dim]rm -rf ~/.config/sow-admin/db/sow.db*[/dim]")
        console.print("  [dim]sow-admin db sync[/dim]")
    elif "metadata file" in error_msg:
        # ... existing tip ...
```

### Fix 5: Update `sync_version` during post-sync validation

**File:** `src/stream_of_worship/admin/db/client.py`

After a successful sync, ensure `sync_version` is updated to match the code's expected version:

```python
current_version = DEFAULT_SYNC_METADATA.get("sync_version", "2")
cursor.execute(
    "UPDATE sync_metadata SET value = ? WHERE key = 'sync_version' AND value != ?",
    (current_version, current_version),
)
```

This prevents drift between the local metadata and the code's expectations. It's a minor fix but eliminates one source of inconsistency.

### Fix 6: Add WalConflict recovery to app-side sync

**File:** `src/stream_of_worship/app/services/sync.py`

The app-side `AppSyncService` has a similar sync-with-recovery pattern. Add the same WalConflict recovery condition to its error handling.

**File:** `src/stream_of_worship/app/db/read_client.py`

The app's `ReadOnlyClient.sync()` method (line 116-129) raises SyncError on any exception. It should also detect WalConflict and attempt the same recovery: delete local DB files → retry sync from Turso.

## Test Plan

### Unit Tests

1. **WalConflict recovery in SyncService:** Mock `client.sync()` to raise `SyncError("WAL frame insert conflict")`, verify `_execute_sync_with_recovery()` enters the recovery path (backs up, deletes, retries).

2. **Malformed recovery still works:** Verify existing "malformed" recovery still functions after the expanded condition.

3. **No pre-sync migrations:** Verify `DatabaseClient.sync()` does not call `apply_column_migrations()` before `conn.sync()`. Mock `apply_column_migrations` and `conn.sync()`, verify call order.

4. **Post-sync migrations run:** Verify `apply_column_migrations()` IS called after `conn.sync()`.

5. **WAL checkpoint before sync:** Verify `PRAGMA wal_checkpoint(TRUNCATE)` is called before `conn.sync()`.

### Integration Tests

6. **End-to-end sync with local writes:** Insert songs/recordings → call `db sync` → verify no WalConflict. This requires a test Turso database.

7. **Recovery from WalConflict:** Force a local WAL conflict (write to local DB outside libsql while sync runs) → verify auto-recovery succeeds.

8. **sync_version update:** After sync, verify `sync_metadata.sync_version` matches `DEFAULT_SYNC_METADATA["sync_version"]`.

## Files to Modify

| File | Changes |
|------|---------|
| `src/stream_of_worship/admin/services/sync.py` | Add WalConflict to recovery conditions (Fix 1) |
| `src/stream_of_worship/admin/db/client.py` | Remove pre-sync migrations (Fix 2), add WAL checkpoint (Fix 3), update sync_version (Fix 5) |
| `src/stream_of_worship/admin/commands/db.py` | Add WalConflict error tip in CLI (Fix 4) |
| `src/stream_of_worship/app/services/sync.py` | Add WalConflict recovery (Fix 6) |
| `src/stream_of_worship/app/db/read_client.py` | Add WalConflict recovery in sync (Fix 6) |

## Files NOT Modified

| File | Why |
|------|-----|
| `src/stream_of_worship/admin/db/schema.py` | `COLUMN_MIGRATIONS` and `apply_column_migrations()` are unchanged — the fix is in when they're called, not what they do |
| `src/stream_of_worship/admin/db/models.py` | No model changes |

## Order of Implementation

1. **Fix 1** (WalConflict recovery) — highest impact, prevents data loss
2. **Fix 2** (move migrations to post-sync) — eliminates the primary cause
3. **Fix 3** (WAL checkpoint) — defense in depth, reduces conflict probability
4. **Fix 4** (CLI error message) — improves user experience for remaining edge cases
5. **Fix 5** (sync_version update) — minor consistency fix
6. **Fix 6** (app-side recovery) — same pattern as Fix 1, applied to app

Fixes 1-3 should be implemented and tested together as they form the core solution. Fixes 4-6 can follow incrementally.

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Post-sync migration may miss columns that remote frames reference | This can't happen if remote schema matches local DDL. Remote frames reference columns that exist in the remote schema; if those columns are in our DDL, they were either created by `initialize_schema()` or will be added by post-sync `apply_column_migrations()`. The frame injection and column addition are independent operations — frame injection modifies row data, column migration modifies schema. |
| WAL TRUNCATE checkpoint may fail if DB is locked by another process | `try/except` makes it non-fatal. Sync proceeds with existing WAL. WalConflict recovery provides fallback. |
| Auto-recovery deletes local DB with unpushed changes | `_backup_local_db()` creates a timestamped backup before deletion. `_verify_turso_health()` ensures Turso has data before nuking local. These safeguards already exist. |
| Removing pre-sync migrations breaks the "existing replica" path | Pre-sync migrations were a safety net for schema drift. Post-sync migrations + `_validate_schema()` provide the same safety net. The only scenario where pre-sync was needed was when the local schema was behind the code but ahead of Turso — but this means the code expected columns that Turso didn't have, which is itself a bug (Turso should always have the latest schema). |
