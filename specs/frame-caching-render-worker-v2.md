# Frame Caching for Render Worker — v2 Implementation Plan

## Problem

The render worker's `FrameRenderer.render_frame()` re-renders ALL text from scratch every frame. For CJK glyphs, each `draw.text()` call costs ~30-50ms. A 20-minute video at 24fps = 28,800 frames × ~40ms = **19.2 minutes** just for frame rendering — exceeding Lambda's 15-minute timeout for 4+ song renders.

But lyrics only change ~200 times in a 20-minute video (every 5-10 seconds). The same text is displayed for hundreds of consecutive frames, yet we re-render it every single time.

## Solution

Add a full-frame **byte** cache inside `FrameRenderer`. When `render_frame_bytes()` is called, compute a cache key from the visual state. If the key matches a cached entry, return the cached `bytes` immediately. Otherwise, render normally, store `tobytes()` in cache, and return the bytes.

For fade-out animations (intro info fade, last-lyric fade), quantize the alpha to discrete steps (configurable, default 16) so consecutive fade frames share cache entries, reducing unique fade frames from ~100 to ~16 per fade event.

### Performance Impact

| Metric | Current | With Frame Caching |
|--------|---------|-------------------|
| Text render ops (20-min video) | 28,800 | ~200 (one per lyric change) + ~32 (fade steps) |
| Per-frame cost (cached hit) | ~40ms (full render) | ~0.05ms (dict lookup + bytes return) |
| Per-frame cost (cache miss) | ~40ms | ~40ms (render + tobytes + store) |
| Frame rendering time (20-min video) | ~19.2 min | ~31s |
| Total video phase | ~19 min | ~4-6 min (FFmpeg encoding becomes bottleneck) |
| Total pipeline (4 songs) | ~15+ min (times out!) | ~5-7 min (fits in Lambda) |

### Memory Impact

- Each cached frame: 1920×1080×4 bytes = ~8.3MB (raw RGBA bytes, no PIL object overhead)
- ~200 unique lyric frames + ~32 fade frames = ~232 entries
- Typical peak memory: ~232 × 8.3MB ≈ **1.9GB**
- Bounded LRU cache with max 300 entries caps memory at ~300 × 8.3MB ≈ **2.5GB**
- Lambda baseline (Python + FFmpeg + audio on disk): ~300MB
- Total worst case: ~2.8GB — within 3GB Lambda limit with ~200MB headroom

---

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Cache granularity | Full-frame **bytes** | Eliminates `.copy()` + `.tobytes()` double-copy on hits; caller (`video_engine.py`) already calls `.tobytes()` immediately |
| Cache return type | `bytes` via `render_frame_bytes()` | New method avoids breaking `render_frame()` → `Image.Image` API; `video_engine.py` switches to bytes method |
| Fade handling | Quantize alpha to configurable steps (default 16) | Reduces unique fade frames from ~100 to ~16 per fade event; tunable via env var |
| Cache location | Inside `FrameRenderer` | Clean separation of concerns; `VideoEngine` code unchanged except switching method call |
| Cache key | Tuple of visual state components | Deterministic, hashable, no string building overhead |
| Cache storage | Bounded `OrderedDict` (LRU eviction) | Caps memory usage; evicts oldest entries when limit reached |
| Cache toggle | Env var `SOW_FRAME_CACHE_ENABLED` | Kill switch for production without redeploy |
| Quantization config | Env var `SOW_FADE_ALPHA_STEPS` | Tunable quality/memory tradeoff in production |
| Visual state resolution | Shared `_resolve_visual_state()` method | Single source of truth; both cache key and rendering consume same result; eliminates logic duplication |

---

## Implementation Plan

### Step 1: Add configuration constants and env var reading

**File:** `services/render-worker/src/sow_render_worker/frame_renderer.py`

Add module-level constants and env var reading:

```python
import os
from collections import OrderedDict

_DEFAULT_FADE_ALPHA_STEPS = 16
_DEFAULT_MAX_CACHE_ENTRIES = 300
_DEFAULT_CACHE_ENABLED = True


def _get_bool_env(name: str, default: bool) -> bool:
    val = os.environ.get(name, "").strip().lower()
    if val in ("1", "true", "yes", "on"):
        return True
    if val in ("0", "false", "no", "off"):
        return False
    return default


def _get_int_env(name: str, default: int) -> int:
    val = os.environ.get(name, "")
    if val.strip():
        try:
            return int(val)
        except ValueError:
            pass
    return default
```

### Step 2: Add cache storage and config to `FrameRenderer.__init__`

**File:** `services/render-worker/src/sow_render_worker/frame_renderer.py`

Add to `__init__`:

```python
self._cache_enabled = _get_bool_env("SOW_FRAME_CACHE_ENABLED", _DEFAULT_CACHE_ENABLED)
self._fade_alpha_steps = max(2, _get_int_env("SOW_FADE_ALPHA_STEPS", _DEFAULT_FADE_ALPHA_STEPS))
self._max_cache_entries = max(10, _get_int_env("SOW_MAX_CACHE_ENTRIES", _DEFAULT_MAX_CACHE_ENTRIES))
self._frame_cache: OrderedDict[tuple, bytes] = OrderedDict()
self._cache_hits = 0
self._cache_misses = 0
self._alpha_step_size = 256 // self._fade_alpha_steps

logger.info(
    "FrameRenderer cache config: enabled=%s, fade_alpha_steps=%d, max_entries=%d",
    self._cache_enabled, self._fade_alpha_steps, self._max_cache_entries,
)
```

Add helper methods:

```python
def clear_cache(self) -> None:
    self._frame_cache.clear()
    self._cache_hits = 0
    self._cache_misses = 0

def get_cache_stats(self) -> dict[str, int]:
    return {
        "entries": len(self._frame_cache),
        "hits": self._cache_hits,
        "misses": self._cache_misses,
        "max_entries": self._max_cache_entries,
    }
```

### Step 3: Add `_quantize_alpha()` method

**File:** `services/render-worker/src/sow_render_worker/frame_renderer.py`

```python
def _quantize_alpha(self, alpha: int) -> int:
    if alpha >= 255:
        return 255
    return (alpha // self._alpha_step_size) * self._alpha_step_size
```

### Step 4: Add `VisualState` dataclass and `_resolve_visual_state()` method

**File:** `services/render-worker/src/sow_render_worker/frame_renderer.py`

This is the **single source of truth** for determining what a frame looks like. Both the cache key computation and the render implementation consume this result.

```python
@dataclass(frozen=True)
class VisualState:
    segment_id: str
    current_title: str
    current_segment: SegmentInfo | None
    current_song_lyrics: list[GlobalLRCLine]
    current_lyric_index: int
    intro_alpha: int
    fade_alpha: int
    is_last_lyric_faded: bool
    current_time: float
```

`current_time` is included because `render_intro_info()` and `render_lyrics()` need it for rendering. It is NOT part of the cache key — the cache key already captures the visual consequences of `current_time` (which segment, which lyric, which alpha).

```python
def _resolve_visual_state(
    self,
    lyrics: list[GlobalLRCLine],
    segments: list[SegmentInfo],
    current_time: float,
) -> VisualState:
    current_title = ""
    current_segment: SegmentInfo | None = None

    for segment in segments:
        segment_start = segment.start_time_seconds
        segment_end = segment_start + segment.duration_seconds
        if segment_start <= current_time < segment_end:
            current_title = segment.song_title or "Unknown"
            current_segment = segment
            break

    lyrics_by_song = group_lyrics_by_song(lyrics)
    current_song_lyrics = lyrics_by_song.get(current_title, [])

    intro_alpha = 0
    if current_segment and current_song_lyrics:
        first_lyric_time = current_song_lyrics[0].global_time_seconds
        if current_time < first_lyric_time:
            intro_alpha = self._compute_intro_alpha(
                current_segment, current_time, first_lyric_time
            )

    current_lyric_index = -1
    fade_alpha = 255
    is_last_lyric_faded = False

    if current_song_lyrics and current_time >= current_song_lyrics[0].global_time_seconds:
        for i, line in enumerate(current_song_lyrics):
            if line.global_time_seconds <= current_time:
                current_lyric_index = i
            else:
                break

        if current_lyric_index < 0 and current_time > current_song_lyrics[-1].global_time_seconds:
            current_lyric_index = len(current_song_lyrics) - 1

        if current_lyric_index >= 0:
            is_last = current_lyric_index == len(current_song_lyrics) - 1
            if is_last:
                fade_alpha = self._compute_last_lyric_fade_alpha(
                    current_song_lyrics, current_time, current_lyric_index
                )
                if fade_alpha <= 0:
                    is_last_lyric_faded = True
                    current_lyric_index = -1

    return VisualState(
        segment_id=current_segment.id if current_segment else "",
        current_title=current_title,
        current_segment=current_segment,
        current_song_lyrics=current_song_lyrics,
        current_lyric_index=current_lyric_index,
        intro_alpha=intro_alpha,
        fade_alpha=fade_alpha,
        is_last_lyric_faded=is_last_lyric_faded,
        current_time=current_time,
    )
```

### Step 5: Add `_compute_cache_key()` method

**File:** `services/render-worker/src/sow_render_worker/frame_renderer.py`

Thin wrapper that quantizes alpha values from the resolved state:

```python
def _compute_cache_key(self, state: VisualState) -> tuple:
    quantized_intro = self._quantize_alpha(state.intro_alpha) if state.intro_alpha > 0 else 0
    quantized_fade = self._quantize_alpha(state.fade_alpha) if state.fade_alpha < 255 else 255

    return (
        state.segment_id,
        state.current_title,
        state.current_lyric_index,
        quantized_intro,
        quantized_fade,
        state.is_last_lyric_faded,
    )
```

### Step 6: Extract alpha computation helpers

**File:** `services/render-worker/src/sow_render_worker/frame_renderer.py`

Extract the fade alpha computation from `render_intro_info()` and `render_lyrics()` into standalone helper methods. These are used by both `_resolve_visual_state()` and the render methods.

```python
def _compute_intro_alpha(
    self,
    segment: SegmentInfo,
    current_time: float,
    first_lyric_time: float,
) -> int:
    """Compute intro info alpha without rendering. Returns 0-255."""
    segment_start = segment.start_time_seconds
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

### Step 7: Refactor `render_frame()` and add `render_frame_bytes()`

**File:** `services/render-worker/src/sow_render_worker/frame_renderer.py`

Refactor `render_frame()` to accept an optional pre-resolved `VisualState`, and extract the rendering body into `_render_frame_impl()`:

```python
def render_frame(
    self,
    lyrics: list[GlobalLRCLine],
    segments: list[SegmentInfo],
    current_time: float,
    _state: VisualState | None = None,
) -> Image.Image:
    state = _state or self._resolve_visual_state(lyrics, segments, current_time)
    return self._render_frame_impl(state)

def render_frame_bytes(
    self,
    lyrics: list[GlobalLRCLine],
    segments: list[SegmentInfo],
    current_time: float,
) -> bytes:
    state = self._resolve_visual_state(lyrics, segments, current_time)

    if self._cache_enabled:
        cache_key = self._compute_cache_key(state)
        if cache_key in self._frame_cache:
            self._cache_hits += 1
            self._frame_cache.move_to_end(cache_key)
            return self._frame_cache[cache_key]

        self._cache_misses += 1
        img = self._render_frame_impl(state)
        frame_bytes = img.tobytes()

        self._frame_cache[cache_key] = frame_bytes
        if len(self._frame_cache) > self._max_cache_entries:
            self._frame_cache.popitem(last=False)

        return frame_bytes

    img = self._render_frame_impl(state)
    return img.tobytes()
```

**Key design points:**

- `render_frame()` still returns `Image.Image` — backward compatible for tests
- `render_frame_bytes()` returns `bytes` — used by `video_engine.py`, with caching
- Both share `_render_frame_impl()` — no duplicate rendering logic
- `_state` parameter on `render_frame()` allows callers to pass pre-resolved state (internal use)
- Cache stores `bytes` — on hit, returns bytes directly (zero copies)
- On miss, renders Image → `tobytes()` → caches bytes → returns bytes (one copy)
- LRU eviction via `OrderedDict.move_to_end()` + `popitem(last=False)`

### Step 8: Implement `_render_frame_impl()`

**File:** `services/render-worker/src/sow_render_worker/frame_renderer.py`

Move the existing `render_frame()` body into `_render_frame_impl()`, refactored to consume `VisualState` instead of re-computing segment/lyric lookups:

```python
def _render_frame_impl(self, state: VisualState) -> Image.Image:
    width, height = self.resolution
    img = Image.new("RGBA", (width, height), (*self.template.background_color, 255))
    draw = ImageDraw.Draw(img)

    current_title = state.current_title
    current_segment = state.current_segment
    current_song_lyrics = state.current_song_lyrics
    current_time = state.current_time

    intro_info_alpha = 0

    if current_segment and current_song_lyrics and state.intro_alpha > 0:
        intro_info_alpha = self.render_intro_info(
            current_segment,
            current_time,
            current_song_lyrics[0].global_time_seconds,
            draw,
            width,
            height,
        )

    if current_title and intro_info_alpha == 0:
        text_r, text_g, text_b = self.template.text_color
        title_font_size_target = math.floor(self.base_font_size * 0.8)
        margin = self.get_margin(draw, title_font_size_target)
        title_font_size = self.fit_text(
            draw, current_title, title_font_size_target, width - margin * 2
        )
        font = self._get_font(title_font_size)
        draw.text(
            (width // 2, 50),
            current_title,
            fill=(text_r, text_g, text_b),
            font=font,
            anchor="mt",
        )

    if current_song_lyrics:
        first_lyric_time = current_song_lyrics[0].global_time_seconds
        if current_time >= first_lyric_time:
            self.render_lyrics(
                current_song_lyrics,
                current_time,
                current_title,
                draw,
                width,
                height,
            )

    return img
```

### Step 9: Refactor `render_intro_info()` to use `_compute_intro_alpha()`

**File:** `services/render-worker/src/sow_render_worker/frame_renderer.py`

Replace the inline alpha computation in `render_intro_info()` with a call to `_compute_intro_alpha()`. The method still does the actual rendering (drawing text), but delegates alpha computation:

```python
def render_intro_info(
    self,
    segment: SegmentInfo,
    current_time: float,
    first_lyric_time: float,
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
) -> int:
    alpha = self._compute_intro_alpha(segment, current_time, first_lyric_time)
    if alpha <= 0:
        return 0

    segment_start = segment.start_time_seconds
    gap_duration = first_lyric_time - segment_start

    if gap_duration < 7.0:
        info_duration = gap_duration * 0.6
        fade_duration = gap_duration * 0.4
        title_only_duration = 0.0
    else:
        fade_duration = 4.0
        title_only_duration = 3.0
        info_duration = gap_duration - fade_duration - title_only_duration

    time_into_gap = current_time - segment_start

    if time_into_gap >= info_duration + fade_duration:
        return 0

    info_lines: list[str] = []

    if segment.song_title:
        info_lines.append(f"歌曲：{segment.song_title}")
    if segment.song_album_name:
        info_lines.append(f"專輯：{segment.song_album_name}")
    if segment.song_composer:
        info_lines.append(f"作曲：{segment.song_composer}")
    if segment.song_lyricist:
        info_lines.append(f"作詞：{segment.song_lyricist}")
    info_lines.append("讚美之泉音樂事工")

    if not info_lines:
        return 0

    line_height = self.base_font_size * 1.3
    total_height = len(info_lines) * line_height
    base_y = height / 2 - total_height / 2

    text_r, text_g, text_b = self.template.text_color
    intro_font_size = math.floor(self.base_font_size * 0.9)
    margin = self.get_margin(draw, intro_font_size)
    max_width = width - margin * 2

    for i, line in enumerate(info_lines):
        fitted_size = self.fit_text(draw, line, intro_font_size, max_width)
        font = self._get_font(fitted_size)
        fill_color = (
            int(text_r * alpha / 255),
            int(text_g * alpha / 255),
            int(text_b * alpha / 255),
        )
        y_pos = base_y + i * line_height + line_height / 2
        draw.text(
            (width // 2, int(y_pos)),
            line,
            fill=fill_color,
            font=font,
            anchor="mm",
        )

    return alpha
```

### Step 10: Refactor `render_lyrics()` to use `_compute_last_lyric_fade_alpha()`

**File:** `services/render-worker/src/sow_render_worker/frame_renderer.py`

Replace the inline fade computation in `render_lyrics()` with a call to `_compute_last_lyric_fade_alpha()`. The method still does the actual rendering, but delegates fade alpha computation:

```python
def render_lyrics(
    self,
    song_lyrics: list[GlobalLRCLine],
    current_time: float,
    current_title: str,
    draw: ImageDraw.ImageDraw,
    width: int,
    height: int,
) -> None:
    current_index = -1
    for i, line in enumerate(song_lyrics):
        if line.global_time_seconds <= current_time:
            current_index = i
        else:
            break

    last_lyric_time = song_lyrics[-1].global_time_seconds
    if current_index == -1 and current_time > last_lyric_time:
        current_index = len(song_lyrics) - 1

    if current_index < 0:
        return

    current_line = song_lyrics[current_index]
    is_last_lyric = current_index == len(song_lyrics) - 1

    fade_alpha = self._compute_last_lyric_fade_alpha(song_lyrics, current_time, current_index)
    is_last_lyric_faded = is_last_lyric and fade_alpha <= 0

    if is_last_lyric_faded:
        return

    highlight_r, highlight_g, highlight_b = self.template.highlight_color
    current_font_size_target = self.base_font_size * 2
    margin = self.get_margin(draw, current_font_size_target)
    current_font_size = self.fit_text(
        draw, current_line.text, current_font_size_target, width - margin * 2
    )
    font = self._get_font(current_font_size)
    fill_color = (
        int(highlight_r * fade_alpha / 255),
        int(highlight_g * fade_alpha / 255),
        int(highlight_b * fade_alpha / 255),
    )
    y = int(height * 0.33)
    draw.text(
        (width // 2, y),
        current_line.text,
        fill=fill_color,
        font=font,
        anchor="mt",
    )

    if not is_last_lyric_faded:
        next_index = current_index + 1
        if next_index < len(song_lyrics):
            next_line = song_lyrics[next_index]

            next_alpha = 128
            if is_last_lyric and fade_alpha < 255:
                fade_progress = 1.0 - fade_alpha / 255.0
                next_alpha = math.floor(128 * (1 - fade_progress))

            text_r, text_g, text_b = self.template.text_color
            next_font_size_target = self.base_font_size
            next_margin = self.get_margin(draw, next_font_size_target)
            next_font_size = self.fit_text(
                draw,
                next_line.text,
                next_font_size_target,
                width - next_margin * 2,
            )
            next_font = self._get_font(next_font_size)
            next_fill_color = (
                int(text_r * next_alpha / 255),
                int(text_g * next_alpha / 255),
                int(text_b * next_alpha / 255),
            )
            next_y = int(height * 0.33 + 200)
            draw.text(
                (width // 2, next_y),
                next_line.text,
                fill=next_fill_color,
                font=next_font,
                anchor="mt",
            )
```

### Step 11: Update `video_engine.py` to use `render_frame_bytes()`

**File:** `services/render-worker/src/sow_render_worker/video_engine.py`

Change the encoding loop (lines 343-350) from:

```python
current_time = frame_count / self.fps
t0 = time.monotonic_ns()
img = self.frame_renderer.render_frame(lyrics, segments, current_time)
render_total_ns += time.monotonic_ns() - t0

t0 = time.monotonic_ns()
frame_bytes = img.tobytes()
tobytes_total_ns += time.monotonic_ns() - t0
```

To:

```python
current_time = frame_count / self.fps
t0 = time.monotonic_ns()
frame_bytes = self.frame_renderer.render_frame_bytes(lyrics, segments, current_time)
render_total_ns += time.monotonic_ns() - t0
```

Remove `tobytes_total_ns` tracking entirely — `tobytes` cost is now folded into `render_frame_bytes()`. Update the timing breakdown logs (lines 393-404, 417-438) to remove `tobytes` references.

### Step 12: Add periodic cache stats logging

**File:** `services/render-worker/src/sow_render_worker/video_engine.py`

Add cache stats to the existing periodic logging (every 5 seconds of video, lines 393-404):

```python
if frame_count > 0 and frame_count % (self.fps * 5) == 0:
    elapsed_so_far = time.monotonic_ns() - ffmpeg_start_ns
    cache_info = ""
    if self.frame_renderer and self.frame_renderer._cache_enabled:
        stats = self.frame_renderer.get_cache_stats()
        hit_rate = stats["hits"] / max(1, stats["hits"] + stats["misses"]) * 100
        cache_info = (
            f", cache={stats['entries']}entries/"
            f"{stats['hits']}hits/{stats['misses']}misses ({hit_rate:.0f}%)"
        )
    logger.info(
        "[%s] Encoding breakdown at frame %d/%d: "
        "render=%.1fs (%.1f%%), pipe_write=%.1fs (%.1f%%)%s",
        job_id or "unknown",
        frame_count, total_frames,
        render_total_ns / 1e9,
        render_total_ns / elapsed_so_far * 100,
        write_total_ns / 1e9,
        write_total_ns / elapsed_so_far * 100,
        cache_info,
    )
```

Also log final cache stats in the `finally` block (after line 416):

```python
if self.frame_renderer and self.frame_renderer._cache_enabled:
    stats = self.frame_renderer.get_cache_stats()
    hit_rate = stats["hits"] / max(1, stats["hits"] + stats["misses"]) * 100
    logger.info(
        "[%s] Frame cache final: %d entries, %d hits, %d misses (%.1f%% hit rate), max=%d",
        job_id or "unknown",
        stats["entries"], stats["hits"], stats["misses"], hit_rate, stats["max_entries"],
    )
```

### Step 13: Add cache env vars to Lambda deployment config

**File:** `services/render-worker/deploy/aws-up.sh`

Add to `build_env_vars()` (lines 230-241):

```bash
"SOW_FRAME_CACHE_ENABLED":"${SOW_FRAME_CACHE_ENABLED:-true}",\
"SOW_FADE_ALPHA_STEPS":"${SOW_FADE_ALPHA_STEPS:-16}",\
"SOW_MAX_CACHE_ENTRIES":"${SOW_MAX_CACHE_ENTRIES:-300}"
```

**File:** `services/render-worker/docker-compose.yml`

Add to environment section:

```yaml
SOW_FRAME_CACHE_ENABLED: ${SOW_FRAME_CACHE_ENABLED:-true}
SOW_FADE_ALPHA_STEPS: ${SOW_FADE_ALPHA_STEPS:-16}
SOW_MAX_CACHE_ENTRIES: ${SOW_MAX_CACHE_ENTRIES:-300}
```

### Step 14: Update Lambda memory to 3072 MB

**File:** `services/render-worker/deploy/aws-up.sh`

Change line 223:
```
--memory-size 3072
```

**File:** `services/render-worker/docker-compose.yml`

Change line 10:
```yaml
AWS_LAMBDA_FUNCTION_MEMORY_SIZE: "3072"
```

### Step 15: Update tests

**File:** `services/render-worker/tests/test_frame_renderer.py`

Add new test classes:

1. **`TestVisualState`** — test `_resolve_visual_state()` returns correct state for various time positions (intro, lyrics, fade, segment transitions)
2. **`TestQuantizeAlpha`** — test alpha quantization edge cases (0, 255, boundary values, different step sizes)
3. **`TestFrameCache`** — test cache hit/miss behavior, LRU eviction, `render_frame_bytes()` returns correct bytes
4. **`TestCacheKeyDeterminism`** — verify same visual state produces same cache key; different states produce different keys
5. **`TestCacheDisabled`** — verify that with `SOW_FRAME_CACHE_ENABLED=false`, no caching occurs
6. **`TestFrameCachePerformance`** — benchmark: cached calls significantly faster than uncached
7. **Update `TestRenderFrame`** — verify `render_frame()` still returns `Image.Image`; verify `render_frame_bytes()` returns bytes identical to `render_frame().tobytes()`

**File:** `services/render-worker/tests/test_video_engine.py`

8. **Update `TestEncodeVideoWithFFmpeg`** — verify encoding still works with `render_frame_bytes()` (existing tests should pass since the cache is internal)

### Step 16: Add performance benchmark test

**File:** `services/render-worker/tests/test_frame_renderer.py`

```python
class TestFrameCachePerformance:
    def test_cache_speedup(self):
        renderer = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        lyrics = _make_lyrics([(5.0, "讚美之泉"), (10.0, "哈利路亞")])
        segments = [_make_segment(start=0.0, duration=60.0)]

        renderer.render_frame_bytes(lyrics, segments, 7.0)

        import time
        start = time.monotonic()
        for _ in range(100):
            renderer.render_frame_bytes(lyrics, segments, 7.0)
        cached_elapsed = time.monotonic() - start

        renderer_uncached = FrameRenderer(template=VIDEO_TEMPLATES["dark"])
        renderer_uncached._cache_enabled = False
        start = time.monotonic()
        for _ in range(100):
            renderer_uncached.render_frame_bytes(lyrics, segments, 7.0)
        uncached_elapsed = time.monotonic() - start

        assert cached_elapsed < uncached_elapsed
```

---

## Detailed Cache Key Design

The cache key is a tuple of these components:

| Component | Type | Description | Example |
|-----------|------|-------------|---------|
| `segment_id` | `str` | Which segment/song we're in | `"seg1"` |
| `current_title` | `str` | Song title (for title rendering) | `"讚美之泉"` |
| `current_lyric_index` | `int` | Index of current lyric line (-1 = none) | `3` |
| `quantized_intro_alpha` | `int` | Intro info alpha, quantized | `192` |
| `quantized_fade_alpha` | `int` | Last lyric fade alpha, quantized | `255` |
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
- `current_time` — its visual consequences are already captured by the other components

---

## Alpha Quantization Analysis

With `FADE_ALPHA_STEPS = 16` (default), each step covers 16 alpha values:

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

**Tuning:** If banding is visible, set `SOW_FADE_ALPHA_STEPS=32` for smoother fades. This doubles fade cache entries from ~16 to ~32 per fade event — negligible memory impact (~32 × 8.3MB × 2 fade events ≈ 530MB additional at most).

---

## LRU Eviction Behavior

The cache uses `collections.OrderedDict` as an LRU:

- **On cache hit:** `move_to_end(key)` — most recently used entry moved to end
- **On cache miss + store:** Entry added at end
- **On eviction:** `popitem(last=False)` — evicts least recently used entry (front of dict)

**Expected behavior for typical renders:**

- Lyric frames are accessed for many consecutive frames → always at end of LRU → never evicted
- Fade frames are accessed briefly then not again → may be evicted if cache is full, but they're small in number
- In practice, with 300 max entries and ~232 typical entries, eviction should never trigger for normal renders

**When eviction triggers:**

- Pathological inputs (very long videos, rapid lyrics) may exceed 300 entries
- Eviction is safe — evicted entries are simply re-rendered on next access (cache miss)
- The only cost is temporary performance degradation, not correctness issues

---

## Edge Cases

| Edge Case | Handling |
|-----------|----------|
| No lyrics / no segments | Cache key = `("", "", -1, 0, 255, False)` — all frames identical, 100% cache hit after first |
| Title card frames | Already cached in `VideoEngine` — `render_frame_bytes()` never called for title card frames |
| Segment transitions | Different `segment_id` → different cache key → cache miss (correct, visual changes) |
| Very short fades (<1s) | Quantization may reduce to 1-2 steps — acceptable, fade is barely visible |
| Multiple songs | Cache naturally handles — different segments have different keys |
| Lambda container reuse | `FrameRenderer` created fresh per `VideoEngine` per render job — no stale cache risk |
| Cache disabled (`SOW_FRAME_CACHE_ENABLED=false`) | `render_frame_bytes()` renders every frame, no cache lookup/store |
| Cache full (eviction) | LRU evicts oldest entry; correctness unaffected, only performance |
| `SOW_FADE_ALPHA_STEPS=2` | Minimal quantization (only 0, 128, 255) — coarse but functional for testing |
| `SOW_MAX_CACHE_ENTRIES` too small | Frequent evictions → lower hit rate → slower but still correct |

---

## Memory Budget (3GB Lambda)

| Component | Memory |
|-----------|--------|
| Python runtime + libraries | ~100 MB |
| FFmpeg subprocess + buffers | ~50-100 MB |
| Audio files on disk (/tmp) | ~50-100 MB |
| LRC data structures | ~5 MB |
| Frame cache (300 entries × 8.3MB) | ~2,490 MB |
| **Total worst case** | **~2,745 MB** |
| **Lambda limit** | **3,072 MB** |
| **Headroom** | **~327 MB** |

If headroom is insufficient in practice, reduce `SOW_MAX_CACHE_ENTRIES` to 250 (~2,075 MB cache, ~525 MB headroom).

---

## Files Changed

| File | Change | Lines |
|------|--------|-------|
| `frame_renderer.py` | Add `VisualState` dataclass | ~10 |
| `frame_renderer.py` | Add env var helpers + constants | ~25 |
| `frame_renderer.py` | Add `_frame_cache`, config, stats to `__init__` | ~15 |
| `frame_renderer.py` | Add `_quantize_alpha()` method | ~5 |
| `frame_renderer.py` | Add `_compute_intro_alpha()` method | ~30 |
| `frame_renderer.py` | Add `_compute_last_lyric_fade_alpha()` method | ~20 |
| `frame_renderer.py` | Add `_resolve_visual_state()` method | ~50 |
| `frame_renderer.py` | Add `_compute_cache_key()` method | ~12 |
| `frame_renderer.py` | Add `render_frame_bytes()` method | ~20 |
| `frame_renderer.py` | Refactor `render_frame()` → `render_frame()` + `_render_frame_impl()` | ~15 |
| `frame_renderer.py` | Refactor `render_intro_info()` to use `_compute_intro_alpha()` | ~5 |
| `frame_renderer.py` | Refactor `render_lyrics()` to use `_compute_last_lyric_fade_alpha()` | ~10 |
| `frame_renderer.py` | Add `clear_cache()`, `get_cache_stats()` methods | ~12 |
| `video_engine.py` | Switch to `render_frame_bytes()`, remove `tobytes` tracking | ~10 |
| `video_engine.py` | Add periodic + final cache stats logging | ~20 |
| `aws-up.sh` | Add cache env vars, increase memory to 3072 | ~5 |
| `docker-compose.yml` | Add cache env vars, update memory | ~4 |
| `test_frame_renderer.py` | Add 7 new test classes + update existing | ~180 |
| `test_video_engine.py` | Update for `render_frame_bytes()` | ~10 |
| **Total** | | **~448** |

---

## Rollout Plan

1. Implement Steps 1-12 (core caching logic + refactoring)
2. Run existing test suite — all tests must pass unchanged
3. Add new tests (Steps 15-16)
4. Update Lambda memory to 3072 MB (Step 14) — deploy infra change first
5. Run a manual render test with a 4-song songset to verify:
   - Output video is pixel-identical to uncached render (compare frame-by-frame)
   - Cache hit rate is >95% for lyric frames
   - Total render time is reduced by ~3-5x
   - Memory usage stays within 3GB Lambda limit
6. Deploy to dev environment and run end-to-end render test
7. Deploy to production with `SOW_FRAME_CACHE_ENABLED=true`, `SOW_FADE_ALPHA_STEPS=16`, `SOW_MAX_CACHE_ENTRIES=300`
8. Monitor CloudWatch logs for cache stats — verify hit rates and memory in production

---

## Future Optimizations (Out of Scope)

- **Text-element compositing**: Pre-render each unique text as a small RGBA image and composite onto background. Would reduce memory from ~2.5GB to ~50MB but adds significant complexity.
- **Background pre-rendering**: Cache the background image (solid color) separately and only composite text layers. Marginal gain since background creation is already fast.
- **FFmpeg filter-based approach**: Use FFmpeg's `drawtext` filter instead of PIL for text rendering. Would eliminate the Python rendering loop entirely but requires significant refactoring and loses the flexibility of PIL-based rendering.
- **GPU acceleration**: Use CUDA/OpenCL for compositing. Overkill given the cache hit rate makes compositing negligible.
