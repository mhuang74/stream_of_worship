# Fix: Title Card Desynchronizes Lyrics and Chapter Timelines

## Problem

When `include_title_card=True`, the title card **replaces** the first N seconds of video content instead of **inserting** time at the beginning. This causes three cascading desynchronization issues:

1. **Audio plays during the title card** — FFmpeg receives both video frames and audio starting at time 0. During the title card period (e.g., 0–10s), the audio is already playing but no lyrics are rendered. Lyrics that fall within the title card window are silently swallowed.

2. **Lyrics appear at wrong video times** — After the title card, `current_time = frame_count / fps` (line 332) maps directly to the audio timeline. A lyric at `global_time_seconds = 36` appears at video time 36s, but the audio has been playing for 36s while the video has been showing content for only 26s (36s − 10s title card). The lyric appears 10s early relative to when it should appear in the video timeline.

3. **Chapter timestamps are not offset** — Both the FFmpeg chapter metadata and the chapters manifest JSON use `seg.start_time_seconds` from the audio engine without adding the title card duration. Chapter markers point to the wrong video positions.

### Concrete Example

With a 10-second title card and a 3-song worship set:

| Event | Audio Timeline | Current Video Time | Expected Video Time |
|---|---|---|---|
| Title card | 0–10s (audio playing!) | 0–10s (title card shown) | 0–10s (silence, no audio) |
| Song 1 starts | 0s | 10s | 10s |
| 2nd lyric line | 36s | 36s | **46s** |
| Song 2 starts | 180s | 180s | **190s** |
| Song 3 starts | 360s | 360s | **370s** |

The 2nd lyric line at audio time 36s appears at video time 36s, but the viewer has only seen 26s of content (36s − 10s title card). The lyric appears 10 seconds too early in the viewing experience.

### Root Cause Analysis

The previous spec (`fix-ffmpeg-epipe-title-card-frame-count.md`) fixed the EPIPE crash and corrected `current_time = frame_count / fps`, but that fix assumed the title card **overlays** the first N seconds of audio. The correct behavior is for the title card to **insert** time — the audio should be delayed, and all timestamps should be shifted.

Three specific code locations cause the desync:

#### 1. `video_engine.py:126` — `total_frames` doesn't include title card duration

```python
total_frames = math.ceil(total_duration_seconds * self.fps)
```

This uses only the audio duration. The title card adds extra video time that isn't accounted for. The video should be `audio_duration + title_card_duration` long.

#### 2. `video_engine.py:262-286` — FFmpeg command doesn't delay audio

```python
args = [
    self.ffmpeg_path, "-y",
    "-f", "rawvideo", "-vcodec", "rawvideo",
    "-s", f"{width}x{height}", "-pix_fmt", "rgba",
    "-r", str(self.fps),
    "-i", "-",           # video stdin (starts at frame 0)
    "-i", audio_path,    # audio file (starts at time 0)
    *self.get_video_codec_args(),
    "-c:a", "aac", "-b:a", "192k",
    "-shortest",
    output_path,
]
```

Both streams start at time 0. Audio plays immediately during the title card. The audio needs to be delayed by `title_card_duration_seconds` using FFmpeg's `adelay` filter.

#### 3. `video_engine.py:332` — `current_time` doesn't subtract title card offset

```python
current_time = frame_count / self.fps
```

After the title card, `frame_count` includes the title card frames. At the first lyrics frame (frame 240 for 10s at 24fps), `current_time = 10.0`. But the audio is also at 10s — the lyric lookup uses the audio timeline, so a lyric at `global_time_seconds = 10` would be found. However, the lyric at `global_time_seconds = 5` was never shown because the title card was displayed during video time 0–10s while audio was at 0–10s.

With the fix (audio delayed), after the title card, `current_time` should map to the **audio** timeline starting at 0.0: `current_time = (frame_count - title_card_frame_count) / self.fps`.

#### 4. `pipeline.py:436-449` — Chapter timestamps not offset

```python
chapters_for_video = [
    _segment_to_chapter_info(seg, i)
    for i, seg in enumerate(audio_result.segments)
]
# ...
chapters_manifest = generate_chapters_manifest(
    list(audio_result.segments),
    asset_fetcher.download_lrc,
    audio_result.total_duration_seconds,
)
```

Both use `seg.start_time_seconds` from the audio engine (starting at 0). With the title card insert, all chapter timestamps need `+ title_card_duration_seconds`.

---

## File Changes

### 1. `src/sow_render_worker/video_engine.py`

#### 1a. Add `title_card_offset` computation in `generate_video()` (after line 125)

```python
title_card_offset = self.title_card_duration_seconds if self.include_title_card else 0.0
video_duration_seconds = total_duration_seconds + title_card_offset
total_frames = math.ceil(video_duration_seconds * self.fps)
```

Pass `title_card_offset` to `encode_video_with_ffmpeg()` and use `video_duration_seconds` for `VideoExportResult.duration_seconds`.

#### 1b. Add `title_card_offset` parameter to `encode_video_with_ffmpeg()` (line 241)

Add parameter `title_card_offset: float = 0.0`.

#### 1c. Delay audio in FFmpeg command when `title_card_offset > 0` (lines 262-286)

When `title_card_offset > 0`, add an `adelay` filter to push audio start by `title_card_offset` milliseconds:

```python
if title_card_offset > 0:
    delay_ms = round(title_card_offset * 1000)
    args = [
        self.ffmpeg_path, "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{width}x{height}", "-pix_fmt", "rgba",
        "-r", str(self.fps),
        "-i", "-",
        "-i", audio_path,
        "-filter_complex", f"[1:a]adelay={delay_ms}|{delay_ms}[delayed]",
        "-map", "0:v", "-map", "[delayed]",
        *self.get_video_codec_args(),
        "-c:a", "aac", "-b:a", "192k",
        output_path,
    ]
else:
    # existing command (no delay needed)
    args = [
        self.ffmpeg_path, "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-s", f"{width}x{height}", "-pix_fmt", "rgba",
        "-r", str(self.fps),
        "-i", "-",
        "-i", audio_path,
        *self.get_video_codec_args(),
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        output_path,
    ]
```

**Note:** Remove `-shortest` when `adelay` is used. With `adelay`, the audio stream becomes `title_card_offset + audio_duration` long, matching the video stream length exactly. `-shortest` is unnecessary and could cause premature termination if there's any rounding difference.

#### 1d. Fix `current_time` calculation after title card (line 332)

```python
# Before:
current_time = frame_count / self.fps

# After:
current_time = (frame_count - title_card_frame_count) / self.fps
```

This maps `current_time` back to the audio timeline (starting at 0.0 after the title card), keeping it in sync with lyrics `global_time_seconds` and segment `start_time_seconds`.

#### 1e. Fix `TitleCardConfig.duration_seconds` (line 206)

```python
# Before:
title_card_config = TitleCardConfig(
    enabled=True,
    duration_seconds=total_duration_seconds,  # confusing: set to audio duration
    ...
)

# After:
title_card_config = TitleCardConfig(
    enabled=True,
    duration_seconds=self.title_card_duration_seconds,  # actual title card display duration
    ...
)
```

The `duration_seconds` field is currently set to the total audio duration, which is semantically wrong. It should represent the title card's own display duration. Note: `render_title_card()` uses `config.total_duration_seconds` (not `config.duration_seconds`) for the "X:XX" subtitle text, so this change doesn't affect rendering.

#### 1f. Update `VideoExportResult` to return video duration (not audio duration)

```python
return VideoExportResult(
    output_path=output_path,
    total_frames=total_frames,
    duration_seconds=video_duration_seconds,  # was total_duration_seconds
    width=self.resolution[0],
    height=self.resolution[1],
    fps=self.fps,
)
```

### 2. `src/sow_render_worker/pipeline.py`

#### 2a. Offset chapter timestamps by `title_card_duration_seconds` (lines 436-441)

```python
title_card_offset = job.title_card_duration_seconds if job.include_title_card else 0.0

chapters_for_video = [
    ChapterInfo(
        position=ch.position,
        song_title=ch.song_title,
        start_seconds=ch.start_seconds + title_card_offset,
        end_seconds=ch.end_seconds + title_card_offset,
        lines=tuple(
            {
                "text": line["text"],
                "startSeconds": line["startSeconds"] + title_card_offset,
            }
            for line in ch.lines
        ) if ch.lines else (),
    )
    for ch in (
        _segment_to_chapter_info(seg, i)
        for i, seg in enumerate(audio_result.segments)
    )
]
```

#### 2b. Offset chapters manifest timestamps (lines 445-449)

Pass `title_card_offset` to `generate_chapters_manifest()` and offset all `start_seconds` values. This requires adding an optional `title_card_offset` parameter to `generate_chapters_manifest()` in `chapters.py`.

### 3. `src/sow_render_worker/chapters.py`

#### 3a. Add `title_card_offset` parameter to `generate_chapters_manifest()` (line 75)

```python
def generate_chapters_manifest(
    segments: list[SegmentInfo],
    download_lrc: Callable[[str], str | None | object],
    total_duration_seconds: float,
    title_card_offset: float = 0.0,  # NEW
) -> ChaptersManifest:
```

Offset all `ChapterLine.start_seconds` and `Chapter.start_seconds`/`Chapter.end_seconds` by `title_card_offset`.

### 4. `src/sow_render_worker/frame_renderer.py`

No changes needed. The `render_frame()` method receives `current_time` on the audio timeline (after title card offset subtraction in video_engine), so segment and lyric lookups work correctly. The `render_intro_info()` method also works correctly since `current_time` maps to the audio timeline.

---

## Test Updates

### `tests/test_video_engine.py`

#### Test 1: `total_frames` includes title card duration

When `include_title_card=True` with `title_card_duration_seconds=10.0` and audio duration 180s:
- `total_frames = ceil((180 + 10) * 24) = 4560` (not `4320`)

#### Test 2: FFmpeg command includes `adelay` when title card enabled

Verify the FFmpeg args contain `-filter_complex` with `adelay={offset_ms}|{offset_ms}` and `-map 0:v -map [delayed]` when `title_card_offset > 0`.

#### Test 3: FFmpeg command has no `adelay` when title card disabled

Verify the existing FFmpeg args (no `-filter_complex`, has `-shortest`) when `title_card_offset == 0`.

#### Test 4: `current_time` starts at 0.0 after title card

With `title_card_duration_seconds=5.0` and `fps=24`:
- Title card frames: 0–119 (`current_time` not computed)
- First lyrics frame (120): `current_time = (120 - 120) / 24 = 0.0`
- Frame 240: `current_time = (240 - 120) / 24 = 5.0`

Capture `current_time` values passed to `render_frame()` and verify the first value is 0.0.

#### Test 5: `VideoExportResult.duration_seconds` includes title card

Verify `result.duration_seconds == audio_duration + title_card_duration_seconds`.

#### Test 6: `TitleCardConfig.duration_seconds` equals title card duration

Verify `title_card_config.duration_seconds == self.title_card_duration_seconds` (not `total_duration_seconds`).

### `tests/test_chapters.py`

#### Test 7: Chapter timestamps offset by `title_card_offset`

Verify that `generate_chapters_manifest()` with `title_card_offset=10.0` shifts all `start_seconds`/`end_seconds` and `ChapterLine.start_seconds` by 10.0.

### `tests/test_pipeline.py`

#### Test 8: Pipeline offsets chapter timestamps when title card enabled

Verify that `chapters_for_video` and `chapters_manifest` have timestamps shifted by `title_card_duration_seconds` when `job.include_title_card=True`.

---

## Implementation Order

1. Add `title_card_offset` computation in `generate_video()` (1a)
2. Add `title_card_offset` parameter to `encode_video_with_ffmpeg()` (1b)
3. Delay audio in FFmpeg command (1c)
4. Fix `current_time` calculation (1d)
5. Fix `TitleCardConfig.duration_seconds` (1e)
6. Update `VideoExportResult` (1f)
7. Offset chapter timestamps in `pipeline.py` (2a, 2b)
8. Add `title_card_offset` to `generate_chapters_manifest()` (3a)
9. Add/update tests
10. Run `PYTHONPATH=src pytest tests/test_video_engine.py tests/test_chapters.py tests/test_pipeline.py -v`

---

## Edge Cases

### Title card disabled (`include_title_card=False`)

`title_card_offset = 0.0`. No changes to FFmpeg command, `current_time`, or chapter timestamps. Behavior identical to current code.

### Title card duration > first song intro gap

If the first song's intro gap (time before first lyric) is shorter than `title_card_duration_seconds`, the title card still shows for the full duration. After the title card, `current_time = 0.0` and the intro info display in `render_intro_info()` will show correctly from the start of the audio timeline. No lyrics are lost.

### Very short audio (< title card duration)

If audio is shorter than `title_card_duration_seconds`, the `adelay` filter would push audio beyond the video end. The video would show the title card then silence. This is unlikely in practice (worship sets are long) but should be documented.

### `generate_blank_video()` with title card

When no lyrics are found, `generate_blank_video()` is called. This method doesn't support title cards — it generates a solid-color video with audio. This is acceptable since title cards are only meaningful when lyrics exist. No change needed.

---

## Verification Checklist

- [ ] `total_frames = ceil((audio_duration + title_card_offset) * fps)` when title card enabled
- [ ] FFmpeg command includes `adelay` filter when `title_card_offset > 0`
- [ ] FFmpeg command omits `adelay` and uses `-shortest` when `title_card_offset == 0`
- [ ] `current_time = (frame_count - title_card_frame_count) / fps` after title card
- [ ] `TitleCardConfig.duration_seconds = self.title_card_duration_seconds`
- [ ] `VideoExportResult.duration_seconds` includes title card offset
- [ ] Chapter timestamps in FFmpeg metadata offset by `title_card_offset`
- [ ] Chapter timestamps in manifest JSON offset by `title_card_offset`
- [ ] All existing tests pass
- [ ] New tests pass
