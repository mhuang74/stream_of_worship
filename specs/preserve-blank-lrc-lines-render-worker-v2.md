# Preserve Blank LRC Lines in Render Worker Lyrics Video

## Summary

The render-worker currently skips timestamped LRC lines whose lyric text is empty after trimming. This means an LRC event such as `[00:10.00]` is not represented in the parsed lyric timeline, so the previous non-empty lyric can remain visible until the next non-empty lyric appears.

The desired behavior is to treat blank timestamped LRC lines as intentional timing events. When the current lyric event is blank, the rendered lyric area should be blank. As the next non-blank lyric approaches, the renderer should show the next-line preview within a four-beat window so worship leaders/congregants get a cue that singing resumes soon.

## Current Behavior

- `services/render-worker/src/sow_render_worker/lrc_parser.py`
  - `parse_lrc()` extracts text with `match.group(4).strip()`.
  - It only appends the parsed line when `if text:` is truthy.
  - Therefore `[00:10.00]` and `[00:10.00]   ` are dropped.
- `services/render-worker/src/sow_render_worker/video_engine.py`
  - `VideoEngine.generate_video()` uses `parse_lrc()` before converting lyrics into the global timeline.
  - Dropped blank lines never reach `FrameRenderer`.
  - Chapter manifests are built from `local_lyrics` after `parse_lrc()`.
- `services/render-worker/src/sow_render_worker/frame_renderer.py`
  - `render_lyrics()` renders the current line and then renders the immediate next line as preview.
  - If blank lines are preserved, renderer behavior must avoid drawing the immediate next lyric too early during a blank interval.
  - `_compute_cache_key()` does not include preview visibility, so a fixed `current_lyric_index` during a blank interval would cache one frame for the entire interval.

## Implementation Changes

### 1. LRC Parser (`lrc_parser.py`)

- Update `parse_lrc()` to append every syntactically valid timestamped line, including empty text:
  - Preserve existing timestamp parsing, millisecond handling, sorting, and whitespace trimming.
  - Keep ignoring lines without valid LRC timestamps.
  - Normalize whitespace-only lyric text to `""`.

### 2. Video Engine (`video_engine.py`)

- Update `generate_video()` blank-video fallback logic:
  - After building `all_lyrics`, if the list is non-empty but **every** line has `text == ""`, fall back to `generate_blank_video()` instead of frame-by-frame rendering.
  - This avoids wasting Lambda CPU/memory on thousands of visually identical frames.
- Chapter manifest behavior:
  - Keep blank entries in chapter `lines` tuples (accepted shared-parser impact).
  - Do **not** filter blank lines when building `ChapterInfo.lines`.

### 3. Frame Renderer (`frame_renderer.py`)

#### 3a. Blank lyric rendering

- When `current_line.text == ""`, do not draw current lyric text.
- Do not render the immediate next-line preview by default during a blank interval.
- Find the next future lyric whose `text` is non-empty after trimming.
- Render that non-blank next-line preview only when `current_time >= next_non_blank.global_time_seconds - four_beats_seconds`.
- The preview pops in abruptly at fixed `alpha=128` (matching existing next-line preview behavior).

#### 3b. Four-beat preview window

- `four_beats_seconds = 4 * 60 / tempo_bpm`.
- Use the current segment tempo from `SegmentInfo.tempo_bpm` when present and positive.
- Fall back to `70 BPM` when tempo is missing or invalid, matching existing render-worker fallback behavior.

#### 3c. Passing tempo context

- Preferred minimal change: pass the resolved `VisualState` or current `SegmentInfo` into lyric rendering from `_render_frame_impl()`.
- Keep existing public behavior of `render_frame()` and `render_frame_bytes()` unchanged.

#### 3d. Frame cache key

- Add a boolean `preview_visible` to `_compute_cache_key()`.
- The value is `True` when the current lyric is blank **and** the next non-blank lyric is within the four-beat preview window.
- This ensures the cache correctly distinguishes "blank with no preview" from "blank with preview" frames.
- During non-blank intervals, `preview_visible` is always `False` (or omitted) so existing cache behavior is unchanged.

#### 3e. Title/header rendering

- Blank lyric events should blank only the lyric area.
- The song title/header may continue to render as it does today.

#### 3f. Last-lyric fade with trailing blanks

- If a song ends with blank timestamped lines after the final non-blank lyric, the fade-out logic applies to the blank line (which is invisible).
- The last non-blank lyric will cut off abruptly when the first trailing blank line is reached.
- **This is accepted behavior** — trailing blank lines are intentional end-of-song markers.

## Test Plan

- `services/render-worker/tests/test_lrc_parser.py`
  - Replace the existing expectation that empty text lines are ignored.
  - Add coverage that `[00:01.00]` parses to `LRCLine(time_seconds=1.0, text="")`.
  - Add coverage that whitespace-only text parses to `text == ""`.
  - Confirm parsed blank lines still sort by timestamp.
- `services/render-worker/tests/test_frame_renderer.py`
  - Add a test where the current lyric is blank and the next non-blank lyric is outside the four-beat window; assert no lyric pixels are rendered.
  - Add a test where the current lyric is blank and the next non-blank lyric is inside the four-beat window; assert preview text is rendered.
  - Add a test where one or more future blank lines appear before the next non-blank lyric; assert preview timing uses the next non-blank line.
  - Add a tempo fallback test for missing/invalid tempo using `70 BPM`.
  - Add a test verifying the cache key differs between "blank no preview" and "blank with preview" states.
- `services/render-worker/tests/test_video_engine.py`
  - Add coverage that an LRC containing blank timing lines mixed with non-blank lines is treated as lyric timing data and does not fall back to `generate_blank_video()`.
  - Add coverage that an LRC containing **only** blank timing lines **does** fall back to `generate_blank_video()`.

Recommended focused test command:

```bash
PYTHONPATH=src:services/render-worker/src uv run --python 3.11 --extra test pytest \
  services/render-worker/tests/test_lrc_parser.py \
  services/render-worker/tests/test_frame_renderer.py \
  services/render-worker/tests/test_video_engine.py -v
```

## Acceptance Criteria

- Timestamped blank LRC lines are preserved in parsed lyric data.
- During a blank lyric interval, the currently displayed lyric text disappears instead of the previous lyric lingering.
- The next non-blank lyric preview appears only inside the four-beat resume window.
- Missing or invalid tempo uses a deterministic `70 BPM` fallback.
- Existing non-blank lyric rendering behavior remains unchanged outside blank intervals.
- Frame cache correctly distinguishes blank states with and without preview.
- All-blank LRC files fall back to efficient blank-video generation.
- Chapter manifests include blank entries (downstream consumers must handle empty `text`).

## Assumptions

- Blank timestamped LRC lines are intentional lyric timing markers, not malformed data.
- Whitespace-only lyric text should be treated as blank.
- "Within 4 beats" means four beats before the next non-blank lyric timestamp.
- The implementation should not add a new render-job option; this becomes the default lyrics renderer behavior.
- Trailing blank lines after the last non-blank lyric are intentional end-of-song markers; abrupt cutoff of the last sung lyric is acceptable.
- Next-line preview during blank intervals pops in abruptly at fixed alpha (no fade-in animation).
