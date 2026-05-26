# Frame Caching for Render Worker

## Problem

The render worker's `FrameRenderer.render_frame()` re-renders ALL text from scratch every frame. For CJK glyphs, each `draw.text()` call costs ~30-50ms. A 20-minute video at 24fps = 28,800 frames × ~40ms = **19.2 minutes** just for frame rendering — exceeding Lambda's 15-minute timeout for 4+ song renders.

But lyrics only change ~200 times in a 20-minute video (every 5-10 seconds). The same text is displayed for hundreds of consecutive frames, yet we re-render it every single time.

## Solution

Add a full-frame byte cache inside `FrameRenderer`. When `render_frame()` is called, compute a cache key from the visual state. If the key matches a cached entry, return the cached `Image` immediately. Otherwise, render normally and store in cache.

For fade-out animations (intro info fade, last-lyric fade), quantize the alpha to discrete steps (e.g., 16 levels) so consecutive fade frames share cache entries, reducing unique fade frames from ~100 to ~16 per fade event.

### Performance Impact

| Metric | Current | With Frame Caching |
|--------|---------|-------------------|
| Text render ops (20-min video) | 28,800 | ~200 (one per lyric change) + ~32 (fade steps) |
| Per-frame cost (cached hit) | ~40ms (full render) | ~0.1ms (dict lookup + Image copy) |
| Per-frame cost (cache miss) | ~40ms | ~40ms (render + store) |
| Frame rendering time (20-min video) | ~19.2 min | ~31s |
| Total video phase | ~19 min | ~4-6 min (FFmpeg encoding becomes bottleneck) |
| Total pipeline (4 songs) | ~15+ min (times out!) | ~5-7 min (fits in Lambda) |

### Memory Impact

- Each cached frame: 1920×1080×4 bytes = ~8.3MB
- ~200 unique lyric frames + ~32 fade frames = ~232 entries
- Peak memory: ~232 × 8.3MB ≈ **1.9GB**
- Lambda supports up to 10GB — well within limits

---

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Cache granularity | Full-frame bytes | Simplest implementation; memory is available on Lambda (up to 10GB) |
| Fade handling | Quantize alpha to 16 steps | Reduces unique fade frames from ~100 to ~16 per fade event; visually indistinguishable at 24fps |
| Cache location | Inside `FrameRenderer` | Clean separation of concerns; `VideoEngine` code unchanged |
| Cache key | Tuple of visual state components | Deterministic, hashable, no string building overhead |
| Cache storage | `dict[cache_key, Image.Image]` | PIL `Image` objects support `.copy()` for safe return; avoids re-creating from bytes |

---

## Implementation Plan

### Step 1: Add cache key computation to `FrameRenderer`

**File:** `services/render-worker/src/sow_render_worker/frame_renderer.py`

Add a method `_compute_cache_key()` that returns a hashable tuple representing the visual state of a frame. The key must capture everything that affects the rendered output:

```python
def _compute_cache_key(
    self,
    lyrics: list[GlobalLRCLine],
    segments: list[SegmentInfo],
    current_time: float,
) -> tuple:
```

The cache key components:

1. **Segment identity**: `(current_title, segment_id)` — which song we're in
2. **Lyric state**: `(current_lyric_index, next_lyric_index)` — which lyric lines are visible
3. **Intro info state**: `(is_in_intro, quantized_alpha)` — whether intro info is showing and at what alpha level
4. **Last-lyric fade state**: `(is_last_lyric, quantized_fade_alpha)` — whether last lyric is fading

Quantization: Round alpha values to the nearest step of `256 // FADE_ALPHA_STEPS`. With 16 steps, each step covers 16 alpha values (0, 16, 32, ..., 240, 255). This means:
- `quantize_alpha(200)` → `floor(200 / 16) * 16 = 192`
- `quantize_alpha(255)` → `255` (special case: full opacity stays full)

The key does NOT need to include:
- Template/resolution/font_size — these are constant for the lifetime of a `FrameRenderer` instance
- The actual text content — the lyric index already uniquely identifies the text

**Implementation details:**

```python
FADE_ALPHA_STEPS = 16
_ALPHA_STEP_SIZE = 256 // FADE_ALPHA_STEPS  # = 16

def _quantize_alpha(self, alpha: int) -> int:
    if alpha >= 255:
        return 255
    return (alpha // _ALPHA_STEP_SIZE) * _ALPHA_STEP_SIZE
```

The `_compute_cache_key` method needs to replicate the segment/lyric lookup logic currently in `render_frame()` and `render_lyrics()`. To avoid duplicating this logic, we'll refactor `render_frame()` to first determine the visual state, then use it for both caching and rendering.

### Step 2: Add cache storage to `FrameRenderer.__init__`

**File:** `services/render-worker/src/sow_render_worker/frame_renderer.py`

Add to `__init__`:

```python
self._frame_cache: dict[tuple, Image.Image] = {}
self._cache_hits = 0
self._cache_misses = 0
```

Add a `clear_cache()` method for use between render jobs (though in practice, a `FrameRenderer` is created per `VideoEngine` instance which is per render job):

```python
def clear_cache(self) -> None:
    self._frame_cache.clear()
    self._cache_hits = 0
    self._cache_misses = 0
```

Add a `get_cache_stats()` method for logging:

```python
def get_cache_stats(self) -> dict[str, int]:
    return {
        "entries": len(self._frame_cache),
        "hits": self._cache_hits,
        "misses": self._cache_misses,
    }
```

### Step 3: Refactor `render_frame()` to use cache

**File:** `services/render-worker/src/sow_render_worker/frame_renderer.py`

Refactor `render_frame()` into two phases:

1. **Determine visual state** — compute which segment, which lyric index, which fade state
2. **Render or retrieve from cache** — check cache key, return cached image or render + store

```python
def render_frame(
    self,
    lyrics: list[GlobalLRCLine],
    segments: list[SegmentInfo],
    current_time: float,
) -> Image.Image:
    # Phase 1: Determine visual state and cache key
    cache_key = self._compute_cache_key(lyrics, segments, current_time)

    # Phase 2: Cache lookup
    if cache_key in self._frame_cache:
        self._cache_hits += 1
        return self._frame_cache[cache_key].copy()

    # Phase 3: Render (existing logic, unchanged)
    self._cache_misses += 1
    img = self._render_frame_impl(lyrics, segments, current_time)

    # Phase 4: Store in cache
    self._frame_cache[cache_key] = img.copy()

    return img
```

**Important:** We return `img` directly (not a copy) for the cache miss case, and store a copy in the cache. This avoids an unnecessary copy for the common case (cache miss on first occurrence of a visual state). For cache hits, we return a copy so the caller can't mutate the cached image.

Move the existing `render_frame()` body into `_render_frame_impl()` — no logic changes needed.

### Step 4: Implement `_compute_cache_key()`

**File:** `services/render-worker/src/sow_render_worker/frame_renderer.py`

This method must replicate the visual state determination from `render_frame()` and `render_lyrics()`:

```python
def _compute_cache_key(
    self,
    lyrics: list[GlobalLRCLine],
    segments: list[SegmentInfo],
    current_time: float,
) -> tuple:
    # Determine current segment
    current_title = ""
    current_segment_id = ""
    for segment in segments:
        segment_start = segment.start_time_seconds
        segment_end = segment_start + segment.duration_seconds
        if segment_start <= current_time < segment_end:
            current_title = segment.song_title or "Unknown"
            current_segment_id = segment.id
            break

    lyrics_by_song = group_lyrics_by_song(lyrics)
    current_song_lyrics = lyrics_by_song.get(current_title, [])

    # Intro info state
    intro_alpha = 0
    if current_song_lyrics:
        first_lyric_time = current_song_lyrics[0].global_time_seconds
        if current_time < first_lyric_time:
            intro_alpha = self._compute_intro_alpha(
                segments, current_title, current_time, first_lyric_time
            )

    quantized_intro_alpha = self._quantize_alpha(intro_alpha) if intro_alpha > 0 else 0

    # Lyric state
    current_lyric_index = -1
    quantized_fade_alpha = 255
    is_last_lyric_faded = False

    if current_song_lyrics and current_time >= current_song_lyrics[0].global_time_seconds:
        for i, line in enumerate(current_song_lyrics):
            if line.global_time_seconds <= current_time:
                current_lyric_index = i
            else:
                break

        if current_lyric_index >= 0:
            is_last = current_lyric_index == len(current_song_lyrics) - 1
            if is_last:
                fade_alpha = self._compute_last_lyric_fade_alpha(
                    current_song_lyrics, current_time, current_lyric_index
                )
                if fade_alpha < 255:
                    quantized_fade_alpha = self._quantize_alpha(fade_alpha)
                    if fade_alpha <= 0:
                        is_last_lyric_faded = True
                        current_lyric_index = -1

    return (
        current_segment_id,
        current_title,
        current_lyric_index,
        quantized_intro_alpha,
        quantized_fade_alpha,
        is_last_lyric_faded,
    )
```

### Step 5: Extract alpha computation helpers

**File:** `services/render-worker/src/sow_render_worker/frame_renderer.py`

Extract the fade alpha computation from `render_intro_info()` and `render_lyrics()` into standalone helper methods so they can be used by both `_compute_cache_key()` and the render methods:

```python
def _compute_intro_alpha(
    self,
    segments: list[SegmentInfo],
    current_title: str,
    current_time: float,
    first_lyric_time: float,
) -> int:
    """Compute intro info alpha without rendering. Returns 0-255."""
    # Find the current segment
    current_segment = None
    for segment in segments:
        if segment.song_title == current_title or (not segment.song_title and current_title == "Unknown"):
            segment_start = segment.start_time_seconds
            segment_end = segment_start + segment.duration_seconds
            if segment_start <= current_time < segment_end:
                current_segment = segment
                break
    if not current_segment:
        return 0

    segment_start = current_segment.start_time_seconds
    gap_duration = first_lyric_time - segment_start

    if current_time >= first_lyric_time or gap_duration < 3.0:
        return 0

    if gap_duration < 7.0:
        info_duration = gap_duration * 0.6
        fade_duration = gap_duration * 0.4
    else:
        fade_duration = 4.0
        title_only_duration = 3.0
        info_duration = gap_duration - fade_duration - title_only_duration

    time_into_gap = current_time - segment_start
    if time_into_gap >= info_duration + fade_duration:
        return 0

    if time_into_gap < info_duration:
        return 255

    fade_progress = (time_into_gap - info_duration) / fade_duration
    return math.floor(255 * (1.0 - math.sqrt(fade_progress)))


def _compute_last_lyric_fade_alpha(
    self,
    song_lyrics: list[GlobalLRCLine],
    current_time: float,
    current_index: int,
) -> int:
    """Compute last lyric fade alpha without rendering. Returns 0-255, 255 = fully visible."""
    if current_index != len(song_lyrics) - 1:
        return 255

    max_display = estimate_last_lyric_duration(song_lyrics)
    elapsed_since_last_lyric = current_time - song_lyrics[current_index].global_time_seconds

    fade_duration = 7.0
    margin = 1.3
    fade_start_threshold = max_display * margin

    if elapsed_since_last_lyric > fade_start_threshold + fade_duration:
        return 0
    elif elapsed_since_last_lyric > fade_start_threshold:
        fade_progress = min(
            1.0,
            (elapsed_since_last_lyric - fade_start_threshold) / fade_duration,
        )
        return math.floor(255 * (1.0 - math.sqrt(fade_progress)))

    return 255
```

### Step 6: Log cache stats at end of encoding

**File:** `services/render-worker/src/sow_render_worker/video_engine.py`

After the encoding loop in `encode_video_with_ffmpeg()`, log the cache stats:

```python
if self.frame_renderer:
    stats = self.frame_renderer.get_cache_stats()
    logger.info(
        "[%s] Frame cache stats: %d entries, %d hits, %d misses (%.1f%% hit rate)",
        job_id or "unknown",
        stats["entries"],
        stats["hits"],
        stats["misses"],
        stats["hits"] / max(1, stats["hits"] + stats["misses"]) * 100,
    )
```

### Step 7: Update tests

**File:** `services/render-worker/tests/test_frame_renderer.py`

Add new test classes:

1. **`TestFrameCache`** — test cache key computation, quantization, hit/miss behavior
2. **`TestQuantizeAlpha`** — test alpha quantization edge cases
3. **`TestCacheKeyDeterminism`** — verify same visual state produces same cache key
4. **Update `TestRenderFrame`** — verify that repeated calls with same time produce same result (already implicitly tested, but add explicit cache hit test)

**File:** `services/render-worker/tests/test_video_engine.py`

5. **Update `TestEncodeVideoWithFFmpeg`** — verify that encoding still works correctly with caching enabled (existing tests should pass unchanged since the cache is internal to FrameRenderer)

### Step 8: Add performance benchmark test

**File:** `services/render-worker/tests/test_frame_renderer.py`

Add a benchmark test that measures the speedup:

```python
class TestFrameCachePerformance:
    def test_cache_speedup(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        lyrics = _make_lyrics([(5.0, "讚美之泉"), (10.0, "哈利路亞")])
        segments = [_make_segment(start=0.0, duration=60.0)]

        # Warm up cache
        renderer.render_frame(lyrics, segments, 7.0)

        # Measure cached performance
        import time
        start = time.monotonic()
        for _ in range(100):
            renderer.render_frame(lyrics, segments, 7.0)
        cached_elapsed = time.monotonic() - start

        # Measure uncached performance (new renderer)
        renderer_uncached = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        start = time.monotonic()
        for _ in range(100):
            renderer_uncached.render_frame(lyrics, segments, 7.0)
        uncached_elapsed = time.monotonic() - start

        # Cached should be significantly faster
        assert cached_elapsed < uncached_elapsed
```

---

## Detailed Cache Key Design

The cache key is a tuple of these components:

| Component | Type | Description | Example |
|-----------|------|-------------|---------|
| `current_segment_id` | `str` | Which segment/song we're in | `"seg1"` |
| `current_title` | `str` | Song title (for title rendering) | `"讚美之泉"` |
| `current_lyric_index` | `int` | Index of current lyric line (-1 = none) | `3` |
| `quantized_intro_alpha` | `int` | Intro info alpha, quantized to 16 steps | `192` |
| `quantized_fade_alpha` | `int` | Last lyric fade alpha, quantized to 16 steps | `255` |
| `is_last_lyric_faded` | `bool` | Whether last lyric has fully faded out | `False` |

**Why this key works:**

- Two frames with the same key will produce pixel-identical output
- The key is cheap to compute (no text rendering, just index lookups and simple math)
- The key is hashable (all components are immutable primitives)
- The key captures all visual state: segment identity, lyric position, fade state

**What the key does NOT need:**

- Text content — implied by `(segment_id, lyric_index)`
- Font size/template/resolution — constant for a `FrameRenderer` instance
- `next_lyric_index` — implied by `current_lyric_index + 1` within the same segment
- Exact alpha values — quantized alpha is visually equivalent at 24fps

---

## Alpha Quantization Analysis

With `FADE_ALPHA_STEPS = 16`, each step covers 16 alpha values:

| Step | Alpha Range | Visual |
|------|-------------|--------|
| 0 | 0-15 | Fully transparent |
| 1 | 16-31 | Nearly invisible |
| 2 | 32-47 | Very faint |
| ... | ... | ... |
| 14 | 224-239 | Nearly opaque |
| 15 | 240-254 | Almost fully opaque |
| 16 | 255 | Fully opaque |

At 24fps, a 4-second fade produces ~96 frames. Without quantization, each frame has a unique alpha → 96 cache entries. With 16 steps, only ~16 unique alpha levels → ~16 cache entries (the rest are cache hits).

**Visual quality:** At 24fps, a 16-step fade over 4 seconds means each step lasts ~6 frames (0.25s). The human eye cannot perceive a 16-unit alpha jump at this frame rate — it appears smooth.

---

## Edge Cases

| Edge Case | Handling |
|-----------|----------|
| No lyrics / no segments | Cache key = `("", "", -1, 0, 255, False)` — all frames are identical, 100% cache hit after first |
| Title card frames | Already cached in `VideoEngine` — `FrameRenderer.render_frame()` is never called for title card frames |
| Segment transitions | Different `segment_id` → different cache key → cache miss (correct, as the visual changes) |
| Very short fades (<1s) | Quantization may reduce to 1-2 steps — acceptable, fade is barely visible anyway |
| Multiple songs | Cache naturally handles this — different segments have different keys |
| Lambda container reuse | `FrameRenderer` is created fresh per `VideoEngine` per render job — no stale cache risk |

---

## Files Changed

| File | Change | Lines |
|------|--------|-------|
| `frame_renderer.py` | Add `_frame_cache`, `_cache_hits`, `_cache_misses` to `__init__` | ~3 |
| `frame_renderer.py` | Add `_quantize_alpha()` method | ~5 |
| `frame_renderer.py` | Add `_compute_intro_alpha()` method | ~35 |
| `frame_renderer.py` | Add `_compute_last_lyric_fade_alpha()` method | ~20 |
| `frame_renderer.py` | Add `_compute_cache_key()` method | ~50 |
| `frame_renderer.py` | Add `clear_cache()`, `get_cache_stats()` methods | ~12 |
| `frame_renderer.py` | Refactor `render_frame()` → `render_frame()` + `_render_frame_impl()` | ~10 |
| `frame_renderer.py` | Add `FADE_ALPHA_STEPS` constant | ~2 |
| `video_engine.py` | Log cache stats after encoding | ~8 |
| `test_frame_renderer.py` | Add `TestFrameCache`, `TestQuantizeAlpha`, `TestCacheKeyDeterminism`, `TestFrameCachePerformance` | ~120 |
| **Total** | | **~265** |

---

## Rollout Plan

1. Implement Steps 1-6 (core caching logic)
2. Run existing test suite — all tests must pass unchanged
3. Add new tests (Step 7-8)
4. Run a manual render test with a 4-song songset to verify:
   - Output video is visually identical to uncached render
   - Cache hit rate is >95% for lyric frames
   - Total render time is reduced by ~3-5x
5. Deploy to dev environment and run end-to-end render test
6. Deploy to production

---

## Future Optimizations (Out of Scope)

- **Text-element compositing**: Pre-render each unique text as a small RGBA image and composite onto background. Would reduce memory from ~1.9GB to ~50MB but adds significant complexity.
- **Background pre-rendering**: Cache the background image (solid color) separately and only composite text layers. Marginal gain since background creation is already fast.
- **FFmpeg filter-based approach**: Use FFmpeg's `drawtext` filter instead of PIL for text rendering. Would eliminate the Python rendering loop entirely but requires significant refactoring and loses the flexibility of PIL-based rendering.
- **GPU acceleration**: Use CUDA/OpenCL for compositing. Overkill given the cache hit rate makes compositing negligible.
