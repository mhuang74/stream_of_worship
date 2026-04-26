# Turso V2 Code Review Fix — Remaining Issues Implementation Plan

**Date:** 2026-04-26
**Source:** `specs/turso_v2_code_review_fix_impl_plan.md`, `reports/turso_v2_code_review_fixes_impl_summary.md`
**Scope:** Remaining work from original 12-item plan + P3/M5 + pre-existing lint issues + integration tests for all 12 items
**M4 Status:** DROPPED — no user songsets to migrate; audio pipeline data is safe (R2/cache paths use `hash_prefix`, never `song_id`)

---

## Overview

The first round of implementation (branch `sow_db_sync_fixes`) completed 11 of 12 original P0–P2 items. This plan covers:

| # | Issue | Status | Action |
|---|-------|--------|--------|
| R1 | M4: User-side songset ID migration | DROPPED | No user songsets exist; audio data is safe |
| R2 | Admin `migrate-song-ids` command orphaned | NEW | Register in admin CLI (song IDs not yet migrated) |
| R3 | Extract `_compute_new_song_id` to shared module | NEW | Needed for testability + admin command reuse |
| R4 | Pre-existing lint: f-string without placeholder | NEW | Fix in `db.py:439` |
| R5 | Pre-existing lint: lambda assignment | NEW | Fix via extraction to `id_utils.py` (eliminates both issues) |
| R6 | Integration tests for all 12 items | NEW | No integration tests were written in round 1 |
| R7 | M5: `last_sync.json` stored outside database | P3 | Move sync metadata into songsets.db to prevent state divergence |

---

## R2: Register Admin `migrate-song-ids` Command

**Severity:** HIGH — song IDs not yet migrated; command is inaccessible
**Files:** `src/stream_of_worship/admin/main.py`, `src/stream_of_worship/admin/commands/migrate.py`

### Problem

`migrate.py` defines `app = typer.Typer(help="Database migration operations")` with `@app.command("song-ids")`, but this Typer sub-app is never imported or registered in `main.py`. The command is orphaned code.

Song IDs have NOT been migrated yet — the admin database still uses old row-based IDs. The migration command must be accessible to run the one-time `song-ids` migration.

### Implementation Steps

**Step 1: Import the migrate module in `main.py`.**

In `src/stream_of_worship/admin/main.py`, add import:

```python
from stream_of_worship.admin.commands import migrate as migrate_commands
```

**Step 2: Register the migrate Typer sub-app under `db`.**

The migrate command logically belongs under the `db` subcommand group since it operates on the database. Add it as a nested sub-app:

```python
db_commands.app.add_typer(migrate_commands.app, name="migrate", help="Database migration operations")
```

This produces the CLI path: `sow-admin db migrate song-ids`

**Step 3: Update the main callback docstring.**

Add `migrate` to the `db` subcommand documentation in `main.py`:

```python
* [bold cyan]db[/bold cyan] - Database operations (init, status, reset, migrate)
```

**Step 4: Update migrate.py's user-warning message.**

Since M4 is dropped, lines 189-190 of `migrate.py` warn about user songset migration. Update to reflect current state:

```python
console.print(f"[green]Successfully migrated {len(id_map)} song IDs![/green]")
```

Remove the two `[dim]` lines about manual user migration.

### Verification

- Run `sow-admin db migrate --help` — should show the `song-ids` subcommand
- Run `sow-admin db migrate song-ids --help` — should show `--config` and `--dry-run` options
- Run `sow-admin db migrate song-ids --dry-run` — should report song IDs needing migration (since migration hasn't been run yet)

---

## R3: Extract `_compute_new_song_id` to Shared Module

**Severity:** MEDIUM — testability + reuse
**Files:** `src/stream_of_worship/admin/db/id_utils.py` (NEW), `src/stream_of_worship/admin/commands/migrate.py`

### Problem

`_compute_new_song_id` is a private function inside `migrate.py`. It cannot be imported by other modules or tested independently. Additionally, it contains a lambda assignment (lint issue R5) that should be converted to a proper `def`.

### Implementation Steps

**Step 1: Create `src/stream_of_worship/admin/db/id_utils.py`.**

```python
"""Shared utilities for song ID computation."""

import hashlib
import re
import unicodedata

from pypinyin import lazy_pinyin


def _normalize(s: str) -> str:
    """Normalize string for ID computation: NFKC + strip."""
    return unicodedata.normalize("NFKC", (s or "").strip())


def compute_new_song_id(title: str, composer: str, lyricist: str) -> str:
    """Compute the new stable song ID format.

    Format: <pinyin_slug>_<8-hex-hash>
    Hash is computed from: sha256(NFKC(title) + "|" + NFKC(composer) + "|" + NFKC(lyricist))[:8]

    Args:
        title: Song title (Chinese or English)
        composer: Composer name (may be None/empty)
        lyricist: Lyricist name (may be None/empty)

    Returns:
        New content-hash-based song ID
    """
    pinyin_parts = lazy_pinyin(_normalize(title))
    slug = re.sub(r"[^a-z0-9_]", "", "_".join(pinyin_parts).lower())
    payload = f"{_normalize(title)}|{_normalize(composer)}|{_normalize(lyricist)}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]
    song_id = f"{slug}_{digest}"
    if len(song_id) > 100:
        song_id = f"{slug[:91]}_{digest}"
    return song_id
```

Key changes from the original:
- `norm = lambda s: ...` → `def _normalize(s: str) -> str:` (fixes E731 lint issue)
- Function renamed from `_compute_new_song_id` to `compute_new_song_id` (public, no underscore prefix — it's a shared utility)
- Added docstring with Args/Returns

**Step 2: Update `migrate.py` to import from `id_utils.py`.**

In `src/stream_of_worship/admin/commands/migrate.py`:

Remove the `_compute_new_song_id` function definition (lines 23-37) and the `norm` lambda (line 29).

Add import:

```python
from stream_of_worship.admin.db.id_utils import compute_new_song_id
```

Update all call sites in `migrate.py` from `_compute_new_song_id(...)` to `compute_new_song_id(...)`.

Remove unused imports from `migrate.py`: `hashlib`, `re`, `unicodedata`, `pypinyin.lazy_pinyin` (all moved to `id_utils.py`).

**Step 3: Verify `id_utils.py` is importable from app code.**

The app package (`stream_of_worship.app`) can import from admin since both are under the same namespace package. Verify with:

```python
from stream_of_worship.admin.db.id_utils import compute_new_song_id
```

### Verification

- `from stream_of_worship.admin.db.id_utils import compute_new_song_id` succeeds
- `migrate.py` still works: `sow-admin db migrate song-ids --dry-run` produces same output
- Ruff lint: E731 no longer triggered on the old lambda (it's removed)
- Unit tests for `compute_new_song_id` pass (see R6)

---

## R4: Fix Pre-existing Lint — F-string Without Placeholder

**Severity:** LOW — lint violation
**File:** `src/stream_of_worship/admin/commands/db.py:439`

### Problem

```python
console.print(f"[bold]Bootstrapping Turso database...[/bold]")
```

The `f` prefix is unnecessary — the `{...}` syntax is Rich markup, not f-string interpolation. This triggers RUF027 / similar lint rules.

### Implementation Steps

**Step 1: Remove the `f` prefix.**

```python
# BEFORE:
console.print(f"[bold]Bootstrapping Turso database...[/bold]")

# AFTER:
console.print("[bold]Bootstrapping Turso database...[/bold]")
```

### Verification

- Ruff lint: RUF027 no longer triggered on this line
- Rich markup still renders correctly (the `[bold]` tags are Rich, not f-string)

---

## R5: Fix Pre-existing Lint — Lambda Assignment

**Severity:** LOW — lint violation (E731)
**File:** `src/stream_of_worship/admin/commands/migrate.py:29`

### Problem

```python
norm = lambda s: unicodedata.normalize("NFKC", (s or "").strip())
```

Assigning a lambda to a variable violates PEP 8 (E731: "do not assign a lambda expression, use a def").

### Implementation Steps

This is resolved by R3 — the lambda is eliminated when `_compute_new_song_id` is extracted to `id_utils.py` with a proper `def _normalize(s: str) -> str:` function. No separate fix needed.

If R3 is deferred, a standalone fix would be to convert the lambda in-place:

```python
# BEFORE:
norm = lambda s: unicodedata.normalize("NFKC", (s or "").strip())

# AFTER:
def _normalize(s: str) -> str:
    return unicodedata.normalize("NFKC", (s or "").strip())
```

And update `norm(title)` → `_normalize(title)`, `norm(composer)` → `_normalize(composer)`, `norm(lyricist)` → `_normalize(lyricist)`.

### Verification

- Ruff lint: E731 no longer triggered
- `compute_new_song_id` produces identical output after refactoring

---

## R6: Integration Tests for All 12 Original Issues

The implementation summary notes: "Integration tests have not been written yet." The original plan specified per-PR integration tests for each issue. This section provides detailed test plans for all 12 items (11 implemented + M4 which is dropped, but a test for the migration command is still needed).

### Test File Organization

Add tests to the existing test directories following current conventions:

| Location | Purpose |
|----------|---------|
| `tests/admin/test_models.py` | Extend with `Song.from_row` / `Recording.from_row` schema version tests |
| `tests/admin/db/test_id_utils.py` | NEW — tests for `compute_new_song_id` |
| `tests/admin/commands/test_migrate_commands.py` | NEW — tests for `migrate-song-ids` CLI command |
| `tests/app/db/test_models.py` | Extend with `SongsetItem.to_dict(include_joined=False)` test |
| `tests/app/db/test_songset_client.py` | Extend with `snapshot_db` integrity test |
| `tests/app/services/test_catalog.py` | Extend with JOIN offset / orphan detection tests |
| `tests/app/services/test_catalog_cross_db.py` | Extend with cross-DB orphan / deleted_at tests |
| `tests/app/services/test_songset_io.py` | Extend with import-via-client / export-no-nulls tests |
| `tests/app/services/test_sync.py` | Extend with thread-safety / worker-callable tests |

---

### R6.1: Test C1 — JOIN Column Offset (P0)

**What was fixed:** `SELECT s.*` replaced with explicit column list; row split offset changed from 16 to `SONG_COLUMN_COUNT` (17).

**Test location:** `tests/app/services/test_catalog_cross_db.py` (extends existing `TestCrossDBLookup`)

**Test cases:**

1. **`test_join_query_splits_at_correct_offset`**
   - Create a catalog DB with full 17-column songs and 28-column recordings
   - Insert a song with `deleted_at=NULL` and a recording with known `content_hash`
   - Call `CatalogService._list_analyzed_songs()` (or equivalent public method)
   - Assert `SongWithRecording.recording.content_hash` matches the inserted recording's `content_hash` (not shifted)
   - Assert `SongWithRecording.song.deleted_at` is `None` (correct column, not off-by-one)

2. **`test_join_query_with_deleted_at_populated`**
   - Insert a song with `deleted_at='2026-01-01'` and a recording
   - Call the analyzed songs list method with `deleted_at IS NULL` filter
   - Assert the soft-deleted song is NOT returned

3. **`test_all_three_join_methods_correct`**
   - Test `_list_analyzed_songs()`, `_list_lrc_songs()`, and `_search_lrc_songs()` individually
   - Each should split at `SONG_COLUMN_COUNT`, not at a hard-coded value

**Key test data setup:**

```python
# Full 17-column song row
song_row = (
    "new_format_id",        # 0: id
    "Test Song",            # 1: title
    "test_song_pinyin",     # 2: title_pinyin
    "Test Composer",        # 3: composer
    "Test Lyricist",        # 4: lyricist
    "Test Album",           # 5: album_name
    NULL,                   # 6: album_series
    "G",                    # 7: musical_key
    NULL,                   # 8: lyrics_raw
    NULL,                   # 9: lyrics_lines
    NULL,                   # 10: sections
    "http://test",          # 11: source_url
    1,                      # 12: table_row_number
    "2024-01-01",           # 13: scraped_at
    "2024-01-01",           # 14: created_at
    "2024-01-01",           # 15: updated_at
    None,                   # 16: deleted_at
)
```

---

### R6.2: Test C2 — Recording.from_row 28-Column Schema (P0)

**What was fixed:** Added 28-column case for `Recording.from_row` with `youtube_url` at 25, `visibility_status` at 26, `deleted_at` at 27.

**Test location:** `tests/admin/test_models.py` (extend existing Recording tests)

**Test cases:**

1. **`test_recording_from_row_28_columns`**
   - Construct a 28-element tuple with known values at indices 25 (youtube_url), 26 (visibility_status), 27 (deleted_at)
   - Call `Recording.from_row(row)`
   - Assert `recording.youtube_url == expected_youtube_url`
   - Assert `recording.visibility_status == "published"`
   - Assert `recording.deleted_at == "2026-01-01"`
   - Assert `recording.is_published == True`

2. **`test_recording_from_row_27_columns`**
   - 27-element tuple: youtube_url at 25, visibility_status at 26, deleted_at=None
   - Assert `deleted_at is None`

3. **`test_recording_from_row_26_columns`**
   - 26-element tuple: youtube_url at 25, visibility_status=None
   - Assert `visibility_status is None` and `deleted_at is None`

4. **`test_recording_from_row_25_columns`**
   - 25-element tuple: no youtube_url, no visibility_status
   - Assert `youtube_url is None`, `visibility_status is None`, `deleted_at is None`

**Test data setup:**

```python
def _make_recording_row(extra_columns: list) -> tuple:
    """Build a recording row tuple with variable trailing columns."""
    base = [
        "abc123" * 8,       # 0: content_hash
        "abc123def456",      # 1: hash_prefix
        "song_1",            # 2: song_id
        "test.mp3",          # 3: original_filename
        1000,                # 4: file_size_bytes
        "2024-01-01",        # 5: imported_at
        None,                # 6: r2_audio_url
        None,                # 7: r2_stems_url
        None,                # 8: r2_lrc_url
        180.5,               # 9: duration_seconds
        120.0,               # 10: tempo_bpm
        "G",                 # 11: musical_key
        "Major",             # 12: musical_mode
        0.95,                # 13: key_confidence
        -14.0,               # 14: loudness_db
        None,                # 15: beats
        None,                # 16: downbeats
        None,                # 17: sections
        None,                # 18: embeddings_shape
        "completed",         # 19: analysis_status
        None,                # 20: analysis_job_id
        "completed",         # 21: lrc_status
        None,                # 22: lrc_job_id
    ]
    base.extend(extra_columns)
    return tuple(base)

# 28-col: add created_at(23), updated_at(24), youtube_url(25), visibility_status(26), deleted_at(27)
row_28 = _make_recording_row(["2024-01-01", "2024-01-02", "https://youtube.com/watch?v=x", "published", "2026-01-01"])
```

---

### R6.3: Test H6 — `run_worker` Called with Result Instead of Callable (P0)

**What was fixed:** `self.run_worker(do_sync(), ...)` → `self.run_worker(do_sync, thread=True, ...)` and similar for `_sync_in_background`.

**Test location:** `src/stream_of_worship/tests/unit/test_tui_services.py` (extend existing)

**Test cases:**

1. **`test_action_sync_catalog_passes_callable_to_run_worker`**
   - Mock `self.run_worker`
   - Call `app.action_sync_catalog()`
   - Assert `run_worker` was called with a callable (first arg is callable, not a return value)
   - Assert `thread=True` was passed

2. **`test_on_mount_passes_callable_to_run_worker`**
   - Mock `self.run_worker`
   - Call `app.on_mount()`
   - Assert `run_worker` was called with `self._sync_in_background` (method reference, not invocation)

**Note:** These are unit tests using mocks. Full integration test (UI responsiveness) requires manual testing or a Textual test harness.

---

### R6.4: Test H2 — ALTER TABLE Migration for `deleted_at` (P1)

**What was fixed:** Added `ALTER TABLE {songs,recordings} ADD COLUMN deleted_at TIMESTAMP` to three locations: `DatabaseClient.initialize_schema()`, `ReadOnlyClient._migrate_schema()`, `turso_bootstrap`.

**Test location:** `tests/admin/commands/test_db_commands.py` (extend existing), `tests/app/db/test_read_client.py` (extend existing)

**Test cases:**

1. **`test_initialize_schema_adds_deleted_at_to_existing_db`**
   - Create a database with schema version that lacks `deleted_at`
   - Call `DatabaseClient.initialize_schema()`
   - Run `PRAGMA table_info(songs)` and `PRAGMA table_info(recordings)`
   - Assert `deleted_at` column exists in both tables

2. **`test_initialize_schema_idempotent_with_deleted_at`**
   - Call `initialize_schema()` twice
   - Assert no error on second call
   - Assert `deleted_at` column still exists

3. **`test_read_client_migrate_schema_adds_deleted_at`**
   - Create a catalog DB file without `deleted_at`
   - Create `ReadOnlyClient` pointing to it
   - Access `read_client.connection` (triggers lazy init + migration)
   - Assert `deleted_at` column exists in both tables

4. **`test_turso_bootstrap_includes_deleted_at_migration`**
   - Use `CliRunner` to invoke `turso-bootstrap` (with mocked Turso connection)
   - Verify the resulting schema includes `deleted_at`

---

### R6.5: Test H4 — Redundant sqlite3 Connection (P1)

**What was fixed:** Verified no redundant connection exists in current code.

**Test location:** `tests/app/db/test_read_client.py` (new test class)

**Test cases:**

1. **`test_no_redundant_sqlite3_connection_to_catalog_db`**
   - Search the `app/` package for `sqlite3.connect` calls that reference the catalog DB path
   - This is a static analysis test: import `app.app`, `app.db.read_client`, `app.services.*`
   - Assert no module opens a second `sqlite3.connect` to the same path as `ReadOnlyClient`
   - Alternatively, use `unittest.mock.patch("sqlite3.connect")` and verify that only expected calls are made during app initialization

---

### R6.6: Test H5 — `_sync_in_background` Blocks Event Loop (P1)

**What was fixed:** Converted `_sync_in_background` from async to sync; added `thread=True` to `run_worker`; uses `call_from_thread` for UI updates.

**Test location:** `src/stream_of_worship/tests/unit/test_tui_services.py` (extend existing)

**Test cases:**

1. **`test_sync_in_background_is_synchronous_function`**
   - Import `SowApp._sync_in_background`
   - Assert `inspect.iscoroutinefunction(SowApp._sync_in_background) is False`

2. **`test_sync_in_background_uses_call_from_thread_for_notifications`**
   - Mock `self.call_from_thread` and `self.sync_service.execute_sync`
   - Call `_sync_in_background()`
   - Assert `call_from_thread` was called (not `self.notify` directly)

3. **`test_sync_worker_runs_on_thread`**
   - Mock `self.run_worker`
   - Trigger sync via `on_mount` or `action_sync_catalog`
   - Assert `run_worker` called with `thread=True`

---

### R6.7: Test H1+M3 — `deleted_at` on Models + Orphan Detection (P1)

**What was fixed:** Added `deleted_at` to Song and Recording; `SongsetItemWithDetails.is_orphan` checks for `deleted_at is not None`; `display_title` returns "Removed: {title}".

**Test location:** `tests/admin/test_models.py` (Song/Recording), `tests/app/services/test_catalog_cross_db.py` (orphan detection)

**Test cases — Song model:**

1. **`test_song_from_row_17_columns_has_deleted_at`**
   - 17-element tuple with `deleted_at` at index 16
   - Assert `song.deleted_at == "2026-01-01"`

2. **`test_song_from_row_16_columns_deleted_at_is_none`**
   - 16-element tuple (legacy)
   - Assert `song.deleted_at is None`

3. **`test_song_to_dict_includes_deleted_at`**
   - Create a Song with `deleted_at="2026-01-01"`
   - Assert `"deleted_at"` in `song.to_dict()`

**Test cases — Orphan detection:**

4. **`test_is_orphan_returns_true_for_soft_deleted_song`**
   - Create `SongsetItemWithDetails` with a song where `song.deleted_at is not None`
   - Assert `item.is_orphan is True`

5. **`test_is_orphan_returns_true_for_soft_deleted_recording`**
   - Create `SongsetItemWithDetails` with a recording where `recording.deleted_at is not None`
   - Assert `item.is_orphan is True`

6. **`test_is_orphan_returns_false_for_active_items`**
   - Both song and recording have `deleted_at=None`
   - Assert `item.is_orphan is False`

7. **`test_display_title_shows_removed_prefix_for_deleted_song`**
   - Song with `deleted_at="2026-01-01"`, `title="Amazing Grace"`
   - Assert `item.display_title == "Removed: Amazing Grace"`

---

### R6.8: Test H3 — `import_songset` Bypasses SongsetClient API (P1)

**What was fixed:** `import_songset` now uses `SongsetClient.create_songset()` and `SongsetClient.add_item()` instead of raw SQL.

**Test location:** `tests/app/services/test_songset_io.py` (extend existing `TestSongsetImport`)

**Test cases:**

1. **`test_import_uses_create_songset_with_id`**
   - Import a songset JSON with a specific ID
   - Assert the created songset has the imported ID (not a generated one)
   - Verify by calling `songset_client.get_songset(imported_id)`

2. **`test_import_uses_add_item_for_each_item`**
   - Import a songset JSON with 3 items
   - Query `songset_items` table directly
   - Assert 3 items exist with correct `song_id`, `position`, etc.

3. **`test_import_validates_recordings`**
   - Import a songset with a `recording_hash_prefix` that doesn't exist in catalog
   - Assert a warning is generated
   - Assert the item is still imported (orphan handling)

4. **`test_import_preserves_crossfade_params`**
   - Import a songset item with `crossfade_enabled=true`, `crossfade_duration_seconds=3.0`, `key_shift_semitones=2`, `tempo_ratio=1.1`
   - Assert the imported item has these values in the database

5. **`test_import_no_raw_sql_in_songset_io`**
   - Static analysis: read `songset_io.py` source
   - Assert `sqlite3` is not imported
   - Assert no `cursor.execute` calls exist in the import method

---

### R6.9: Test M1 — `snapshot_db` Uses SQLite Backup API (P2)

**What was fixed:** Replaced `shutil.copy2()` with `sqlite3.Connection.backup()`.

**Test location:** `tests/app/db/test_songset_client.py` (extend existing)

**Test cases:**

1. **`test_snapshot_db_creates_valid_backup`**
   - Create a SongsetClient, add a songset with items
   - Call `snapshot_db()`
   - Open the backup file with `sqlite3.connect`
   - Run `PRAGMA integrity_check` — assert returns `ok`
   - Assert songset data is present in backup

2. **`test_snapshot_db_uses_backup_api_not_file_copy`**
   - Static analysis: read `songset_client.py` source
   - Assert `shutil` is not imported
   - Assert `connection.backup(` exists in `snapshot_db` method

3. **`test_snapshot_db_prunes_old_backups`**
   - Set `retention=2`
   - Call `snapshot_db()` 4 times (with small delays for different timestamps)
   - Assert only 2 backup files remain

---

### R6.10: Test M6+M7 — `executemany` for Bootstrap Seeding and Migration UPDATEs (P2)

**What was fixed:** Replaced per-row INSERT/UPDATE loops with `executemany()`.

**Test location:** `tests/admin/commands/test_db_commands.py` (M6), `tests/admin/commands/test_migrate_commands.py` (M7)

**Test cases — M6 (bootstrap seeding):**

1. **`test_turso_bootstrap_uses_executemany_for_songs`**
   - Static analysis: read `db.py` source for `turso_bootstrap`
   - Assert `cursor.executemany(` appears in the songs seeding section
   - Assert no per-row `cursor.execute(INSERT OR REPLACE INTO songs...)` loop

2. **`test_turso_bootstrap_uses_executemany_for_recordings`**
   - Same pattern for recordings

**Test cases — M7 (migration UPDATEs):**

3. **`test_migrate_uses_executemany_for_recordings_update`**
   - Static analysis: read `migrate.py` source
   - Assert `cursor.executemany("UPDATE recordings SET song_id = ?...")` exists
   - Assert chunked approach is used (chunk_size variable)

4. **`test_migrate_uses_executemany_for_songs_update`**
   - Same pattern for songs.id UPDATE

---

### R6.11: Test M4 — Migration Command (P2, dropped as auto-migration but CLI still needed)

M4 (user-side auto-migration) is dropped. However, the admin `migrate-song-ids` command needs tests since it hasn't been tested at all and song IDs haven't been migrated yet.

**Test location:** `tests/admin/commands/test_migrate_commands.py` (NEW)

**Test cases:**

1. **`test_migrate_song_ids_dry_run`**
   - Create a database with old-format song IDs
   - Run `sow-admin db migrate song-ids --dry-run`
   - Assert output shows sample ID mappings
   - Assert no changes were made to the database

2. **`test_migrate_song_ids_updates_recordings_song_id`**
   - Create DB with old-format IDs, recordings referencing those IDs
   - Run migration
   - Assert `recordings.song_id` now matches new format
   - Assert FK integrity maintained

3. **`test_migrate_song_ids_updates_songs_id`**
   - After migration, assert `songs.id` is in new format

4. **`test_migrate_song_ids_idempotent`**
   - Run migration twice
   - Second run should print "No migration needed" and exit

5. **`test_migrate_song_ids_preserves_recording_data`**
   - Create a recording with `analysis_status='completed'`, `lrc_status='completed'`, `visibility_status='published'`
   - Run migration
   - Assert all recording status columns are unchanged
   - Assert `r2_audio_url`, `r2_lrc_url` are unchanged

6. **`test_migrate_song_ids_with_empty_database`**
   - Empty songs table
   - Run migration
   - Assert "No songs found" message

---

### R6.12: Test M2 — SongsetItem Export Excludes Null Joined Fields (P2)

**What was fixed:** Added `include_joined: bool = False` parameter to `SongsetItem.to_dict()`.

**Test location:** `tests/app/db/test_models.py` (extend existing), `tests/app/services/test_songset_io.py` (extend existing)

**Test cases:**

1. **`test_songset_item_to_dict_excludes_joined_by_default`**
   - Create a `SongsetItem` with `song_title="Test"`, `duration_seconds=180.0`, etc.
   - Call `item.to_dict()`
   - Assert `song_title` NOT in dict
   - Assert `duration_seconds` NOT in dict

2. **`test_songset_item_to_dict_includes_joined_when_requested`**
   - Call `item.to_dict(include_joined=True)`
   - Assert `song_title` IS in dict
   - Assert `duration_seconds` IS in dict

3. **`test_export_songset_json_has_no_joined_fields`**
   - Export a songset to JSON
   - Parse the JSON
   - Assert items do NOT contain `song_title`, `duration_seconds`, etc.

---

### R6.13: Test `compute_new_song_id` — New Shared Utility

**Test location:** `tests/admin/db/test_id_utils.py` (NEW)

**Test cases:**

1. **`test_compute_new_song_id_english_title`**
   - `compute_new_song_id("Amazing Grace", "John Newton", "")`
   - Assert result matches `amazing_grace_<8-hex-hash>`
   - Verify hash is deterministic

2. **`test_compute_new_song_id_chinese_title`**
   - `compute_new_song_id("奇妙恩典", "牛顿", "作者")`
   - Assert result starts with pinyin slug (e.g., `qi_miao_en_dian_`)
   - Assert 8-hex-hash suffix

3. **`test_compute_new_song_id_none_composer_lyricist`**
   - `compute_new_song_id("Test Song", None, None)`
   - Assert no crash; hash computed from `"Test Song||"`

4. **`test_compute_new_song_id_idempotent`**
   - Call twice with same arguments
   - Assert identical results

5. **`test_compute_new_song_id_truncation`**
   - Use a very long title (>100 chars)
   - Assert result length <= 100

6. **`test_normalize_function`**
   - `""` → empty string
   - `None` → empty string
   - `"  test  "` → `"test"` (NFKC + strip)

---

### R6.14: Test M5 — Sync Metadata in Database Instead of JSON File

**What was fixed:** Moved `last_sync.json` metadata to `_sync_metadata` table in songsets DB.

**Test location:** `tests/app/services/test_sync.py` (extend existing)

**Test cases:**

1. **`test_sync_metadata_stored_in_database`**
   - Run sync with real `SongsetClient`
   - Assert `_sync_metadata` table has `last_sync_at` key with non-None value
   - Assert no `last_sync.json` file is created

2. **`test_get_sync_status_reads_from_database`**
   - Set `last_sync_at` in `_sync_metadata` via `songset_client.set_metadata()`
   - Call `AppSyncService.get_sync_status()`
   - Assert `status.last_sync_at` matches the stored value

3. **`test_last_sync_json_migration`**
   - Create a `last_sync.json` file with `last_sync_at` and `sync_version`
   - Initialize `AppSyncService`
   - Assert `_sync_metadata` table has the migrated values
   - Assert `last_sync.json` file was deleted

4. **`test_sync_metadata_no_stale_json_after_db_wipe`**
   - Sync to populate `_sync_metadata`
   - Delete the songsets DB
   - Create a new `SongsetClient` (fresh DB)
   - Assert `get_metadata("last_sync_at")` returns None (no stale state)

5. **`test_set_metadata_upsert`**
   - Call `set_metadata("last_sync_at", "2024-01-01")`
   - Call `set_metadata("last_sync_at", "2024-06-01")`
   - Assert only one row exists and value is "2024-06-01"

---

## R7: Move Sync Metadata from `last_sync.json` into Songsets Database

**Severity:** MEDIUM (P3) — state divergence risk
**Source:** `reports/turso_v2_implementation_code_review.md` (M5)
**Files:** `src/stream_of_worship/app/db/songset_client.py`, `src/stream_of_worship/app/db/schema.py`, `src/stream_of_worship/app/services/sync.py`

### Problem

Sync timestamp is written to a standalone JSON file (`config_dir / "last_sync.json"`). This file can:

- Be deleted while the DB is current → app re-syncs unnecessarily
- Survive while the DB is wiped → app thinks it's up to date
- Become stale if the DB is replaced by copy/restore
- Be shared across multiple users on the same machine (if config dir is shared)

The songsets database is local and read-write, making it the natural home for this metadata. The catalog replica is read-only (RO token), so a `_metadata` table there is not an option.

### Implementation Steps

**Step 1: Add `_sync_metadata` table to app schema.**

In `src/stream_of_worship/app/db/schema.py`, add:

```python
CREATE_SYNC_METADATA_TABLE = """
CREATE TABLE IF NOT EXISTS _sync_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
```

Add it to `ALL_APP_SCHEMA_STATEMENTS`:

```python
ALL_APP_SCHEMA_STATEMENTS = [
    CREATE_SONGSETS_TABLE,
    CREATE_SONGSET_ITEMS_TABLE,
    CREATE_SYNC_METADATA_TABLE,
    *CREATE_APP_INDEXES,
    CREATE_SONGSETS_UPDATE_TRIGGER,
]
```

**Step 2: Add metadata methods to `SongsetClient`.**

In `src/stream_of_worship/app/db/songset_client.py`, add two methods:

```python
def get_metadata(self, key: str, default: Optional[str] = None) -> Optional[str]:
    cursor = self.connection.cursor()
    cursor.execute("SELECT value FROM _sync_metadata WHERE key = ?", (key,))
    row = cursor.fetchone()
    return row[0] if row else default

def set_metadata(self, key: str, value: str) -> None:
    with self.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO _sync_metadata (key, value) VALUES (?, ?)",
            (key, value),
        )
```

**Step 3: Update `AppSyncService` to read/write from `SongsetClient` instead of JSON file.**

In `src/stream_of_worship/app/services/sync.py`:

Remove `self._last_sync_file = config_dir / "last_sync.json"` from `__init__`.

Update `_update_last_sync()`:

```python
def _update_last_sync(self) -> None:
    self.songset_client.set_metadata("last_sync_at", datetime.now().isoformat())
    self.songset_client.set_metadata("sync_version", "2")
```

Update `get_sync_status()` — replace the JSON file read with:

```python
last_sync_at = self.songset_client.get_metadata("last_sync_at")
sync_version = self.songset_client.get_metadata("sync_version", "2")
```

**Step 4: Add migration for existing `last_sync.json` files.**

In `AppSyncService.__init__` or `execute_sync()`, add a one-time migration that reads the old `last_sync.json` (if it exists) and writes it to `_sync_metadata`, then deletes the file:

```python
def _migrate_last_sync_json(self) -> None:
    last_sync_file = self.config_dir / "last_sync.json"
    if last_sync_file.exists():
        try:
            with open(last_sync_file) as f:
                data = json.load(f)
            if data.get("last_sync_at"):
                self.songset_client.set_metadata("last_sync_at", data["last_sync_at"])
            if data.get("sync_version"):
                self.songset_client.set_metadata("sync_version", data["sync_version"])
            last_sync_file.unlink()
        except Exception:
            pass
```

Call this once during initialization (e.g., at the start of `execute_sync()` or in `__init__`). After migration, the JSON file is deleted, so this is a no-op on subsequent runs.

**Step 5: Remove `last_sync.json` references from tests.**

Update `tests/app/services/test_sync.py` to use real `SongsetClient` instead of file-based sync metadata. Replace `last_sync_file.write_text(...)` patterns with `songset_client.set_metadata(...)` calls.

### Verification

- Run `sow-app sync` — verify `last_sync_at` is stored in `_sync_metadata` table, not in a JSON file
- Delete the songsets DB and re-sync — verify `last_sync_at` is `None` (no stale JSON file)
- Create a `last_sync.json` file manually, then sync — verify it's migrated and deleted
- `sow-app sync` status command should still display `last_sync_at` correctly

---

## Implementation Order and Dependencies

```
R3 (extract compute_new_song_id) ─────► R2 (register migrate command)
       │                                      │
       │                                      └──► R6.11 (migrate command tests)
       │
       └──► R6.13 (id_utils tests)

R4 (f-string lint) ─── independent

R5 (lambda lint) ─── resolved by R3

R7 (M5: last_sync.json → songsets DB) ─── independent
       │
       └──► R6.14 (M5 sync metadata tests)

R6.1  (C1 JOIN offset tests)         ─── independent
R6.2  (C2 Recording 28-col tests)    ─── independent
R6.3  (H6 run_worker tests)          ─── independent
R6.4  (H2 migration tests)           ─── independent
R6.5  (H4 redundant conn tests)      ─── independent
R6.6  (H5 blocking sync tests)       ─── independent
R6.7  (H1+M3 deleted_at tests)       ─── independent
R6.8  (H3 import tests)              ─── independent
R6.9  (M1 snapshot tests)            ─── independent
R6.10 (M6+M7 executemany tests)      ─── depends on R3 (migrate.py refactored)
R6.12 (M2 export tests)             ─── independent
```

### Recommended Commit Structure

| # | Scope | Items | Files Changed |
|---|-------|-------|---------------|
| 1 | Extract id_utils + register migrate command + lint fixes | R2, R3, R4, R5 | `admin/db/id_utils.py` (new), `admin/commands/migrate.py`, `admin/main.py`, `admin/commands/db.py` |
| 2 | Move sync metadata to songsets DB | R7 | `app/db/schema.py`, `app/db/songset_client.py`, `app/services/sync.py` |
| 3 | Integration tests — model layer | R6.2, R6.7, R6.12, R6.13 | `tests/admin/test_models.py`, `tests/admin/db/test_id_utils.py`, `tests/app/db/test_models.py` |
| 4 | Integration tests — service layer | R6.1, R6.3, R6.6, R6.8, R6.14 | `tests/app/services/test_catalog*.py`, `test_tui_services.py`, `test_songset_io.py`, `test_sync.py` |
| 5 | Integration tests — DB/infra layer | R6.4, R6.5, R6.9, R6.10, R6.11 | `tests/admin/commands/test_db_commands.py`, `tests/admin/commands/test_migrate_commands.py` (new), `tests/app/db/test_songset_client.py`, `tests/app/db/test_read_client.py` |

### Testing Strategy

After each commit:

```bash
# Lint check
ruff check src/stream_of_worship/

# Run all tests
pytest tests/ src/stream_of_worship/tests/ -v
```

After all commits, verify:

```bash
# Full quality gate
ruff check src/stream_of_worship/ && pytest tests/ src/stream_of_worship/tests/ -v
```

---

## Files to Create/Modify Summary

| File | Action | Items |
|------|--------|-------|
| `src/stream_of_worship/admin/db/id_utils.py` | CREATE | R3 |
| `src/stream_of_worship/admin/commands/migrate.py` | MODIFY | R3, R5 |
| `src/stream_of_worship/admin/main.py` | MODIFY | R2 |
| `src/stream_of_worship/admin/commands/db.py` | MODIFY | R4 |
| `src/stream_of_worship/app/db/schema.py` | MODIFY | R7 |
| `src/stream_of_worship/app/db/songset_client.py` | MODIFY | R7 |
| `src/stream_of_worship/app/services/sync.py` | MODIFY | R7 |
| `tests/admin/db/test_id_utils.py` | CREATE | R6.13 |
| `tests/admin/commands/test_migrate_commands.py` | CREATE | R6.10, R6.11 |
| `tests/admin/test_models.py` | MODIFY | R6.2, R6.7 |
| `tests/app/db/test_models.py` | MODIFY | R6.12 |
| `tests/app/db/test_songset_client.py` | MODIFY | R6.9 |
| `tests/app/db/test_read_client.py` | MODIFY | R6.4, R6.5 |
| `tests/app/services/test_catalog.py` | MODIFY | R6.1 |
| `tests/app/services/test_catalog_cross_db.py` | MODIFY | R6.1, R6.7 |
| `tests/app/services/test_songset_io.py` | MODIFY | R6.8, R6.12 |
| `tests/app/services/test_sync.py` | MODIFY | R6.6, R6.14 |
| `src/stream_of_worship/tests/unit/test_tui_services.py` | MODIFY | R6.3, R6.6 |
