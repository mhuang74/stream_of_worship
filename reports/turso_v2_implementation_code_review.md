# Turso V2 Implementation — Code Review

**Date:** 2026-04-26  
**Commit:** `a7f96bf` — feat: implement Turso DB Sync V2  
**Reviewer:** Automated review against `reports/turso_v2_implementation_summary.md`  
**Supplemented by:** PR #41 review (Gemini Code Assist)

---

## Executive Summary

The V2 implementation introduces stable content-hash song IDs, soft-delete tombstones, Turso embedded-replica sync, songset export/import, and pre-sync snapshots. The architectural decisions are sound: stable IDs fix the critical V1 integrity hole, soft-deletes preserve referential traceability, and the two-DB split (catalog replica + local songsets) is correct for the RO-token constraint.

However, the implementation contains **two critical bugs** that will cause silent data corruption or runtime failures in production, **six high-severity issues** (three from this review, three from PR #41) that undermine data integrity and runtime reliability, and several medium/low issues that should be addressed before deployment. The PR review uniquely identified concurrency and UI responsiveness problems that the automated code review missed.

---

## Critical Bugs

### C1. JOIN column offset misalignment in CatalogService

**File:** `src/stream_of_worship/app/services/catalog.py:200, 225, 344`  
**Severity:** CRITICAL — silent data corruption

The `songs` table now has 17 columns (index 0–16, with `deleted_at` at index 16). All three JOIN queries use `SELECT s.*, r.content_hash, ...`, which returns 17 song columns followed by recording columns. The code splits the result at a hardcoded offset of 16:

```python
song = Song.from_row(row_tuple[0:16])        # indices 0–15 ✓
recording = Recording.from_row(row_tuple[16:]) # starts at s.deleted_at, NOT r.content_hash ✗
```

`row_tuple[16:]` begins with `s.deleted_at` (NULL), shifting every recording column by 1. `Recording.from_row` interprets the wrong data for every field: `s.deleted_at` → `content_hash`, `r.content_hash` → `hash_prefix`, and so on. All Recording objects produced through CatalogService JOIN queries are silently corrupted.

**Affected methods:**
- `_list_analyzed_songs()` (line ~200)
- `_list_lrc_songs()` (line ~225)
- `_search_lrc_songs()` (line ~344)

**Impact:** The Browse screen, search results, and any feature using `SongWithRecording` will display wrong recording metadata (wrong hash, wrong duration, wrong key, etc.). Playback lookups using `hash_prefix` will fail or play the wrong file.

**Recommendation:**
- Immediate fix: Change split to `row_tuple[0:17]` / `row_tuple[17:]` in all three methods.
- Better fix: Stop using `SELECT s.*` and instead enumerate the song columns explicitly, or use column-name-based row parsing. This prevents recurrence whenever columns are added.

---

### C2. `Recording.from_row` doesn't handle 28-column schema

**File:** `src/stream_of_worship/admin/db/models.py:200-221`  
**Severity:** CRITICAL — data loss on every Recording read

With `deleted_at` added, `SELECT * FROM recordings` now returns 28 columns. The versioned logic in `from_row` handles 25, 26, and 27 columns but has no 28-column case. The `else` branch (line 216–221) is hit:

```python
else:
    visibility_status = None
    created_at = row[23] if row_len > 23 else None
    updated_at = row[24] if row_len > 24 else None
    youtube_url = None  # always lost
```

Every recording read from the new schema loses `youtube_url` and `visibility_status`. The latter is critical: `is_published` returns `False` for all recordings, causing the UI to hide published content.

**Impact:** All recordings appear unpublished in the user app. LRC-filtered views return empty results. YouTube URL metadata is silently dropped.

**Recommendation:**
- Add a 28-column case to `from_row`:
  ```python
  if row_len == 28:
      created_at = row[23]
      updated_at = row[24]
      youtube_url = row[25]
      visibility_status = row[26]
      # deleted_at = row[27] — not in model yet (see M8)
  ```
- Long-term: Refactor to column-name-based parsing (e.g., use `sqlite3.Row` and access by name) to make the model resilient to schema additions.

---

## High-Severity Issues

### H1. Soft-deleted items not detected as orphans

**File:** `src/stream_of_worship/app/services/catalog.py:465-475`  
**Severity:** HIGH — contradicts design spec

`get_songset_with_items()` fetches songs and recordings with `include_deleted=True`, so soft-deleted entities return non-None objects. But `is_orphan` is defined as:

```python
is_orphan = song is None or recording is None
```

A soft-deleted recording (with `deleted_at` set) returns a valid Recording object, so `is_orphan` evaluates to `False`. Soft-deleted items appear as fully active in the UI, contradicting the spec: "Missing references shown as 'Removed: <title>'".

**Root cause:** The `Song` and `Recording` models lack a `deleted_at` field (see M8), so there's no way to check deletion status through the model.

**Recommendation:**
- Add `deleted_at: Optional[str] = None` to both `Song` and `Recording` dataclasses.
- Update `is_orphan` to also check `song.deleted_at is not None or recording.deleted_at is not None`.
- Or: Don't use `include_deleted=True` and instead treat "not found in active set" as orphan, then provide a separate lookup for displaying the deleted entity's title.

---

### H2. No ALTER TABLE migration for existing databases

**File:** `src/stream_of_worship/admin/db/schema.py`, `src/stream_of_worship/admin/commands/db.py`  
**Severity:** HIGH — runtime crash on upgrade

The `deleted_at` column is only defined in `CREATE TABLE IF NOT EXISTS` statements. SQLite's `CREATE TABLE IF NOT EXISTS` is a no-op when the table already exists — it does NOT add new columns. For existing databases (both local and Turso), the column is never added.

Consequences:
- Every query with `AND deleted_at IS NULL` throws `no such column: deleted_at` on pre-V2 databases.
- `turso-bootstrap` runs the same `CREATE TABLE IF NOT EXISTS` statements, so it also won't add the column to existing Turso databases.
- Users upgrading from V1 will experience crashes on every database operation.

**Recommendation:**
- Add a schema migration check (e.g., `PRAGMA table_info(songs)`) that detects missing columns.
- If `deleted_at` is missing, run:
  ```sql
  ALTER TABLE songs ADD COLUMN deleted_at TIMESTAMP;
  ALTER TABLE recordings ADD COLUMN deleted_at TIMESTAMP;
  ```
- Run this migration in `DatabaseClient.__init__`, `turso-bootstrap`, and `ReadOnlyClient.connection` initialization.
- Consider a dedicated `sow-admin db migrate-schema` command for explicit version bumps.

---

### H3. `import_songset` bypasses SongsetClient API

**File:** `src/stream_of_worship/app/services/songset_io.py:151-192`  
**Severity:** HIGH — integrity bypass, future breakage  
**Sources:** This review + PR #41 (PR-M5: missing transaction)

The import method directly executes raw SQL against `self.songset_client.connection` instead of using `SongsetClient.create_songset()` and `SongsetClient.add_item()`:

```python
cursor = conn.cursor()
cursor.execute(
    "INSERT INTO songsets (id, name, ...) VALUES (?, ?, ...)",
    (songset.id, songset.name, ...),
)
# ... then raw INSERT INTO songset_items ...
conn.commit()
```

This bypasses:
- **Recording validation:** Despite `get_recording` being provided to the constructor, it's only checked for warning generation but items are still inserted. Compare with `add_item()` which calls `validate_recording_exists()` and raises `MissingReferenceError`.
- **Position auto-assignment:** `add_item()` handles `position=None` by computing the next position; import hardcodes from JSON.
- **Transaction safety:** The raw SQL commits everything in one shot without the per-item error handling that `add_item()` provides. Per PR #41, the multiple inserts across two tables lack explicit transaction management — if an error occurs during the item import loop, the songset is left in a partially imported state with no rollback.
- **Future business logic:** Any validation, deduplication, or side effects added to `add_item()` will be silently skipped by imports.

**Recommendation:**
- Refactor to use `songset_client.create_songset()` (preserving the imported ID) and `songset_client.add_item()` for each item. This fixes both the validation bypass and the transaction safety issue, since `SongsetClient` methods manage transactions.
- If preserving the original ID in `create_songset()` is needed, add an `id` parameter to `create_songset()` rather than bypassing it.
- If the raw-SQL path must be kept temporarily, wrap the entire operation in `with conn:` for atomicity.

---

### H4. Redundant sqlite3 connection to `db_path` while libSQL replica is open

**File:** `src/stream_of_worship/app/app.py:445`  
**Severity:** HIGH — database locking / corruption risk  
**Source:** PR #41 (PR-H1)

`app.py` opens a standard `sqlite3.connect(config.db_path)` connection to the catalog database file while that same file is already opened as a libSQL embedded replica (`conn`). Two concurrent connections to the same SQLite file — one via libSQL and one via `sqlite3` — can cause database locking issues or corruption, especially under concurrent sync operations.

**Recommendation:**
- Remove the standalone `sqlite3.connect(config.db_path)` connection.
- Use the existing libSQL `conn` for all reads on the local replica file.
- If a `sqlite3` connection is needed for a specific API (e.g., `backup()`), open it only for the duration of that operation, not as a persistent connection.

---

### H5. `_sync_in_background` blocks the Textual event loop

**File:** `src/stream_of_worship/app/app.py`  
**Severity:** HIGH — UI freeze during sync  
**Source:** PR #41 (PR-H2)

The `_sync_in_background` method is defined as `async`, but it calls `self.sync_service.execute_sync()`, which is a blocking synchronous operation (performing network requests and file I/O). When run inside the Textual event loop, this blocks all UI rendering and input handling for the duration of the sync — potentially many seconds.

**Recommendation:**
- Run sync in a separate thread: `self.run_worker(do_sync, thread=True, exclusive=True)`.
- Use Textual's `Worker` API to report progress/completion back to the UI (e.g., update a status bar when sync starts/finishes).

---

### H6. `run_worker` called with result instead of callable

**File:** `src/stream_of_worship/app/app.py`  
**Severity:** HIGH — background sync never actually runs  
**Source:** PR #41 (PR-H3)

There is a bug in the `run_worker` call: `do_sync()` is being called immediately (returning `None`), and its return value is passed to `run_worker`. This means:
1. The sync runs synchronously at the call site, freezing the UI.
2. `run_worker` receives `None` instead of a callable, so no background worker is actually created.

```python
# Current (broken):
self.run_worker(do_sync(), ...)

# Fixed:
self.run_worker(do_sync, thread=True, exclusive=True)
```

**Impact:** Background sync is completely non-functional. Combined with H5, the sync always runs synchronously on the main thread, freezing the UI.

---

## Medium-Severity Issues

### M1. `snapshot_db` uses file-copy on a live SQLite database

**File:** `src/stream_of_worship/app/db/songset_client.py:278`  
**Risk:** Corrupt backup under concurrent writes  
**Sources:** This review + PR #41 (PR-M4) — **overlap, both sources independently flagged**

`shutil.copy2()` copies the database file at the filesystem level while SQLite may have in-flight WAL pages. The resulting backup may fail integrity checks or lose the last committed transaction. This was flagged independently by both this review and PR #41, reinforcing its importance.

**Recommendation:** Use SQLite's backup API:
```python
backup_conn = sqlite3.connect(backup_path)
with backup_conn:
    self.connection.backup(backup_conn)
backup_conn.close()
```

---

### M2. SongsetItem export includes always-null joined fields

**File:** `src/stream_of_worship/app/db/models.py:174-201`  
**Risk:** Bloated/confusing exports

`SongsetItem.to_dict()` exports 8 joined fields (`song_title`, `duration_seconds`, `tempo_bpm`, `recording_key`, `loudness_db`, `song_composer`, `song_lyricist`, `song_album_name`) that are always `None` when using `get_items_raw()` — the path used by export. The JSON format in the summary doc doesn't include these fields, suggesting this was unintentional.

**Recommendation:** Add an `include_joined: bool = False` parameter to `to_dict()` and omit joined fields when False. Or remove them from export since the two-step lookup pattern resolves them at read time.

---

### M3. `Song` and `Recording` models lack `deleted_at` field

**File:** `src/stream_of_worship/admin/db/models.py`  
**Risk:** No way to inspect deletion status through the model layer

Neither `Song` nor `Recording` has a `deleted_at` attribute. The column exists in the schema and is returned by `SELECT *`, but `from_row()` discards it (Song reads indices 0–15, stopping before index 16 where `deleted_at` lives). This is the root cause of H1 and makes any deletion-aware logic impossible without raw SQL.

**Recommendation:** Add `deleted_at: Optional[str] = None` to both models. Update `from_row()` to read it from the appropriate index. Update `to_dict()` to include it.

---

### M4. No user-side songset ID migration path

**File:** `src/stream_of_worship/admin/commands/migrate.py:171-172`  
**Risk:** Orphaned songset items for all existing users

The `migrate-song-ids` command updates the admin's `songsets.db` but not user-side databases. The code prints a dim warning:

```
Note: User songset_items tables will need manual migration
or will be resolved when songs are re-added to songsets.
```

But there's no tooling for users to perform this migration, and "re-adding songs" is not a realistic recovery step for non-technical worship leaders.

**Recommendation:**
- Provide a `sow-app songsets migrate-ids` command that updates `songset_items.song_id` references using the same ID mapping logic.
- Or: Include a migration step in `sow-app sync` that runs on first post-V2 sync.
- At minimum: Document the exact manual steps in a migration guide.

---

### M5. `last_sync.json` stored outside the database

**File:** `src/stream_of_worship/app/services/sync.py:141-147`  
**Risk:** State divergence between sync metadata and actual DB

Sync timestamp is written to a standalone JSON file in the config directory. This file can:
- Be deleted while the DB is current → app re-syncs unnecessarily
- Survive while the DB is wiped → app thinks it's up to date
- Become stale if the DB is replaced by copy/restore
- Be shared across multiple users on the same machine (if config dir is shared)

**Recommendation:** Store sync metadata in the songsets database (which is local and RW) or in a SQLite metadata table within the catalog replica, rather than in a standalone JSON file.

---

### M6. Turso bootstrap seeding uses individual INSERTs

**File:** `src/stream_of_worship/admin/commands/db.py`  
**Risk:** Unacceptable performance for large catalogs  
**Source:** PR #41 (PR-M1)

The `--seed` logic iterates through each song/recording/sync-metadata row and executes an individual `INSERT` statement. For a catalog with thousands of entries, this is significantly slower than using `executemany`.

**Recommendation:** Rewrite seeding loops to use `cursor.executemany()`:
```python
if songs:
    columns = ", ".join(songs[0].keys())
    placeholders = ", ".join(["?" for _ in songs[0].keys()])
    cursor.executemany(
        f"INSERT OR REPLACE INTO songs ({columns}) VALUES ({placeholders})",
        [tuple(song) for song in songs],
    )
```

---

### M7. Migration UPDATEs use individual statements

**File:** `src/stream_of_worship/admin/commands/migrate.py`  
**Risk:** Slow migration for large databases  
**Source:** PR #41 (PR-M2)

The `migrate-song-ids` command iterates through the ID map and executes individual `UPDATE` statements for each mapping. Using `executemany` would significantly improve performance.

**Recommendation:**
```python
cursor.executemany(
    "UPDATE recordings SET song_id = ? WHERE song_id = ?",
    [(new_id, old_id) for old_id, new_id in id_map.items()],
)
```

---

## Low-Severity Issues

### L1. `SONGSET_ITEMS_FULL_QUERY` is identical to `SONGSET_ITEMS_QUERY`

**File:** `src/stream_of_worship/app/db/schema.py`

The two constants contain the exact same SQL. `SONGSET_ITEMS_FULL_QUERY` is unused dead code.

**Recommendation:** Remove `SONGSET_ITEMS_FULL_QUERY` or differentiate it (e.g., include columns useful for backup/export).

---

### L2. f-string SQL injection pattern for `deleted_clause`

**Files:** `src/stream_of_worship/admin/db/client.py:416`, `src/stream_of_worship/app/db/read_client.py:231`

The `search_songs()` methods in both clients build SQL via:
```python
deleted_clause = "" if include_deleted else "deleted_at IS NULL AND "
sql = f"SELECT * FROM songs WHERE {deleted_clause}..."
```

While `include_deleted` is a bool (not user-controlled), this pattern mixes f-string SQL construction with parameterized queries in the same method. It's fragile — a future refactor could inadvertently pass user input into the f-string.

**Recommendation:** Use parameterized conditions or a query builder that avoids string concatenation entirely.

---

### L3. `list_available_albums` is N+1

**File:** `src/stream_of_worship/app/services/catalog.py:366`

The method calls `list_albums()` then `list_songs(album=..., limit=1)` per album in a loop. With ~50 albums, this issues 51 queries instead of 1.

**Recommendation:** Replace with a single query:
```sql
SELECT DISTINCT album_name FROM songs
WHERE deleted_at IS NULL
AND album_name IS NOT NULL
AND id IN (SELECT song_id FROM recordings WHERE deleted_at IS NULL)
ORDER BY album_name
```

---

### L4. `sync_version` hardcoded to "2"

**File:** `src/stream_of_worship/app/services/sync.py:108`

`get_sync_status()` defaults `sync_version` to `"2"` and only reads it from `last_sync.json` if the file exists. This should be read from the database schema or a canonical source.

---

### L5. `DatabaseStats.sync_version` default is still "1"

**File:** `src/stream_of_worship/admin/db/models.py:363`

`DatabaseStats` has `sync_version: str = "1"` while `DEFAULT_SYNC_METADATA` in schema.py is `"2"`. Inconsistency.

**Recommendation:** Update `DatabaseStats.sync_version` default to `"2"` or derive it from schema constants.

---

### L6. `validate_recording_exists` performs redundant lookups

**File:** `src/stream_of_worship/app/db/songset_client.py:243`

`add_item()` calls `validate_recording_exists()` which calls `get_recording(hash_prefix)`. Later, when the recording data is needed (e.g., for display), the same callable is invoked again. For network-backed or expensive lookups, this doubles the cost.

**Recommendation:** Return the recording object from validation and pass it through, avoiding the double lookup.

---

## Summary Table

| ID | Severity | Issue | File(s) | Source |
|----|----------|-------|---------|--------|
| C1 | CRITICAL | JOIN column offset off-by-one corrupts Recording objects | `app/services/catalog.py` | This review |
| C2 | CRITICAL | `Recording.from_row` loses fields on 28-column schema | `admin/db/models.py` | This review |
| H1 | HIGH | Soft-deleted items not detected as orphans | `app/services/catalog.py` | This review |
| H2 | HIGH | No ALTER TABLE migration for existing databases | `admin/db/schema.py`, `admin/commands/db.py` | This review |
| H3 | HIGH | `import_songset` bypasses SongsetClient validation/logic | `app/services/songset_io.py` | This review + PR #41 |
| H4 | HIGH | Redundant sqlite3 connection while libSQL replica is open | `app/app.py` | PR #41 |
| H5 | HIGH | `_sync_in_background` blocks Textual event loop | `app/app.py` | PR #41 |
| H6 | HIGH | `run_worker(do_sync(), ...)` passes result not callable | `app/app.py` | PR #41 |
| M1 | MEDIUM | File-copy backup of live SQLite database | `app/db/songset_client.py` | This review + PR #41 |
| M2 | MEDIUM | Export includes 8 always-null joined fields per item | `app/db/models.py` | This review |
| M3 | MEDIUM | Models lack `deleted_at` attribute | `admin/db/models.py` | This review |
| M4 | MEDIUM | No user-side songset ID migration path | `admin/commands/migrate.py` | This review |
| M5 | MEDIUM | `last_sync.json` can diverge from DB state | `app/services/sync.py` | This review |
| M6 | MEDIUM | Bootstrap seeding uses individual INSERTs (use `executemany`) | `admin/commands/db.py` | PR #41 |
| M7 | MEDIUM | Migration UPDATEs use individual statements (use `executemany`) | `admin/commands/migrate.py` | PR #41 |
| L1 | LOW | Duplicate `SONGSET_ITEMS_FULL_QUERY` constant | `app/db/schema.py` | This review |
| L2 | LOW | f-string SQL construction for deleted_clause | `admin/db/client.py`, `app/db/read_client.py` | This review |
| L3 | LOW | N+1 query in `list_available_albums` | `app/services/catalog.py` | This review |
| L4 | LOW | `sync_version` hardcoded to "2" | `app/services/sync.py` | This review |
| L5 | LOW | `DatabaseStats.sync_version` default is "1" | `admin/db/models.py` | This review |
| L6 | LOW | Redundant recording lookups in validation | `app/db/songset_client.py` | This review |

---

## Recommended Fix Priority

### P0 — Fix immediately (blocks any V2 deployment)

1. **C1: JOIN column offset** — Change split to `row_tuple[0:17]` / `row_tuple[17:]` in all three CatalogService methods. Better: enumerate song columns explicitly instead of `SELECT s.*`.
2. **C2: `Recording.from_row` 28-column case** — Add `row_len == 28` branch reading `youtube_url=row[25]`, `visibility_status=row[26]`, `deleted_at=row[27]`.
3. **H6: `run_worker` callable bug** — Change `run_worker(do_sync(), ...)` to `run_worker(do_sync, thread=True, exclusive=True)`. This is a showstopper: background sync is completely non-functional without this fix.

### P1 — Fix before any production or upgrade path

4. **H2: ALTER TABLE migration** — Add `PRAGMA table_info()` check on init; run `ALTER TABLE ... ADD COLUMN deleted_at` if missing. Apply in `DatabaseClient.__init__`, `turso-bootstrap`, and `ReadOnlyClient` init. Without this, every existing user will hit `no such column: deleted_at` crashes.
5. **H4: Redundant sqlite3 connection** — Remove the standalone `sqlite3.connect(config.db_path)` in `app.py`; use the existing libSQL `conn` for all reads on the local replica file.
6. **H5: Blocking sync in event loop** — Use `run_worker(..., thread=True)` to run sync in a separate thread. Add a Textual `Worker` that reports progress/completion back to the UI.
7. **H1+M3: `deleted_at` on models** — Add `deleted_at: Optional[str] = None` to `Song` and `Recording` dataclasses. Update `from_row()` and `to_dict()`. Update `is_orphan` to check `song.deleted_at is not None or recording.deleted_at is not None`.
8. **H3: Import bypasses SongsetClient** — Refactor `import_songset` to call `songset_client.create_songset()` (with `id` param) and `songset_client.add_item()` for each item. This also fixes the transaction safety issue flagged by PR #41 (no rollback on partial import).

### P2 — Fix before relying on affected features

9. **M1: SQLite backup API** — Replace `shutil.copy2()` with `sqlite3.Connection.backup()` in `snapshot_db()`. Flagged by both sources independently.
10. **M6+M7: `executemany`** — Rewrite bootstrap seeding and migration UPDATEs to use `cursor.executemany()`.
11. **M4: User-side migration** — Add `sow-app songsets migrate-ids` command or a first-sync migration step.
12. **M2: Export null fields** — Add `include_joined: bool = False` to `SongsetItem.to_dict()`.

### P3 — Batch in follow-up cleanup

13. **M5: `last_sync.json`** — Move sync metadata into songsets DB or a `_metadata` table in the catalog replica.
14. **L1–L6** — Remove dead constant, parameterize `deleted_clause`, fix N+1 in `list_available_albums`, derive `sync_version` from schema, update `DatabaseStats.sync_version` default to `"2"`, return recording from validation.

---

## Source Comparison Notes

- **C1 and C2 are the most urgent** — they corrupt every user-facing query result silently. Neither was caught by the PR review (which focused on concurrency/performance patterns rather than data-model correctness).
- **H4, H5, H6 are unique to the PR review** — these are runtime reliability problems (database locking, UI freeze, broken async dispatch) that only manifest under load or during sync. The automated code review didn't catch them because they require understanding the Textual framework's threading model and SQLite's concurrency semantics.
- **M1 is the only direct overlap** — both sources independently flagged the `shutil.copy2` backup issue, reinforcing its importance.
- **H3 + PR-M5 are complementary** — H3 says import bypasses validation; PR #41 says import lacks transaction safety. Fixing H3 (use SongsetClient methods) would also fix the transaction issue since those methods manage transactions.
