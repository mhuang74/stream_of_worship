# Hand-off: Remote-Write + Local-Read Implementation

**Date:** 2026-05-05
**Spec:** `specs/remote_write_local_read_impl_plan_v2.md`
**Status:** 7 of 9 phases complete

---

## Summary

Implementation is 80% complete. All core HTTP write infrastructure and refactoring is done. Remaining work is CLI command update (Phase 7) and testing (Phase 9).

---

## Completed Phases

### Phase 1: HTTP Write Infrastructure ✅
**File:** `src/stream_of_worship/admin/db/client.py`

Added:
- `http_pipeline_url` property (derives HTTPS URL from libsql://)
- `_format_param()` module function (formats Python values for Turso HTTP API)
- `_execute_remote_pipeline()` (core HTTP executor)
- `_check_pipeline_results()` (error checking with optional suppression)
- `_execute_remote()` (single-statement helper)
- `_execute_remote_transaction()` (multi-statement with BEGIN/COMMIT)
- `_sync_replica()` (pull-only sync, fatal vs non-fatal modes)

### Phase 3.1: Remote DDL Helper ✅
**File:** `src/stream_of_worship/admin/db/schema.py`

Added:
- `apply_column_migrations_remote()` (queries remote schema via PRAGMA, applies ALTER TABLE via HTTP)

### Phase 2: Refactor Write Methods ✅
**File:** `src/stream_of_worship/admin/db/client.py`

Refactored all write methods to use remote writes when `is_turso_enabled`:
- `insert_song()` 
- `bulk_insert_songs()` (NEW - batch insert for scrape flow)
- `insert_recording()`
- `update_recording_status()`
- `update_recording_analysis()`
- `update_recording_lrc()`
- `update_recording_download()`
- `update_recording_visibility()`
- `delete_recording()`
- `soft_delete_song()`
- `restore_song()`
- `restore_recording()`

Pattern: Check `is_turso_enabled`, use `_execute_remote()` for Turso or `transaction()` for local sqlite3, then `_sync_replica(fatal=False)` for post-write pull.

### Phase 3.2/3.3: Refactor sync() and initialize_schema() ✅
**File:** `src/stream_of_worship/admin/db/client.py`

- `sync()`: Simplified to pull-only (removed bidirectional push, pre-sync migrations, post-sync migrations)
- `initialize_schema()`: Split into Turso path (HTTP DDL) and local sqlite3 path

### Phase 4: Stale-Read Protection ✅
**Files:** 
- `src/stream_of_worship/admin/commands/audio.py`
- `src/stream_of_worship/admin/commands/catalog.py`

Changes:
- Added `import os` and `import SyncError` to audio.py
- Updated `get_db_client()` in catalog.py to pass Turso credentials
- Added sync before read in reconcile flow (audio.py line ~1879):
  ```python
  if db_client.is_turso_enabled:
      try:
          db_client._sync_replica(fatal=True)
      except SyncError as e:
          console.print(f"[red]Sync failed before reconcile: {e}[/red]")
          console.print("Aborting reconcile to prevent stale reads.")
          raise typer.Exit(1)
  ```

### Phase 5: Refactor turso-init ✅
**File:** `src/stream_of_worship/admin/commands/infra.py`

Complete rewrite:
- Removed libsql embedded replica connection
- Uses `DatabaseClient` with HTTP write path
- `initialize_schema()` sends DDL via HTTP
- Seeding uses `_execute_remote_transaction()` for batch inserts
- Removed LIBSQL_AVAILABLE check (no longer needed)

### Phase 6: App-Side Recovery ✅
**File:** `src/stream_of_worship/app/db/read_client.py`

Added:
- Auto-recovery in `sync()` for WAL/metadata errors
- `_recover_replica()` method (deletes local DB and sidecar files)

---

## Remaining Work

### Phase 7: CLI Commands (IN PROGRESS)

**File:** `src/stream_of_worship/admin/commands/db.py`

Need to:
1. Rename `sync_db` command to `pull_db` (line 420-564)
2. Update docstring to clarify pull-only behavior
3. Update help text to remove "bidirectional" references
4. Add recovery documentation to `--help`:
   ```
   To recover from corruption:
     1. Delete local DB: rm <db_path> <db_path>-wal <db_path>-shm <db_path>-info
     2. Re-run: sow-admin db pull
   ```
5. Update error messages (remove references to "push", "write access")

**Note:** Per spec v2, we're NOT creating deprecated aliases. Just replace `db sync` with `db pull`.

### Phase 9: Testing (NOT STARTED)

**Files to test:**
- `tests/admin/db/test_client.py` (add unit tests for HTTP write methods)
- `tests/admin/commands/test_db_commands.py` (update sync → pull tests)

**Test cases needed:**
1. `_execute_remote_pipeline()` with mocked `requests.post`
2. `_format_param()` for all Python types
3. `apply_column_migrations_remote()` idempotency
4. `_sync_replica(fatal=True)` vs `fatal=False`
5. Reconcile stale-read protection
6. Integration test with real Turso test DB (optional, requires credentials)

---

## Known Issues / Notes

1. **PRAGMA response format:** The spec noted to verify HTTP API PRAGMA response. The implementation assumes `result["rows"]` format. This should be tested with a real Turso instance.

2. **update_sync_metadata() stays local:** Per spec decision, this is admin-internal metadata not replicated to app.

3. **Return values for remote writes:** Methods like `update_recording_visibility()` now check existence via SELECT before UPDATE when using Turso. This adds an extra HTTP call but maintains the same API contract.

4. **Bulk operations:** `bulk_insert_songs()` was added for scraper efficiency. Consider if other bulk operations need similar treatment.

---

## File Change Summary

| File | Lines Changed | Status |
|------|---------------|--------|
| `admin/db/client.py` | ~400 | ✅ Complete |
| `admin/db/schema.py` | ~30 | ✅ Complete |
| `admin/commands/audio.py` | ~20 | ✅ Complete |
| `admin/commands/catalog.py` | ~10 | ✅ Complete |
| `admin/commands/infra.py` | ~100 (rewrite) | ✅ Complete |
| `app/db/read_client.py` | ~30 | ✅ Complete |
| `admin/commands/db.py` | 0 | ❌ Phase 7 remaining |
| Tests | 0 | ❌ Phase 9 remaining |

---

## Next Steps for Continuing Agent

1. **Read the spec:** `specs/remote_write_local_read_impl_plan_v2.md`

2. **Complete Phase 7:** Edit `src/stream_of_worship/admin/commands/db.py`
   - Rename `@app.command("sync")` to `@app.command("pull")`
   - Rename function `sync_db` to `pull_db`
   - Update docstring and help text
   - Remove "bidirectional" references
   - Update error messages

3. **Run linting:** `PYTHONPATH=src uv run --python 3.11 --extra admin ruff check src/stream_of_worship/`

4. **Run type checking:** `PYTHONPATH=src uv run --python 3.11 --extra admin mypy src/stream_of_worship/`

5. **Run existing tests:** `PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/ -v`

6. **Write new tests** (Phase 9)

7. **Manual verification** (if Turso credentials available):
   ```bash
   SOW_TURSO_TOKEN=... uv run --extra admin sow-admin catalog scrape --limit 5
   uv run --extra admin sow-admin db pull
   uv run --extra app sow-app run
   ```

8. **Commit:** After all phases complete, commit with message following project conventions.

---

## References

- Spec: `specs/remote_write_local_read_impl_plan_v2.md`
- Analysis: `reports/turso_embedded_replica_analysis_v2_2026-05-05.md`
- Turso HTTP API docs: https://docs.turso.tech/api-reference/pipeline
