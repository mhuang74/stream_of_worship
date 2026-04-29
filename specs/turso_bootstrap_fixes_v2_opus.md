# Turso Bootstrap Migration + Seed Safety

## Context

`sow_admin db turso-bootstrap` currently fails with `local state is incorrect, db file exists but metadata file does not` whenever the user has a pre-existing vanilla SQLite database at `~/.config/sow-admin/db/sow.db` (e.g., one created with the `sqlite3` CLI). libsql's embedded replica mode requires sidecar metadata files (`-info`, `-wal`, `-shm`) it creates itself; a hand-built SQLite file lacks these.

The spec at `specs/turso_bootstrap_fixes.md` proposes auto-migrating the existing data into Turso on first bootstrap. The proposal is directionally correct but has three operational risks (sidecar orphans on retry, backup name collisions on re-run, fragile error-string detection) and leaves two orthogonal data-loss bugs in the seed path untouched. This plan addresses all of those in a single change.

## Current code (file/line references)

- `src/stream_of_worship/admin/commands/db.py:443-452` — initial `libsql.connect` (the call that throws)
- `src/stream_of_worship/admin/commands/db.py:454-473` — schema + idempotent migrations, ends with `conn.commit()`
- `src/stream_of_worship/admin/commands/db.py:476-530` — seed logic (the broken remote-empty check + non-transactional inserts live here)
- `src/stream_of_worship/admin/commands/db.py:534` — `conn.sync()` push to remote
- `src/stream_of_worship/admin/config.py:45,267-273` — `config.db_path` (`Path` to `~/.config/sow-admin/db/sow.db`)
- `src/stream_of_worship/app/db/songset_client.py:278-313` — existing `snapshot_db` with `bak-YYYYMMDDTHHMMSS` naming convention to mirror

## Plan

### 1. Detect & migrate vanilla SQLite via sidecar absence (not error string)

In `commands/db.py`, replace the bare `libsql.connect` block (lines 443–452) with a pre-check:

- Compute `info_path = config.db_path.parent / f"{config.db_path.name}-info"`.
- If `config.db_path.exists()` **and** `not info_path.exists()` → treat as vanilla SQLite needing migration.
  - Require either `--seed` (migrate) or `--force` (discard); otherwise print the same options block the spec shows and `raise typer.Exit(1)`.
  - Build a timestamped backup directory: `backup_dir = config.db_path.parent / f"{config.db_path.name}.bak-{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}"`.
  - `backup_dir.mkdir(parents=False, exist_ok=False)` — fail loud if it somehow exists.
  - Move `config.db_path` **and** every sibling matching `f"{config.db_path.name}-*"` (covers `-info`, `-wal`, `-shm`, `-client_wal_index`, etc.) into `backup_dir/` using `shutil.move`.
  - On `--seed`: set `source_db_path = backup_dir / config.db_path.name` so the seed reads from the backup (avoids the dual-connection-on-same-file issue at the existing line 493).
  - On `--force`: leave `source_db_path = None`.
- If `config.db_path.exists()` and `info_path.exists()` → already a libsql replica; proceed to `libsql.connect` directly (no migration needed).
- If neither exists → fresh replica; `libsql.connect` as today.

After the branch, call `libsql.connect` once with the standard error-handling wrapper.

### 2. Use the backup as seed source

At `commands/db.py:493`, change:
```python
local_conn = sqlite3.connect(config.db_path)
```
to:
```python
seed_source = source_db_path if source_db_path else config.db_path
local_conn = sqlite3.connect(seed_source)
```
Wrap `local_conn` usage in `try/finally: local_conn.close()` so a mid-seed exception doesn't leak the connection (current code only closes on the success path at line 529).

### 3. Fix the broken remote-empty check (seed safety #1)

Currently `cursor.execute("SELECT COUNT(*) FROM songs")` at line 480 reads the *embedded replica*, which has not been synced yet — so it always reports 0 and the `--force` guard is dead. Fix:

- Call `conn.sync()` once **before** the count check, so the replica reflects remote state.
- Then run the existing count check; `--force` now actually means something.

### 4. Wrap seed in an explicit transaction (seed safety #2)

Around the three `executemany` blocks (`songs`, `recordings`, `sync_metadata` at lines 498–527):

- `cursor.execute("BEGIN")` before the first `executemany`.
- On success: `conn.commit()`, then `conn.sync()` (move the existing line 534 sync to here so remote only sees a fully-committed dataset).
- On exception: `conn.rollback()`, log the failure, and `raise typer.Exit(1)` — do **not** call `conn.sync()`. This guarantees a partial seed never reaches Turso.

### 5. Imports & docstring

- Add `import shutil` and `from datetime import datetime` at the top of `commands/db.py` (next to existing `import os`).
- Update the `turso_bootstrap` docstring to document: auto-migration behavior, timestamped backup directory location, `--seed` vs `--force` semantics with an existing vanilla SQLite db.

## Files to modify

- `src/stream_of_worship/admin/commands/db.py` — the only file changed.

## Verification

1. **Reproduce the original failure** (sanity check current behavior):
   ```bash
   rm -rf ~/.config/sow-admin/db && mkdir -p ~/.config/sow-admin/db
   sqlite3 ~/.config/sow-admin/db/sow.db "CREATE TABLE songs(id INTEGER PRIMARY KEY); INSERT INTO songs VALUES (1);"
   uv run --extra admin sow-admin db turso-bootstrap --seed   # should fail today
   ```

2. **Migration path (`--seed`)** with the fix applied:
   ```bash
   uv run --extra admin sow-admin db turso-bootstrap --seed
   ls ~/.config/sow-admin/db/                                  # see sow.db.bak-<timestamp>/ with main + sidecars
   uv run --extra admin sow-admin db stats                     # row counts match pre-migration
   uv run --extra admin sow-admin db sync                      # subsequent sync is a no-op
   ```

3. **Force path (`--force`)** with a separate vanilla-SQLite fixture:
   ```bash
   # rebuild fixture, then
   uv run --extra admin sow-admin db turso-bootstrap --force   # backup dir created, remote stays untouched (no seed)
   ```

4. **Retry-after-partial-failure** (the case the spec mishandles):
   ```bash
   # rebuild fixture; simulate failure by killing mid-seed (or temporarily revoking the token)
   # run again — second invocation should see a real libsql replica (info file present), skip migration, and succeed.
   ```

5. **Re-run does not clobber prior backup**:
   ```bash
   # Run --seed twice in a row against fresh fixtures; confirm two distinct bak-<timestamp>/ dirs exist.
   ```

6. **Transaction safety** (seed rollback): temporarily inject a deliberate failure between the `songs` and `recordings` `executemany` calls and confirm:
   - `conn.sync()` is **not** called.
   - Remote Turso `songs` count is unchanged from before the run (use `turso db shell sow-catalog ...` or equivalent).

7. **User app sync after successful bootstrap**:
   ```bash
   export SOW_TURSO_READONLY_TOKEN=...
   uv run --extra app sow-app sync
   uv run --extra app sow-app run
   ```

8. **Tests**: run the admin test suite to confirm no regressions in unrelated `db` commands:
   ```bash
   PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/ -v
   ```
