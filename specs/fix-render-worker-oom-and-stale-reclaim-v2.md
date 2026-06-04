# Fix Render Worker OOM at 97.5% + Stale Job Reclamation (v2)

**Service:** Render Worker (`services/render-worker/`)
**Status:** Draft
**Created:** 2026-06-02
**Incident:** 2026-05-30 — Lambda OOM at frame 35040/35451 (97.5%), job stuck in `running`

## Changelog (v1 → v2)

| Change | Reason |
|--------|--------|
| Frame cache is essential, not disposable | v1 incorrectly concluded cache was unnecessary because render was only 3% of wall-clock time. That 3% is the *result* of 99% cache hit rate. Without cache, render time = ~21 min > 15 min Lambda timeout. |
| Per-frame render cost corrected to ~37ms | v1 used ~0.5ms (amortized across all frames including cache hits). Actual cost per cache miss = 17.1s / 456 misses ≈ 37ms. |
| Static `_DEFAULT_MAX_CACHE_ENTRIES = 200` instead of resolution-aware computation | Total wall-clock time is identical from 100→300 entries (FFmpeg bottleneck dominates). Resolution-aware sizing is over-engineering with no user-visible benefit. Static 200 is simpler and sufficient. |
| Removed Lambda memory increase to 4096 MB | With RGB + 200 entries, 3072 MB has ~940 MB headroom. No need for larger Lambda. |
| Standardized on 3072 MB Lambda config | Simplifies sizing math; 2048 MB is not supported at 1080p with this cache design. |
| Added cache hit rate model | v1 had no quantitative model of how cache size affects hit rate and render time. v2 includes a working-set analysis. |

## Problem Statement

On 2026-05-30, a render worker Lambda invocation hit `Runtime.OutOfMemory` at 3008 MB while encoding a 5-song worship set at 1080p. The Lambda was killed hard — no `finally` blocks, no `fail_render_job()` call. The job remained in `running` status. A second SQS trigger arrived ~4 minutes later but could not reclaim the job (not yet stale per the 5-minute threshold), so it silently skipped. The job is now stuck in `running` indefinitely.

## Root Cause Analysis

### Why OOM at 97.5%

The frame cache is the dominant memory consumer. At 1080p, each cached frame is `1920 × 1080 × 4 = 8,294,400 bytes (~7.9 MB)` (RGBA). With `SOW_MAX_CACHE_ENTRIES=300`, the cache alone can consume **~2.37 GB**.

| Component | Memory |
|-----------|--------|
| Frame cache (266 entries × 7.9 MB at time of OOM) | ~2,101 MB |
| Python runtime + libraries | ~200 MB |
| FFmpeg subprocess | ~200-300 MB |
| Title card bytes + LRC cache + lyrics data | ~20 MB |
| stderr_chunks accumulation | ~10-20 MB |
| CPython memory fragmentation / high-water-mark | ~100-200 MB |
| **TOTAL (estimated)** | **~2,630 - 2,830 MB** |

The cache fills progressively as encoding encounters new visual states across all 5 songs. By 97.5%, 266 unique `(segment_id, lyric_index, fade_alpha)` combinations have been cached. Combined with FFmpeg's growing internal buffers for the output MP4 and CPython's inability to return freed memory to the OS (fragmentation), total memory exceeds 3008 MB.

**Key insight:** The cache uses RGBA (4 bytes/pixel) but the rendering pipeline never uses the alpha channel for compositing. All alpha fading is implemented via color pre-multiplication (folding alpha into RGB values mathematically). The 4th byte per pixel is wasted memory.

### Why Two SQS Triggers

1. First invocation: OOM → hard kill → `fail_render_job()` never called → job stays `running`
2. SQS message not deleted → becomes visible after visibility timeout
3. Second invocation: `start_render_job()` fails (status is `running`, not `queued`) → `reclaim_stale_job()` fails (only ~4 min stale, threshold is 5 min) → silently skips

### Why Frame Cache Is Essential

The cache was originally added because frame rendering was estimated at ~40ms/frame. Production instrumentation confirmed the actual per-miss cost is ~37ms:

```
Total render time: 17.1s
Cache misses: 456 (out of 35,451 frames)
Per-miss cost: 17.1s / 456 ≈ 37ms
```

**Without cache:**
```
35,451 frames × 37ms = ~1,301s = 21.7 minutes → EXCEEDS 15-MINUTE LAMBDA TIMEOUT
```

**With cache (99% hit rate, 300 entries):**
```
456 misses × 37ms + 34,995 hits × ~0.01ms ≈ 17.1s render time
Total time = max(17.1s render, 654s FFmpeg) = 654s → FITS IN LAMBDA TIMEOUT
```

The cache is what keeps rendering below the FFmpeg bottleneck. Without it, rendering becomes the bottleneck and exceeds the Lambda timeout.

### Cache Size vs. Hit Rate Model

The cache key is `(segment_id, title, lyric_index, quantized_intro, quantized_fade, is_last_lyric_faded)`. For a 5-song set:

| Phase per song | Unique cache keys | Duration | Frames |
|---|---|---|---|
| Intro fade (16 quantized alpha steps) | 16 | ~7s | ~168 |
| Title-only display | 1 | ~3s | ~72 |
| Main lyrics (25 lyric indices) | 25 | ~200s | ~4,800 |
| Outro fade (16 quantized alpha steps) | 16 | ~7s | ~168 |
| **Per song** | **~58** | | |
| **5 songs total** | **~290** | | |

Songs play **sequentially**. With LRU eviction, old songs' entries are naturally evicted as new songs' entries are added. The "active working set" at any point is the current song's ~58 entries.

| Cache Size | Can Hold | Hit Rate (est.) | Cache Misses | Render Time | Total Time |
|---|---|---|---|---|---|
| 300 (current) | All 5 songs | ~99.0% | ~456 | ~17s | ~654s |
| 200 | ~3.5 songs | ~98.5% | ~530 | ~20s | ~654s |
| 100 | ~1.7 songs | ~95% | ~1,773 | ~65s | ~654s |
| 58 | 1 song | ~80% | ~7,090 | ~260s | ~654s |
| 0 | None | 0% | 35,451 | ~1,301s | **1,301s (TIMEOUT)** |

**Total wall-clock time is identical from 58→300 entries** because FFmpeg encoding (~654s) is always the bottleneck. Render time only exceeds FFmpeg time below ~58 entries.

**Practical safe minimum: ~100 entries** — gives ~95% hit rate, ~65s render time, still 10× below FFmpeg time.

**Chosen value: 200 entries** — gives ~98.5% hit rate, ~20s render time, and at 1080p/RGB uses only 1,180 MB of cache (well within 3072 MB Lambda limit).

---

## Implementation Plan

### Fix 1: RGBA → RGB Pixel Format (P0)

**Goal:** Reduce per-frame memory by 25% (7.9 MB → 5.9 MB at 1080p) by eliminating the unused alpha channel.

**Why this is safe:** The rendering pipeline never uses PIL alpha compositing. All alpha fading is implemented via color pre-multiplication — the alpha value is folded into the RGB fill color mathematically (e.g., `fill_color = (int(r * alpha / 255), int(g * alpha / 255), int(b * alpha / 255))`). The background is always fully opaque. No `Image.paste()`, `Image.alpha_composite()`, or `ImageDraw` RGBA fill values are used anywhere in the codebase.

**Impact on FFmpeg:** `rgb24` is a standard FFmpeg input pixel format. The `libx264` encoder accepts RGB input and internally converts to YUV420P for H.264 encoding. No quality loss — the RGB→YUV conversion happens regardless of input pixel format. The `-pix_fmt rgb24` flag tells FFmpeg to expect 3 bytes/pixel on stdin instead of 4.

#### File: `services/render-worker/src/sow_render_worker/video_engine.py`

**Change 1: FFmpeg `-pix_fmt` argument (line 283)**

```python
# BEFORE:
"-pix_fmt", "rgba",

# AFTER:
"-pix_fmt", "rgb24",
```

**Change 2: Title card image conversion (lines 328-331)**

```python
# BEFORE:
title_card_img = self.frame_renderer.render_title_card(title_card_config)
title_card_bytes = title_card_img.tobytes()

# AFTER:
title_card_img = self.frame_renderer.render_title_card(title_card_config)
title_card_img = title_card_img.convert("RGB")
title_card_bytes = title_card_img.tobytes()
title_card_img.close()
```

#### File: `services/render-worker/src/sow_render_worker/frame_renderer.py`

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
# Cache miss path (lines 392-393):
# BEFORE:
img = self._render_frame_impl(state)
frame_bytes = img.tobytes()

# AFTER:
img = self._render_frame_impl(state)
frame_bytes = img.tobytes()
img.close()

# No-cache path (lines 401-402):
# BEFORE:
img = self._render_frame_impl(state)
return img.tobytes()

# AFTER:
img = self._render_frame_impl(state)
frame_bytes = img.tobytes()
img.close()
return frame_bytes
```

**Files changed:**
- `services/render-worker/src/sow_render_worker/video_engine.py` — FFmpeg args, title card conversion + close
- `services/render-worker/src/sow_render_worker/frame_renderer.py` — `Image.new("RGB",...)` in 2 places, `img.close()` in 2 places

---

### Fix 2: Reduce `_DEFAULT_MAX_CACHE_ENTRIES` from 300 to 200 (P0)

**Goal:** Reduce max cache memory from ~2.37 GB to ~1.18 GB at 1080p/RGB, providing ~940 MB headroom at 3072 MB Lambda memory.

**Why 200 (not resolution-aware):** Total wall-clock time is identical from 100→300 entries (FFmpeg bottleneck dominates). A static 200 is simpler, gives ~98.5% hit rate, and provides ample headroom. No user-visible benefit from resolution-aware sizing.

**Memory budget at 1080p / 3072 MB / RGB:**

| Component | Memory |
|-----------|--------|
| Frame cache (200 entries × 5.9 MB) | 1,180 MB |
| Python runtime + libraries | ~200 MB |
| FFmpeg subprocess | ~300 MB |
| Title card bytes + LRC cache + misc | ~50 MB |
| CPython fragmentation reserve | ~200 MB |
| **TOTAL** | **~1,930 MB** |
| **Headroom** | **~1,140 MB (37%)** |

This is comfortable even with FFmpeg buffer growth at the end of encoding.

#### File: `services/render-worker/src/sow_render_worker/frame_renderer.py`

**Change 1: Default constant (line 25)**

```python
# BEFORE:
_DEFAULT_MAX_CACHE_ENTRIES = 300

# AFTER:
_DEFAULT_MAX_CACHE_ENTRIES = 200
```

#### File: `services/render-worker/docker-compose.yml`

**Change 2: Remove hardcoded `SOW_MAX_CACHE_ENTRIES` default (line 20)**

```yaml
# BEFORE:
SOW_MAX_CACHE_ENTRIES: ${SOW_MAX_CACHE_ENTRIES:-300}

# AFTER:
# (remove this line entirely — let the code default of 200 apply)
# Or keep as optional override without default:
SOW_MAX_CACHE_ENTRIES: ${SOW_MAX_CACHE_ENTRIES:-}
```

#### File: `services/render-worker/docker-compose.dev.yml`

**Change 3: Same as docker-compose.yml — remove hardcoded default**

#### File: `services/render-worker/deploy/aws-up.sh`

**Change 4: Remove `SOW_MAX_CACHE_ENTRIES:-300` from Lambda env vars (lines 233, 243)**

```bash
# BEFORE:
"SOW_MAX_CACHE_ENTRIES":"%s" ... "${SOW_MAX_CACHE_ENTRIES:-300}"

# AFTER:
# (remove SOW_MAX_CACHE_ENTRIES from the env vars JSON, or keep as optional override without default)
```

#### File: `services/render-worker/deploy/DEPLOY.md`

**Change 5: Update memory recommendation**

```markdown
# BEFORE:
Memory should be at least 2048 MB for video encoding.

# AFTER:
Memory must be at least 3072 MB for 1080p video encoding (frame cache + FFmpeg overhead).
```

**Files changed:**
- `services/render-worker/src/sow_render_worker/frame_renderer.py` — constant 300 → 200
- `services/render-worker/docker-compose.yml` — remove hardcoded default
- `services/render-worker/docker-compose.dev.yml` — remove hardcoded default
- `services/render-worker/deploy/aws-up.sh` — remove hardcoded default from Lambda env vars
- `services/render-worker/deploy/DEPLOY.md` — update memory recommendation

---

### Fix 3: Periodic `gc.collect()` + `img.close()` (P1)

**Goal:** Promptly reclaim evicted cache entries and temporary PIL Images that CPython's allocator may not immediately return to the OS.

**Note:** `img.close()` is already included in Fix 1 (Change 5). This fix covers only the periodic `gc.collect()`.

**Why this is P1 not P0:** CPython's reference counting should free memory immediately when the last reference is dropped (e.g., when `OrderedDict.popitem()` removes an entry). `gc.collect()` primarily helps with:
1. Memory fragmentation — CPython's `free()` may not return memory to the OS; `gc.collect()` can trigger `malloc_trim()` behavior
2. Transient PIL Images whose pixel buffers haven't been freed yet despite being GC-eligible

This is a safety net, not the primary fix. The primary memory reduction comes from Fixes 1 and 2.

#### File: `services/render-worker/src/sow_render_worker/video_engine.py`

**Change 1: Add `import gc` at top of file (after line 7)**

```python
import gc
```

**Change 2: Add `gc.collect()` inside existing periodic logging block (after line 411)**

```python
if frame_count > 0 and frame_count % (self.fps * 5) == 0:
    # ... existing logging ...

    # Periodic GC to reclaim evicted cache entries and transient PIL Images
    gc.collect()
```

**Why every 5 seconds of video (not every frame):** `gc.collect()` has non-trivial overhead (~1-5ms). Calling it every frame would add ~24-120ms/s of overhead. Every 5 seconds of video (= every 120 frames at 24fps) means ~12 calls per minute of video, negligible overhead.

**Files changed:**
- `services/render-worker/src/sow_render_worker/video_engine.py` — add `import gc`, add `gc.collect()` call

---

### Fix 4: Memory Monitoring and Graceful OOM Handling (P1)

**Goal:** Detect memory pressure before hitting the hard OOM kill, and call `fail_render_job()` so the job doesn't get stuck in `running`.

**Background:** The existing SIGTERM handler (`pipeline.py:37-42`) and `check_lambda_timeout()` (`pipeline.py:209-222`) already provide graceful shutdown for Lambda timeout. However, OOM kills the process instantly with no cleanup — worse than SIGTERM. We need a memory analog of the timeout check.

#### File: `services/render-worker/src/sow_render_worker/video_engine.py`

**Change 1: Add memory check function (after imports, before class definition)**

```python
import os

_MEMORY_WARNING_FRACTION = 0.90


def _check_memory_pressure() -> None:
    """
    Raise MemoryError if process RSS exceeds 90% of Lambda memory limit.

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
        pass
```

**Change 2: Call memory check in the encoding loop (after existing timeout check, line 339-340)**

```python
while frame_count < total_frames:
    if timeout_check_callback:
        timeout_check_callback()

    _check_memory_pressure()
    ...
```

**Why check every frame:** Reading `/proc/self/status` is a simple file read (~0.01ms). The overhead is negligible compared to the ~37ms per-frame render cost on cache misses. Checking every frame ensures we catch memory pressure as soon as it crosses the threshold, giving us the maximum window to call `fail_render_job()` before the hard OOM.

**Why 90% threshold:** At 90% of 3072 MB = 2765 MB, we still have ~300 MB of headroom for the `fail_render_job()` DB write and cleanup. The OOM kill happens at 100% (3008 MB). This gives us a ~300 MB / ~5.9 MB per frame = ~50 frame window (~2 seconds at 24fps) to detect and fail gracefully.

#### File: `services/render-worker/src/sow_render_worker/pipeline.py`

**Change 3: Add `except MemoryError` handler before generic `except Exception` (before line 516)**

The existing `except Exception` handler already catches `MemoryError` (it's a subclass of `Exception`). However, a specific handler provides distinct logging and ensures the error is clearly identified as memory-related:

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

except PipelineCancelledError:
    # ... existing handler ...

except Exception as exc:
    # ... existing handler ...
```

**Files changed:**
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

#### File: `services/render-worker/src/sow_render_worker/db.py`

**Change 1: Reduce `STALE_JOB_THRESHOLD_SECONDS` from 300 to 120 (line 26)**

```python
# BEFORE:
STALE_JOB_THRESHOLD_SECONDS = 300  # 5 minutes

# AFTER:
STALE_JOB_THRESHOLD_SECONDS = 120  # 2 minutes
```

**Rationale:** A Lambda invocation that hasn't updated `updated_at` in 2 minutes is almost certainly dead. The encoding loop updates progress every 1 second of video (every `fps` frames). Even the slowest phases (audio mixing, uploading) complete in under 2 minutes. The only scenario where a healthy worker wouldn't update progress for 2+ minutes is if it's stuck in a non-progress-reporting code path, which is itself a bug.

**Change 2: Add `LIKELY_DEAD_THRESHOLD_SECONDS` and `reclaim_likely_dead_job()` (after `reclaim_stale_job`)**

```python
LIKELY_DEAD_THRESHOLD_SECONDS = 60  # 1 minute


def reclaim_likely_dead_job(
    conn: psycopg2.extensions.connection,
    job_id: str,
    user_id: int,
) -> Optional[RenderJob]:
    """
    Reclaim a job that's been running with no progress for 1+ minute.

    More aggressive than reclaim_stale_job (2 min threshold).
    Used when a second SQS invocation arrives — the first invocation
    is almost certainly dead if it hasn't updated progress in 60 seconds.
    """
    return reclaim_stale_job(conn, job_id, user_id, stale_threshold_seconds=LIKELY_DEAD_THRESHOLD_SECONDS)
```

**Why 60 seconds is safe:** The encoding loop calls `update_render_progress()` every second of video (every 24 frames). A healthy worker will update `updated_at` at least once every ~1 second of wall-clock time during encoding. If `updated_at` hasn't changed in 60 seconds, the worker is either:
1. Dead (OOM, SIGKILL, crash) — should be reclaimed
2. Stuck in a non-progress-reporting code path — should be reclaimed (it's broken)
3. In a very slow phase with no progress callback — unlikely, all phases report progress

**Risk:** A very brief network hiccup could cause a 60-second gap in progress updates while the worker is still alive. However, the `start_render_job()` SQL uses `WHERE status = 'queued'`, so even if we reclaim a job that's actually still running, the original worker will continue encoding (it already has the job claimed). The worst case is a duplicate render, which is caught by the `COALESCE(started_at, now)` pattern — the original worker's `started_at` is preserved.

#### File: `services/render-worker/src/sow_render_worker/pipeline.py`

**Change 3: Import and use `reclaim_likely_dead_job` instead of `reclaim_stale_job` (lines 15-23, 228-243)**

```python
# BEFORE (imports):
from sow_render_worker.db import (
    ...
    reclaim_stale_job,
    ...
)

# AFTER:
from sow_render_worker.db import (
    ...
    reclaim_likely_dead_job,
    reclaim_stale_job,
    ...
)

# BEFORE (usage, lines 228-243):
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

**Files changed:**
- `services/render-worker/src/sow_render_worker/db.py` — reduce `STALE_JOB_THRESHOLD_SECONDS` to 120, add `LIKELY_DEAD_THRESHOLD_SECONDS = 60`, add `reclaim_likely_dead_job()`
- `services/render-worker/src/sow_render_worker/pipeline.py` — import and use `reclaim_likely_dead_job`

---

## File Change Summary

### Render Worker (`services/render-worker/`)

| File | Changes | Fix |
|------|---------|-----|
| `src/sow_render_worker/frame_renderer.py` | `_DEFAULT_MAX_CACHE_ENTRIES` 300 → 200; `Image.new("RGBA",...)` → `Image.new("RGB",...)` in `_render_frame_impl` and `render_title_card`; add `img.close()` after `.tobytes()` in 2 places | 1, 2 |
| `src/sow_render_worker/video_engine.py` | FFmpeg `-pix_fmt rgba` → `-pix_fmt rgb24`; add `title_card_img.convert("RGB")` + `.close()` before `.tobytes()`; add `import gc` and periodic `gc.collect()`; add `_check_memory_pressure()` function; call memory check in encoding loop | 1, 3, 4 |
| `src/sow_render_worker/pipeline.py` | Add `except MemoryError` handler; import and use `reclaim_likely_dead_job` instead of `reclaim_stale_job` | 4, 5 |
| `src/sow_render_worker/db.py` | `STALE_JOB_THRESHOLD_SECONDS` 300 → 120; add `LIKELY_DEAD_THRESHOLD_SECONDS = 60`; add `reclaim_likely_dead_job()` | 5 |
| `docker-compose.yml` | Remove `SOW_MAX_CACHE_ENTRIES: ${SOW_MAX_CACHE_ENTRIES:-300}` hardcoded default | 2 |
| `docker-compose.dev.yml` | Same as above | 2 |
| `deploy/aws-up.sh` | Remove `SOW_MAX_CACHE_ENTRIES:-300` from Lambda env vars JSON | 2 |
| `deploy/DEPLOY.md` | Update memory recommendation to 3072 MB minimum for 1080p | 2 |

### Tests (`services/render-worker/tests/`)

| File | Changes | Fix |
|------|---------|-----|
| `test_frame_renderer.py` | Add tests for RGB mode (`Image.new("RGB",...)`); add test for `img.close()` after `.tobytes()`; update `test_default_max_cache_entries` from 300 → 200 | 1, 2 |
| `test_video_engine.py` | Add test for FFmpeg args containing `-pix_fmt rgb24`; add test for `_check_memory_pressure()` raising `MemoryError` at 90% threshold; add test for periodic `gc.collect()` | 1, 3, 4 |
| `test_pipeline.py` | Add test for `except MemoryError` handler; add test for `reclaim_likely_dead_job` (60s threshold); update existing `test_pipeline_skips_when_job_already_claimed` to use new function | 4, 5 |
| `test_db.py` | Add test for `LIKELY_DEAD_THRESHOLD_SECONDS = 60`; add test for `reclaim_likely_dead_job()` with 60s threshold; update `test_default_threshold_5_minutes` → `test_default_threshold_2_minutes` | 5 |

---

## Implementation Order

1. **Fix 1** (P0) — RGBA → RGB pixel format + `img.close()`
   - Highest impact per line of code changed
   - 25% memory reduction with no quality loss
   - Must be done first — Fix 2's memory budget assumes RGB

2. **Fix 2** (P0) — Reduce `_DEFAULT_MAX_CACHE_ENTRIES` to 200
   - Depends on Fix 1 for correct memory budget (5.9 MB/entry vs 7.9 MB/entry)
   - Simple constant change + deploy config cleanup

3. **Fix 3** (P1) — Periodic `gc.collect()`
   - Simple, independent, no risk
   - Can be done in parallel with Fix 4

4. **Fix 4** (P1) — Memory monitoring and graceful OOM handling
   - Safety net for cases where Fixes 1+2 aren't enough
   - Independent of other fixes

5. **Fix 5** (P1) — Aggressive stale job reclamation
   - Independent of other fixes
   - Prevents stuck jobs on retry

---

## Testing Plan

### Unit Tests

**Fix 1 — RGBA → RGB:**
- `test_render_frame_impl_creates_rgb_image` — verify `Image.new("RGB",...)` is used
- `test_render_title_card_creates_rgb_image` — verify title card is RGB
- `test_ffmpeg_args_rgb24` — verify FFmpeg args contain `-pix_fmt rgb24`
- `test_img_close_after_tobytes` — verify `img.close()` is called after `.tobytes()`
- `test_title_card_convert_rgb_before_tobytes` — verify title card is converted to RGB before `.tobytes()`

**Fix 2 — Cache entries:**
- `test_default_max_cache_entries_200` — verify `_DEFAULT_MAX_CACHE_ENTRIES == 200`

**Fix 3 — gc.collect():**
- `test_gc_collect_called_periodically` — verify `gc.collect()` is called every 5 seconds of video

**Fix 4 — Memory monitoring:**
- `test_check_memory_pressure_raises_at_90_percent` — verify `MemoryError` raised when RSS > 90% of Lambda memory
- `test_check_memory_pressure_passes_below_threshold` — verify no raise when RSS is below threshold
- `test_check_memory_pressure_noop_without_proc` — verify graceful no-op when `/proc` is unavailable
- `test_pipeline_memory_error_handler` — verify `fail_render_job()` is called on `MemoryError`

**Fix 5 — Stale job reclamation:**
- `test_likely_dead_threshold_60_seconds` — verify `LIKELY_DEAD_THRESHOLD_SECONDS == 60`
- `test_stale_threshold_120_seconds` — verify `STALE_JOB_THRESHOLD_SECONDS == 120`
- `test_reclaim_likely_dead_job_60s_threshold` — verify job reclaimed after 60s with no progress
- `test_reclaim_likely_dead_job_skips_recent_job` — verify job with recent progress is not reclaimed
- `test_pipeline_uses_reclaim_likely_dead_job` — verify pipeline uses the new function

### Integration Tests

1. **Memory profile test:** Run a 5-song 1080p render locally with memory tracking. Verify RSS stays below 90% of 3072 MB throughout encoding.

2. **OOM recovery test:** Set `SOW_MAX_CACHE_ENTRIES` artificially high to force memory pressure. Verify `MemoryError` is raised and `fail_render_job()` is called.

3. **SQS retry test:** Manually set a job to `running` with `updated_at` 90 seconds ago. Send a test SQS message. Verify job is reclaimed and re-processed.

---

## Out of Scope

- **Increasing Lambda memory to 4096+ MB** — Not needed. With RGB + 200 entries, 3072 MB has ~1,140 MB headroom (37%).
- **Resolution-aware cache sizing** — Total wall-clock time is identical from 100→300 entries. Static 200 is simpler with same results.
- **Disk-based frame cache** — Would eliminate memory pressure entirely but adds I/O latency and complexity. Not needed with RGB + 200 entries.
- **Compressed frame cache (PNG/JPEG in memory)** — Would reduce per-entry size by 80-95% but adds CPU overhead for compression/decompression. Not needed.
- **FFmpeg `-threads 1`** — Saves ~30 MB but increases encoding time by 10-20%. With the Lambda already at 654s of 900s timeout, this risks timeout failures.
- **Two-pass encoding (raw frames to disk, then FFmpeg from file)** — Eliminates pipe blocking but requires 2× disk I/O for the full frame sequence. Not needed.
- **Supporting 2048 MB Lambda config at 1080p** — 200 entries × 5.9 MB = 1,180 MB cache + ~950 MB overhead = 2,130 MB > 2,048 MB. 3072 MB is the minimum for 1080p with this cache design.
