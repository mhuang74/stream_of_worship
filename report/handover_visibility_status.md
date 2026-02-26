# Handover: Add Visibility Status to Recordings

## Overview

Implementing a `visibility_status` field to control which recordings are visible in the User App's Browse list. Some recordings have poor quality LRC and should be excluded until manually fixed.

**Visibility States:**
- `published` - Visible in Browse screen (auto-set when LRC completes)
- `review` - Needs review, hidden from users
- `hold` - On hold, hidden from users

## Implementation Progress

### ✅ Task #1: Update Database Schema (COMPLETED)
**File:** `src/stream_of_worship/admin/db/schema.py`

Changes made:
- Added `visibility_status TEXT DEFAULT NULL` column to `CREATE_RECORDINGS_TABLE` (after `lrc_job_id`)
- Added index: `CREATE INDEX IF NOT EXISTS idx_recordings_visibility_status ON recordings(visibility_status)`

### ✅ Task #2: Update Recording Model (COMPLETED)
**File:** `src/stream_of_worship/admin/db/models.py`

Changes made:
- Added field: `visibility_status: Optional[str] = None`
- Updated `from_row()` to handle 27-column schema (detects 25/26/27 column schemas)
- Updated `to_dict()` to include visibility_status
- Added property: `is_published -> bool`

### ✅ Task #3: Update DatabaseClient (COMPLETED)
**File:** `src/stream_of_worship/admin/db/client.py`

Changes made:
- Added migration in `initialize_schema()` (~line 206):
  - Adds visibility_status column if missing
  - Migrates existing completed LRC recordings to 'published'
- Modified `update_recording_lrc()` (~line 696): Uses `COALESCE(visibility_status, 'published')` to auto-publish only when NULL (first-time LRC)
- Added new method `update_recording_visibility()` (~line 718): Updates visibility with validation
- Updated `insert_recording()`: Added visibility_status to INSERT statement

### 🔄 Task #4: Add Admin CLI Commands (IN PROGRESS)
**File:** `src/stream_of_worship/admin/commands/audio.py`

Changes made:
- Added `_colorize_visibility()` helper function (~line 246) with visual indicators:
  - `published` → green dot (●)
  - `review` → yellow half-circle (◐)
  - `hold` → dim circle (○)
- Updated `list_recordings` command (~line 768): Added Visibility column, replaced Status/Job ID columns with LRC status
- Updated `show_recording` command (~line 886): Added visibility display line

**NOT YET DONE:**
- Add `set-visibility` command - should be added after `show_recording` command (~line 913)

Command signature should be:
```python
@app.command("set-visibility")
def set_visibility(
    song_id: str = typer.Argument(..., help="Song ID to update visibility for"),
    status: str = typer.Option(..., "--status", "-s", help="Visibility status (published|review|hold)"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
```

### ❌ Task #5: Update CatalogService Filtering (NOT STARTED)
**File:** `src/stream_of_worship/app/services/catalog.py`

Changes needed:
1. Modify `_list_lrc_songs()` query (line 240-251):
   - Add `r.visibility_status` to SELECT
   - Add `AND r.visibility_status = 'published'` to WHERE clause

2. Modify `_search_lrc_songs()` similarly (line 332-343):
   - Add `r.visibility_status` to SELECT
   - Add `AND r.visibility_status = 'published'` to WHERE clause

**Note:** The Recording.from_row() in catalog.py expects specific column counts. After adding visibility_status to SELECT, ensure the row parsing handles the additional column correctly. The Recording model's from_row() already handles 27 columns, so this should work if visibility_status is included in the right position.

## Changes from Original Plan

1. **Column position:** visibility_status is added AFTER lrc_job_id but BEFORE created_at/updated_at/youtube_url in the schema. The Recording.from_row() method was updated to handle this by detecting row length (25/26/27).

2. **INSERT statement:** The original plan didn't mention updating insert_recording(), but this was necessary to properly persist visibility_status.

## Concerns / Notes

1. **Schema column order:** The visibility_status column is logically placed with other processing status fields, but this creates a 27-column schema. The from_row() method handles backward compatibility with 25/26 column schemas.

2. **Catalog queries:** The `_list_lrc_songs()` and `_search_lrc_songs()` methods in catalog.py construct Recording objects from JOIN queries. The comment says "Recording has 25 columns (16-40)" which is now outdated. After adding visibility_status to the SELECT, verify the column count matches.

3. **Migration safety:** The migration uses `ALTER TABLE ADD COLUMN` wrapped in try/except for idempotency. The UPDATE to set existing completed LRC to 'published' only runs if the column was just added (same transaction).

## Files Modified

| File | Status | Changes |
|------|--------|---------|
| `admin/db/schema.py` | ✅ Complete | Column + index added |
| `admin/db/models.py` | ✅ Complete | Field, from_row, to_dict, is_published |
| `admin/db/client.py` | ✅ Complete | Migration, update_recording_lrc, update_recording_visibility, insert_recording |
| `admin/commands/audio.py` | 🔄 Partial | Helper added, list/show updated, **set-visibility command NOT added** |
| `app/services/catalog.py` | ❌ Not started | Visibility filter needed |

## Verification Steps (from original plan)

After completing implementation:
1. **Migration test:** Run `sow-admin db init` and verify existing completed LRC recordings have `visibility_status = 'published'`
2. **Auto-publish test:** Generate LRC for a song and verify it gets `visibility_status = 'published'`
3. **Manual status test:** Run `sow-admin audio set-visibility <song_id> --status review` and verify status changes
4. **Browse test:** Verify only `published` recordings appear in User App browse
5. **Admin display test:** Run `sow-admin audio list` and `sow-admin audio show <song_id>` to verify visibility display
