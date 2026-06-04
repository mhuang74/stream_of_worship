# Fix Render Worker OOM at 97.5% + Stale Job Reclamation — Implementation Summary

**Date:** 2026-06-02
**Branch:** `render_worker_same_request_bug`
**PR:** #82
**Spec:** `specs/fix-render-worker-oom-and-stale-reclaim-v2.md`
**Incident:** 2026-05-30 — Lambda OOM at frame 35040/35451 (97.5%), job stuck in `running`

---

## Root Cause

On 2026-05-30, a render worker Lambda hit `Runtime.OutOfMemory` at 3008 MB while encoding a 5-song worship set at 1080p. The Lambda was killed hard — no `finally` blocks, no `fail_render_job()` call. The job remained in `running` status. A second SQS trigger arrived ~4 minutes later but could not reclaim the job (only ~4 min stale, threshold was 5 min), so it silently skipped.

The dominant memory consumer was the frame cache: 266 entries × 7.9 MB/entry (RGBA at 1080p) = ~2.1 GB, leaving insufficient headroom for Python runtime, FFmpeg, and CPython fragmentation.

---

## Changes by Fix

### Fix 1 (P0): RGBA → RGB Pixel Format

**Files:** `frame_renderer.py`, `video_engine.py`

| Change | File | Detail |
|--------|------|--------|
| `Image.new("RGBA",...)` → `Image.new("RGB",...)` | `frame_renderer.py:406,618` | `_render_frame_impl` and `render_title_card` |
| `img.close()` after `.tobytes()` | `frame_renderer.py:393,402` | Both cache-hit and no-cache paths in `render_frame_bytes` |
| FFmpeg `-pix_fmt rgba` → `-pix_fmt rgb24` | `video_engine.py:283` | FFmpeg rawvideo input format |
| Title card `.convert("RGB")` + `.close()` | `video_engine.py:328-331` | Before `.tobytes()` for title card bytes |

**Impact:** 25% memory reduction per cached frame (7.9 MB → 5.9 MB at 1080p). No quality loss — the alpha channel was never used for compositing; all alpha fading is implemented via color pre-multiplication.

### Fix 2 (P0): Reduce `_DEFAULT_MAX_CACHE_ENTRIES` from 300 to 200

**Files:** `frame_renderer.py`, `docker-compose.yml`, `deploy/aws-up.sh`, `deploy/DEPLOY.md`

| Change | File | Detail |
|--------|------|--------|
| `_DEFAULT_MAX_CACHE_ENTRIES = 200` | `frame_renderer.py:25` | Code default |
| Remove `SOW_MAX_CACHE_ENTRIES:-300` | `docker-compose.yml:20` | Let code default apply |
| Remove from Lambda env vars JSON | `deploy/aws-up.sh:233-243` | No longer hardcoded in deploy script |
| Memory recommendation → 3072 MB | `deploy/DEPLOY.md:98,110` | Updated both create and update commands |

**Impact:** Max cache at 1080p/RGB: ~1.18 GB, leaving ~1.14 GB headroom at 3072 MB. Hit rate remains ~98.5% (FFmpeg encoding is always the bottleneck, not render time).

### Fix 3 (P1): Periodic `gc.collect()`

**Files:** `video_engine.py`

| Change | File | Detail |
|--------|------|--------|
| `import gc` | `video_engine.py:3` | After `from __future__ import annotations` |
| `gc.collect()` every 5s of video | `video_engine.py:438` | Inside existing periodic logging block |

**Impact:** Reclaims evicted cache entries and transient PIL Images. ~12 calls/minute, negligible overhead.

### Fix 4 (P1): Memory Monitoring and Graceful OOM Handling

**Files:** `video_engine.py`, `pipeline.py`

| Change | File | Detail |
|--------|------|--------|
| `_MEMORY_WARNING_FRACTION = 0.90` | `video_engine.py:35` | Constant |
| `_check_memory_pressure()` | `video_engine.py:38-55` | Reads `/proc/self/status`, raises `MemoryError` when RSS > 90% of Lambda memory |
| Call in encoding loop | `video_engine.py:366` | After timeout check, every frame |
| `except MemoryError` handler | `pipeline.py:516-526` | Calls `fail_render_job()` before re-raising |

**Impact:** Detects memory pressure ~2 seconds before hard OOM kill (90% of 3072 MB = 2765 MB; at ~5.9 MB/frame, ~50 frame window). Job is marked as `failed` instead of stuck in `running`.

### Fix 5 (P1): Aggressive Stale Job Reclamation on SQS Retry

**Files:** `db.py`, `pipeline.py`

| Change | File | Detail |
|--------|------|--------|
| `STALE_JOB_THRESHOLD_SECONDS` 300 → 120 | `db.py:26` | 2-minute threshold |
| `LIKELY_DEAD_THRESHOLD_SECONDS = 60` | `db.py:28` | New constant |
| `reclaim_likely_dead_job()` | `db.py:223-230` | Delegates to `reclaim_stale_job` with 60s threshold |
| Use `reclaim_likely_dead_job` | `pipeline.py:17,231` | Import and use instead of `reclaim_stale_job` |

**Impact:** A job with no progress update for 60s is reclaimed on the next SQS retry, preventing the stuck-forever scenario from the incident.

---

## Memory Budget at 1080p / 3072 MB / RGB

| Component | Memory |
|-----------|--------|
| Frame cache (200 × 5.9 MB) | 1,180 MB |
| Python runtime + libraries | ~200 MB |
| FFmpeg subprocess | ~300 MB |
| Title card + LRC cache + misc | ~50 MB |
| CPython fragmentation reserve | ~200 MB |
| **TOTAL** | **~1,930 MB** |
| **Headroom** | **~1,140 MB (37%)** |

Previous budget at 1080p / 3008 MB / RGBA / 300 entries: ~2,630-2,830 MB with ~0-180 MB headroom.

---

## Tests

All 559 render worker tests pass. New tests added:

| Test File | New Tests | Fix |
|-----------|-----------|-----|
| `test_frame_renderer.py` | `TestRGBMode` (4 tests): RGB mode, `img.close()` in both paths | 1 |
| `test_frame_renderer.py` | `TestDefaultMaxCacheEntries` (1 test): `_DEFAULT_MAX_CACHE_ENTRIES == 200` | 2 |
| `test_video_engine.py` | `TestFFmpegArgsRGB24` (1 test): `-pix_fmt rgb24` | 1 |
| `test_video_engine.py` | `TestCheckMemoryPressure` (4 tests): raise/pass/noop/fraction | 4 |
| `test_video_engine.py` | `TestGCCollect` (1 test): `gc.collect()` called periodically | 3 |
| `test_pipeline.py` | `test_pipeline_memory_error_handler` (1 test): `fail_render_job()` called | 4 |
| `test_pipeline.py` | `test_pipeline_uses_reclaim_likely_dead_job` (1 test) | 5 |
| `test_db.py` | `TestLikelyDeadThreshold` (2 tests): constants | 5 |
| `test_db.py` | `TestReclaimLikelyDeadJob` (3 tests): reclaim/skip/threshold | 5 |

Updated existing tests:
- `test_db.py`: `test_default_threshold_5_minutes` → `test_default_threshold_2_minutes` (300 → 120)
- `test_frame_renderer.py`: Background color assertions `(r,g,b,255)` → `(r,g,b)` for RGB mode
- `test_frame_renderer.py`: Cache-disabled byte length `1920*1080*4` → `1920*1080*3`
- `test_video_engine.py`: FFmpeg pix_fmt assertions `rgba` → `rgb24`
- `test_pipeline.py`: `reclaim_stale_job` mock → `reclaim_likely_dead_job` mock

---

## Migration Notes

- **Lambda memory:** Must be at least 3072 MB for 1080p encoding. Update via `aws lambda update-function-configuration --memory-size 3072`
- **`SOW_MAX_CACHE_ENTRIES` env var:** Hardcoded default of 300 removed from deploy configs. Code default is now 200. If previously set explicitly, consider removing it to use the new default.
- **No video quality change:** RGB vs RGBA has zero visual difference — the alpha channel was never used for compositing.

---

## Commits

| Commit | Description |
|--------|-------------|
| `948223a` | fix: render worker OOM at 97.5% + stale job reclamation |
| `9f8dc04` | specs: add render worker OOM and stale reclaim design docs |
