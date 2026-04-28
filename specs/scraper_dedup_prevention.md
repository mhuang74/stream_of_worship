# Scraper Dedup Prevention

## Context

The sop.org `tablepress-3` source table contains genuine duplicate rows: multiple rows whose `(title, composer, lyricist)` collapse to the same content-hash song id (`scraper.py:_compute_song_id`). The 2026-04-28 one-off migration (see `reports/handover_db_migration_song_ids.md`) discovered 104 such duplicates among 685 songs, kept the earliest-scraped row, and soft-deleted the others.

The current scraper does not detect these in-run collisions. `CatalogScraper.scrape_all_songs` appends every parseable row to its in-memory `songs` list with no `seen_ids` check, and `DatabaseClient.insert_song` then does `INSERT OR REPLACE` on the PK — so the *last* duplicate row silently overwrites the first, with no log entry, no count in the CLI summary, and no preference for the earliest table position. This contradicts the migration's tiebreak (earliest wins) and means a future full scrape can re-introduce the noise the migration cleaned up.

The goal is a small, surgical change so that:

1. Within a single scrape pass, when two source rows hash to the same id, the **first-seen** row wins; subsequent collisions are skipped and counted/logged.
2. The CLI reports duplicate count alongside the existing scraped/saved counts so the operator can see the source-side duplication signal.
3. Soft-deleted-then-reappearing songs continue to resurrect (`deleted_at` cleared), preserving today's behavior. No change to `INSERT OR REPLACE` semantics.

This is intentionally narrow — no schema migration, no new CLI flags, no upsert refactor.

## Recommended approach

### Change 1 — In-run dedup in the scrape loop

File: `src/stream_of_worship/admin/services/scraper.py`

Modify `scrape_all_songs` (currently `:46–154`). Today the loop appends to `songs` for every row whose id isn't already in `existing_ids`. Add a second guard: if `song.id` is already in `seen_ids`, skip it (first-seen wins, since `seen_ids.add` happens after the check) and increment a `duplicate_count`. The existing `seen_ids.add(song.id)` line at `:129` already runs before `existing_ids` is consulted, so we just need to reorder slightly and check `seen_ids` first.

Concrete shape (replace the body of the `for row_num, row in enumerate(...)` loop at `:121–142`):

```python
duplicate_count = 0
for row_num, row in enumerate(data_rows, 1):
    cells = row.find_all(["td", "th"])
    if not cells:
        continue
    try:
        song = self._parse_row(cells, col_indices, row_num)
        if not song:
            continue

        # In-run dedup: first-seen wins.
        if song.id in seen_ids:
            duplicate_count += 1
            logger.debug(
                f"Skipping duplicate row {row_num}: id={song.id} "
                f"title={song.title!r} (first seen earlier in this run)"
            )
            continue
        seen_ids.add(song.id)

        # Cross-run incremental skip (existing behavior).
        if incremental and not force and song.id in existing_ids:
            logger.debug(f"Skipping existing song: {song.id}")
            continue

        songs.append(song)
        if row_num % 100 == 0:
            logger.info(f"Processed {row_num}/{len(data_rows)} songs...")
    except Exception as e:
        logger.warning(f"Failed to parse row {row_num}: {e}")
        continue

if duplicate_count:
    logger.info(
        f"Skipped {duplicate_count} duplicate row(s) within this scrape "
        f"(same title/composer/lyricist as an earlier row)"
    )
```

Notes:

- `seen_ids` is still the input to the soft-delete reaper at `:144–151`, so dedup *upstream* of that line is correct — we don't want to soft-delete a song just because it appeared as a within-run duplicate.
- Order matters: check `seen_ids` *before* `existing_ids` so a duplicate of an already-saved song still counts as a duplicate (not as "incrementally skipped").
- `existing_ids` continues to load only active rows (`list_songs(...)` filters `deleted_at IS NULL`). Soft-deleted songs that re-appear in the source table will fall through to `songs.append(song)`, hit `INSERT OR REPLACE`, and resurrect with `deleted_at = NULL` — preserving today's behavior per the user's choice.

### Change 2 — Return + surface the duplicate count

To make the dedup visible at the CLI layer without changing the public return type, the simplest move is to log the count (Change 1 already does this via `logger.info`) and additionally expose it as an instance attribute the CLI can read.

In `CatalogScraper.__init__` (`scraper.py:31–44`), initialize:

```python
self.last_run_duplicate_count: int = 0
```

In `scrape_all_songs`, set `self.last_run_duplicate_count = duplicate_count` just before the final `return songs` at `:154`.

Then in `src/stream_of_worship/admin/commands/catalog.py:scrape_catalog` (`:36–140`), after the `songs = scraper.scrape_all_songs(...)` call at `:95`, add:

```python
if scraper.last_run_duplicate_count:
    console.print(
        f"[yellow]Skipped {scraper.last_run_duplicate_count} duplicate row(s) "
        f"in source table (first occurrence kept)[/yellow]"
    )
```

Place this between the existing "Found N songs" line at `:104` and the preview table.

### Change 3 — Tests

File: `tests/admin/test_scraper.py`

Add a unit test that constructs a fake HTML table with two rows that share `(title, composer, lyricist)` and asserts:

- `scrape_all_songs` returns exactly 1 `Song` (not 2).
- `scraper.last_run_duplicate_count == 1`.
- The returned song's `table_row_number` is the **first** of the two duplicate rows (proves first-seen-wins ordering, not just a count).

Use the existing fixture style in `test_scraper.py` (mocked `requests.get` returning a `BeautifulSoup`-parseable string). One new test function is sufficient; no modification to existing tests is needed since they don't construct duplicate rows.

## What this plan deliberately does NOT do

- **No schema change.** No `UNIQUE(title, composer, lyricist)` index — the PK already enforces this since the id is a hash of those three fields, and adding a partial unique index introduces a second constraint that can't be tested without a schema migration. If we ever change the id formula, that's the moment to revisit.
- **No `INSERT OR REPLACE` → `ON CONFLICT DO UPDATE` refactor.** The user explicitly chose to keep resurrection behavior. The current statement at `db/client.py:308–345` stays unchanged.
- **No new CLI flags.** No `--on-duplicate=skip|warn|fail`, no `--no-resurrect`. First-seen-wins + resurrect-on-reappear is the only policy.
- **No changes to `_compute_song_id`** (`scraper.py:302–324`). The id formula is the dedup key, and it's already stable.

## Critical files

| File | Change |
|------|--------|
| `src/stream_of_worship/admin/services/scraper.py` | Add `seen_ids` collision check + `last_run_duplicate_count` attribute (loop at `:121–142`, init at `:31–44`, return at `:154`) |
| `src/stream_of_worship/admin/commands/catalog.py` | Print duplicate count after scrape (after `:104`) |
| `tests/admin/test_scraper.py` | New test for in-run duplicate skip + first-seen ordering |

No changes to: `db/client.py`, `db/schema.py`, `db/models.py`, `commands/catalog.py` flag surface, `main.py`.

## Verification

1. **Unit test passes:**
   ```bash
   PYTHONPATH=src uv run --python 3.11 --extra admin --extra test \
     pytest tests/admin/test_scraper.py -v
   ```

2. **Full scrape against live sop.org reproduces migration counts:**
   ```bash
   PYTHONPATH=src uv run --python 3.11 --extra admin python -m \
     stream_of_worship.admin.main catalog scrape --force
   ```
   Expect log line `Skipped 104 duplicate row(s)...` (matches the 685 → 581 collapse the migration found). The "Successfully saved N/N" count should be ≤ 581 (exact number depends on whether sop.org has added rows since 2026-04-28).

3. **Re-scrape is idempotent:**
   ```bash
   PYTHONPATH=src uv run --python 3.11 --extra admin python -m \
     stream_of_worship.admin.main catalog scrape
   ```
   Should print `No new songs to scrape.` and `Skipped 104 duplicate row(s)...` — duplicates are still detected during parsing even when nothing is saved.

4. **DB invariant unchanged:**
   ```bash
   sqlite3 ~/.config/sow-admin/db/sow.db \
     "SELECT COUNT(*) FROM songs WHERE deleted_at IS NULL;"
   ```
   Should remain at 581 (or whatever the active count was before the run) — confirms no song was lost and none was wrongly resurrected from a non-source-side cause.
