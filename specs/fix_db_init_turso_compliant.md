# Fix: Make `db init` create Turso-compliant local DB

**Date:** 2026-05-04  
**Status:** Spec  
**Branch:** `robust_schema_updates`  
**Predecessor:** `specs/fix_db_sync_schema_mismatch_v2.md`

## Problem

After implementing the schema mismatch fixes, `db init` + `db sync` still fails on the first attempt:

```
$ sow_admin db init        # creates vanilla sqlite3 file (no libsql metadata)
$ sow_admin db sync        # FAILS: "file is not a database"
$ sow_admin db sync        # succeeds (libsql sidecar was partially created on first attempt)
```

**Root cause:** `db init` creates the local DB via `DatabaseClient(db_path)` — no `turso_url` passed — so it uses plain `sqlite3.connect()`. When `db sync` later opens the same file via `libsql.connect()`, the replication protocol can't handle a non-libsql file, and the error `"file is not a database"` doesn't match any recovery condition in sync.py.

## Design Goals

The fix must satisfy two use cases:

1. **New admin user onboarding** (no local db): `db init` should produce a working, synced database in one step
2. **Existing admin pushing schema changes**: `db sync` continues to apply `COLUMN_MIGRATIONS` locally, push them to Turso via `conn.sync()`, and validate column counts

## Solution: `db init` with Turso syncs instead of creating empty tables

When Turso is configured, `db init` should use `libsql.connect()` and sync from Turso immediately — pulling schema + data — then apply post-sync column migrations. This makes `db init` a complete one-step onboarding command.

When Turso is NOT configured (offline/local-only mode), `db init` keeps current behavior: creates tables via sqlite3 + `initialize_schema()`.

### Key Principle

The existing `db sync` flow (`client.py:sync()`) already handles both use cases correctly:
- **First sync (no tables):** skips pre-sync migrations, pulls everything from Turso, applies post-sync migrations
- **Subsequent sync (tables exist):** applies pre-sync migrations, syncs bidirectionally, validates schema

So `db init` with Turso just needs to delegate to the same sync mechanism.

## Changes

### File: `src/stream_of_worship/admin/commands/db.py`

#### Change 1: `init_db` — use libsql + sync when Turso is configured

**Current behavior (lines 98-102):**
```python
else:
    console.print(f"Creating database at {db_path}...")
    client = DatabaseClient(db_path)
    client.initialize_schema()
    console.print("[green]Database initialized successfully![/green]")
```

**New behavior:**
```python
else:
    console.print(f"Creating database at {db_path}...")
    turso_url = config.effective_turso_url
    if turso_url and LIBSQL_AVAILABLE:
        # Turso configured: create libsql replica and sync from remote
        # Do NOT call initialize_schema() — let sync pull schema from Turso
        client = DatabaseClient(
            db_path,
            turso_url=turso_url,
            turso_token=os.environ.get("SOW_TURSO_TOKEN"),
        )
        try:
            client.sync()
            console.print("[green]Database initialized and synced from Turso![/green]")
        except SyncError as e:
            # If sync fails, fall back to local-only init
            client.close()
            if db_path.exists():
                db_path.unlink()
            # Delete any sidecar files
            for f in db_path.parent.glob(f"{db_path.name}-*"):
                if f.is_dir():
                    import shutil
                    shutil.rmtree(f)
                else:
                    f.unlink(missing_ok=True)
            console.print(f"[yellow]Turso sync failed: {e}[/yellow]")
            console.print("[yellow]Falling back to local-only initialization...[/yellow]")
            client = DatabaseClient(db_path)
            client.initialize_schema()
            console.print("[green]Database initialized locally (run 'db sync' later to pull data).[/green]")
    else:
        # No Turso: standard local-only init
        client = DatabaseClient(db_path)
        client.initialize_schema()
        console.print("[green]Database initialized successfully![/green]")
```

#### Change 2: `init_db --force` — also use libsql when Turso configured

**Current behavior (lines 86-90):**
```python
if force and db_path.exists():
    console.print(f"[red]Resetting database at {db_path}...[/red]")
    client = DatabaseClient(db_path)
    client.reset_database()
    console.print("[green]Database reset and re-initialized successfully![/green]")
```

**New behavior:**
```python
if force and db_path.exists():
    console.print(f"[red]Resetting database at {db_path}...[/red]")
    # Delete existing db + sidecar files for clean slate
    db_path.unlink()
    for f in db_path.parent.glob(f"{db_path.name}-*"):
        if f.is_dir():
            import shutil
            shutil.rmtree(f)
        else:
            f.unlink(missing_ok=True)

    turso_url = config.effective_turso_url
    if turso_url and LIBSQL_AVAILABLE:
        client = DatabaseClient(
            db_path,
            turso_url=turso_url,
            turso_token=os.environ.get("SOW_TURSO_TOKEN"),
        )
        try:
            client.sync()
            console.print("[green]Database reset and synced from Turso![/green]")
        except SyncError as e:
            client.close()
            if db_path.exists():
                db_path.unlink()
            for f in db_path.parent.glob(f"{db_path.name}-*"):
                if f.is_dir():
                    import shutil
                    shutil.rmtree(f)
                else:
                    f.unlink(missing_ok=True)
            console.print(f"[yellow]Turso sync failed: {e}[/yellow]")
            console.print("[yellow]Falling back to local-only reset...[/yellow]")
            client = DatabaseClient(db_path)
            client.initialize_schema()
            console.print("[green]Database reset locally (run 'db sync' later).[/green]")
    else:
        client = DatabaseClient(db_path)
        client.reset_database()
        console.print("[green]Database reset and re-initialized successfully![/green]")
```

#### Change 3: `init_db` (db already exists, no --force) — run migrations via libsql if Turso configured

**Current behavior (lines 91-97):**
```python
elif db_path.exists():
    console.print(f"[yellow]Database already exists at {db_path}[/yellow]")
    console.print("Running migrations...")
    client = DatabaseClient(db_path)
    client.initialize_schema()
    console.print("[green]Migrations applied successfully![/green]")
```

**New behavior:**
```python
elif db_path.exists():
    console.print(f"[yellow]Database already exists at {db_path}[/yellow]")
    console.print("Running migrations...")
    turso_url = config.effective_turso_url
    if turso_url and LIBSQL_AVAILABLE:
        # Use libsql to apply migrations and push to Turso
        client = DatabaseClient(
            db_path,
            turso_url=turso_url,
            turso_token=os.environ.get("SOW_TURSO_TOKEN"),
        )
        try:
            client.sync()
            console.print("[green]Migrations applied and synced with Turso![/green]")
        except SyncError as e:
            console.print(f"[yellow]Sync failed ({e}), applying migrations locally only.[/yellow]")
            client.close()
            client = DatabaseClient(db_path)
            client.initialize_schema()
            console.print("[green]Migrations applied locally.[/green]")
    else:
        client = DatabaseClient(db_path)
        client.initialize_schema()
        console.print("[green]Migrations applied successfully![/green]")
```

#### Change 4: Add necessary imports at top of `db.py`

```python
import os
import shutil
from stream_of_worship.admin.db.client import DatabaseClient, SyncError, LIBSQL_AVAILABLE
```

### File: `src/stream_of_worship/admin/services/sync.py`

#### Change 5: Add `"not a database"` to recovery conditions

Even with the `db init` fix, this safety net is still needed for edge cases (e.g., user manually copies a vanilla sqlite3 file into place, or old `db init` was run before this fix).

**Line 275, current:**
```python
if ("metadata file does not" in error_msg.lower() or "metadata is missing" in error_msg.lower()) and attempt < max_attempts:
```

**New:**
```python
if ("metadata file does not" in error_msg.lower() or "metadata is missing" in error_msg.lower() or "not a database" in error_msg.lower()) and attempt < max_attempts:
```

## Use Case Walkthrough

### Use Case 1: New Admin User Onboarding

```bash
rm -rf ~/.config/sow-admin/db
sow_admin db init
```

Flow:
1. `db_path` doesn't exist → enters "create" branch
2. `config.effective_turso_url` returns the configured Turso URL
3. Creates `DatabaseClient(db_path, turso_url=..., turso_token=...)`
4. Calls `client.sync()`:
   - `libsql.connect()` creates a fresh file with proper sidecar metadata
   - `tables_exist = False` (no tables yet)
   - `conn.sync()` pulls full schema + data from Turso (685 songs, 73 recordings)
   - Post-sync `apply_column_migrations()` adds any columns Turso doesn't have yet (e.g., `download_status`)
   - Second `conn.sync()` pushes those new columns back to Turso
   - `_validate_schema()` confirms 29 recording columns, 17 song columns
5. Output: "Database initialized and synced from Turso!"
6. `show_status()` displays song/recording counts

No separate `db sync` needed. One command, fully onboarded.

### Use Case 2: Existing Admin Pushing Schema Changes

```bash
# Developer adds a new entry to COLUMN_MIGRATIONS in schema.py
# Then runs:
sow_admin db sync
```

Flow (unchanged from current behavior):
1. `DatabaseClient` connects via libsql (existing sidecar metadata)
2. `tables_exist = True`
3. Integrity check passes
4. Pre-sync `apply_column_migrations()` adds the new column locally
5. `self.connection.commit()` commits the ALTER TABLE
6. First `conn.sync()` pushes ALTER TABLE to Turso + pulls any remote changes
7. Post-sync `apply_column_migrations()` re-checks (defensive)
8. Second `conn.sync()` pushes remaining changes
9. `_validate_schema()` confirms expected column counts

This use case is entirely handled by `client.py:sync()` and requires NO changes.

### Use Case 2b: Existing Admin runs `db init` to apply migrations

```bash
# Developer adds new column to COLUMN_MIGRATIONS
# Then runs db init (instead of db sync):
sow_admin db init
```

Flow:
1. `db_path` exists → enters "already exists" branch
2. Turso configured → creates `DatabaseClient` with turso_url
3. Calls `client.sync()` which handles the full migration + sync cycle
4. Output: "Migrations applied and synced with Turso!"

## Files to Modify

| File | Change |
|------|--------|
| `src/stream_of_worship/admin/commands/db.py` | `init_db`: use libsql+sync when Turso configured (all 3 branches: new, existing, --force) |
| `src/stream_of_worship/admin/services/sync.py` | Add `"not a database"` to recovery condition on line 275 |

## What Does NOT Change

- `src/stream_of_worship/admin/db/client.py` — `sync()` method is already correct for both use cases
- `src/stream_of_worship/admin/db/schema.py` — no changes needed
- `db sync` command — no changes needed, already works for both use cases

## Verification

```bash
# Test 1: New user onboarding via db init (the primary fix)
rm -rf ~/.config/sow-admin/db && mkdir -p ~/.config/sow-admin/db
sow_admin db init
sow_admin db status      # Should show 685+ songs, 73+ recordings, last_sync set

# Test 2: db init --force (reset + sync)
sow_admin db init --force
sow_admin db status      # Same result as Test 1

# Test 3: db sync still works for existing replica (no regression)
sow_admin db sync
sow_admin db sync        # Idempotent, no errors

# Test 4: db sync with no prior init (pure sync onboarding)
rm -rf ~/.config/sow-admin/db
sow_admin db sync
sow_admin db status      # Should show all data

# Test 5: Fallback when Turso unreachable
# (set invalid token or disconnect network)
rm -rf ~/.config/sow-admin/db && mkdir -p ~/.config/sow-admin/db
SOW_TURSO_TOKEN=invalid sow_admin db init
# Should fall back to local-only init with warning

# Test 6: Safety net — vanilla sqlite3 file + db sync
rm -rf ~/.config/sow-admin/db && mkdir -p ~/.config/sow-admin/db
python3 -c "import sqlite3; sqlite3.connect('/Users/mhuang/.config/sow-admin/db/sow.db').execute('CREATE TABLE songs(id TEXT)')"
sow_admin db sync        # Should auto-recover via "not a database" condition
```
