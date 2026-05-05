# Simplify Turso Sync: Explicit Publish/Update Model

**Date:** 2026-05-05
**Status:** Spec
**Supersedes:** `specs/fix_walconflict_sync_error.md`, `specs/fix_walconflict_sync_error_opus.md`

## Problem Statement

The current `sow-admin db sync` command uses libsql's bidirectional `conn.sync()` which simultaneously pushes local changes AND pulls remote changes in a single operation. This design causes recurring failures:

1. **WalConflict errors** — pre-sync `apply_column_migrations()` writes local WAL frames that conflict with Turso's replication frames when `conn.sync()` tries to inject remote frames
2. **Metadata corruption** — `"db file exists but metadata file does not"` errors on both admin and app sides
3. **Confusing mental model** — user doesn't know if sync is pushing, pulling, or both; recovery requires manual `rm -rf` of local DB files

### Root Cause: Bidirectional Sync is Wrong for Single-Admin Architecture

The project has a clear unidirectional data flow:

```
Admin (single writer) → Turso (distribution hub) → App users (read-only consumers)
```

Using bidirectional sync for this topology creates unnecessary complexity:
- The admin never needs to pull (except for recovery/fresh-setup)
- The app never needs to push (uses read-only token)
- Yet both sides call `conn.sync()` which attempts both directions, creating frame conflicts

### Why the WAL Error Happened (answering user's question)

The user ran `sow-admin audio status --reconcile` and `sow-admin catalog scrape` before syncing. Both operations write to the local SQLite database (INSERT/UPDATE on songs and recordings tables). These writes generate WAL frames in the local `.wal` file. Then:

1. `client.py:207` runs `apply_column_migrations()` + commit — more WAL frames
2. `client.py:213` calls `conn.sync()` — libsql's Rust replicator tries to inject remote WAL frames
3. The remote frames conflict with local un-checkpointed WAL pages → `WalConflict`

The fundamental issue: **any local writes between syncs can cause WAL conflicts** when the replicator tries to reconcile frame histories. The pre-sync migration makes it worse but isn't the only trigger.

## Design: Explicit Directional Commands

### Principle

Replace ambiguous "sync" with two explicit, unidirectional operations:

| Command | Direction | Analogy |
|---------|-----------|---------|
| `sow-admin db publish` | Local → Turso | `git push` |
| `sow-admin db update` | Turso → Local | `git clone` / `git pull --force` |

### libsql Constraint

`conn.sync()` is always bidirectional (no parameters, no push-only or pull-only mode). We achieve directionality through pre-conditions:

- **Publish**: Checkpoint WAL first → `conn.sync()`. Since admin is the only Turso writer, the "pull" half of sync is a no-op (nothing new on remote). The "push" half sends local changes to Turso.
- **Update**: Delete local DB first → `conn.sync()`. With no local state, the "push" half is a no-op. The "pull" half downloads everything from Turso.

---

## Detailed Design

### Command: `sow-admin db publish`

**Purpose:** Push local database state to Turso. This is the day-to-day command after making local changes.

**Flow:**

```python
def publish(self) -> None:
    """Push local changes to Turso."""
    cursor = self.connection.cursor()

    # 1. Ensure schema is current before publishing
    apply_column_migrations(cursor)
    self.connection.commit()

    # 2. Checkpoint WAL — collapse all frames into main DB file
    #    This gives conn.sync() a clean starting point with no conflicting frames
    try:
        cursor.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass  # Non-fatal: may fail if locked

    # 3. Sync (pushes local state to Turso; pull is no-op for single admin)
    try:
        conn = self.connection
        conn.sync()
    except Exception as e:
        raise SyncError(f"Publish failed: {e}", cause=e)

    # 4. Update metadata
    self.update_sync_metadata("last_sync_at", datetime.now().isoformat())
    self.update_sync_metadata("last_publish_at", datetime.now().isoformat())
```

**Key differences from current `sync()`:**
- No integrity check (that's a separate concern; run via `db status`)
- Migrations run AFTER checkpoint, not before `conn.sync()` — WAL is clean when sync starts
- Single `conn.sync()` call (not two)
- No post-sync schema validation (publish is authoritative — local is truth)

**CLI UX:**

```
$ sow-admin db publish

Database: ~/.config/sow-admin/db/sow.db
Turso URL: libsql://sow-catalog-mhuang.aws-us-west-2.turso.io
Last publish: 2026-05-04 09:15:00

Publishing to Turso...

✓ Published successfully
  Songs: 245 | Recordings: 312
  Published at: 2026-05-05 10:30:00
```

### Command: `sow-admin db update`

**Purpose:** Pull fresh state from Turso, replacing local database. Used for fresh setup, recovery, or getting the last-published version after corruption.

**Flow:**

```python
def update(self, force: bool = False) -> None:
    """Pull fresh state from Turso, replacing local database."""
    
    if self.db_path.exists() and not force:
        # Check for unpublished local changes
        # (compare local last_modify time vs last_publish_at metadata)
        raise SyncError(
            "Local database exists. Use --force to overwrite, "
            "or run 'db publish' first to push local changes to Turso."
        )

    # 1. Backup existing local DB (if any)
    backup_dir = self._backup_local_db() if self.db_path.exists() else None

    # 2. Delete local DB + all sidecar files
    self._delete_local_db()

    # 3. Fresh connection (no local state)
    self._connection = None  # Reset cached connection
    conn = self.connection   # Creates new libsql connection

    # 4. Sync (pulls from Turso; push is no-op since local is empty)
    try:
        conn.sync()
    except Exception as e:
        raise SyncError(f"Update failed: {e}", cause=e)

    # 5. Apply migrations post-pull (ensure local schema is complete)
    cursor = conn.cursor()
    apply_column_migrations(cursor)
    conn.commit()

    # 6. Update metadata
    self.update_sync_metadata("last_sync_at", datetime.now().isoformat())
```

**CLI UX:**

```
$ sow-admin db update

Database: ~/.config/sow-admin/db/sow.db
Turso URL: libsql://sow-catalog-mhuang.aws-us-west-2.turso.io

⚠ Local database exists (last published: 2026-05-04 09:15:00)
  This will replace it with the version from Turso.
  Backup will be saved to: ~/.config/sow-admin/db/backups/

Continue? [y/N]: y

Pulling from Turso...

✓ Updated successfully
  Songs: 245 | Recordings: 312
  Backup saved: ~/.config/sow-admin/db/backups/2026-05-05_103000/
```

### Command: `sow-admin db sync` (deprecated alias)

Keep `db sync` as an alias for `db publish` with a deprecation notice:

```
$ sow-admin db sync

[dim]Note: 'db sync' is now 'db publish'. Use 'db publish' to push changes to Turso,
or 'db update' to pull from Turso.[/dim]

Publishing to Turso...
✓ Published successfully
```

### Auto-Recovery (Both Commands)

If `publish` fails with a WAL-related error, offer automatic recovery:

```python
# In execute_publish() service method
except SyncError as e:
    error_msg = str(e).lower()
    if any(pattern in error_msg for pattern in [
        "wal" and "conflict",  # WalConflict
        "metadata file does not",  # metadata corruption
        "invalid local state",  # metadata corruption
        "malformed",  # DB corruption
    ]):
        # Auto-recovery: checkpoint failed to resolve conflict
        # Offer to do update (delete + re-pull) then re-publish
        ...
```

### App-Side Sync (User App)

The app uses a read-only token and only pulls. The fix is simple auto-recovery:

**`ReadOnlyClient.sync()` — enhanced with recovery:**

```python
def sync(self) -> None:
    """Sync with Turso (pull-only via read-only token)."""
    if not self.is_turso_enabled:
        raise SyncError("Turso sync is not configured")

    try:
        conn = self.connection
        conn.sync()
    except Exception as e:
        error_msg = str(e).lower()
        # Auto-recover from any state corruption by re-pulling fresh
        if any(p in error_msg for p in [
            "wal", "metadata", "malformed", "invalid local state", "not a database"
        ]):
            self._recover_and_retry()
        else:
            raise SyncError(f"Sync failed: {e}", cause=e)

def _recover_and_retry(self) -> None:
    """Delete local DB and re-pull from Turso."""
    self.close()
    # Delete DB + sidecars
    for suffix in ["", "-shm", "-wal", "-journal", "-info"]:
        path = Path(str(self.db_path) + suffix)
        if path.exists():
            path.unlink()
    # Re-connect and sync fresh
    self._connection = None
    try:
        conn = self.connection
        conn.sync()
    except Exception as e:
        raise SyncError(f"Recovery sync failed: {e}", cause=e)
```

**`AppSyncService.execute_sync()` — no changes needed** since recovery is handled in `ReadOnlyClient.sync()`.

---

## Files to Modify

| File | Changes |
|------|---------|
| `src/stream_of_worship/admin/db/client.py` | Add `publish()` and `update()` methods; keep `sync()` as alias for `publish()` |
| `src/stream_of_worship/admin/services/sync.py` | Add `execute_publish()` and `execute_update()` with recovery; keep `execute_sync()` as alias |
| `src/stream_of_worship/admin/commands/db.py` | Add `publish` and `update` commands; make `sync` print deprecation note and call publish |
| `src/stream_of_worship/app/db/read_client.py` | Add `_recover_and_retry()` method; use it in `sync()` error path |
| `src/stream_of_worship/app/services/sync.py` | No changes (recovery handled in read_client) |
| `tests/admin/services/test_sync.py` | Add tests for publish, update, WAL recovery |

## Files NOT Modified

| File | Reason |
|------|--------|
| `admin/db/schema.py` | `apply_column_migrations()` unchanged — we change *when* it's called, not what it does |
| `admin/commands/infra.py` | `turso-init` is a one-time bootstrap, unrelated to daily sync |
| `app/config.py`, `admin/config.py` | Config structure unchanged |

## Implementation Order

1. `admin/db/client.py` — add `publish()` and `update()` methods
2. `admin/services/sync.py` — add service methods with recovery
3. `admin/commands/db.py` — add CLI commands
4. `app/db/read_client.py` — add auto-recovery
5. Tests
6. Manual verification: `sow-admin db publish`, `sow-admin db update`, `sow-app db sync`

## Migration Path

- `db sync` continues to work (aliased to `db publish`) — no breaking change
- Print one-line deprecation note when `db sync` is used
- Existing scripts and documentation can be updated at leisure

## Answers to User's Questions

### Q: Why did I hit WAL error as the only admin?

Because `conn.sync()` is bidirectional even for a single writer. Your local writes (from `status --reconcile`, `catalog scrape`, and the pre-sync `apply_column_migrations()`) created WAL frames. When `conn.sync()` tried to also *pull* from Turso (even though nothing new was there), the replicator's frame injection logic detected page-level conflicts between your local WAL and the remote frame sequence. This is a design flaw in using bidirectional sync for a unidirectional workflow.

### Q: Is the current flow too complicated?

Yes. The current `client.py:sync()` does: integrity check → pre-sync migrations → commit → `conn.sync()` → post-sync migrations → commit → second `conn.sync()` → schema validation → metadata update. That's **two sync calls and two migration passes** in a single operation. This complexity exists to handle the bidirectional case (remote might have fewer columns), but since you're the only writer, local is always authoritative. One checkpoint + one sync is sufficient.

### Q: Does it follow Turso guidelines?

Partially. Turso's embedded replica model is designed for read-heavy replicas that occasionally sync. The pattern of writing locally then syncing is supported but fragile — libsql's WAL injection assumes the local replica hasn't diverged significantly from the remote frame sequence. The recommended pattern for a primary writer is to either (a) sync frequently with minimal local WAL accumulation, or (b) use the Turso HTTP API for writes and only use embedded replicas for reads. Our new `publish` with WAL checkpoint before sync aligns with option (a).

### Q: Should there be explicit publish/update?

Yes — this spec implements exactly that. `publish` = push local to Turso (one-way), `update` = pull fresh from Turso (one-way). Direction is always explicit and unambiguous.

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| WAL checkpoint fails (DB locked) | Non-fatal try/except; if sync then also fails, suggest `db update` |
| User forgets to publish after local changes | `db status` shows "unpublished changes" indicator |
| `db update` destroys unpublished local work | Confirmation prompt + timestamped backup |
| Future multi-admin scenario breaks publish model | At that point, revisit with proper conflict resolution; current single-admin model doesn't need it |
| App auto-recovery deletes local DB unnecessarily | Only for catalog replica (disposable); songsets.db is separate and never touched |
