# Plan: Add Visibility Status to Recordings

## Context

Some recordings have poor quality LRC despite the dual-prone approach (YouTube transcription + Whisper). These recordings should be excluded from the User App's Browse list until manually fixed. This feature adds a `visibility_status` field to control which recordings users can see.

**Visibility States:**
- `published` - Visible in Browse screen (auto-set when LRC completes)
- `review` - Needs review, hidden from users
- `hold` - On hold, hidden from users

## Implementation Steps

### 1. Update Database Schema
**File:** `src/stream_of_worship/admin/db/schema.py`

- Add `visibility_status TEXT DEFAULT NULL` column to `CREATE_RECORDINGS_TABLE` (after `lrc_job_id`)
- Add index: `CREATE INDEX IF NOT EXISTS idx_recordings_visibility_status ON recordings(visibility_status)`

### 2. Update Recording Model
**File:** `src/stream_of_worship/admin/db/models.py`

- Add field to dataclass: `visibility_status: Optional[str] = None`
- Update `from_row()` to handle 27-column schema (add visibility_status parsing)
- Update `to_dict()` to include visibility_status
- Add property: `is_published -> bool`

### 3. Update DatabaseClient
**File:** `src/stream_of_worship/admin/db/client.py`

**Migration in `initialize_schema()` (~line 203):**
```python
# Add column
cursor.execute("ALTER TABLE recordings ADD COLUMN visibility_status TEXT")
# Migrate existing completed LRC to published
cursor.execute("""
    UPDATE recordings SET visibility_status = 'published'
    WHERE lrc_status = 'completed' AND visibility_status IS NULL
""")
```

**Modify `update_recording_lrc()` (line 682):** Auto-publish only when visibility_status is NULL (first-time LRC). When visibility is already set (review/hold), keep current status so user can explicitly publish after fixing.

**Add new method `update_recording_visibility()`:** Update visibility_status with validation

### 4. Add Admin CLI Command
**File:** `src/stream_of_worship/admin/commands/audio.py`

**Add command `set-visibility`:**
```
sow-admin audio set-visibility <song_id> --status <published|review|hold>
```

**Update `list_recordings` command:** Add visibility column with visual indicators (green dot = published, yellow = review, dim = hold)

**Update `show_recording` command:** Display visibility status with color coding

### 5. Update CatalogService Filtering
**File:** `src/stream_of_worship/app/services/catalog.py`

**Modify `_list_lrc_songs()` query (line 240-251):**
- Add `r.visibility_status` to SELECT
- Add `AND r.visibility_status = 'published'` to WHERE clause

**Modify `_search_lrc_songs()` similarly**

## Files to Modify

| File | Changes |
|------|---------|
| `admin/db/schema.py` | Add column definition, index |
| `admin/db/models.py` | Add field, update from_row/to_dict |
| `admin/db/client.py` | Migration, update_recording_lrc, new method |
| `admin/commands/audio.py` | New command, update list/show display |
| `app/services/catalog.py` | Add visibility filter to queries |

## Verification

1. **Migration test:** Run `sow-admin db init` and verify existing completed LRC recordings have `visibility_status = 'published'`
2. **Auto-publish test:** Generate LRC for a song and verify it gets `visibility_status = 'published'`
3. **Manual status test:** Run `sow-admin audio set-visibility <song_id> --status review` and verify status changes
4. **Browse test:** Verify only `published` recordings appear in User App browse
5. **Admin display test:** Run `sow-admin audio list` and `sow-admin audio show <song_id>` to verify visibility display
