# Render Worker Encoding Bottleneck Instrumentation

## Problem

The frame-caching spec (`specs/frame-caching-render-worker.md`) assumes frame rendering is the bottleneck (~40ms/frame, ~19.2 min for a 20-min video). However, production logs show the entire FFmpeg encoding step takes 641.8s for 26,094 frames (~24.6ms/frame) — far less than the spec's rendering estimate alone. This suggests FFmpeg H.264 encoding (not frame rendering) may be the actual bottleneck, which would make frame caching ineffective at reducing wall-clock time.

### Why the current logs are ambiguous

The encoding loop in `video_engine.py:332-378` is a producer-consumer pipeline:

```
render_frame() → img.tobytes() → process.stdin.write() → [OS pipe buffer] → FFmpeg encodes
```

- When rendering is slower than encoding: `write()` never blocks, rendering time = wall-clock time
- When encoding is slower than rendering: pipe buffer fills, `write()` blocks, encoding time = wall-clock time
- The 641.8s wall-clock time = `max(render_time, encode_time)`, not their sum

We need to decompose this into separate measurements to determine the true bottleneck.

### Caveat on `pipe_write` measurement

`write_total_ns` measures the wall-clock time of `process.stdin.write()`, which includes both the time waiting for OS pipe buffer space **and** the actual data copy into the buffer. When the OS pipe buffer is large enough to absorb a full frame, `write()` returns immediately even if FFmpeg hasn't consumed the data yet. This means `pipe_write %` may **understate** FFmpeg's true encoding burden — the encoding work is happening concurrently in the FFmpeg process, but our measurement only captures it when the pipe backs up. The correlation between high `pipe_write %` and encoding being the bottleneck is strong, but it is not identity.

---

## Implementation Plan

**File:** `services/render-worker/src/sow_render_worker/video_engine.py`

### Step 1: Change `ffmpeg_start` to nanosecond precision

Replace `ffmpeg_start = time.monotonic()` (line 264) with `ffmpeg_start_ns = time.monotonic_ns()`. Update the existing elapsed calculation at line 397 accordingly:

```python
# BEFORE:
ffmpeg_elapsed = time.monotonic() - ffmpeg_start

# AFTER:
ffmpeg_elapsed = (time.monotonic_ns() - ffmpeg_start_ns) / 1e9
```

### Step 2: Add timing accumulators before the encoding loop

Before the `while frame_count < total_frames:` loop (line 333), add:

```python
render_total_ns = 0
tobytes_total_ns = 0
write_total_ns = 0
```

### Step 3: Instrument the per-frame operations inside the loop

Replace the current frame production code (lines 337-342):

```python
# BEFORE:
if title_card_config and frame_count < title_card_frame_count:
    frame_bytes = title_card_bytes
else:
    current_time = frame_count / self.fps
    img = self.frame_renderer.render_frame(lyrics, segments, current_time)
    frame_bytes = img.tobytes()
```

With:

```python
if title_card_config and frame_count < title_card_frame_count:
    frame_bytes = title_card_bytes
else:
    current_time = frame_count / self.fps
    t0 = time.monotonic_ns()
    img = self.frame_renderer.render_frame(lyrics, segments, current_time)
    render_total_ns += time.monotonic_ns() - t0

    t0 = time.monotonic_ns()
    frame_bytes = img.tobytes()
    tobytes_total_ns += time.monotonic_ns() - t0
```

And replace the `process.stdin.write(frame_bytes)` call (line 345):

```python
# BEFORE:
process.stdin.write(frame_bytes)

# AFTER:
t0 = time.monotonic_ns()
process.stdin.write(frame_bytes)
write_total_ns += time.monotonic_ns() - t0
```

### Step 4: Log the breakdown after FFmpeg exits (with try/finally and div-by-zero guard)

Wrap the encoding loop body (from the `while` loop through FFmpeg exit) in a `try/finally` block so the breakdown log fires even on error paths (pipe broken, Lambda timeout, etc.). Place the breakdown logging in the `finally` block.

After the existing log line at line 398-401 (`FFmpeg process exited with code`), add:

```python
total_elapsed_ns = time.monotonic_ns() - ffmpeg_start_ns
if total_elapsed_ns > 0:
    logger.info(
        "[%s] Encoding breakdown: total=%.1fs, render=%.1fs (%.1f%%), "
        "tobytes=%.1fs (%.1f%%), pipe_write=%.1fs (%.1f%%), "
        "other=%.1fs (%.1f%%)",
        job_id or "unknown",
        total_elapsed_ns / 1e9,
        render_total_ns / 1e9,
        render_total_ns / total_elapsed_ns * 100,
        tobytes_total_ns / 1e9,
        tobytes_total_ns / total_elapsed_ns * 100,
        write_total_ns / 1e9,
        write_total_ns / total_elapsed_ns * 100,
        (total_elapsed_ns - render_total_ns - tobytes_total_ns - write_total_ns) / 1e9,
        (total_elapsed_ns - render_total_ns - tobytes_total_ns - write_total_ns) / total_elapsed_ns * 100,
    )
else:
    logger.info(
        "[%s] Encoding breakdown: total=0s (too fast to measure)",
        job_id or "unknown",
    )
```

**Note:** The `if total_elapsed_ns > 0` guard prevents `ZeroDivisionError` in the percentage calculations if FFmpeg exits extremely quickly or on an error path.

### Step 5: Add periodic breakdown logging every 5 seconds

Inside the existing progress callback block (line 380-381), add a periodic detailed log:

```python
if progress_callback and frame_count % self.fps == 0:
    progress_callback(frame_count, total_frames)

# Add after the above:
if frame_count > 0 and frame_count % (self.fps * 5) == 0:
    elapsed_so_far = time.monotonic_ns() - ffmpeg_start_ns
    logger.info(
        "[%s] Encoding breakdown at frame %d/%d: "
        "render=%.1fs (%.1f%%), pipe_write=%.1fs (%.1f%%)",
        job_id or "unknown",
        frame_count, total_frames,
        render_total_ns / 1e9,
        render_total_ns / elapsed_so_far * 100,
        write_total_ns / 1e9,
        write_total_ns / elapsed_so_far * 100,
    )
```

---

## How to Interpret the Results

| Observation | `render %` | `pipe_write %` | Conclusion | Action |
|---|---|---|---|---|
| Rendering dominates | >70% | <20% | Frame rendering is the bottleneck | Frame caching spec is valid — proceed |
| Pipe write dominates | <20% | >70% | FFmpeg encoding is the bottleneck (write blocks on full pipe; see caveat above — pipe_write may understate encoding burden) | Frame caching won't help wall-clock time; optimize FFmpeg args instead |
| Roughly equal | ~40-60% | ~40-60% | Both are bottlenecks | Frame caching helps partially; also consider FFmpeg tuning |
| `tobytes` dominates | — | — | PIL serialization is the bottleneck | Optimize `.tobytes()` (unlikely) |

---

## FFmpeg Optimization Options (if encoding is the bottleneck)

If pipe_write dominates, the frame-caching spec should be deprioritized in favor of:

1. **Faster FFmpeg preset**: Already using `ultrafast` — no room here
2. **Lower CRF**: Currently 23 — increasing to 28 would reduce quality but speed up encoding
3. **Lower bitrate**: Currently 8000k — reducing would help but affects quality
4. **Hardware encoding**: Use `h264_nvcc` or `h264_vaapi` if Lambda supports GPU (currently doesn't)
5. **Lower resolution**: 720p instead of 1080p — 4x fewer pixels to encode
6. **Two-pass approach**: Write raw frames to disk, then FFmpeg encodes from file (avoids pipe blocking but uses disk I/O)
7. **Parallel encoding**: Split video into segments, encode in parallel, concatenate

---

## Files Changed

| File | Change | Lines |
|---|---|---|
| `video_engine.py` | Change `ffmpeg_start` to `ffmpeg_start_ns` (nanosecond precision) | ~2 |
| `video_engine.py` | Add timing accumulators before loop | ~3 |
| `video_engine.py` | Instrument `render_frame()` call | ~4 |
| `video_engine.py` | Instrument `img.tobytes()` call | ~4 |
| `video_engine.py` | Instrument `process.stdin.write()` call | ~3 |
| `video_engine.py` | Add breakdown log after FFmpeg exits (with try/finally and div-by-zero guard) | ~20 |
| `video_engine.py` | Add periodic breakdown log every 5s | ~12 |
| **Total** | | **~48** |

---

## Rollout

1. Implement Steps 1-5
2. Run existing test suite — all tests must pass (instrumentation is additive, no logic changes)
3. Deploy to dev and trigger a 4-song render
4. Analyze the breakdown logs to determine the true bottleneck
5. Based on results, either proceed with frame-caching spec or pivot to FFmpeg optimization
