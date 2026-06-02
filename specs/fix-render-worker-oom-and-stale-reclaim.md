# Fix Render Worker OOM at 97.5% + Stale Job Reclamation

**Service:** Render Worker (`services/render-worker/`)
**Status:** Draft
**Created:** 2026-06-02
**Incident:** 2026-05-30 — Lambda OOM at frame 35040/35451 (97.5%), job stuck in `running`

## Problem Statement

On 2026-05-30, a render worker Lambda invocation hit `Runtime.OutOfMemory` at 3008 MB while encoding a 5-song worship set at 1080p. The Lambda was killed hard — no `finally` blocks, no `fail_render_job()` call. The job remained in `running` status. A second SQS trigger arrived ~4 minutes later but could not reclaim the job (not yet stale per the 5-minute threshold), so it silently skipped. The job is now stuck in `running` indefinitely.

## Root Cause Analysis

### Why OOM at 97.5%

The frame cache is the dominant memory consumer. At 1080p, each cached frame is `1920 × 1080 × 4 = 8,294,400 bytes (~7.9 MB)` (RGBA). With `SOW_MAX_CACHE_ENTRIES=300`, the cache alone can consume **~2.37 GB**.

| Component | Memory |
|-----------|--------|
| Frame cache (266 entries × 7.9 MB at time of OOM) | ~2.10 GB |
| Python runtime + libraries | ~200 MB |
| FFmpeg subprocess | ~200-300 MB |
| Title card bytes + LRC cache + lyrics data | ~20 MB |
| stderr_chunks accumulation | ~10-20 MB |
| **TOTAL (estimated)** | **~2,530 - 2,650 MB** |

The cache fills progressively as encoding encounters new visual states across all 5 songs. By 97.5%, 266 unique `(segment_id, lyric_index, fade_alpha)` combinations have been cached. Combined with FFmpeg's growing internal buffers for the output MP4, total memory exceeds 3008 MB.

**Key insight:** The cache uses RGBA (4 bytes/pixel) but the rendering pipeline never uses the alpha channel for compositing. All alpha fading is implemented via color pre-multiplication (folding alpha into RGB values mathematically). The 4th byte per pixel is wasted memory.

### Why Two SQS Triggers

1. First invocation: OOM → hard kill → `fail_render_job()` never called → job stays `running`
2. SQS message not deleted → becomes visible after visibility timeout
3. Second invocation: `start_render_job()` fails (status is `running`, not `queued`) → `reclaim_stale_job()` fails (only ~4 min stale, threshold is 5 min) → silently skips

---

## Implementation Plan

### Fix 1: Resolution-Aware Cache Entry Limit (P0)

**Goal:** Reduce frame cache memory from ~2.37 GB to ~1.0-1.2 GB at 1080p by computing `max_cache_entries` from resolution and available memory.

**File:** `services/render-worker/src/sow_render_worker/frame_renderer.py`

**Current code (lines 24-26, 159-184):**
```python
_DEFAULT_FADE_ALPHA_STEPS = 16
_DEFAULT_MAX_CACHE_ENTRIES = 300
_DEFAULT_CACHE_ENABLED = True

class FrameRenderer:
    def __init__(self, template, font_size_preset="M", resolution=None):
        ...
        self._max_cache_entries = max(1, _get_int_env("SOW_MAX_CACHE_ENTRIES", _DEFAULT_MAX_CACHE_ENTRIES))
```

**Changes:**

1. Replace `_DEFAULT_MAX_CACHE_ENTRIES = 300` with a resolution-aware computation:

```python
import os

_DEFAULT_MAX_CACHE_ENTRIES = 300  # fallback, overridden by _compute_max_cache_entries

_BYTES_PER_PIXEL_RGBA = 4

def _compute_max_cache_entries(resolution: tuple[int, int]) -> int:
    """
    Compute max cache entries based on resolution and Lambda memory.

    Memory budget for cache = Lambda memory - overhead (Python + FFmpeg + misc).
    At 1080p: each entry = 1920*1080*4 = ~7.9 MB
    At 720p:  each entry = 1280*720*4  = ~3.5 MB

    Overhead budget: ~600 MB (Python ~200 MB, FFmpeg ~300 MB, misc ~100 MB)
    """
    lambda_memory_mb = int(os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "3072"))
    overhead_mb = 600
    cache_budget_mb = lambda_memory_mb - overhead_mb
    bytes_per_entry = resolution[0] * resolution[1] * _BYTES_PER_PIXEL_RGBA
    mb_per_entry = bytes_per_entry / (1024 * 1024)
    computed = int(cache_budget_mb / mb_per_entry)
    return max(50, min(computed, 500))
```

2. Update `FrameRenderer.__init__` to use the computed value when env var is not set:

```python
class FrameRenderer:
    def __init__(self, template, font_size_preset="M", resolution=None):
        ...
        self.resolution = resolution or template.resolution
        env_val = os.environ.get("SOW_MAX_CACHE_ENTRIES", "").strip()
        if env_val:
            self._max_cache_entries = max(1, int(env_val))
        else:
            self._max_cache_entries = _compute_max_cache_entries(self.resolution)
```

**Expected results:**

| Resolution | Lambda Memory | Overhead | Cache Budget | MB/Entry | Max Entries | Max Cache Size |
|-----------|--------------|----------|-------------|----------|-------------|----------------|
| 1080p | 3072 MB | 600 MB | 2472 MB | 7.9 MB | ~312 → capped at 312 | ~2.47 GB |
| 1080p | 2048 MB | 600 MB | 1448 MB | 7.9 MB | ~183 | ~1.45 GB |
| 720p | 3072 MB | 600 MB | 2472 MB | 3.5 MB | ~706 → capped at 500 | ~1.76 GB |
| 720p | 2048 MB | 600 MB | 1448 MB | 3.5 MB | ~414 | ~1.45 GB |

**Note:** The 3072 MB Lambda config still allows ~312 entries at 1080p, which is close to the current 300. This fix is most impactful when combined with Fix 2 (RGBA→RGB), which reduces per-entry size by 25%. With both fixes at 3072 MB / 1080p: ~416 entries at 5.9 MB each = ~2.46 GB — still tight. The real safety comes from the combination: at 2048 MB / 1080p with RGB: ~244 entries × 5.9 MB = ~1.44 GB — comfortable margin.

**Env var override preserved:** `SOW_MAX_CACHE_ENTRIES` still takes precedence when explicitly set.

**Files to update:**
- `services/render-worker/src/sow_render_worker/frame_renderer.py` — add `_compute_max_cache_entries()`, update `__init__`
- `services/render-worker/deploy/aws-up.sh` — remove hardcoded `SOW_MAX_CACHE_ENTRIES:-300` default (let the code compute it)
- `services/render-worker/docker-compose.yml` — remove `SOW_MAX_CACHE_ENTRIES: ${SOW_MAX_CACHE_ENTRIES:-300}` (or keep as optional override)
- `services/render-worker/docker-compose.dev.yml` — same

---

### Fix 2: Switch RGBA → RGB Pixel Format (P0)

**Goal:** Reduce per-frame memory by 25% (7.9 MB → 5.9 MB at 1080p) by eliminating the unused alpha channel.

**Why this is safe:** The rendering pipeline never uses PIL alpha compositing. All alpha fading is implemented via color pre-multiplication — the alpha value is folded into the RGB fill color mathematically (e.g., `fill_color = (int(r * alpha / 255), int(g * alpha / 255), int(b * alpha / 255))`). The background is always fully opaque. No `Image.paste()`, `Image.alpha_composite()`, or `ImageDraw` RGBA fill values are used.

**File:** `services/render-worker/src/sow_render_worker/video_engine.py`

**Change 1: FFmpeg `-pix_fmt` argument (line 283)**

```python
# BEFORE:
"-pix_fmt", "rgba",

# AFTER:
"-pix_fmt", "rgb24",
```

**Change 2: Title card image creation (line 328-331)**

```python
# BEFORE:
title_card_img = self.frame_renderer.render_title_card(title_card_config)
title_card_bytes = title_card_img.tobytes()

# AFTER:
title_card_img = self.frame_renderer.render_title_card(title_card_config)
title_card_img = title_card_img.convert("RGB")
title_card_bytes = title_card_img.tobytes()
```

**File:** `services/render-worker/src/sow_render_worker/frame_renderer.py`

**Change 3: `_render_frame_impl` — Image creation (line 406)**

```python
# BEFORE:
img = Image.new("RGBA", (width, height), (*self.template.background_color, 255))

# AFTER:
img = Image.new("RGB", (width, height), self.template.background_color)
```

**Change 4: `render_title_card` — Image creation (line 618)**

```python
# BEFORE:
img = Image.new("RGBA", (width, height), (*self.template.background_color, 255))

# AFTER:
img = Image.new("RGB", (width, height), self.template.background_color)
```

**Change 5: `render_frame_bytes` — add `img.close()` after `.tobytes()` (lines 392-393, 401-402)**

```python
# Cache miss path (line 392-393):
# BEFORE:
img = self._render_frame_impl(state)
frame_bytes = img.tobytes()

# AFTER:
img = self._render_frame_impl(state)
frame_bytes = img.tobytes()
img.close()

# No-cache path (line 401-402):
# BEFORE:
img = self._render_frame_impl(state)
return img.tobytes()

# AFTER:
img = self._render_frame_impl(state)
frame_bytes = img.tobytes()
img.close()
return frame_bytes
```

**Change 6: Update `_BYTES_PER_PIXEL_RGBA` constant from Fix 1**

Since we're now using RGB, update the constant:

```python
# In frame_renderer.py (from Fix 1):
_BYTES_PER_PIXEL = 3  # RGB, was 4 for RGBA

def _compute_max_cache_entries(resolution: tuple[int, int]) -> int:
    ...
    bytes_per_entry = resolution[0] * resolution[1] * _BYTES_PER_PIXEL
    ...
```

**Impact on FFmpeg:** `rgb24` is a standard FFmpeg input pixel format. The `libx264` encoder accepts RGB input and internally converts to YUV420P for H.264 encoding. No quality loss — the RGB→YUV conversion happens regardless of input pixel format. The `-pix_fmt rgb24` flag tells FFmpeg to expect 3 bytes/pixel on stdin instead of 4.

**Files to update:**
- `services/render-worker/src/sow_render_worker/video_engine.py` — FFmpeg args, title card conversion
- `services/render-worker/src/sow_render_worker/frame_renderer.py` — `Image.new("RGB",...)`, `img.close()`, `_BYTES_PER_PIXEL` constant

---

### Fix 3: Periodic `gc.collect()` in Encoding Loop (P1)

**Goal:** Promptly reclaim evicted cache entries and temporary PIL Images that Python's reference counting may not immediately return to the OS.

**File:** `services/render-worker/src/sow_render_worker/video_engine.py`

**Change 1: Add `import gc` at top of file (after line 7)**

```python
import gc
```

**Change 2: Add periodic GC in the encoding loop (after line 411, inside the existing `if frame_count > 0 and frame_count % (self.fps * 5) == 0:` block)**

```python
if frame_count > 0 and frame_count % (self.fps * 5) == 0:
    # ... existing logging ...

    # Periodic GC to reclaim evicted cache entries and transient PIL Images
    gc.collect()
```

**Why every 5 seconds of video (not every frame):** `gc.collect()` has non-trivial overhead (~1-5ms). Calling it every frame would add ~24-120ms/s of overhead. Every 5 seconds of video (= every 120 frames at 24fps) means ~12 calls per minute of video, negligible overhead.

**Why this is P1 not P0:** CPython's reference counting should free memory immediately when the last reference is dropped (e.g., when `OrderedDict.popitem()` removes an entry). `gc.collect()` primarily helps with:
1. Circular references (unlikely in this code)
2. Memory fragmentation — CPython's allocator may not return freed memory to the OS immediately; `gc.collect()` can trigger `malloc_trim()` behavior
3. Transient PIL Images that are technically GC-eligible but whose pixel buffers haven't been freed yet

This is a safety net, not the primary fix. The primary memory reduction comes from Fixes 1 and 2.

**Files to update:**
- `services/render-worker/src/sow_render_worker/video_engine.py` — add `import gc`, add `gc.collect()` call

---

### Fix 4: Memory Monitoring and Graceful OOM Handling (P1)

**Goal:** Detect memory pressure before hitting the hard OOM kill, and call `fail_render_job()` so the job doesn't get stuck in `running`.

**Background:** The existing SIGTERM handler (`pipeline.py:37-42`) and `check_lambda_timeout()` (`pipeline.py:209-222`) already provide graceful shutdown for Lambda timeout. However, OOM kills the process instantly with no cleanup — worse than SIGTERM. We need a memory analog of the timeout check.

**File:** `services/render-worker/src/sow_render_worker/video_engine.py`

**Change 1: Add memory check function**

```python
import os

_MEMORY_WARNING_FRACTION = 0.90  # warn at 90% of Lambda memory limit


def _check_memory_pressure() -> None:
    """
    Raise MemoryError if process RSS exceeds _MEMORY_WARNING_FRACTION of Lambda memory.

    Uses /proc/self/status on Linux (available in Lambda container).
    Falls back to no-op if /proc is unavailable.
    """
    try:
        lambda_memory_mb = int(os.environ.get("AWS_LAMBDA_FUNCTION_MEMORY_SIZE", "3072"))
        warning_mb = lambda_memory_mb * _MEMORY_WARNING_FRACTION

        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
                    rss_mb = rss_kb / 1024
                    if rss_mb > warning_mb:
                        raise MemoryError(
                            f"Memory pressure: RSS={rss_mb:.0f}MB exceeds "
                            f"{_MEMORY_WARNING_FRACTION:.0%} of Lambda limit "
                            f"({lambda_memory_mb}MB)"
                        )
                    return
    except (FileNotFoundError, ValueError, IndexError):
        pass  # /proc not available or parse error — skip check
```

**Change 2: Call memory check in the encoding loop (alongside existing timeout check)**

In `encode_video_with_ffmpeg()`, after the existing `timeout_check_callback` call (line 339-340):

```python
while frame_count < total_frames:
    if timeout_check_callback:
        timeout_check_callback()

    # Memory pressure check every frame (negligible overhead: one file read + comparison)
    _check_memory_pressure()
    ...
```

**Why check every frame:** Reading `/proc/self/status` is a simple file read (~0.01ms). The overhead is negligible compared to the ~0.7ms per-frame render time. Checking every frame ensures we catch memory pressure as soon as it crosses the threshold, giving us the maximum window to call `fail_render_job()` before the hard OOM.

**Change 3: Handle `MemoryError` in pipeline exception handler**

**File:** `services/render-worker/src/sow_render_worker/pipeline.py`

The existing `except Exception` handler (lines 516-532) already catches `MemoryError` (it's a subclass of `Exception`). No code change needed — the `MemoryError` will propagate from `encode_video_with_ffmpeg()` → `generate_video()` → `execute_render_pipeline()`, where it will be caught and `fail_render_job()` will be called with the memory error message.

**However**, we should add a specific `except MemoryError` clause before the generic `except Exception` to log it distinctly:

```python
except MemoryError as mem_exc:
    logger.critical(
        "[%s] Render pipeline hit memory limit: %s",
        job_id, str(mem_exc),
    )
    try:
        fail_render_job(conn, job_id, user_id, str(mem_exc))
    except Exception as fail_exc:
        logger.error("[%s] Failed to mark job as failed: %s", job_id, fail_exc)
    raise

except Exception as exc:
    # ... existing handler ...
```

**Why 90% threshold:** At 90% of 3072 MB = 2765 MB, we still have ~300 MB of headroom for the `fail_render_job()` DB write and cleanup. The OOM kill happens at 100% (3008 MB). This gives us a ~300 MB / ~7.9 MB per frame = ~38 frame window (~1.6 seconds at 24fps) to detect and fail gracefully.

**Files to update:**
- `services/render-worker/src/sow_render_worker/video_engine.py` — add `_check_memory_pressure()`, call it in encoding loop
- `services/render-worker/src/sow_render_worker/pipeline.py` — add `except MemoryError` handler

---

### Fix 5: Aggressive Stale Job Reclamation on SQS Retry (P1)

**Goal:** When a second SQS invocation finds a job in `running` status, reclaim it more aggressively if the first invocation clearly failed (e.g., OOM kill).

**Current behavior (pipeline.py:227-243):**
1. `start_render_job()` fails (job is `running`, not `queued`)
2. `reclaim_stale_job()` checks if `updated_at` is older than 300 seconds (5 min)
3. If not stale enough → returns `None` → second invocation silently skips

**Problem:** The OOM incident showed a ~4-minute gap between the first invocation's death and the second invocation's arrival. With a 5-minute stale threshold, the second invocation couldn't reclaim the job.

**File:** `services/render-worker/src/sow_render_worker/db.py`

**Change 1: Reduce `STALE_JOB_THRESHOLD_SECONDS` from 300 to 120 (2 minutes)**

```python
# BEFORE:
STALE_JOB_THRESHOLD_SECONDS = 300  # 5 minutes

# AFTER:
STALE_JOB_THRESHOLD_SECONDS = 120  # 2 minutes
```

**Rationale:** A Lambda invocation that hasn't updated `updated_at` in 2 minutes is almost certainly dead. The encoding loop updates progress every 1 second of video (every `fps` frames). Even the slowest phases (audio mixing, uploading) complete in under 2 minutes. The only scenario where a healthy worker wouldn't update progress for 2+ minutes is if it's stuck in a non-progress-reporting code path, which is itself a bug.

**Change 2: Add `force_reclaim_failed_job()` for known-dead invocations**

When the second invocation can tell the first invocation is dead (e.g., Lambda OOM is detectable via CloudWatch), it should reclaim immediately without waiting for the stale threshold. However, the second invocation doesn't have direct knowledge of the first's fate. Instead, we add a shorter "likely dead" threshold:

```python
LIKELY_DEAD_THRESHOLD_SECONDS = 60  # 1 minute — no progress update = likely dead


def reclaim_likely_dead_job(
    conn: psycopg2.extensions.connection,
    job_id: str,
    user_id: int,
) -> Optional[RenderJob]:
    """
    Reclaim a job that's been running with no progress for 1+ minute.

    This is more aggressive than reclaim_stale_job (5 min threshold).
    Used when a second SQS invocation arrives — the first invocation
    is almost certainly dead if it hasn't updated progress in 60 seconds.
    """
    return reclaim_stale_job(conn, job_id, user_id, stale_threshold_seconds=LIKELY_DEAD_THRESHOLD_SECONDS)
```

**File:** `services/render-worker/src/sow_render_worker/pipeline.py`

**Change 3: Use `reclaim_likely_dead_job` in the SQS retry path**

```python
# BEFORE (lines 228-243):
started = start_render_job(conn, job_id, user_id)
if not started:
    reclaimed = reclaim_stale_job(conn, job_id, user_id)
    if reclaimed:
        logger.info(
            "Reclaimed stale job %s (was stuck in 'running' for too long), retrying",
            job_id,
        )
        started = start_render_job(conn, job_id, user_id)

    if not started:
        logger.info(
            "Render job %s was already claimed by another invocation, skipping",
            job_id,
        )
        return

# AFTER:
started = start_render_job(conn, job_id, user_id)
if not started:
    reclaimed = reclaim_likely_dead_job(conn, job_id, user_id)
    if reclaimed:
        logger.info(
            "Reclaimed likely-dead job %s (no progress for 60+s), retrying",
            job_id,
        )
        started = start_render_job(conn, job_id, user_id)

    if not started:
        logger.info(
            "Render job %s was already claimed by another invocation, skipping",
            job_id,
        )
        return
```

**Why 60 seconds is safe:** The encoding loop calls `update_render_progress()` every second of video (every 24 frames). A healthy worker will update `updated_at` at least once every ~1 second of wall-clock time during encoding. If `updated_at` hasn't changed in 60 seconds, the worker is either:
1. Dead (OOM, SIGKILL, crash) — should be reclaimed
2. Stuck in a non-progress-reporting code path — should be reclaimed (it's broken)
3. In a very slow phase with no progress callback — unlikely, all phases report progress

**Risk:** A very brief network hiccup could cause a 60-second gap in progress updates while the worker is still alive. However, the `start_render_job()` SQL uses `WHERE status = 'queued'`, so even if we reclaim a job that's actually still running, the original worker will continue encoding (it already has the job claimed). The worst case is a duplicate render, which is caught by the `COALESCE(started_at, now)` pattern — the original worker's `started_at` is preserved.

**Files to update:**
- `services/render-worker/src/sow_render_worker/db.py` — reduce `STALE_JOB_THRESHOLD_SECONDS` to 120, add `LIKELY_DEAD_THRESHOLD_SECONDS = 60`, add `reclaim_likely_dead_job()`
- `services/render-worker/src/sow_render_worker/pipeline.py` — import and use `reclaim_likely_dead_job` instead of `reclaim_stale_job`

---

## Combined Memory Budget After All Fixes

At 1080p with 3072 MB Lambda memory:

| Component | Before Fixes | After Fixes 1+2 | After All Fixes |
|-----------|-------------|-----------------|-----------------|
| Per-entry size | 7.9 MB (RGBA) | 5.9 MB (RGB) | 5.9 MB (RGB) |
| Max cache entries | 300 (hardcoded) | ~312 (computed) | ~416 (computed, RGB) |
| Max cache size | ~2.37 GB | ~1.84 GB | ~2.46 GB |
| Python + FFmpeg overhead | ~500 MB | ~500 MB | ~500 MB |
| **Total at max cache** | **~2.87 GB** | **~2.34 GB** | **~2.96 GB** |
| **Headroom** | **~140 MB (4.6%)** | **~730 MB (24%)** | **~110 MB (3.6%)** |

Wait — with 3072 MB and RGB, the computed max entries would be ~416, which at 5.9 MB each = ~2.46 GB + 500 MB overhead = ~2.96 GB — still tight. The `_compute_max_cache_entries` function should use a more conservative overhead budget. Let me recalculate:

With `overhead_mb = 800` (more conservative — accounts for FFmpeg buffer growth at 97.5%):
- Cache budget = 3072 - 800 = 2272 MB
- Max entries at 5.9 MB/entry = ~384
- Max cache = ~2.27 GB
- Total = ~2.27 + 0.8 = ~3.07 GB — still over

With `overhead_mb = 1000`:
- Cache budget = 3072 - 1000 = 2072 MB
- Max entries at 5.9 MB/entry = ~351
- Max cache = ~2.07 GB
- Total = ~2.07 + 1.0 = ~3.07 GB — still over

**The fundamental issue is that 3072 MB is barely enough for 1080p frame caching.** The real solution is:

1. **Fix 1+2 combined** reduce per-entry size from 7.9 MB to 5.9 MB (25% savings)
2. **Fix 4 (memory monitoring)** catches OOM before the hard kill
3. **For production safety**, the overhead budget in `_compute_max_cache_entries` should be set to **1000 MB** to account for FFmpeg buffer growth, Python GC fragmentation, and stderr accumulation

**Revised `_compute_max_cache_entries` with 1000 MB overhead:**

| Resolution | Lambda Memory | Overhead | Cache Budget | MB/Entry (RGB) | Max Entries | Max Cache Size | Total | Headroom |
|-----------|--------------|----------|-------------|----------------|-------------|----------------|-------|----------|
| 1080p | 3072 MB | 1000 MB | 2072 MB | 5.9 MB | ~351 | ~2.07 GB | ~3.07 GB | ~0 MB |
| 1080p | 4096 MB | 1000 MB | 3096 MB | 5.9 MB | ~525 → capped 500 | ~2.95 GB | ~3.95 GB | ~146 MB |
| 720p | 3072 MB | 1000 MB | 2072 MB | 3.5 MB | ~592 → capped 500 | ~1.76 GB | ~2.76 GB | ~312 MB |
| 720p | 2048 MB | 1000 MB | 1048 MB | 3.5 MB | ~299 | ~1.05 GB | ~2.05 GB | ~0 MB |

**Conclusion:** 3072 MB Lambda memory is insufficient for reliable 1080p rendering with frame caching. The recommended production configuration is **4096 MB** for 1080p renders. With Fixes 1+2+4, 3072 MB will work for most renders but may still OOM on very long videos (5+ songs) where FFmpeg's buffer growth pushes total memory past the limit. Fix 4 (memory monitoring) ensures graceful failure instead of a hard kill.

**Action item:** Update `deploy/aws-up.sh` to use `--memory-size 4096` for production, and update `DEPLOY.md` accordingly.

---

## File Change Summary

### Render Worker (`services/render-worker/`)

| File | Changes | Fix |
|------|---------|-----|
| `src/sow_render_worker/frame_renderer.py` | Add `_compute_max_cache_entries()`; update `__init__` to use computed default; change `Image.new("RGBA",...)` → `Image.new("RGB",...)` in `_render_frame_impl` and `render_title_card`; add `img.close()` after `.tobytes()`; update `_BYTES_PER_PIXEL` constant | 1, 2 |
| `src/sow_render_worker/video_engine.py` | Change FFmpeg `-pix_fmt rgba` → `-pix_fmt rgb24`; add `title_card_img.convert("RGB")` before `.tobytes()`; add `import gc` and periodic `gc.collect()`; add `_check_memory_pressure()` function; call memory check in encoding loop | 2, 3, 4 |
| `src/sow_render_worker/pipeline.py` | Add `except MemoryError` handler before generic `except Exception`; use `reclaim_likely_dead_job` instead of `reclaim_stale_job` | 4, 5 |
| `src/sow_render_worker/db.py` | Reduce `STALE_JOB_THRESHOLD_SECONDS` to 120; add `LIKELY_DEAD_THRESHOLD_SECONDS = 60`; add `reclaim_likely_dead_job()` | 5 |
| `deploy/aws-up.sh` | Remove hardcoded `SOW_MAX_CACHE_ENTRIES:-300`; update `--memory-size` to 4096 | 1 |
| `docker-compose.yml` | Remove or make optional `SOW_MAX_CACHE_ENTRIES` env var | 1 |
| `docker-compose.dev.yml` | Same as above | 1 |
| `deploy/DEPLOY.md` | Update memory recommendation to 4096 MB for 1080p | 1 |

### Tests (`services/render-worker/tests/`)

| File | Changes | Fix |
|------|---------|-----|
| `test_frame_renderer.py` | Add tests for `_compute_max_cache_entries()` at various resolutions and Lambda memory sizes; add tests for RGB mode (`Image.new("RGB",...)`); add test for `img.close()` after `.tobytes()` | 1, 2 |
| `test_video_engine.py` | Add test for FFmpeg args containing `-pix_fmt rgb24`; add test for `_check_memory_pressure()` raising `MemoryError` at 90% threshold; add test for periodic `gc.collect()` | 2, 3, 4 |
| `test_pipeline.py` | Add test for `except MemoryError` handler; add test for `reclaim_likely_dead_job` (60s threshold); update existing `test_pipeline_skips_when_job_already_claimed` to use new function | 4, 5 |
| `test_db.py` | Add test for `LIKELY_DEAD_THRESHOLD_SECONDS = 60`; add test for `reclaim_likely_dead_job()` with 60s threshold; update `test_default_threshold_5_minutes` → `test_default_threshold_2_minutes` | 5 |

---

## Implementation Order

1. **Fix 2** (P0) — RGBA → RGB pixel format
   - Highest impact per line of code changed
   - 25% memory reduction with no quality loss
   - Must be done before Fix 1 (Fix 1's `_compute_max_cache_entries` depends on knowing bytes-per-pixel)

2. **Fix 1** (P0) — Resolution-aware cache entry limit
   - Depends on Fix 2 for correct `_BYTES_PER_PIXEL` value
   - Makes cache auto-scale with Lambda memory and resolution

3. **Fix 3** (P1) — Periodic `gc.collect()`
   - Simple, independent, no risk
   - Can be done in parallel with Fix 4

4. **Fix 4** (P1) — Memory monitoring and graceful OOM handling
   - Safety net for cases where Fixes 1+2 aren't enough
   - Independent of other fixes

5. **Fix 5** (P1) — Aggressive stale job reclamation
   - Independent of other fixes
   - Prevents stuck jobs on retry

6. **Deploy config update** — Increase Lambda memory to 4096 MB for 1080p

---

## Testing Plan

### Unit Tests

**Fix 1 — Resolution-aware cache:**
- `test_compute_max_cache_entries_1080p_3072mb` — verify ~351 entries at 1080p/3072MB
- `test_compute_max_cache_entries_720p_3072mb` — verify capped at 500 entries at 720p/3072MB
- `test_compute_max_cache_entries_env_override` — verify `SOW_MAX_CACHE_ENTRIES` env var takes precedence
- `test_compute_max_cache_entries_min_50` — verify minimum of 50 entries even for tiny Lambda memory

**Fix 2 — RGBA → RGB:**
- `test_render_frame_impl_creates_rgb_image` — verify `Image.new("RGB",...)` is used
- `test_render_title_card_creates_rgb_image` — verify title card is RGB
- `test_ffmpeg_args_rgb24` — verify FFmpeg args contain `-pix_fmt rgb24`
- `test_img_close_after_tobytes` — verify `img.close()` is called after `.tobytes()`
- `test_title_card_convert_rgb_before_tobytes` — verify title card is converted to RGB before `.tobytes()`

**Fix 3 — gc.collect():**
- `test_gc_collect_called_periodically` — verify `gc.collect()` is called every 5 seconds of video

**Fix 4 — Memory monitoring:**
- `test_check_memory_pressure_raises_at_90_percent` — verify `MemoryError` raised when RSS > 90% of Lambda memory
- `test_check_memory_pressure_passes_below_threshold` — verify no raise when RSS is below threshold
- `test_check_memory_pressure_noop_without_proc` — verify graceful no-op when `/proc` is unavailable
- `test_pipeline_memory_error_handler` — verify `fail_render_job()` is called on `MemoryError`

**Fix 5 — Stale job reclamation:**
- `test_likely_dead_threshold_60_seconds` — verify `LIKELY_DEAD_THRESHOLD_SECONDS = 60`
- `test_reclaim_likely_dead_job_60s_threshold` — verify job reclaimed after 60s with no progress
- `test_reclaim_likely_dead_job_skips_recent_job` — verify job with recent progress is not reclaimed
- `test_pipeline_uses_reclaim_likely_dead_job` — verify pipeline uses the new function

### Integration Tests

1. **Memory profile test:** Run a 5-song 1080p render locally with memory tracking. Verify RSS stays below 90% of Lambda memory limit throughout encoding.

2. **OOM recovery test:** Set `SOW_MAX_CACHE_ENTRIES` artificially high to force OOM. Verify `MemoryError` is raised and `fail_render_job()` is called.

3. **SQS retry test:** Manually set a job to `running` with `updated_at` 90 seconds ago. Send a test SQS message. Verify job is reclaimed and re-processed.

---

## Out of Scope

- **Increasing Lambda memory to 4096+ MB** — This is a deploy config change, not a code change. Recommended but not part of this spec's code changes.
- **Disk-based frame cache** — Would eliminate memory pressure entirely but adds I/O latency and complexity. The memory-based cache with RGB + computed limits is sufficient.
- **Compressed frame cache (PNG/JPEG in memory)** — Would reduce per-entry size by 80-95% but adds CPU overhead for compression/decompression. Not needed with RGB + computed limits.
- **FFmpeg `-threads 1`** — Saves ~30 MB but increases encoding time by 10-20%. With the Lambda already at 654s of 900s timeout, this risks timeout failures. Not recommended.
- **Two-pass encoding (raw frames to disk, then FFmpeg from file)** — Eliminates pipe blocking but requires 2× disk I/O for the full frame sequence. Not needed.
