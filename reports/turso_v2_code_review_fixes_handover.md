# Turso V2 Code Review Fixes Handover

**Date:** 2026-04-26  
**Source Plan:** `specs/turso_v2_code_review_fix_remaining_issues_impl_plan.md`  
**Branch:** `sow_db_sync_fixes`  
**Status:** ~70% Complete - Ready for continuation

---

## Background

This implementation plan addresses remaining issues from a previous Turso V2 code review. The previous round (branch `sow_db_sync_fixes`) completed 11 of 12 original P0-P2 items. This plan covers:

| Item | Status | Notes |
|------|--------|-------|
| R1/M4 | DROPPED | No user songsets exist; audio pipeline safe (uses `hash_prefix`, not `song_id`) |
| R2 | **DONE** | Admin `migrate-song-ids` command registered in CLI |
| R3 | **DONE** | `compute_new_song_id` extracted to `id_utils.py` |
| R4 | **DONE** | F-string lint fix in `db.py:439` |
| R5 | **DONE** | Lambda assignment resolved by R3 extraction |
| R6.1 | PARTIAL | Tests added to `test_catalog_cross_db.py` |
| R6.2 | **DONE** | Recording 28-column schema tests added |
| R6.3+R6.6 | TODO | `run_worker` callable + sync_in_background |
| R6.4+R6.5 | TODO | Deleted_at migration + redundant connection |
| R6.7 | TODO | Deleted_at on models + orphan detection |
| R6.8 | PARTIAL | Import tests added to `test_songset_io.py` |
| R6.9 | PARTIAL | Snapshot tests added to `test_songset_client.py` |
| R6.10+R6.11 | PARTIAL | Executemany tests in `test_migrate_commands.py` |
| R6.12 | **DONE** | SongsetItem export tests added |
| R6.13 | **DONE** | `id_utils` tests in new file |
| R6.14 | PARTIAL | Sync metadata tests added to `test_sync.py` |
| **R7** | **DONE** | Last sync metadata moved from JSON to songsets DB |

---

## What's Been Completed

### High Priority Items (R2, R3, R7)

**R2: Register `migrate-song-ids` command in CLI**
- Created `src/stream_of_worship/admin/db/id_utils.py` with `compute_new_song_id()`
- Updated `admin/commands/migrate.py` to import from `id_utils` instead of defining locally
- Registered migrate command in `admin/main.py`:
  ```python
  from stream_of_worship.admin.commands import migrate as migrate_commands
  db_commands.app.add_typer(migrate_commands.app, name="migrate", help="Database migration operations")
  ```
- Updated CLI documentation to show `migrate` subcommand under `db`
- Removed legacy user warning about manual migration (M4 dropped)

**R3: Extract `compute_new_song_id` to `id_utils.py`**
- New file: `src/stream_of_worship/admin/db/id_utils.py`
- Contains public `compute_new_song_id(title, composer, lyricist)` function
- Contains private `_normalize(s: str)` helper (fixes E731 lambda lint)
- Updated `migrate.py` to import from `id_utils`
- Removed now-unnecessary imports: `hashlib`, `re`, `unicodedata`, `pypinyin.lazy_pinyin`

**R7: Move sync metadata from `last_sync.json` into songsets DB**
- Added `CREATE_SYNC_METADATA_TABLE` to `app/db/schema.py`
- Added `get_metadata(key, default)` and `set_metadata(key, value)` methods to `SongsetClient`
- Updated `AppSyncService` to use DB instead of JSON:
  - Added `_migrate_last_sync_json()` for one-time migration
  - Removed `_last_sync_file` from `__init__`
  - Updated `get_sync_status()` to query DB
  - Updated `_update_last_sync()` to write to DB

### Low Priority Items (R4, R5)

**R4:** Fixed f-string lint in `admin/commands/db.py:439` - removed `f` prefix from Rich markup  
**R5:** Fixed lambda assignment - resolved by R3 extraction

### Test Files Created/Modified

**New Test Files:**
1. `tests/admin/db/test_id_utils.py` - Tests for `compute_new_song_id()` and `_normalize()`
2. `tests/admin/commands/test_migrate_commands.py` - Tests for migrate CLI command

**Modified Test Files:**
1. `tests/admin/test_models.py` - Added 28-column Recording tests (R6.2)
2. `tests/app/db/test_models.py` - Added `to_dict(include_joined)` tests (R6.12)
3. `tests/app/services/test_sync.py` - Added metadata + migration tests (R6.14)
4. `tests/app/services/test_songset_io.py` - Added client usage tests (R6.8)
5. `tests/app/services/test_catalog_cross_db.py` - Added JOIN offset tests (R6.1)
6. `tests/app/db/test_songset_client.py` - Added snapshot tests (R6.9)

---

## What Remains

### Priority 1: Remaining Tests (~30% of work)

**R6.3+R6.6: Test `run_worker` callable + `sync_in_background`**
- Location: `src/stream_of_worship/tests/unit/test_tui_services.py`
- What needs testing:
  1. `action_sync_catalog` passes callable (not result) to `run_worker`
  2. `on_mount` passes callable to `run_worker`
  3. `_sync_in_background` is synchronous (not async)
  4. Thread-safe notification via `call_from_thread`
- Implementation note: The code in `app/app.py` already does this correctly - just need tests

**R6.4+R6.5: Test deleted_at migration + redundant connection check**
- R6.4: Deleted_at migration in:
  - `DatabaseClient.initialize_schema()` - adds column to admin DB
  - `ReadOnlyClient._migrate_schema()` - adds column to read-only replica
  - `turso-bootstrap` command - adds column during bootstrap
- R6.5: Static analysis test for redundant `sqlite3.connect` in `src/stream_of_worship/app`

**R6.7: Test deleted_at on models + orphan detection**
- Location: `tests/admin/test_models.py` (Song/Recording) + `tests/app/services/test_catalog_cross_db.py`
- Song model tests: 17-column rows, `to_dict` includes `deleted_at`
- Recording model tests: 28-column rows, `to_dict` includes `deleted_at`
- Orphan detection tests in `catalog_cross_db`: `SongsetItemWithDetails.is_orphan` checks `deleted_at`

**R6.9: Snapshot tests (partial)**
- Added basic `snapshot_db` tests to `test_songset_client.py`
- May need additional edge case tests

**R6.10+R6.11: Executemany tests (partial)**
- Added basic migrate command tests to `test_migrate_commands.py`
- May need:
  - Static analysis tests for `executemany` usage in `migrate.py`
  - Tests for chunked UPDATE loops

### Priority 2: Code Quality Verification

**Run Quality Gates:**
```bash
# Lint
ruff check src/stream_of_worship/

# Run all tests  
pytest tests/ src/stream_of_worship/tests/ -v

# Full check
ruff check src/stream_of_worship/ && pytest tests/ src/stream_of_worship/tests/ -v
```

**Known Lint Issues to Fix:**
- None expected after R4/R5 fixes

---

## Testing Strategy

### For Remaining Tests

**R6.3+R6.6 (`run_worker` + sync)**:
```python
# Test in tests/unit/test_tui_services.py
def test_action_sync_catalog_passes_callable_to_run_worker(self, sample_app):
    mock_worker = MagicMock()
    sample_app.run_worker = mock_worker
    
    sample_app.action_sync_catalog()
    
    # Verify run_worker called with callable, not result
    assert mock_worker.called
    first_arg = mock_worker.call_args[0][0]
    assert callable(first_arg)  # Key assertion
```

**R6.4 (deleted_at migration)**:
```python
# Test in tests/admin/commands/test_db_commands.py
def test_initialize_schema_adds_deleted_at_to_existing_db(self, initialized_db):
    client = DatabaseClient(initialized_db)
    cursor = client.connection.cursor()
    cursor.execute("PRAGMA table_info(songs)")
    columns = [row[1] for row in cursor.fetchall()]
    assert "deleted_at" in columns
```

**R6.7 (orphan detection)**:
```python
# Test in tests/app/services/test_catalog_cross_db.py
def test_is_orphan_returns_true_for_soft_deleted_song(self):
    song = Song(id="s1", title="T", source_url="http://t", scraped_at="2024-01-01", deleted_at="2024-01-02")
    item = SongsetItem(id="i1", songset_id="set1", song_id="s1", position=0)
    details = SongsetItemWithDetails(item=item, song=song, recording=None)
    assert details.is_orphan is True
```

### Test Organization per Plan

The spec defines these test locations (follow them):

| Test Group | Location |
|------------|----------|
| Model layer | `tests/admin/test_models.py`, `tests/admin/db/test_id_utils.py`, `tests/app/db/test_models.py` |
| Service layer | `tests/app/services/test_catalog*.py`, `tests/app/services/test_tui_services.py`, `tests/app/services/test_songset_io.py`, `tests/app/services/test_sync.py` |
| DB/infra layer | `tests/admin/commands/test_db_commands.py`, `tests/admin/commands/test_migrate_commands.py`, `tests/app/db/test_songset_client.py`, `tests/app/db/test_read_client.py` |

---

## Files Changed (Git Status)

```
Modified:
  src/stream_of_worship/admin/commands/db.py       # R4: f-string fix
  src/stream_of_worship/admin/commands/migrate.py  # R3: remove priv function
  src/stream_of_worship/admin/main.py              # R2: register migrate
  src/stream_of_worship/app/db/schema.py           # R7: add _sync_metadata table
  src/stream_of_worship/app/db/songset_client.py  # R7: metadata methods
  src/stream_of_worship/app/services/sync.py       # R7: use DB not JSON
  tests/admin/test_models.py                       # R6.2: Recording 28-col tests

Untracked:
  src/stream_of_worship/admin/db/id_utils.py       # R3: extracted utility
  tests/admin/db/test_id_utils.py                   # R6.13: id_utils tests
  tests/admin/commands/test_migrate_commands.py    # R6.11: migrate command tests
```

---

## Next Steps for Continue Agent

1. **Do this first:**
   - Run `ruff check src/stream_of_worship/` and `pytest tests/ -v` to verify current state
   - Fix any failing tests or lint errors

2. **Then complete remaining tests:**
   1. Add R6.3+R6.6 tests to `tests/unit/test_tui_services.py`
   2. Add R6.4 tests to `tests/admin/commands/test_db_commands.py`
   3. Add R6.5 static analysis test (grep for sqlite3.connect in `app/`)
   4. Add R6.7 tests to `tests/admin/test_models.py` (Song deleted_at) and `tests/app/services/test_catalog_cross_db.py` (orphan detection)
   5. Ensure R6.9 tests are complete in `tests/app/db/test_songset_client.py`
   6. Add R6.10 executemany tests if needed (check current `test_migrate_commands.py` coverage)

3. **Quality Gate:**
   ```bash
   ruff check src/stream_of_worship/ && pytest tests/ src/stream_of_worship/tests/ -v
   ```

4. **Git push (MANDATORY):**
   ```bash
   git add -A
   git commit -m "Fix remaining Turso V2 code review issues"
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```

5. **Cleanup:**
   - Verify no uncommitted changes
   - Ensure all tests pass

---

## Key Implementation Notes

### R3 Extraction Details
- Moved from private `_compute_new_song_id` to public `compute_new_song_id`
- Changed from lambda `norm = lambda s:` to proper `def _normalize(s):`
- Removed unused imports from `migrate.py`

### R7 Metadata Migration
- Uses INSERT OR REPLACE for upsert behavior
- Migrates existing `last_sync.json` on first service init
- Deletes JSON after successful migration

### Test Patterns
- Use `@pytest.fixture` for test setup
- Mock `run_worker` with `MagicMock` for callable verification
- Static analysis tests: check for specific patterns in source code

---

## Blocking Issues / Risks

**None known** - Most critical items (R2, R3, R7) are complete. Remaining work is mostly test coverage.

---

## Contact / Questions

- Review the spec: `specs/turso_v2_code_review_fix_remaining_issues_impl_plan.md`
- Implementation summary: `reports/turso_v2_code_review_fixes_impl_summary.md`
- Quality gates: Run lint + unit tests before pushing

**Remember:** Work is NOT complete until `git push` succeeds! If push fails, resolve conflicts and retry.
