# Handover: Render Worker Debug Trace Implementation

**Date:** 2026-05-23
**Spec:** `services/render-worker/specs/render-worker-debug-trace.md`
**Status:** Implementation ~90% complete, all tests passing

## What Was Done

All 8 source files and their corresponding test files have been modified per the spec. All 499 tests pass.

### Completed Changes

| # | File | Changes |
|---|------|---------|
| 1 | `src/sow_render_worker/db.py` | Added `percent_complete` and `estimated_seconds_left` to `RenderProgress`; added both fields to `update_render_progress()` SQL; added `AND status = 'running'` guard to WHERE clause; added module-level logger with progress update logging |
| 2 | `src/sow_render_worker/audio_engine.py` | Added module-level logger; added `job_id` param to `generate_songset_audio()` and `concatenate_audio_files()`; added per-song download/probe logging; added FFmpeg concat start/end logging |
| 3 | `src/sow_render_worker/video_engine.py` | Added module-level logger; added `job_id` param to `generate_video()`, `encode_video_with_ffmpeg()`, `inject_chapters()`, `generate_blank_video()`; added periodic progress logging every 30s of video content (OUTSIDE the `progress_callback` guard); added FFmpeg start/exit/BrokenPipe logging; added chapter injection logging |
| 4 | `src/sow_render_worker/frame_renderer.py` | Added module-level logger; fixed `_load_font()` to log BEFORE return inside the loop (not after); added logging for middle fallback (`truetype("sans-serif")`); changed "bitmap font" to "default font"; added `FrameRenderer.__init__()` logging |
| 5 | `src/sow_render_worker/uploader.py` | Added module-level logger; added upload logging to `upload_file()` (using `len(body)` not `stat()`), `upload_buffer()`, `upload_render_artifacts()`; replaced inline logger in `delete_render_artifacts()` with module-level logger |
| 6 | `src/sow_render_worker/asset_fetcher.py` | Added `logger.info` for audio cache hit and download success; added `logger.debug` for LRC cache hit; added `logger.info` for LRC download success |
| 7 | `src/sow_render_worker/pipeline.py` | Wired `audio_progress_callback` and `video_progress_callback`; added `percent_complete` and `estimated_seconds_left` to all 5 phase `RenderProgress` calls; added phase transition log messages; added pipeline start/completion logs; added `job_id` prefix `[job_id]` to all log messages; passed `job_id` through to `generate_songset_audio()`, `generate_video()`, `inject_chapters()`; used `PHASES.index("encoding_video")` instead of hardcoded `3` for dynamic phase index |
| 8 | `src/sow_render_worker/lambda_handler.py` | Added `import time`; added `time.monotonic()` timing around `execute_render_pipeline()`; updated completion log to include `duration_seconds` |

### Test Updates

| File | Changes |
|------|---------|
| `tests/test_db.py` | Added `percent_complete` and `estimated_seconds_left` to `TestRenderProgressDataclass`; added `test_update_percent_complete`, `test_update_estimated_seconds_left`, `test_status_guard_running`, `test_status_guard_returns_none_when_not_running` |
| `tests/test_audio_engine.py` | Updated `concatenate_audio_files` calls to pass `job_id="test-job"` |
| `tests/test_video_engine.py` | Updated `generate_blank_video`, `inject_chapters`, `generate_video` calls to pass `job_id="test-job"` |
| `tests/test_lambda_handler.py` | Updated `_process_record` mock calls to pass `mock_context`; fixed `side_effect` function signature from 3 to 4 args |

## Review Fixes Applied (Beyond Spec)

These were identified during code review and applied during implementation:

1. **`AND status = 'running'` guard** in `update_render_progress()` — prevents stale writes to cancelled/failed jobs from the `video_progress_callback` (which fires every 5s)
2. **Periodic video log outside `progress_callback` guard** — the spec placed the 30s log "after the existing progress_callback call" but that's inside `if progress_callback and ...`. Moved it outside so it always fires
3. **`_load_font()` flow fix** — spec said "log after the loop" but the function `return`s inside the loop. Log is now placed before the `return`
4. **Dynamic phase index** — used `PHASES.index("encoding_video")` instead of hardcoded `3` in `video_progress_callback`
5. **`len(body)` instead of `stat()`** in `uploader.py` — avoids redundant syscall since `body = file_path_obj.read_bytes()` already reads the file
6. **"default font" instead of "bitmap font"** — Pillow 10+ `load_default()` may not be a bitmap font
7. **Middle font fallback logged** — `ImageFont.truetype("sans-serif", size)` path now has its own log line

## Remaining Work

### 1. Add `percent_complete` assertion to pipeline test

The `test_pipeline_progress_updates_through_phases` test checks that all 5 phases appear in progress updates, but does NOT yet assert `percent_complete` values. Add:

```python
# In test_pipeline_progress_updates_through_phases, after checking phases_seen:
progress_objs = [call[0][3] for call in mock_update.call_args_list]
for p in progress_objs:
    if p.phase_index is not None:
        assert p.percent_complete is not None
        assert p.percent_complete == (p.phase_index / len(PHASES)) * 100
```

### 2. Add test for `video_progress_callback` updating DB with `percent_complete`

The `video_progress_callback` is defined as a closure inside `execute_render_pipeline()`. To test it, you need to invoke the pipeline with a mock `VideoEngine` whose `generate_video()` calls the `progress_callback` with frame counts. The existing `test_full_pipeline_flow` mocks `VideoEngine` entirely, so the callback never fires.

A new test should:
- Use a real `VideoEngine` (or partially mock it) so `generate_video()` actually calls `progress_callback`
- OR: Extract `video_progress_callback` into a testable function
- Verify that `update_render_progress` is called with `percent_complete` values between 60-80% (phase 3 range)

### 3. Add test for `estimated_seconds_left` computation

Similar to above — verify that `estimated_seconds_left` is computed correctly at each phase transition.

### 4. Verify `update_render_progress` returns `None` when job is not running

The new `AND status = 'running'` guard means `update_render_progress()` returns `None` when the job is cancelled/failed. The pipeline code currently doesn't check the return value. Consider adding a check in `video_progress_callback`:

```python
result = update_render_progress(...)
if result is None:
    return  # Job no longer running, stop updating
```

This prevents the callback from continuing to compute and attempt DB writes after a job is cancelled mid-encoding.

### 5. Update spec document

The spec at `specs/render-worker-debug-trace.md` should be updated to reflect the review fixes (status guard, dynamic phase index, _load_font flow fix, etc.).

## Test Results

```
PYTHONPATH=src pytest tests/test_db.py tests/test_audio_engine.py tests/test_video_engine.py \
  tests/test_pipeline.py tests/test_lambda_handler.py tests/test_uploader.py \
  tests/test_asset_fetcher.py tests/test_frame_renderer.py -v

=== 499 passed ===
```

## Key Files Modified

```
services/render-worker/src/sow_render_worker/db.py
services/render-worker/src/sow_render_worker/audio_engine.py
services/render-worker/src/sow_render_worker/video_engine.py
services/render-worker/src/sow_render_worker/frame_renderer.py
services/render-worker/src/sow_render_worker/uploader.py
services/render-worker/src/sow_render_worker/asset_fetcher.py
services/render-worker/src/sow_render_worker/pipeline.py
services/render-worker/src/sow_render_worker/lambda_handler.py
services/render-worker/tests/test_db.py
services/render-worker/tests/test_audio_engine.py
services/render-worker/tests/test_video_engine.py
services/render-worker/tests/test_lambda_handler.py
```
