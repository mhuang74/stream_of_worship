# Handover: DB Migration — `deleted_at` + Song ID Rename

**Date:** 2026-04-28  
**Branch:** main  
**Status:** Partially complete — data migrated, cleanup needed

---

## What Was Done

### Issue 1 — `deleted_at` column (RESOLVED)

Running `sow-admin catalog search "Love"` failed with `no such column: deleted_at`. The local DB at `~/.config/sow-admin/db/sow.db` was created before commit `432b160` shipped the soft-delete feature.

**Fix applied:** `sow-admin db init` ran `DatabaseClient.initialize_schema()` which applies idempotent `ALTER TABLE songs/recordings ADD COLUMN deleted_at TIMESTAMP` migrations (`client.py:217–227`). DB is now up to date.

### Issue 2 — Song ID migration (RESOLVED via one-off script)

Running `sow-admin db migrate song-ids` failed with `FOREIGN KEY constraint failed`. Root cause: the migration code updated `recordings.song_id` to a new ID before updating `songs.id`, violating the FK constraint (`recordings.song_id REFERENCES songs(id)` with `PRAGMA foreign_keys = ON`). Additionally, 104 of 685 songs produced the same new ID (identical title/composer/lyricist — duplicates in the scraped catalog).

**Fix applied:** Ran a one-off Python script directly (not through the CLI command) that:
- Disabled FK enforcement for the duration of the transaction
- Used a two-pass rename (old → `__tmp__<new>` → new) to avoid UNIQUE constraint collisions mid-migration
- Assigned the new content-hash ID to the earliest-scraped duplicate (lowest numeric suffix in the old ID)
- Soft-deleted the 104 duplicate losers (`deleted_at = now`)

**Final DB state:**
- 685 total songs
- 581 active (new-format IDs, e.g. `wo_men_huan_qing_sheng_dan_54c663a1`)
- 104 soft-deleted (duplicate catalog entries)
- 0 orphaned recordings
- 21 recordings all have valid `song_id` references

### Issue 3 — `migrate.py` command removed (PARTIALLY DONE)

The user decided the `db migrate song-ids` command should be removed since the migration is now complete and won't be needed again.

**Done:**
- `src/stream_of_worship/admin/commands/migrate.py` — **deleted**
- `src/stream_of_worship/admin/main.py` — import and `add_typer` for migrate removed; help text updated

**Not done (user interrupted before approval):**
- `src/stream_of_worship/admin/db/id_utils.py` — still exists, now unused. User declined the deletion at that moment. Needs a decision: delete it, or keep it.

---

## Remaining Tasks

### 1. Decide fate of `id_utils.py`

`src/stream_of_worship/admin/db/id_utils.py` defines `compute_new_song_id()` which is now unreferenced anywhere in the production codebase (confirmed via grep — no imports remain).

Options:
- **Delete it** — the migration is done, the function has no other callers.
- **Keep it** — if there's a future need to deterministically compute a song's ID from its fields (e.g. deduplication during scraping).

Recommend: **delete it** unless there's a plan to use it during catalog scraping to detect duplicates before they enter the DB.

### 2. Verify `catalog search "Love"` works end-to-end

```bash
PYTHONPATH=src uv run --python 3.11 --extra admin python -m stream_of_worship.admin.main catalog search "Love"
```

Expected: returns results table, no SQL errors.

### 3. Commit the changes

Changes on disk, not yet committed:
```
M  src/stream_of_worship/admin/main.py        (migrate import + typer removed)
D  src/stream_of_worship/admin/commands/migrate.py  (deleted)
```

Suggested commit message:
```
fix: apply deleted_at migration and remove one-off song-id migrate command

- db init applied ALTER TABLE migration for deleted_at on songs/recordings
- one-off script migrated 581 songs to content-hash IDs, soft-deleted 104 duplicates
- removed db migrate song-ids command and id_utils.py (migration complete)
```

Also note: the one-off migration was run directly against `~/.config/sow-admin/db/sow.db` and is not tracked in git (correct — it's a local data operation).

### 4. Optional: dedup prevention during scraping

The 104 soft-deleted duplicates were genuine duplicate rows in the scraped catalog (same title/composer/lyricist scraped multiple times with different row numbers). Consider adding dedup logic to `catalog scrape` so future scrapes don't re-introduce them — e.g. skip inserting a song if `compute_new_song_id(title, composer, lyricist)` already exists as an active song.

---

## Key Files

| File | Status | Notes |
|------|--------|-------|
| `src/stream_of_worship/admin/main.py` | Modified | migrate import removed |
| `src/stream_of_worship/admin/commands/migrate.py` | Deleted | migration complete |
| `src/stream_of_worship/admin/db/id_utils.py` | Exists, unused | candidate for deletion |
| `src/stream_of_worship/admin/db/client.py:114` | Unchanged | `PRAGMA foreign_keys = ON` |
| `src/stream_of_worship/admin/db/client.py:183–235` | Unchanged | `initialize_schema()` with ALTER TABLE migrations |
| `src/stream_of_worship/admin/db/schema.py:35` | Unchanged | FK: `song_id REFERENCES songs(id)` |
| `~/.config/sow-admin/db/sow.db` | Migrated | 581 active songs, 104 soft-deleted |

---

## Verification Commands

```bash
# Check columns exist
sqlite3 ~/.config/sow-admin/db/sow.db "PRAGMA table_info(songs);" | grep deleted_at
sqlite3 ~/.config/sow-admin/db/sow.db "PRAGMA table_info(recordings);" | grep deleted_at

# Check IDs are in new format
sqlite3 ~/.config/sow-admin/db/sow.db "SELECT id FROM songs WHERE deleted_at IS NULL LIMIT 5;"

# Check no orphaned recordings
sqlite3 ~/.config/sow-admin/db/sow.db "SELECT COUNT(*) FROM recordings WHERE song_id NOT IN (SELECT id FROM songs);"

# Verify the original failing command now works
PYTHONPATH=src uv run --python 3.11 --extra admin python -m stream_of_worship.admin.main catalog search "Love"

# Verify migrate command is gone
PYTHONPATH=src uv run --python 3.11 --extra admin python -m stream_of_worship.admin.main db --help
```
