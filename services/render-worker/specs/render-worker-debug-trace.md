# Spec: Add Debug Trace to Render Worker Pipeline

## Problem

The render worker is a "black box" during execution. The longest phases (audio mixing, video encoding) have **zero logging**, and the only progress signals are 5 DB updates (one per phase transition). When the Next.js server times out after 5 min, there's no way to know where the worker is stuck.

### Current State

| Module | Has Logger | Logging Level |
|--------|-----------|---------------|
| `pipeline.py` | Yes | Minimal (5 INFO, 3 ERROR/WARN) |
| `lambda_handler.py` | Yes | Minimal (3 INFO, 1 WARN, 1 ERROR) |
| `video_engine.py` | **No** | **Zero logging** |
| `audio_engine.py` | **No** | **Zero logging** |
| `frame_renderer.py` | **No** | **Zero logging** |
| `uploader.py` | Inline only | 1 WARN on delete failure |
| `asset_fetcher.py` | Yes | Only on failure (2 EXCEPTION) |
| `db.py` | **No** | **Zero logging** |

Additionally:

- `progress_callback` params exist in `audio_engine` and `video_engine` but are **never wired** by the pipeline
- `percent_complete` DB column is only set to 0 or 100, never to intermediate values
- `estimated_seconds_left` DB column is never computed

## Goals

1. Add structured logging (with `job_id`) to every module in the pipeline
2. Wire up existing `progress_callback` parameters so intra-phase progress is visible
3. Compute and persist `percent_complete` and `estimated_seconds_left` to DB during rendering
4. Log video encoding progress every 30 seconds of video content
5. Log audio mixing progress per-song and at FFmpeg concat boundaries

## Design Decisions

- **`job_id` threaded through** to `generate_songset_audio()`, `generate_video()`, `encode_video_with_ffmpeg()`, `concatenate_audio_files()` for structured log enrichment
- **Video encoding progress**: log every 30 seconds of video content rendered (not every frame); update DB `percent_complete` every ~5 seconds of video time
- **Audio mixing progress**: log per-song download/probe; log FFmpeg concat start/end
- **`RenderProgress` extended** with `percent_complete` and `estimated_seconds_left` fields so the webapp can show real progress instead of just phase names
- **Log level**: All new trace logs use `INFO` level (not DEBUG) so they appear in Lambda CloudWatch logs by default

---

## File Changes

### 1. `src/sow_render_worker/db.py`

#### 1a. Add `percent_complete` and `estimated_seconds_left` to `RenderProgress`

```python
@dataclass
class RenderProgress:
    phase: Optional[str] = None
    phase_index: Optional[int] = None
    total_phases: Optional[int] = None
    estimated_total_seconds: Optional[float] = None
    total_duration_seconds: Optional[float] = None
    started_at: Optional[datetime] = None
    elapsed_seconds: Optional[float] = None
    percent_complete: Optional[float] = None          # NEW
    estimated_seconds_left: Optional[float] = None   # NEW
```

#### 1b. Update `update_render_progress()` to handle new fields

Add two new conditional blocks after the existing `elapsed_seconds` block (around line 233):

```python
if progress.percent_complete is not None:
    updates.append("percent_complete = %s")
    params.append(progress.percent_complete)

if progress.estimated_seconds_left is not None:
    updates.append("estimated_seconds_left = %s")
    params.append(progress.estimated_seconds_left)
```

#### 1c. Add module-level logger and log each progress update

```python
import logging

logger = logging.getLogger(__name__)
```

At the end of `update_render_progress()`, before returning, add:

```python
logger.info(
    "Progress update for job %s: phase=%s (%d/%d), elapsed=%.1fs, percent=%.1f%%, est_remaining=%s",
    job_id,
    progress.phase,
    progress.phase_index if progress.phase_index is not None else get_phase_index(progress.phase or ""),
    progress.total_phases,
    progress.elapsed_seconds or 0,
    progress.percent_complete or 0,
    f"{progress.estimated_seconds_left:.0f}s" if progress.estimated_seconds_left is not None else "N/A",
)
```

---

### 2. `src/sow_render_worker/pipeline.py`

#### 2a. Add `job_id` to all existing log messages

Replace all `logger.info/warning/error` calls that don't include `job_id` with versions that do. For example:

- Line 253-258: Add `job_id` to the warning about missing `duration_seconds`
- Line 395: Add `job_id` to the info about cancelled job

#### 2b. Add phase transition log messages

After each `update_render_progress()` call, add a log line:

```python
logger.info(
    "[%s] Phase %d/%d: %s (elapsed=%.1fs)",
    job_id, phase_index + 1, len(PHASES), PHASES[phase_index], elapsed_seconds()
)
```

This applies at lines 232, 264, 299, 328, 364.

#### 2c. Wire `progress_callback` to `generate_songset_audio()`

Create a callback that logs per-song progress:

```python
def audio_progress_callback(step: int, total_steps: int) -> None:
    logger.info(
        "[%s] Audio mixing: step %d/%d (%d%%)",
        job_id, step, total_steps, int(step / total_steps * 100) if total_steps > 0 else 0,
    )
```

Pass it to `generate_songset_audio()`:

```python
audio_result = generate_songset_audio(
    items,
    audio_output_path,
    asset_fetcher,
    progress_callback=audio_progress_callback,  # NEW
    job_id=job_id,                                # NEW
)
```

#### 2d. Wire `progress_callback` to `video_engine.generate_video()`

Create a callback that logs video encoding progress and updates DB:

```python
_last_video_progress_log_seconds = 0.0
_last_video_db_update_time = pipeline_start

def video_progress_callback(frame_count: int, total_frames: int) -> None:
    nonlocal _last_video_progress_log_seconds, _last_video_db_update_time

    now = time.monotonic()
    video_seconds = frame_count / video_engine.fps
    total_video_seconds = total_frames / video_engine.fps

    # Log every 30 seconds of video content
    if video_seconds - _last_video_progress_log_seconds >= 30:
        logger.info(
            "[%s] Video encoding: %.0fs/%.0fs (%d/%d frames, %.1f%%)",
            job_id, video_seconds, total_video_seconds,
            frame_count, total_frames,
            frame_count / total_frames * 100 if total_frames > 0 else 0,
        )
        _last_video_progress_log_seconds = video_seconds

    # Update DB percent_complete every ~5 seconds of wall time
    if now - _last_video_db_update_time >= 5:
        phase_base = 3 / len(PHASES) * 100  # phase 3 starts at 60%
        phase_weight = 1 / len(PHASES) * 100  # phase 3 is worth 20%
        frame_progress = frame_count / total_frames if total_frames > 0 else 0
        current_percent = phase_base + frame_progress * phase_weight

        update_render_progress(
            conn,
            job_id,
            user_id,
            RenderProgress(
                phase=PHASES[3],
                phase_index=3,
                total_phases=len(PHASES),
                elapsed_seconds=elapsed_seconds(),
                percent_complete=current_percent,
                estimated_seconds_left=max(0, accurate_estimated_total - elapsed_seconds()) if accurate_estimated_total else None,
            ),
        )
        _last_video_db_update_time = now
```

Pass it to `generate_video()`:

```python
video_engine.generate_video(
    audio_output_path,
    list(audio_result.segments),
    video_output_path,
    progress_callback=video_progress_callback,  # NEW
    timeout_check_callback=check_lambda_timeout,
    job_id=job_id,                                # NEW
)
```

#### 2e. Compute `percent_complete` and `estimated_seconds_left` at each phase transition

For each `update_render_progress()` call, add:

```python
percent_complete = (phase_index / len(PHASES)) * 100
estimated_seconds_left = max(0, estimated_total_seconds - elapsed_seconds()) if estimated_total_seconds else None
```

And include them in the `RenderProgress`:

```python
RenderProgress(
    phase=PHASES[N],
    phase_index=N,
    total_phases=len(PHASES),
    estimated_total_seconds=...,
    total_duration_seconds=...,
    elapsed_seconds=elapsed_seconds(),
    percent_complete=percent_complete,                    # NEW
    estimated_seconds_left=estimated_seconds_left,        # NEW
)
```

#### 2f. Add timing log at pipeline start and end

At the top of the `try` block (after `start_render_job`):

```python
logger.info(
    "[%s] Pipeline started: resolution=%s, video=%s, audio=%s, items=%d",
    job_id, job.resolution, job.video_enabled, job.audio_enabled, len(items),
)
```

At the end, before `complete_render_job()`:

```python
logger.info("[%s] Pipeline completed in %.1fs", job_id, elapsed_seconds())
```

---

### 3. `src/sow_render_worker/video_engine.py`

#### 3a. Add module-level logger

```python
import logging

logger = logging.getLogger(__name__)
```

#### 3b. Add `job_id` parameter to `generate_video()`

```python
def generate_video(
    self,
    audio_path: str,
    segments: list[AudioSegmentInfo],
    output_path: str,
    progress_callback: ProgressCallback | None = None,
    timeout_check_callback: TimeoutCheckCallback | None = None,
    job_id: str | None = None,  # NEW
) -> VideoExportResult:
```

Log at start:

```python
logger.info(
    "[%s] generate_video: duration=%.1fs, total_frames=%d, resolution=%s, fps=%d",
    job_id or "unknown", total_duration_seconds, total_frames,
    f"{self.resolution[0]}x{self.resolution[1]}", self.fps,
)
```

Log at end (before return):

```python
logger.info(
    "[%s] generate_video: complete, %d frames encoded",
    job_id or "unknown", total_frames,
)
```

#### 3c. Add `job_id` parameter to `encode_video_with_ffmpeg()`

```python
def encode_video_with_ffmpeg(
    self,
    audio_path: str,
    output_path: str,
    total_frames: int,
    total_duration_seconds: float,
    lyrics: list[GlobalLRCLine],
    segments: list[SegmentInfo],
    progress_callback: ProgressCallback | None = None,
    title_card_config: TitleCardConfig | None = None,
    timeout_check_callback: TimeoutCheckCallback | None = None,
    job_id: str | None = None,  # NEW
) -> None:
```

Log at start:

```python
ffmpeg_start = time.monotonic()
logger.info(
    "[%s] encode_video_with_ffmpeg: starting FFmpeg pipe, %d frames (%.1fs at %dfps)",
    job_id or "unknown", total_frames, total_duration_seconds, self.fps,
)
```

Add periodic progress logging inside the frame loop. After the existing `progress_callback` call (line 339-340), add:

```python
if frame_count % (self.fps * 30) == 0 and frame_count > 0:
    video_seconds = frame_count / self.fps
    logger.info(
        "[%s] Video encoding progress: %.0fs/%.0fs (%d/%d frames, %.1f%%)",
        job_id or "unknown", video_seconds, total_duration_seconds,
        frame_count, total_frames,
        frame_count / total_frames * 100 if total_frames > 0 else 0,
    )
```

Log FFmpeg process result (after `process.wait()` at line 355):

```python
ffmpeg_elapsed = time.monotonic() - ffmpeg_start
logger.info(
    "[%s] FFmpeg process exited with code %d in %.1fs",
    job_id or "unknown", return_code, ffmpeg_elapsed,
)
```

Log on error (in the BrokenPipeError handler, line 322-335):

```python
logger.error(
    "[%s] FFmpeg pipe broken at frame %d/%d",
    job_id or "unknown", frame_count, total_frames,
)
```

#### 3d. Add `job_id` parameter to `inject_chapters()`

```python
def inject_chapters(
    self,
    video_path: str,
    chapters: list[ChapterInfo],
    job_id: str | None = None,  # NEW
) -> bool:
```

Log at start and end:

```python
logger.info(
    "[%s] inject_chapters: %d chapters into %s",
    job_id or "unknown", len(chapters), video_path,
)
# ... existing code ...
# On success:
logger.info("[%s] inject_chapters: complete", job_id or "unknown")
# On failure (existing return False paths):
logger.warning("[%s] inject_chapters: failed", job_id or "unknown")
```

#### 3e. Add `job_id` parameter to `generate_blank_video()`

```python
def generate_blank_video(
    self,
    audio_path: str,
    output_path: str,
    duration_seconds: float,
    job_id: str | None = None,  # NEW
) -> VideoExportResult:
```

Log at start:

```python
logger.info(
    "[%s] generate_blank_video: %.1fs, %s",
    job_id or "unknown", duration_seconds, output_path,
)
```

#### 3f. Update `generate_video()` to pass `job_id` through

```python
self.encode_video_with_ffmpeg(
    audio_path,
    output_path,
    total_frames,
    total_duration_seconds,
    all_lyrics,
    segment_infos,
    progress_callback,
    title_card_config,
    timeout_check_callback,
    job_id=job_id,  # NEW
)
```

And for the blank video fallback:

```python
return self.generate_blank_video(
    audio_path, output_path, total_duration_seconds,
    job_id=job_id,  # NEW
)
```

And for chapter injection (in `pipeline.py`):

```python
video_engine.inject_chapters(video_output_path, chapters_for_video, job_id=job_id)
```

---

### 4. `src/sow_render_worker/audio_engine.py`

#### 4a. Add module-level logger

```python
import logging

logger = logging.getLogger(__name__)
```

#### 4b. Add `job_id` parameter to `generate_songset_audio()`

```python
def generate_songset_audio(
    items: list[SongsetItem],
    output_path: str,
    asset_fetcher: AssetFetcherProtocol,
    progress_callback: Callable[[int, int], None] | None = None,
    normalize: bool = True,
    target_lufs: float = -14.0,
    output_bitrate: str = "320k",
    sample_rate: int = 44100,
    channels: int = 2,
    job_id: str | None = None,  # NEW
) -> ExportResult:
```

Log per-song download and probe (inside the `for i, item` loop):

```python
logger.info(
    "[%s] Audio: processing song %d/%d - %s (hash=%s)",
    job_id or "unknown", i + 1, len(items),
    item.song_title or "untitled", item.recording_hash_prefix or "N/A",
)
```

After probing:

```python
logger.info(
    "[%s] Audio: song %d probed - duration=%.1fs, gap_ms=%d, crossfade_ms=%d",
    job_id or "unknown", i + 1, duration_ms / 1000.0, gap_ms, crossfade_ms,
)
```

Log before FFmpeg concat:

```python
logger.info(
    "[%s] Audio: starting FFmpeg concatenation of %d files -> %s",
    job_id or "unknown", len(audio_files), output_path,
)
```

Log after FFmpeg concat:

```python
logger.info(
    "[%s] Audio: concatenation complete, total duration=%.1fs, %d segments",
    job_id or "unknown", current_time_ms / 1000.0, len(segments),
)
```

#### 4c. Add `job_id` parameter to `concatenate_audio_files()`

```python
def concatenate_audio_files(
    audio_files: list[dict[str, Any]],
    output_path: str,
    normalize: bool = True,
    target_lufs: float = -14.0,
    output_bitrate: str = "320k",
    sample_rate: int = 44100,
    channels: int = 2,
    job_id: str | None = None,  # NEW
) -> None:
```

Log FFmpeg command start and completion:

```python
logger.info("[%s] FFmpeg audio concat: starting (timeout=1800s)", job_id or "unknown")
subprocess.run(cmd, check=True, capture_output=True, timeout=1800)
logger.info("[%s] FFmpeg audio concat: complete", job_id or "unknown")
```

#### 4d. Update `generate_songset_audio()` to pass `job_id` through

```python
concatenate_audio_files(
    audio_files,
    output_path,
    normalize=normalize,
    target_lufs=target_lufs,
    output_bitrate=output_bitrate,
    sample_rate=sample_rate,
    channels=channels,
    job_id=job_id,  # NEW
)
```

---

### 5. `src/sow_render_worker/frame_renderer.py`

#### 5a. Add module-level logger

```python
import logging

logger = logging.getLogger(__name__)
```

#### 5b. Log font load result in `_load_font()`

After the for loop (line 96-100), if a font is found:

```python
logger.info("Loaded font: %s (size=%d)", path, size)
```

If falling back to default:

```python
logger.warning("No TrueType font found, using default bitmap font (size=%d)", size)
```

#### 5c. Log init in `FrameRenderer.__init__()`

```python
logger.info(
    "FrameRenderer init: template=%s, font_size=%s, resolution=%dx%d",
    self.template.name, self.font_size_preset, self.resolution[0], self.resolution[1],
)
```

---

### 6. `src/sow_render_worker/uploader.py`

#### 6a. Add module-level logger

```python
import logging

logger = logging.getLogger(__name__)
```

#### 6b. Log each upload in `upload_file()`

```python
def upload_file(self, key, file_path, content_type=None, cache_control=None, metadata=None):
    file_size = Path(file_path).stat().st_size
    logger.info(
        "Uploading %s (%s, %d bytes)", key, content_type or infer_content_type(key), file_size
    )
    # ... existing code ...
    logger.info("Upload complete: %s", key)
```

#### 6c. Log each upload in `upload_buffer()`

```python
def upload_buffer(self, key, buffer, content_type=None, cache_control=None, metadata=None):
    logger.info(
        "Uploading %s (%s, %d bytes)", key, content_type or infer_content_type(key), len(buffer)
    )
    # ... existing code ...
    logger.info("Upload complete: %s", key)
```

#### 6d. Log each artifact in `upload_render_artifacts()`

```python
def upload_render_artifacts(self, render_job_id, artifacts):
    logger.info(
        "Uploading render artifacts for job %s: mp3=%s, mp4=%s, chapters=%s",
        render_job_id,
        "yes" if artifacts.mp3_path else "no",
        "yes" if artifacts.mp4_path else "no",
        "yes" if artifacts.chapters is not None else "no",
    )
    # ... existing code ...
    logger.info("All render artifacts uploaded for job %s", render_job_id)
    return result
```

#### 6e. Replace inline logger in `delete_render_artifacts()`

Replace the inline `import logging` + `logging.getLogger(__name__)` with the module-level `logger`.

---

### 7. `src/sow_render_worker/asset_fetcher.py`

#### 7a. Add cache hit/miss logging in `download_audio()`

After cache check:

```python
if cache_path.exists():
    logger.info("Audio cache hit: %s", hash_prefix)
    return str(cache_path)
```

After successful download:

```python
logger.info(
    "Audio downloaded: %s (%d bytes, cached at %s)",
    hash_prefix, len(response.data), cache_path,
)
```

#### 7b. Add cache hit/miss logging in `download_lrc()`

After cache check:

```python
if hash_prefix in self._lrc_cache:
    logger.debug("LRC cache hit: %s", hash_prefix)
    return self._lrc_cache[hash_prefix]
```

After successful download:

```python
logger.info("LRC downloaded: %s (%d bytes)", hash_prefix, len(response.data))
```

---

### 8. `src/sow_render_worker/lambda_handler.py`

#### 8a. Add total pipeline duration timing

In `_process_record()`, add timing around `execute_render_pipeline()`:

```python
import time

def _process_record(record, config, conn, context):
    # ... existing parsing code ...

    logger.info(
        "Processing render job",
        extra={"job_id": job_id, "user_id": user_id},
    )

    start = time.monotonic()
    execute_render_pipeline(job_id, user_id, conn, lambda_context=context)
    duration = time.monotonic() - start

    logger.info(
        "Render job completed successfully in %.1fs",
        duration,
        extra={"job_id": job_id, "user_id": user_id, "duration_seconds": duration},
    )
```

---

## Pipeline Call Chain Updates

The `job_id` parameter needs to be threaded through the following call chain:

```
lambda_handler.handler()
  └─ _process_record(job_id=...)
       └─ execute_render_pipeline(job_id=...)
            ├─ generate_songset_audio(job_id=...)          # NEW param
            │    └─ concatenate_audio_files(job_id=...)    # NEW param
            ├─ VideoEngine.generate_video(job_id=...)      # NEW param
            │    └─ encode_video_with_ffmpeg(job_id=...)   # NEW param
            │    └─ generate_blank_video(job_id=...)       # NEW param
            ├─ VideoEngine.inject_chapters(job_id=...)     # NEW param
            └─ R2Uploader.upload_render_artifacts()        # already has render_job_id
```

---

## Test Updates Required

### `tests/test_pipeline.py`

- Update any calls to `generate_songset_audio()` to pass `job_id` (or `None`)
- Update any calls to `VideoEngine.generate_video()` to pass `job_id` (or `None`)
- Verify that `RenderProgress` includes `percent_complete` and `estimated_seconds_left`
- Add test for `video_progress_callback` updating DB with `percent_complete`

### `tests/test_video_engine.py`

- Update calls to `generate_video()`, `encode_video_with_ffmpeg()`, `inject_chapters()`, `generate_blank_video()` to accept `job_id` parameter

### `tests/test_audio_engine.py`

- Update calls to `generate_songset_audio()`, `concatenate_audio_files()` to accept `job_id` parameter

### `tests/test_db.py`

- Add tests for `RenderProgress` with `percent_complete` and `estimated_seconds_left`
- Add tests for `update_render_progress()` handling the new fields

### `tests/test_uploader.py`

- Update tests for the module-level logger (replace inline logger)

### `tests/test_frame_renderer.py`

- Update tests for the new logger in `FrameRenderer.__init__()` and `_load_font()`

### `tests/test_asset_fetcher.py`

- Update tests for cache hit/miss logging

---

## Log Output Examples

### Before (current)

```
Processing render job [job_id=abc, user_id=1]
Reclaimed stale job abc (was stuck in 'running' for too long), retrying
Render pipeline failed for job abc: TimeoutError(...)
```

### After (with this spec)

```
Processing render job [job_id=abc, user_id=1]
[abc] Pipeline started: resolution=720p, video=True, audio=True, items=5
Progress update for job abc: phase=preparing (0/5), elapsed=0.0s, percent=0.0%, est_remaining=N/A
[abc] Phase 1/5: preparing (elapsed=0.0s)
Audio cache hit: hash001
[abc] Audio: processing song 1/5 - 奇妙神蹟 (hash=hash001)
[abc] Audio: song 1 probed - duration=245.3s, gap_ms=0, crossfade_ms=0
Audio downloaded: hash002 (8234567 bytes, cached at /tmp/sow-assets/cache/hash002.mp3)
[abc] Audio: processing song 2/5 - 祢的恩典 (hash=hash002)
[abc] Audio: song 2 probed - duration=198.7s, gap_ms=2000, crossfade_ms=3000
...
[abc] Audio: starting FFmpeg concatenation of 5 files -> /tmp/sow-assets/temp/abc/output.mp3
[abc] FFmpeg audio concat: starting (timeout=1800s)
[abc] FFmpeg audio concat: complete
[abc] Audio: concatenation complete, total duration=1245.0s, 5 segments
Progress update for job abc: phase=mixing_audio (1/5), elapsed=12.3s, percent=20.0%, est_remaining=49s
[abc] Phase 2/5: mixing_audio (elapsed=12.3s)
Progress update for job abc: phase=rendering_frames (2/5), elapsed=12.5s, percent=40.0%, est_remaining=37s
[abc] Phase 3/5: rendering_frames (elapsed=12.5s)
FrameRenderer init: template=dark, font_size=M, resolution=1280x720
Loaded font: /usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc (size=48)
[abc] generate_video: duration=1245.0s, total_frames=29880, resolution=1280x720, fps=24
[abc] encode_video_with_ffmpeg: starting FFmpeg pipe, 29880 frames (1245.0s at 24fps)
Progress update for job abc: phase=encoding_video (3/5), elapsed=15.0s, percent=60.0%, est_remaining=35s
[abc] Video encoding progress: 30s/1245s (720/29880 frames, 2.4%)
Progress update for job abc: phase=encoding_video (3/5), elapsed=20.0s, percent=61.0%, est_remaining=30s
[abc] Video encoding progress: 60s/1245s (1440/29880 frames, 4.8%)
Progress update for job abc: phase=encoding_video (3/5), elapsed=25.0s, percent=62.0%, est_remaining=25s
...
[abc] FFmpeg process exited with code 0 in 812.3s
[abc] generate_video: complete, 29880 frames encoded
[abc] inject_chapters: 5 chapters into /tmp/sow-assets/temp/abc/output.mp4
[abc] inject_chapters: complete
Progress update for job abc: phase=uploading (4/5), elapsed=830.0s, percent=80.0%, est_remaining=0s
[abc] Phase 5/5: uploading (elapsed=830.0s)
Uploading render artifacts for job abc: mp3=yes, mp4=yes, chapters=yes
Uploading renders/abc/output.mp3 (audio/mpeg, 9823456 bytes)
Upload complete: renders/abc/output.mp3
Uploading renders/abc/output.mp4 (video/mp4, 45234567 bytes)
Upload complete: renders/abc/output.mp4
Uploading renders/abc/chapters.json (application/json, 2345 bytes)
Upload complete: renders/abc/chapters.json
All render artifacts uploaded for job abc
[abc] Pipeline completed in 845.2s
Render job completed successfully in 845.2s [job_id=abc, user_id=1, duration_seconds=845.2]
```

---

## Review Fixes Applied (Beyond Original Spec)

These were identified during code review and applied during implementation:

1. **`AND status = 'running'` guard** in `update_render_progress()` — prevents stale writes to cancelled/failed jobs from the `video_progress_callback` (which fires every 5s). The spec's `update_render_progress()` SQL had no status guard; now the WHERE clause includes `AND status = 'running'`.

2. **Periodic video log outside `progress_callback` guard** — the spec placed the 30s log "after the existing progress_callback call" but that's inside `if progress_callback and ...`. Moved it outside so it always fires regardless of whether `progress_callback` is provided.

3. **`_load_font()` flow fix** — spec said "log after the loop" but the function `return`s inside the loop. Log is now placed before the `return` inside the loop.

4. **Dynamic phase index** — used `PHASES.index("encoding_video")` instead of hardcoded `3` in `video_progress_callback` for maintainability.

5. **`len(body)` instead of `stat()`** in `uploader.py` — avoids redundant syscall since `body = file_path_obj.read_bytes()` already reads the file.

6. **"default font" instead of "bitmap font"** — Pillow 10+ `load_default()` may not be a bitmap font.

7. **Middle font fallback logged** — `ImageFont.truetype("sans-serif", size)` path now has its own log line.

8. **`video_progress_callback` stops when job not running** — when `update_render_progress()` returns `None` (job cancelled/failed), the callback now returns early instead of continuing to compute and attempt DB writes.

---

## Implementation Order

1. **`db.py`** — Extend `RenderProgress` + `update_render_progress()` (foundation for all other changes)
2. **`audio_engine.py`** — Add logger + `job_id` param
3. **`video_engine.py`** — Add logger + `job_id` param + periodic progress logging
4. **`frame_renderer.py`** — Add logger
5. **`uploader.py`** — Add module logger
6. **`asset_fetcher.py`** — Add cache hit/miss logging
7. **`pipeline.py`** — Wire callbacks, compute `percent_complete`/`estimated_seconds_left`, add phase transition logs, pass `job_id` through
8. **`lambda_handler.py`** — Add timing log
9. **Tests** — Update all affected test files
