# Fix: Title Card Audio Mode — Configurable Overlay vs Delay (v3)

## Problem

When `include_title_card=True`, the title card **replaces** the first N seconds of video frames while audio continues playing from time 0. This has two observable effects:

1. **Audio plays during the title card** — The prelude/intro music is audible while the title card is displayed. Any lyrics that fall within the title card window (0–N seconds of the audio timeline) are never rendered on screen.

2. **`TitleCardConfig.duration_seconds` is semantically wrong** — Set to `total_duration_seconds` (the full audio duration) instead of the title card's own display duration. This doesn't affect rendering today (`render_title_card` uses `config.total_duration_seconds`), but it's a misleading data model.

### Is This Actually a Bug?

**No — it's a design choice with tradeoffs.** After the title card ends, `current_time = frame_count / fps` maps directly to the audio timeline, so lyrics ARE in sync with what the viewer hears. The only "loss" is lyrics in the first N seconds of audio that fall under the title card window. For worship sets with long intros (15s+), this is typically unnoticeable.

The current "overlay" behavior (prelude plays under title card) is often desirable — it provides a musical intro before lyrics begin. The alternative "delay" behavior (silence during title card, audio shifted) is also valid for cases where you want a clean separation.

**The fix should make this configurable rather than replacing one behavior with another.**

### What the Previous Specs Got Wrong

**v1** (`fix-title-card-lyrics-timeline-desync.md`) presented three "cascading desynchronization issues," but:

- **Problem #1 (audio plays during title card)** — This IS the current behavior, not a bug. It's often desirable.
- **Problem #2 (lyrics appear at wrong video times)** — Misleading. After the title card, `current_time` maps to the audio timeline, so lyrics are in sync with the audio the viewer hears. The only issue is lyrics in the title card window are lost.
- **Problem #3 (chapter timestamps not offset)** — NOT a current bug. In the current behavior, audio and video timelines are identical — a chapter at audio time 180s IS at video time 180s, so seeking works correctly. This issue only arises AFTER implementing the audio delay fix. It's a necessary accompaniment to the delay mode, not a pre-existing bug.

**v2** (`fix-title-card-lyrics-timeline-desync-v2.md`) corrected the above but introduced new issues:

- **adelay timing mismatch** — `adelay` was computed from `title_card_duration_seconds` (a float), but the actual title card display duration is `ceil(title_card_duration_seconds * fps) / fps`, which can be up to ~42ms longer at 24fps. This causes audio to start slightly before lyrics frames begin at the title-card-to-lyrics transition.
- **Missing full-stack schema changes** — No mention of adding `title_card_audio_mode` to `RenderJob`, DB schema, API validation, or pipeline wiring. The feature can't be used from the web app without these.
- **Stale `ChaptersManifest.total_duration_seconds`** — Chapter/line timestamps were offset but the manifest's `total_duration_seconds` was not updated, misrepresenting the video length.
- **Dead code in pipeline chapter offset** — The proposed code offset `ch.lines` but `_segment_to_chapter_info()` never populates `lines`, making the offset logic a no-op.

---

## Proposed Solution: Configurable `title_card_audio_mode`

Add a `title_card_audio_mode` option to `VideoEngine` with two modes:

| Mode | Behavior | Video Length | Chapter Offset | Audio During Title Card |
|---|---|---|---|---|
| `overlay` (default) | Prelude plays under title card | `audio_duration` | No offset needed | Audible (current behavior) |
| `delay` | Silence during title card, audio shifted | `audio_duration + title_card_duration` | Offset by `title_card_duration` | Silent, then audio starts |

### Why `overlay` as Default

- Preserves current behavior (no breaking change)
- Prelude under title card is often musically desirable
- No video length increase, no silence period
- Existing videos render identically

---

## File Changes

### 1. `src/sow_render_worker/video_engine.py`

#### 1a. Add `TitleCardAudioMode` enum and `title_card_audio_mode` parameter to `VideoEngine.__init__()` (line 64)

```python
class TitleCardAudioMode(str, Enum):
    OVERLAY = "overlay"
    DELAY = "delay"

class VideoEngine:
    def __init__(
        self,
        asset_fetcher: AssetFetcherProtocol,
        template: VideoTemplateName = "dark",
        font_size_preset: FontSizePreset = "M",
        resolution: str = "1080p",
        fps: int = 24,
        include_title_card: bool = True,
        title_card_duration_seconds: float = 5.0,
        title_card_audio_mode: TitleCardAudioMode = TitleCardAudioMode.OVERLAY,
        ffmpeg_path: str | None = None,
        ffprobe_path: str | None = None,
    ):
        ...
        self.title_card_audio_mode = title_card_audio_mode
```

#### 1b. Compute `title_card_offset` and `title_card_frame_count` in `generate_video()` (after line 125)

**Key v3 fix:** Compute `title_card_frame_count` once in `generate_video()` (not separately in `encode_video_with_ffmpeg`) and derive `title_card_offset` from the frame-accurate duration, not the float `title_card_duration_seconds`. This eliminates the adelay timing mismatch.

```python
title_card_frame_count = (
    math.ceil(self.title_card_duration_seconds * self.fps)
    if self.include_title_card
    else 0
)
title_card_offset = (
    title_card_frame_count / self.fps
    if (self.include_title_card and self.title_card_audio_mode == TitleCardAudioMode.DELAY)
    else 0.0
)
video_duration_seconds = total_duration_seconds + title_card_offset
total_frames = math.ceil(video_duration_seconds * self.fps)
```

Pass `title_card_offset` and `title_card_frame_count` to `encode_video_with_ffmpeg()`. Use `video_duration_seconds` for `VideoExportResult.duration_seconds`.

**Why frame-accurate offset?** `ceil(title_card_duration_seconds * fps) / fps` can differ from `title_card_duration_seconds` by up to `(fps-1)/fps²` seconds (~41.7ms at 24fps). Using the frame-accurate value ensures the `adelay` filter delays audio by exactly the same duration the title card is displayed, preventing a subtle de-sync at the transition point.

#### 1c. Add `title_card_offset` and `title_card_frame_count` parameters to `encode_video_with_ffmpeg()` (line 241)

Add parameters `title_card_offset: float = 0.0` and `title_card_frame_count: int = 0`.

Remove the local `title_card_frame_count` computation at lines 311-315 (now received from caller).

#### 1d. Delay audio in FFmpeg command when `title_card_offset > 0` (lines 262-286)

When `title_card_offset > 0`, add an `adelay` filter using the **frame-accurate** offset:

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

**Note:** Remove `-shortest` when `adelay` is used. With `adelay`, the audio stream becomes `title_card_offset + audio_duration` long, matching the video stream length exactly.

#### 1e. Fix `current_time` calculation in delay mode (line 332)

```python
if title_card_config and frame_count < title_card_frame_count:
    frame_bytes = title_card_bytes
else:
    current_time = (
        (frame_count - title_card_frame_count) / self.fps
        if title_card_offset > 0
        else frame_count / self.fps
    )
    img = self.frame_renderer.render_frame(lyrics, segments, current_time)
    frame_bytes = img.tobytes()
```

In `overlay` mode, `current_time = frame_count / self.fps` (unchanged — maps to audio timeline).
In `delay` mode, `current_time = (frame_count - title_card_frame_count) / self.fps` (maps to audio timeline starting at 0.0 after title card).

#### 1f. Fix `TitleCardConfig.duration_seconds` (line 206) — applies to BOTH modes

```python
title_card_config = TitleCardConfig(
    enabled=True,
    duration_seconds=self.title_card_duration_seconds,
    ...
)
```

This is a semantic fix regardless of audio mode. `render_title_card()` uses `config.total_duration_seconds` (not `config.duration_seconds`) for the "X:XX" subtitle, so rendering is unaffected.

#### 1g. Update `VideoExportResult` to return video duration

```python
return VideoExportResult(
    output_path=output_path,
    total_frames=total_frames,
    duration_seconds=video_duration_seconds,
    width=self.resolution[0],
    height=self.resolution[1],
    fps=self.fps,
)
```

In `overlay` mode, `video_duration_seconds == total_duration_seconds` (no change).
In `delay` mode, `video_duration_seconds == total_duration_seconds + title_card_offset`.

### 2. `src/sow_render_worker/pipeline.py`

#### 2a. Pass `title_card_audio_mode` to `VideoEngine` (line 347)

```python
video_engine = VideoEngine(
    asset_fetcher,
    template=job.template,
    font_size_preset=job.font_size_preset,
    resolution=job.resolution,
    include_title_card=job.include_title_card,
    title_card_duration_seconds=job.title_card_duration_seconds or 5.0,
    title_card_audio_mode=TitleCardAudioMode(job.title_card_audio_mode)
        if job.title_card_audio_mode
        else TitleCardAudioMode.OVERLAY,
)
```

#### 2b. Offset chapter timestamps in delay mode (lines 436-441)

**v3 fix:** Remove the dead `ch.lines` offset from v2. `_segment_to_chapter_info()` does not populate `lines`, so offsetting them was a no-op. Only offset `start_seconds` and `end_seconds`.

```python
title_card_offset = (
    job.title_card_duration_seconds or 5.0
    if (job.include_title_card and job.title_card_audio_mode == TitleCardAudioMode.DELAY)
    else 0.0
)

chapters_for_video = [
    ChapterInfo(
        position=ch.position,
        song_title=ch.song_title,
        start_seconds=ch.start_seconds + title_card_offset,
        end_seconds=ch.end_seconds + title_card_offset,
    )
    for ch in (
        _segment_to_chapter_info(seg, i)
        for i, seg in enumerate(audio_result.segments)
    )
]
```

#### 2c. Pass `title_card_offset` to `generate_chapters_manifest()` (lines 445-449)

```python
chapters_manifest = generate_chapters_manifest(
    list(audio_result.segments),
    asset_fetcher.download_lrc,
    audio_result.total_duration_seconds,
    title_card_offset=title_card_offset,
)
```

### 3. `src/sow_render_worker/chapters.py`

#### 3a. Add `title_card_offset` parameter to `generate_chapters_manifest()` (line 75)

```python
def generate_chapters_manifest(
    segments: list[SegmentInfo],
    download_lrc: Callable[[str], str | None | object],
    total_duration_seconds: float,
    title_card_offset: float = 0.0,
) -> ChaptersManifest:
```

Offset all `ChapterLine.start_seconds` and `Chapter.start_seconds`/`Chapter.end_seconds` by `title_card_offset`. When `title_card_offset == 0.0`, behavior is identical to current code.

**v3 fix:** Also update `ChaptersManifest.total_duration_seconds` to `total_duration_seconds + title_card_offset` so the manifest accurately reflects the video duration.

```python
return ChaptersManifest(
    chapters=tuple(offset_chapters),
    total_duration_seconds=total_duration_seconds + title_card_offset,
    generated_at=datetime.now(timezone.utc).isoformat(),
)
```

### 4. `src/sow_render_worker/frame_renderer.py`

No changes needed. `render_frame()` receives `current_time` on the audio timeline (regardless of mode), so segment and lyric lookups work correctly.

### 5. `src/sow_render_worker/db.py`

#### 5a. Add `title_card_audio_mode` field to `RenderJob` (line 28)

```python
@dataclass
class RenderJob:
    ...
    include_title_card: bool = False
    title_card_duration_seconds: Optional[float] = None
    title_card_audio_mode: Optional[str] = None  # "overlay" or "delay"
    ...
```

Update `_row_to_render_job()` to read the new column.

### 6. Web App — DB Schema

#### 6a. Add `titleCardAudioMode` column to `renderJobs` table (`webapp/src/db/schema.ts`, line 237)

```typescript
titleCardDurationSeconds: real("title_card_duration_seconds").default(10),
titleCardAudioMode: text("title_card_audio_mode").default("overlay"),
```

Run `npx drizzle-kit push` to apply the schema change.

### 7. Web App — API Validation

#### 7a. Add `titleCardAudioMode` to Zod schema (`webapp/src/app/api/render-jobs/route.ts`, line 7)

```typescript
const createRenderJobSchema = z.object({
  songsetId: z.string().min(1),
  template: z.enum(["dark", "gradient_warm", "gradient_blue"]).optional(),
  resolution: z.enum(["720p", "1080p"]).optional(),
  audioEnabled: z.boolean().optional(),
  videoEnabled: z.boolean().optional(),
  fontSizePreset: z.enum(["S", "M", "L", "XL"]).optional(),
  includeTitleCard: z.boolean().optional(),
  titleCardDurationSeconds: z.number().min(5).max(30).optional(),
  titleCardAudioMode: z.enum(["overlay", "delay"]).optional(),
});
```

### 8. Web App — Job Manager

#### 8a. Add `titleCardAudioMode` to `CreateRenderJobInput` (`webapp/src/lib/render/job-manager.ts`, line 24)

```typescript
export interface CreateRenderJobInput {
  songsetId: string;
  template?: string;
  resolution?: string;
  audioEnabled?: boolean;
  videoEnabled?: boolean;
  fontSizePreset?: string;
  includeTitleCard?: boolean;
  titleCardDurationSeconds?: number;
  titleCardAudioMode?: "overlay" | "delay";
}
```

#### 8b. Persist `titleCardAudioMode` in `createRenderJob()` (line 129)

```typescript
const [job] = await db
    .insert(renderJobs)
    .values({
      ...
      includeTitleCard: input.includeTitleCard ?? false,
      titleCardDurationSeconds: input.titleCardDurationSeconds ?? null,
      titleCardAudioMode: input.titleCardAudioMode ?? null,
      ...
    })
    .returning();
```

### 9. Web App — Frontend Render Form

Add a dropdown/select for `titleCardAudioMode` (only visible when `includeTitleCard` is true):

- **"Overlay (prelude plays under title card)"** — default, selected
- **"Delay (silence during title card)"**

---

## Test Updates

### `tests/test_video_engine.py`

#### Test 1: `total_frames` includes title card duration in delay mode

When `title_card_audio_mode=DELAY` with `title_card_duration_seconds=10.0` and audio duration 180s:
- `title_card_frame_count = ceil(10.0 * 24) = 240`
- `title_card_offset = 240 / 24 = 10.0`
- `total_frames = ceil((180 + 10.0) * 24) = 4560`

When `title_card_audio_mode=OVERLAY`:
- `title_card_offset = 0.0`
- `total_frames = ceil(180 * 24) = 4320` (unchanged)

#### Test 2: Frame-accurate `title_card_offset` when duration doesn't divide evenly by fps

With `title_card_duration_seconds=5.5` and `fps=24`:
- `title_card_frame_count = ceil(5.5 * 24) = 132`
- `title_card_offset = 132 / 24 = 5.5` (exact in this case)

With `title_card_duration_seconds=5.3` and `fps=24`:
- `title_card_frame_count = ceil(5.3 * 24) = ceil(127.2) = 128`
- `title_card_offset = 128 / 24 = 5.333...` (NOT 5.3 — this is the frame-accurate value)
- `adelay_ms = round(5.333... * 1000) = 5333`

This ensures audio delay matches the actual title card display duration.

#### Test 3: FFmpeg command includes `adelay` in delay mode

Verify the FFmpeg args contain `-filter_complex` with `adelay={offset_ms}|{offset_ms}` and `-map 0:v -map [delayed]` when `title_card_audio_mode=DELAY`.

#### Test 4: FFmpeg command has no `adelay` in overlay mode

Verify the existing FFmpeg args (no `-filter_complex`, has `-shortest`) when `title_card_audio_mode=OVERLAY`.

#### Test 5: `current_time` starts at 0.0 after title card in delay mode

With `title_card_audio_mode=DELAY`, `title_card_duration_seconds=5.0`, `fps=24`:
- Title card frames: 0–119
- First lyrics frame (120): `current_time = (120 - 120) / 24 = 0.0`
- Frame 240: `current_time = (240 - 120) / 24 = 5.0`

With `title_card_audio_mode=OVERLAY`:
- Title card frames: 0–119
- First lyrics frame (120): `current_time = 120 / 24 = 5.0` (maps to audio timeline, unchanged)

#### Test 6: `VideoExportResult.duration_seconds` includes title card in delay mode

In delay mode: `result.duration_seconds == audio_duration + title_card_offset`
In overlay mode: `result.duration_seconds == audio_duration`

#### Test 7: `TitleCardConfig.duration_seconds` equals title card duration (both modes)

Verify `title_card_config.duration_seconds == self.title_card_duration_seconds` (not `total_duration_seconds`).

### `tests/test_chapters.py`

#### Test 8: Chapter timestamps offset by `title_card_offset`

Verify that `generate_chapters_manifest()` with `title_card_offset=10.0` shifts all `start_seconds`/`end_seconds` and `ChapterLine.start_seconds` by 10.0.
Verify that `title_card_offset=0.0` produces identical output to current behavior.

#### Test 9: `ChaptersManifest.total_duration_seconds` includes offset

Verify that `ChaptersManifest.total_duration_seconds == audio_duration + title_card_offset` when `title_card_offset > 0`.
Verify that `ChaptersManifest.total_duration_seconds == audio_duration` when `title_card_offset == 0.0`.

### `tests/test_pipeline.py`

#### Test 10: Pipeline offsets chapter timestamps in delay mode only

Verify that `chapters_for_video` and `chapters_manifest` have timestamps shifted by `title_card_offset` when `title_card_audio_mode=DELAY`.
Verify no offset when `title_card_audio_mode=OVERLAY`.

#### Test 11: Pipeline passes `title_card_audio_mode` to `VideoEngine`

Verify that `VideoEngine` is constructed with the correct `title_card_audio_mode` from the job.

---

## Implementation Order

1. Add `TitleCardAudioMode` enum and `title_card_audio_mode` parameter to `VideoEngine.__init__()` (1a)
2. Compute `title_card_frame_count` and `title_card_offset` in `generate_video()` (1b)
3. Add `title_card_offset` and `title_card_frame_count` parameters to `encode_video_with_ffmpeg()` (1c)
4. Delay audio in FFmpeg command when `title_card_offset > 0` (1d)
5. Fix `current_time` calculation for delay mode (1e)
6. Fix `TitleCardConfig.duration_seconds` (1f) — applies to both modes
7. Update `VideoExportResult` (1g)
8. Add `title_card_audio_mode` to `RenderJob` dataclass (5a)
9. Offset chapter timestamps in `pipeline.py` (2a, 2b, 2c)
10. Add `title_card_offset` to `generate_chapters_manifest()` (3a)
11. Add DB column, API validation, job manager, and frontend changes (6–9)
12. Add/update tests
13. Run `PYTHONPATH=src pytest tests/test_video_engine.py tests/test_chapters.py tests/test_pipeline.py -v`

---

## Edge Cases

### Title card disabled (`include_title_card=False`)

`title_card_offset = 0.0` regardless of mode. No changes to FFmpeg command, `current_time`, or chapter timestamps. Behavior identical to current code.

### Overlay mode (default)

`title_card_offset = 0.0`. All code paths fall through to existing behavior. The only change is `TitleCardConfig.duration_seconds` (semantic fix, no rendering impact).

### Delay mode with title card duration > first song intro gap

If the first song's intro gap is shorter than `title_card_duration_seconds`, the title card still shows for the full duration. After the title card, `current_time = 0.0` and the intro info display works correctly from the start of the audio timeline. No lyrics are lost.

### Very short audio (< title card duration) in delay mode

If audio is shorter than `title_card_duration_seconds`, the `adelay` filter would push audio beyond the video end. The video would show the title card then silence. This is unlikely in practice (worship sets are long) but should be documented.

### `generate_blank_video()` with title card

When no lyrics are found, `generate_blank_video()` is called. This method doesn't support title cards. No change needed — title cards are only meaningful when lyrics exist.

### Frame-accurate offset rounding

When `title_card_duration_seconds` doesn't divide evenly by `1/fps`, the frame-accurate offset (`title_card_frame_count / fps`) may differ from the float duration by up to `(fps-1)/fps²` seconds (~41.7ms at 24fps). This is intentional — the `adelay` filter must match the actual title card display duration to prevent de-sync. The title card is always displayed for an integer number of frames, so the audio delay must match that exact frame-accurate duration.

### `title_card_duration_seconds` default inconsistency

The current defaults are inconsistent across the stack:

| Location | Default |
|---|---|
| DB column (`schema.ts:237`) | `10` |
| `VideoEngine.__init__` (`video_engine.py:73`) | `5.0` |
| Pipeline fallback (`pipeline.py:353`) | `5.0` |

This is a pre-existing issue, not introduced by this spec. A separate cleanup should align these defaults (recommend `5.0` everywhere, since the DB column default of `10` is never actually used — the API schema defaults to `null` and the pipeline falls back to `5.0`).

---

## Verification Checklist

- [ ] `TitleCardAudioMode` enum added with `OVERLAY` and `DELAY` values
- [ ] `VideoEngine.__init__()` accepts `title_card_audio_mode` parameter (default `OVERLAY`)
- [ ] `title_card_frame_count` computed in `generate_video()`, not `encode_video_with_ffmpeg()`
- [ ] `title_card_offset` computed from **frame-accurate** duration (`title_card_frame_count / fps`), not float `title_card_duration_seconds`
- [ ] `total_frames = ceil((audio_duration + title_card_offset) * fps)` in delay mode
- [ ] `total_frames = ceil(audio_duration * fps)` in overlay mode (unchanged)
- [ ] FFmpeg command includes `adelay` filter when `title_card_offset > 0`
- [ ] FFmpeg command omits `adelay` and uses `-shortest` when `title_card_offset == 0`
- [ ] `current_time = (frame_count - title_card_frame_count) / fps` in delay mode
- [ ] `current_time = frame_count / fps` in overlay mode (unchanged)
- [ ] `TitleCardConfig.duration_seconds = self.title_card_duration_seconds` (both modes)
- [ ] `VideoExportResult.duration_seconds` includes title card offset in delay mode
- [ ] `RenderJob` dataclass includes `title_card_audio_mode` field
- [ ] DB schema includes `title_card_audio_mode` column
- [ ] API Zod schema validates `titleCardAudioMode`
- [ ] `CreateRenderJobInput` includes `titleCardAudioMode`
- [ ] `createRenderJob()` persists `titleCardAudioMode`
- [ ] Pipeline passes `title_card_audio_mode` to `VideoEngine`
- [ ] Chapter timestamps offset by `title_card_offset` in delay mode
- [ ] Chapter timestamps unchanged in overlay mode
- [ ] `ChaptersManifest.total_duration_seconds` includes `title_card_offset` in delay mode
- [ ] Pipeline chapter offset does NOT reference `ch.lines` (dead code removed)
- [ ] All existing tests pass
- [ ] New tests pass
