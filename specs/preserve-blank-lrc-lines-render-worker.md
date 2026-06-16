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
- `services/render-worker/src/sow_render_worker/frame_renderer.py`
  - `render_lyrics()` renders the current line and then renders the immediate next line as preview.
  - If blank lines are preserved, renderer behavior must avoid drawing the immediate next lyric too early during a blank interval.

## Implementation Changes

- Update `parse_lrc()` to append every syntactically valid timestamped line, including empty text:
  - Preserve existing timestamp parsing, millisecond handling, sorting, and whitespace trimming.
  - Keep ignoring lines without valid LRC timestamps.
  - Normalize whitespace-only lyric text to `""`.
- Update frame rendering so blank current lyrics are real lyric states:
  - When `current_line.text == ""`, do not draw current lyric text.
  - Do not render the immediate next-line preview by default during a blank interval.
  - Find the next future lyric whose `text` is non-empty after trimming.
  - Render that non-blank next-line preview only when `current_time >= next_non_blank.global_time_seconds - four_beats_seconds`.
- Define the four-beat preview window as:
  - `four_beats_seconds = 4 * 60 / tempo_bpm`.
  - Use the current segment tempo from `SegmentInfo.tempo_bpm` when present and positive.
  - Fall back to `70 BPM` when tempo is missing or invalid, matching existing render-worker fallback behavior.
- Pass enough context into `render_lyrics()` to calculate the tempo-aware window:
  - Preferred minimal change: pass the resolved `VisualState` or current `SegmentInfo` into lyric rendering from `_render_frame_impl()`.
  - Keep existing public behavior of `render_frame()` and `render_frame_bytes()` unchanged.
- Preserve existing title/header rendering behavior:
  - Blank lyric events should blank only the lyric area.
  - The song title/header may continue to render as it does today.
- Accept shared-parser impact:
  - Chapter manifests also use `parse_lrc()`, so blank timestamped lines will be preserved in generated chapter lyric metadata.

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
- `services/render-worker/tests/test_video_engine.py`
  - Add coverage that an LRC containing blank timing lines is treated as lyric timing data and does not fall back to `generate_blank_video()`.

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

## Assumptions

- Blank timestamped LRC lines are intentional lyric timing markers, not malformed data.
- Whitespace-only lyric text should be treated as blank.
- "Within 4 beats" means four beats before the next non-blank lyric timestamp.
- The implementation should not add a new render-job option; this becomes the default lyrics renderer behavior.
