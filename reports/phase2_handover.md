# Phase 2 Implementation Handover

## Status: IN PROGRESS

Phase 2 of the sow-admin CLI implementation is partially complete. This document summarizes what has been done and what remains.

## Completed Tasks

### 1. Dependencies Added (Task #11 - COMPLETED)

Added the following dependencies to `pyproject.toml` under the `[project.optional-dependencies]` admin section:

```toml
admin = [
    "typer>=0.9.0",
    "rich>=13.0.0",
    "tomli>=2.0.0",
    "tomli-w>=1.0.0",
    "beautifulsoup4>=4.14.3",   # Added for Phase 2
    "lxml>=6.0.2",              # Added for Phase 2
    "requests>=2.32.5",         # Added for Phase 2
    "pypinyin>=0.55.0",         # Added for Phase 2
]
```

Command used:
```bash
uv add --optional admin beautifulsoup4 lxml requests pypinyin
```

### 2. Scraper Service Created (Task #8 - COMPLETED)

Created `src/stream_of_worship/admin/services/scraper.py` - a refactored version of `poc/lyrics_scraper.py` that integrates with the admin CLI database.

**Key Features:**
- `CatalogScraper` class that works with `DatabaseClient`
- `scrape_all_songs(limit, force, incremental)` - Scrapes sop.org/songs table
- `save_songs(songs)` - Saves songs to database
- `validate_test_song()` - Validates the test song "將天敞開"
- Supports incremental scraping (skips existing songs)
- Returns `Song` objects instead of raw dictionaries

**Updated exports** in `src/stream_of_worship/admin/services/__init__.py`:
```python
from stream_of_worship.admin.services.scraper import CatalogScraper
__all__ = ["CatalogScraper"]
```

## Pending Tasks

### Task #9: Create commands/catalog.py

**File to create:** `src/stream_of_worship/admin/commands/catalog.py`

**Commands to implement (from design spec):**

1. `sow-admin catalog scrape [--limit N] [--force] [--dry-run]`
   - Scrape song catalog from sop.org
   - Use `CatalogScraper` from services/scraper.py
   - Support --force to re-scrape all songs
   - Support --dry-run to preview without saving

2. `sow-admin catalog list [--album TEXT] [--key TEXT] [--format table|ids]`
   - List songs from catalog
   - Filter by album, key, composer, has-recording
   - --format ids: Output one song ID per line (for piping)

3. `sow-admin catalog search QUERY [--field title|lyrics|composer|all] [--limit N]`
   - Search songs in catalog
   - Search by title, lyrics, composer, or all fields

4. `sow-admin catalog show SONG_ID`
   - Show detailed info for a song
   - Display all fields including lyrics

**Pattern to follow:** See `src/stream_of_worship/admin/commands/db.py` for Typer command structure.

### Task #10: Update main.py with catalog commands

**File to update:** `src/stream_of_worship/admin/main.py`

Add the catalog commands as a subcommand group:

```python
from stream_of_worship.admin.commands import catalog
app.add_typer(catalog.app, name="catalog")
```

### Task #12: Write unit tests

**Files to create:**
1. `tests/admin/test_scraper.py` - Tests for `CatalogScraper`
2. `tests/admin/test_catalog_commands.py` - Tests for catalog CLI commands

**Test patterns to follow:** See existing tests in `tests/admin/test_client.py` and `tests/admin/test_models.py`

## Database Schema Reference

The `songs` table schema (from `specs/sow_admin_design.md`):

```sql
CREATE TABLE songs (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    title_pinyin TEXT,
    composer TEXT,
    lyricist TEXT,
    album_name TEXT,
    album_series TEXT,
    musical_key TEXT,
    lyrics_raw TEXT,
    lyrics_lines TEXT,  -- JSON array
    sections TEXT,      -- JSON array
    source_url TEXT NOT NULL,
    table_row_number INTEGER,
    scraped_at TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
```

## Useful References

- Design spec: `specs/sow_admin_design.md`
- Original scraper: `poc/lyrics_scraper.py`
- Database client: `src/stream_of_worship/admin/db/client.py`
- Models: `src/stream_of_worship/admin/db/models.py`
- Existing commands: `src/stream_of_worship/admin/commands/db.py`
- CLI entry point: `src/stream_of_worship/admin/main.py`

## Testing Commands

After implementation, verify with:

```bash
# Run scraper
sow-admin catalog scrape --limit 10

# List songs
sow-admin catalog list --limit 5

# Search
sow-admin catalog search "將天敞開"

# Show song details
sow-admin catalog show jiang_tian_chang_kai_209

# Run tests
PYTHONPATH=src uv run --extra admin pytest tests/admin/test_scraper.py -v
PYTHONPATH=src uv run --extra admin pytest tests/admin/test_catalog_commands.py -v
```

## Notes

- The scraper service is already implemented and tested via `validate_test_song()`
- Use `DatabaseClient` for all database operations
- Use `Rich` tables for formatted output in CLI commands
- Follow the existing pattern from `commands/db.py` for consistency
- Ensure all commands handle missing config/database gracefully
