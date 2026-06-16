# Preserve Blank LRC Lines in Render Worker Lyrics Video (v3)

## Summary

The render-worker currently skips timestamped LRC lines whose lyric text is empty after trimming. This means an LRC event such as `[00:10.00]` is not represented in the parsed lyric timeline, so the previous non-empty lyric can remain visible until the next non-empty lyric appears.

The desired behavior is to treat blank timestamped LRC lines as intentional timing events. When the current lyric event is blank, the rendered lyric area should transition smoothly: the previous non-blank lyric holds briefly then fades out, and as the next non-blank lyric approaches, a preview fades in within a four-beat window so worship leaders/congregants get a cue that singing resumes soon.

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
  - `_compute_last_lyric_fade_alpha()` calls `estimate_last_lyric_duration()` which looks at `song_lyrics[-1]`. If the last entry is blank, the duration estimate will be incorrect.

## Implementation Changes

### 1. LRC Parser (`lrc_parser.py`)

- Update `parse_lrc()` to append every syntactically valid timestamped line, including empty text:
  - Preserve existing timestamp parsing, millisecond handling, sorting, and whitespace trimming.
  - Keep ignoring lines without valid LRC timestamps.
  - Normalize whitespace-only lyric text to `""`.

- Update `estimate_last_lyric_duration()` to skip trailing blank lines:
  - Search backward from `song_lyrics[-1]` to find the last entry with non-empty `text`.
  - If all lyrics are blank, return the default `5.0`.
  - Use the found non-blank lyric as the basis for duration estimation (existing logic applies from that point).
  - This ensures fade-out timing is computed against the actual last sung line, not a blank marker.

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
- Render that non-blank next-line preview only when `current_time >= next_non_blank.global_time_seconds - four_beat_window_seconds`.
- The preview fades in linearly from alpha=0 to alpha=128 over 0.5 seconds (see 3b).

#### 3b. Four-beat preview window with fade-in

- `four_beat_window_seconds = 4 * 60 / tempo_bpm`.
- Use the current segment tempo from `SegmentInfo.tempo_bpm` when present and positive.
- Fall back to `70 BPM` when tempo is missing or invalid, matching existing render-worker fallback behavior.
- Preview fade-in: when the current time enters the four-beat window, the preview alpha ramps linearly from 0 to 128 over 0.5 seconds:
  - `preview_elapsed = current_time - (next_non_blank.global_time_seconds - four_beat_window_seconds)`
  - `preview_alpha = min(128, floor(128 * preview_elapsed / 0.5))`
  - After 0.5s within the window, preview holds at alpha=128.

#### 3c. Passing tempo context

- Add `tempo_bpm: float | None` field to `VisualState`.
- Resolve tempo in `_resolve_visual_state()` from `current_segment.tempo_bpm` when the segment is present and the value is positive; otherwise `None`.
- `render_lyrics()` reads `state.tempo_bpm` (passed through `_render_frame_impl()`) instead of receiving segment info directly.
- Keep existing public behavior of `render_frame()` and `render_frame_bytes()` unchanged.

#### 3d. Frame cache key

- Replace the original spec's boolean `preview_visible` with a quantized `preview_alpha` integer in `_compute_cache_key()`.
- During non-blank intervals, `preview_alpha` is `0` (no preview rendered, existing cache behavior unchanged).
- During blank intervals:
  - Before the four-beat window: `preview_alpha = 0`.
  - During fade-in (0.0–0.5s into window): quantized using the existing `_quantize_alpha()` applied to the 0–128 range, producing ~8 distinct cache entries.
  - After fade-in complete: `preview_alpha = 128` (quantized to 128).
- This ensures the cache correctly distinguishes intermediate fade-in states and avoids visual glitches on cache hits.

#### 3e. Title/header rendering

- Blank lyric events should blank only the lyric area.
- The song title/header may continue to render as it does today.

#### 3f. Previous-lyric hold-and-fade on blank transition

When a blank lyric line follows a non-blank lyric (whether mid-song or trailing), the previous non-blank lyric does **not** vanish instantly. Instead:

1. **Hold phase (2 seconds):** The previous non-blank lyric remains fully visible (alpha=255) for 2 seconds after the blank timestamp fires.
2. **Fade phase (4 seconds):** After the hold, the previous non-blank lyric fades out over 4 seconds using the existing sqrt-based fade curve (matching intro fade behavior).
3. **Interaction with preview:** The hold-and-fade of the previous lyric and the fade-in of the next-line preview are independent. Both can be visible simultaneously during the overlap period. The previous lyric renders at its current fade alpha; the preview renders at its fade-in alpha.

This applies consistently to both mid-song blanks and trailing blanks after the last sung lyric.

#### 3g. Interaction with existing last-lyric fade

- The existing `_compute_last_lyric_fade_alpha()` logic applies when `current_index == len(song_lyrics) - 1`.
- With blank lines preserved, the "last lyric" may be a blank line. The `estimate_last_lyric_duration()` fix (Section 1) ensures the duration is computed against the last non-blank lyric.
- The hold-and-fade behavior in 3f supersedes the existing last-lyric fade for the case where the current line is blank and follows a non-blank line. The existing fade continues to apply when the current (last) line is non-blank.

## Test Plan

- `services/render-worker/tests/test_lrc_parser.py`
  - Replace the existing expectation that empty text lines are ignored.
  - Add coverage that `[00:01.00]` parses to `LRCLine(time_seconds=1.0, text="")`.
  - Add coverage that whitespace-only text parses to `text == ""`.
  - Confirm parsed blank lines still sort by timestamp.
  - Add coverage that `estimate_last_lyric_duration()` skips trailing blank lines and uses the last non-blank lyric for duration estimation.
  - Add coverage that `estimate_last_lyric_duration()` returns the default when all lyrics are blank.
- `services/render-worker/tests/test_frame_renderer.py`
  - Add a test where the current lyric is blank and the next non-blank lyric is outside the four-beat window; assert no preview pixels are rendered.
  - Add a test where the current lyric is blank and the next non-blank lyric is inside the four-beat window; assert preview text is rendered with appropriate alpha.
  - Add a test verifying the 0.5s linear fade-in: assert preview alpha increases from 0 toward 128 over the first 0.5s of the window.
  - Add a test where one or more future blank lines appear before the next non-blank lyric; assert preview timing uses the next non-blank line.
  - Add a tempo fallback test for missing/invalid tempo using `70 BPM`.
  - Add a test verifying the cache key differs between different `preview_alpha` states (0, intermediate, 128).
  - Add a test for the hold-and-fade behavior: when a blank line follows a non-blank line, the previous lyric holds for 2s then fades over 4s.
  - Add a test that hold-and-fade and preview fade-in can overlap (both visible simultaneously).
  - Add a test for mid-song blank: previous lyric holds then fades, preview fades in within four-beat window.
  - Add a test for trailing blank at song end: last sung lyric holds then fades.
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
- During a blank lyric interval, the previous non-blank lyric holds for 2 seconds then fades over 4 seconds (sqrt curve) instead of vanishing instantly.
- The next non-blank lyric preview fades in linearly from alpha=0 to alpha=128 over 0.5 seconds, starting at the four-beat window boundary.
- Hold-and-fade of the previous lyric and fade-in of the preview can overlap; both render at their respective alphas.
- Missing or invalid tempo uses a deterministic `70 BPM` fallback.
- Existing non-blank lyric rendering behavior remains unchanged outside blank intervals.
- Frame cache correctly distinguishes intermediate preview alpha states via quantized `preview_alpha` in the cache key.
- All-blank LRC files fall back to efficient blank-video generation.
- Chapter manifests include blank entries (downstream consumers must handle empty `text`).
- `estimate_last_lyric_duration()` skips trailing blank lines and computes duration from the last non-blank lyric.

## Assumptions

- Blank timestamped LRC lines are intentional lyric timing markers, not malformed data.
- Whitespace-only lyric text should be treated as blank.
- "Within 4 beats" means four beats before the next non-blank lyric timestamp.
- The implementation should not add a new render-job option; this becomes the default lyrics renderer behavior.
- Next-line preview during blank intervals fades in linearly over 0.5s to alpha=128.
- When a blank line follows a non-blank line, the previous lyric holds for 2s then fades over 4s (consistent for both mid-song and trailing blanks).
- All-blank LRC files produce a fully blank video (no title header overlay).
- Blank entries in chapter manifests are kept as-is; downstream consumers must handle empty `text`.
