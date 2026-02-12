# Song Intro Transition Plan

## Context

Currently, when a song starts playing in the video, there's a gap between the song start (Time A) and the first lyric (Time B) where only the song title is shown at the top. This is a missed opportunity to introduce the song with richer metadata. We want to display song info (title, album, composer, lyricist) during this intro window, then gracefully transition into lyrics display.

## Timeline Design

```
Time A                                              Time B
|---- transition window ----|-- 4s fade --|-- 3s --|-- lyrics start ........... song end
|   Song info displayed     | info fades  | title  |  title in header (persists)  |
|   (no header title)       |   out       | only   |
```

- **Transition window** = `(B - A) - 7` seconds (minimum 0)
- **Fade-out period** = 4 seconds after transition window
- **Title-only period** = 3 seconds before first lyric (header title appears here)
- **Header title persists** from after fade-out all the way until song ends
- **Short intro fallback**: If `(B - A) < 7s`, show info briefly with a compressed/fast fade for whatever time is available

## Changes

### 1. Add metadata fields to SongsetItem

**File: `src/stream_of_worship/app/db/models.py`**
- Add 3 new optional joined fields after `loudness_db` (line 115):
  - `song_composer: Optional[str] = None`
  - `song_lyricist: Optional[str] = None`
  - `song_album_name: Optional[str] = None`
- Update `from_row()` detailed branch: change `len(row) >= 16` to `>= 17` check stays, add parsing for row indices [17], [18], [19] when `len(row) >= 20`
- Update docstring

**File: `src/stream_of_worship/app/db/schema.py`**
- Extend `SONGSET_ITEMS_DETAIL_QUERY` to include 3 new columns:
  ```sql
  s.composer as song_composer,    -- ROW[17]
  s.lyricist as song_lyricist,    -- ROW[18]
  s.album_name as song_album_name -- ROW[19]
  ```

### 2. Add intro rendering to VideoEngine

**File: `src/stream_of_worship/app/services/video_engine.py`**

Add a new method `_render_intro_info()` that:
- Takes the current segment, current_time, and first_lyric_time
- Calculates the transition window: `intro_duration = first_lyric_time - segment_start - 7.0`
- Builds info lines from segment metadata with Traditional Chinese labels:
  - `歌曲：{title}` (Song Title)
  - `專輯：{album}` (Album Name) - only if available
  - `作曲：{composer}` (Composer) - only if available
  - `作詞：{lyricist}` (Lyricist) - only if available
  - `讚美之泉音樂事工` (Worship band name) - always shown, no label prefix
- Renders lines left-aligned as a block, horizontally centered on screen
- Returns the rendered text layer with appropriate alpha

Modify `_render_frame()`:
- In the "before first lyric" period (`current_time < first_lyric_time`, around line 392), call `_render_intro_info()` instead of doing nothing
- During the transition window: suppress the header title rendering (it's redundant since title is in the info block)
- During the fade-out period (4s): render info with decreasing alpha (use similar sqrt-based fade as the last lyric fade)
- During the title-only period (3s before first lyric): show header title only
- After first lyric starts: header title remains visible at top (existing behavior) for rest of song

**Short intro handling** (gap < 7s):
- If total gap is < 3s: skip intro entirely, just show header title
- If total gap is 3-7s: allocate 60% to info display, 40% to fade, no title-only period

### 3. Update tests

**File: `tests/app/services/test_video_engine.py`**
- Add `song_composer`, `song_lyricist`, `song_album_name` to mock SongsetItem objects
- Add test for intro info rendering during transition window
- Add test for fade-out behavior
- Add test for short intro fallback
- Add test for header title suppression during transition window

## Files to Modify

1. `src/stream_of_worship/app/db/models.py` - Add 3 fields to SongsetItem + update from_row
2. `src/stream_of_worship/app/db/schema.py` - Extend SQL query with 3 new columns
3. `src/stream_of_worship/app/services/video_engine.py` - Add `_render_intro_info()`, modify `_render_frame()`
4. `tests/app/services/test_video_engine.py` - New tests for intro transition

## Verification

1. Run existing tests: `PYTHONPATH=src uv run --extra app pytest tests/app/services/test_video_engine.py -v`
2. Run new intro tests
3. Generate a test video with a song that has a long intro (>10s gap before first lyric) and verify:
   - Song info appears centered with Traditional Chinese labels
   - Info fades out smoothly before lyrics start
   - Header title is hidden during info display, shown after fade
   - Songs with missing metadata (no composer/lyricist) display gracefully
