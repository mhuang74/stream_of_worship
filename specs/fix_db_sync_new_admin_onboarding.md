# Fix `db sync` for New Admin Onboarding

## Goal

A new admin user should be able to create a local replica from Turso master with a single command:

```bash
# 1. Install with Turso support
uv sync --extra turso

# 2. Configure Turso URL in config.toml
# 3. Set SOW_TURSO_TOKEN
# 4. Sync — creates local replica + pulls data
sow_admin db sync
```

No `db init` required. The `db init` + `db sync` flow must also work correctly.

## Problem

Five interacting bugs prevent `db sync` from working for new or recovering users.

### Bug 1: `client.sync()` doesn't catch `libsql.Error` from integrity check

**File:** `src/stream_of_worship/admin/db/client.py:142-163`

When a vanilla SQLite DB (no `-info` sidecar) is opened via `libsql.connect()`, `cursor.execute("PRAGMA integrity_check")` raises `libsql.Error`, not `sqlite3.DatabaseError` or `sqlite3.OperationalError`. The current except blocks only catch sqlite3 errors, so the `libsql.Error` propagates as a raw exception — never converted to `SyncError`. This means the auto-recovery path in `_execute_sync_with_recovery()` (which only catches `SyncError`) never triggers.

```python
# Current code — only catches sqlite3 errors
try:
    cursor.execute("PRAGMA integrity_check")
    result = cursor.fetchone()
    if result and result[0] != "ok":
        raise SyncError(...)
except sqlite3.DatabaseError as e:    # <-- misses libsql.Error
    if "malformed" in str(e).lower():
        raise SyncError(...)
    raise
except sqlite3.OperationalError:     # <-- misses libsql.Error
    pass
```

`libsql.Error` is **not** a subclass of any sqlite3 exception class.

### Bug 2: `validate_config()` rejects missing DB even when Turso is configured

**File:** `src/stream_of_worship/admin/services/sync.py:167-169`

```python
if not self.db_path.exists():
    errors.append(f"Database not found: {self.db_path}")
```

This blocks new users from running `db sync` without first running `db init`. But `libsql.connect()` on a non-existent path creates the DB from scratch — the pre-existing file is not required when Turso is configured.

### Bug 3: `_execute_sync_with_recovery()` doesn't ensure DB directory exists

**File:** `src/stream_of_worship/admin/services/sync.py:220-224`

When a new user runs `db sync` without `db init`, the directory `~/.config/sow-admin/db/` may not exist. `DatabaseClient.__init__` doesn't create the directory — only the `connection` property does (at `client.py:100`). But if `validate_config()` passes and `_execute_sync_with_recovery()` creates a `DatabaseClient`, the connection isn't established until `client.sync()` is called. For the case where the DB doesn't exist at all (new user), `libsql.connect()` needs the parent directory to exist.

### Bug 4: `sync_db()` command errors if config.toml doesn't exist

**File:** `src/stream_of_worship/admin/commands/db.py:314-318`

```python
except FileNotFoundError:
    console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
    raise typer.Exit(1)
```

A new user's first command might be `db sync`. If config.toml doesn't exist, they're told to run `db init` first — but `db init` creates a vanilla SQLite DB that's incompatible with `db sync` (see Bug 1). The command should create a default config (like `init_db` does at `db.py:74-80`) so the user can configure Turso and sync.

### Bug 5: `show_status()` crashes on vanilla SQLite after `db init`

**File:** `src/stream_of_worship/admin/commands/db.py:154-211`

After `db init` creates a vanilla SQLite DB, `init_db()` calls `show_status()` (line 103). `show_status()` calls `get_db_client(config)` (line 156), which creates a **Turso-aware** `DatabaseClient` (because Turso URL is in config). When the DB is a vanilla SQLite file, `client.get_stats()` triggers `libsql.connect()` which raises `libsql.Error`. This is caught by the broad `except Exception` at line 209, but the error message `"sync error: invalid local state: db file exists but metadata file does not"` is confusing and obscures the fact that `db init` actually succeeded.

## Impact on Recovery Flows

### Current broken flow: `db init` → `db sync`

1. `db init` creates vanilla SQLite via `sqlite3.connect()` → no `-info` sidecar
2. `db init` calls `show_status()` → `get_db_client(config)` → libsql connection → `libsql.Error` → prints confusing error but init succeeds
3. `db sync` → `client.sync()` → `cursor.execute("PRAGMA integrity_check")` → raw `libsql.Error` (not caught) → crashes

### Current broken flow: `db sync` alone (new user)

1. No config.toml → `FileNotFoundError` → "Run `db init` first" (circular)
2. Even if config exists, `validate_config()` → "Database not found" (because no `db init`)
3. Even with `--force`, `_execute_sync_with_recovery()` → `client.sync()` → same `libsql.Error` crash

### Desired flow: `db sync` alone (new or existing user)

```
New user:
  db sync → creates config (if missing) → validates → libsql.connect() on non-existent path
           → creates sow.db + sow.db-info → conn.sync() → pulls data from Turso

Existing user (vanilla SQLite from db init):
  db sync → validates → client.sync() → libsql.Error caught → SyncError("metadata file does not")
           → _recover_from_missing_metadata() → deletes sow.db → retry
           → libsql.connect() on non-existent path → creates fresh replica → conn.sync() → success

Existing user (working replica):
  db sync → validates → client.sync() → integrity check OK → conn.sync() → success
```

## Plan

### Fix 1: Catch `libsql.Error` in `client.sync()` and wrap as `SyncError`

**File:** `src/stream_of_worship/admin/db/client.py`

Add conditional libsql error import (reuse the pattern from `schema.py`):

```python
try:
    import libsql as _libsql_module

    _LIBSQL_ERROR: tuple = (_libsql_module.Error,)
except ImportError:
    _LIBSQL_ERROR = ()
```

Then in `sync()`, add `libsql.Error` handling to the integrity check block:

```python
# Pre-sync: check local DB integrity
try:
    cursor.execute("PRAGMA integrity_check")
    result = cursor.fetchone()
    if result and result[0] != "ok":
        raise SyncError(
            f"Local database is corrupted ('{result[0]}'). "
            f"Recovery: run 'db sync --force' to recreate from Turso, "
            f"or manually delete {self.db_path} and all sidecar files."
        )
except sqlite3.DatabaseError as e:
    if "malformed" in str(e).lower():
        raise SyncError(
            f"Local database is corrupted. "
            f"Recovery: run 'db sync --force' to recreate from Turso, "
            f"or manually delete {self.db_path} and all sidecar files. "
            f"Original error: {e}"
        )
    raise
except sqlite3.OperationalError:
    pass
except (*_LIBSQL_ERROR, Exception) as e:
    # libsql.Error from missing metadata sidecar or other libsql issues
    error_msg = str(e).lower()
    if "metadata file does not" in error_msg or "invalid local state" in error_msg:
        raise SyncError(
            f"Local database metadata is missing or invalid. "
            f"This typically happens when a vanilla SQLite database was created "
            f"by 'db init' and needs to be migrated to a libsql embedded replica. "
            f"Auto-recovery will recreate the database from Turso. "
            f"Original error: {e}"
        )
    if "malformed" in error_msg:
        raise SyncError(
            f"Local database is corrupted. "
            f"Recovery: run 'db sync' to recreate from Turso, "
            f"or manually delete {self.db_path} and all sidecar files. "
            f"Original error: {e}"
        )
    raise SyncError(f"Local database error: {e}")
```

Note: Using `(*_LIBSQL_ERROR, Exception)` to catch both `libsql.Error` (when installed) and any other unexpected exception type from libsql. The `Exception` catch is scoped to only libsql-related error patterns — unknown errors are re-wrapped as `SyncError` rather than swallowed.

Also add `libsql.Error` handling to `_validate_schema()` at `client.py:205`:

```python
except (sqlite3.OperationalError, *_LIBSQL_ERROR):
    pass  # Table doesn't exist yet (fresh DB)
```

And to `update_sync_metadata()` — currently `update_sync_metadata` at line 208 uses `self.transaction()` which could raise `libsql.Error` if the DB is in a bad state. The `transaction()` context manager at line 233-246 catches exceptions for rollback but re-raises them. This is acceptable — if metadata update fails, the error will propagate to `sync()` and be caught by `_execute_sync_with_recovery()`. No change needed here.

### Fix 2: Relax `validate_config()` when Turso is configured

**File:** `src/stream_of_worship/admin/services/sync.py:167-169`

Change the "Database not found" check from an error to a no-op when Turso is configured:

```python
# Check database exists (only required when not using Turso — libsql can create from scratch)
if not self.turso_url and not self.db_path.exists():
    errors.append(f"Database not found: {self.db_path}")
```

When `turso_url` is set, `libsql.connect()` can create the DB from scratch, so the pre-existing file is not required.

### Fix 3: Ensure DB directory exists before creating `DatabaseClient`

**File:** `src/stream_of_worship/admin/services/sync.py:220-224`

In `_execute_sync_with_recovery()`, add directory creation before creating the client:

```python
def _execute_sync_with_recovery(self, attempt: int = 1, max_attempts: int = 2) -> SyncResult:
    # Ensure parent directory exists (new users won't have it)
    self.db_path.parent.mkdir(parents=True, exist_ok=True)

    client = DatabaseClient(
        self.db_path,
        turso_url=self.turso_url,
        turso_token=self.turso_token or os.environ.get("SOW_TURSO_TOKEN"),
    )
    ...
```

This is safe because `DatabaseClient.connection` also does `self.db_path.parent.mkdir(parents=True, exist_ok=True)` at `client.py:100`, but the connection isn't established until `client.sync()` is called. If the directory doesn't exist and something else (like logging or validation) tries to access it before `sync()`, it would fail. Being explicit here is safer.

### Fix 4: Create default config if missing in `sync_db()`

**File:** `src/stream_of_worship/admin/commands/db.py:314-318`

Replace the `FileNotFoundError` handler:

```python
try:
    config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
except FileNotFoundError:
    # Create default config for new users
    config = AdminConfig()
    config.save()
    console.print(f"[yellow]Created default config at {get_config_path()}[/yellow]")
```

After this, the existing check at line 321-324 will catch if Turso URL isn't configured:

```python
if not config.turso_database_url:
    console.print("[red]Turso database URL not configured.[/red]")
    console.print("Set turso.database_url in your config file.")
    raise typer.Exit(1)
```

This gives the user a clear message about what to configure next.

### Fix 5: Fix `show_status()` crash on vanilla SQLite

**File:** `src/stream_of_worship/admin/commands/db.py:154-211`

Use a sqlite3-only client for the status display (since this is just reading local stats, not syncing):

```python
# Get database stats
try:
    # Use sqlite3-only client for local stats (avoids libsql metadata error
    # when local DB is a vanilla SQLite file created by db init)
    client = DatabaseClient(db_path)
    stats = client.get_stats()
    ...
except Exception as e:
    console.print(f"\n[red]Error reading database: {e}[/red]")
    raise typer.Exit(1)
```

Change `get_db_client(config)` (which creates a Turso-aware client) to `DatabaseClient(db_path)` (which creates a sqlite3-only client). This is correct because `show_status()` only reads local statistics — it doesn't need Turso sync capability. The sync status table is displayed separately via `get_sync_service_from_config()`.

## Files to Modify

| File | Changes | Fixes |
|------|---------|-------|
| `src/.../admin/db/client.py` | 1. Add conditional `_LIBSQL_ERROR` import. 2. Add `libsql.Error` handling in `sync()` integrity check. 3. Add `*_LIBSQL_ERROR` to `_validate_schema()` except clause. | Fix 1 |
| `src/.../admin/services/sync.py` | 1. Skip "Database not found" error when `turso_url` is set. 2. Add `db_path.parent.mkdir()` in `_execute_sync_with_recovery()`. | Fixes 2, 3 |
| `src/.../admin/commands/db.py` | 1. Create default config in `sync_db()` when config missing. 2. Use sqlite3-only client in `show_status()`. | Fixes 4, 5 |

## Verification

### Test 1: New user (no config, no DB)

```bash
rm -rf ~/.config/sow-admin
# Edit ~/.config/sow-admin/config.toml to add Turso URL
export SOW_TURSO_TOKEN=...
sow_admin db sync
# Expected: creates config, creates libsql replica, syncs from Turso
```

### Test 2: Existing user with vanilla SQLite (after `db init`)

```bash
rm -rf ~/.config/sow-admin/db
mkdir -p ~/.config/sow-admin/db
sow_admin db init      # creates vanilla SQLite (no -info sidecar)
sow_admin db sync      # should auto-recover and sync from Turso
# Expected: detects missing metadata, deletes vanilla DB, creates fresh replica, syncs
```

### Test 3: Existing user with working replica

```bash
sow_admin db sync      # already synced
# Expected: no-op sync, "Sync completed successfully"
```

### Test 4: `db init` doesn't crash on `show_status()`

```bash
rm -rf ~/.config/sow-admin/db
mkdir -p ~/.config/sow-admin/db
sow_admin db init
# Expected: initializes DB, shows status without libsql error
```

### Test 5: `db sync` without Turso URL configured

```bash
rm -rf ~/.config/sow-admin
sow_admin db sync
# Expected: creates default config, prints "Turso database URL not configured. Set turso.database_url in your config file."
```

### Test 6: Existing tests pass

```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ \
  --ignore=tests/services/analysis \
  --ignore=services/qwen3/tests \
  --ignore=services/analysis/tests -v
```

## Correct Onboarding Instructions (Post-Fix)

### Option A: `db sync` only (recommended for new users)

```bash
# 1. Install with Turso support
uv sync --extra turso

# 2. Configure Turso URL
cat >> ~/.config/sow-admin/config.toml << 'EOF'
[turso]
database_url = "libsql://sow-catalog-<org>.aws-us-west-2.turso.io"
EOF

# 3. Set Turso auth token (full-access for write sync)
export SOW_TURSO_TOKEN=<your-full-access-token>

# 4. Sync from Turso — creates local replica + pulls data
sow_admin db sync
```

### Option B: `db init` + `db sync` (works, but auto-recovery deletes the vanilla DB)

```bash
# 1-3. Same as above

# 4. Initialize local database (optional — creates vanilla SQLite)
sow_admin db init

# 5. Sync from Turso — auto-recovers from vanilla SQLite → fresh replica
sow_admin db sync
```

### When NOT to use `turso-init`

`turso-init` is for **one-time Turso remote provisioning** — creating tables on an empty Turso database. If the Turso remote already has tables and data (which it does for onboarding), you only need `db sync`. Running `turso-init` on an existing Turso database would fail or be a no-op (since `CREATE TABLE IF NOT EXISTS` is idempotent).

### Recovery from corrupted local DB

```bash
# Option A: Let db sync auto-recover
sow_admin db sync
# Auto-recovery detects corruption → verifies Turso health → backs up → deletes → re-syncs

# Option B: Manual recovery
rm -rf ~/.config/sow-admin/db
sow_admin db sync
# Fresh sync creates replica from scratch
```
