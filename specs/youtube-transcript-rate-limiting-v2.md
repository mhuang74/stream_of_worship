# YouTube Transcript API Rate Limiting — Revised v2

> **Status**: Revised plan incorporating code-review findings and clarifications.  
> **Original**: `specs/youtube-transcript-rate-limiting.md`

## Summary

Add application-level rate limiting, retry with exponential backoff, and a circuit breaker to the YouTube transcript fetch path in the Analysis Service. This prevents HTTP 429 (Too Many Requests) errors when many LRC jobs simultaneously attempt to fetch YouTube transcripts.

## Problem

When the Analysis Service processes a large batch of LRC jobs (e.g., 354 simultaneously), every job on the YouTube transcript path hits the YouTube API with **zero throttling**:

- LRC jobs using the YouTube transcript path acquire **no semaphore** — only the local-model/Whisper path acquires `_local_model_semaphore` (`queue.py:166`).
- `fetch_youtube_transcript()` (`youtube_transcript.py:331` and `youtube_transcript.py:340`) has **no retry, backoff, or rate limiting** when running direct (no proxy). The only fallback is Phase 1 (direct fetch) → Phase 2 (list fallback) → failure.
- The `youtube-transcript-api` library's `retries_when_blocked` only activates when a rotating proxy is configured (`SOW_YOUTUBE_PROXY` set). When running direct, a single 429 → Phase-2 list fallback → failure.
- When YouTube transcript fetch fails (429), each job falls back to the more expensive Whisper/Qwen3 ASR path, which cascades load onto the local model semaphore and DashScope API.

### Error Evidence

Production logs show:

```
LRC[queued:0,processing:354,completed:0,failed:0]
STEM_SEPARATION[queued:0,processing:353,completed:0,failed:0]
```

```
ERROR - [job_c6f104ec106d] Direct fetch failed for 7JDohpxeT4I, trying list fallback:
HTTPSConnectionPool(host='www.youtube.com', port=443): Max retries exceeded with url:
/api/timedtext?v=7JDohpxeT4I&... (Caused by ResponseError('too many 429 error responses'))
```

### Existing Rate Limiting Patterns in the Codebase

| Subsystem | Retry | Backoff | Circuit Breaker | Concurrency Limit |
|---|---|---|---|---|
| Qwen3 ASR (`qwen3_asr_client.py:211-225`) | 3 attempts | Exponential (1s/2s/4s) | Permanent (process restart) | `_dashscope_asr_semaphore` (2) |
| MVSEP (`stem_separation.py`) | 3-6 attempts | Jittered exponential (5-300s) | Permanent disable + daily cap | None (cloud) |
| LLM alignment (`lrc.py:556-675`) | 3 attempts | None (immediate) | None | None |
| Embeddings (`queue.py:169`) | None | None | None | `_embedding_semaphore` (5) |
| **YouTube transcript** | **None** | **None** | **None** | **None** |

## Solution

Add a `_YouTubeRateLimiter` class (module-level singleton in `youtube_transcript.py`) that wraps all YouTube transcript API calls with four layers of protection:

1. **Concurrency semaphore** — limits how many jobs can access the YouTube API simultaneously
2. **Min-interval rate limiting** — ensures a minimum time gap between consecutive API calls
3. **Retry with exponential backoff + jitter** on 429 errors
4. **Circuit breaker** — after N consecutive 429 failures, blocks all YouTube API access for a cooldown period (auto-recovery), allowing earlier fallback to Whisper/Qwen3

### Design Principles

- **Self-contained**: The rate limiter lives in `youtube_transcript.py` (the module that owns YouTube API interaction), not in the Queue or LRC worker. No function signature changes outside the module.
- **Lazy initialization**: Semaphore and lock are created on first use within the event loop (matching the pattern at `queue.py:166-173`).
- **429-only retry**: Non-429 errors (e.g., "Subtitles are disabled for this video") propagate immediately without retry.
- **Auto-recovering circuit breaker**: Unlike Qwen3 ASR (permanent until restart) and MVSEP (permanent disable), the YouTube circuit breaker auto-recovers after a cooldown period, since YouTube rate limits are transient.
- **Configurable**: All parameters are configurable via `SOW_*` env vars, following the existing pydantic-settings pattern.

## Files to Modify

### 1. `src/sow_analysis/config.py`

Add 6 new settings **after** `SOW_YOUTUBE_PROXY_RETRIES`:

```python
# YouTube Transcript Rate Limiting
SOW_YOUTUBE_TRANSCRIPT_MAX_CONCURRENT: int = 1
# Maximum concurrent YouTube transcript API calls (semaphore).
# Default 1 (conservative — prevents IP-level rate limiting from YouTube).
# Increase to 2-3 if using a rotating proxy with multiple IPs.
# Set to 0 to disable the rate limiter entirely (not recommended).

SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS: float = 3.0
# Minimum seconds between consecutive YouTube API calls (global throttle).
# With max_concurrent=1, this caps throughput at 1/min_interval requests per second.
# Default 3.0 = ~20 requests/minute. Lower to 2.0 for ~30 req/min if
# using a rotating proxy with good IP diversity.

SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES: int = 3
# Retry attempts per YouTube API call on HTTP 429 (rate limited).
# Each retry uses exponential backoff with jitter.

SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY: float = 5.0
# Base delay in seconds for exponential backoff on 429 retries.
# Actual delay: min(base * 2^attempt, 60) + jitter(0-25%).
# With base=5: 5s, 10s, 20s (capped at 60s).

SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD: int = 5
# Number of consecutive 429 failures before the circuit breaker opens.
# When open, all YouTube transcript fetches are skipped immediately
# (jobs fall back to Whisper/Qwen3 ASR without hitting YouTube).

SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN: int = 120
# Seconds before the circuit breaker auto-recovers (closes).
# During cooldown, YouTube transcript fetches are skipped.
# After cooldown, the next fetch attempt is allowed (and resets the breaker if successful).
```

Add a field validator:

```python
@field_validator("SOW_YOUTUBE_TRANSCRIPT_MAX_CONCURRENT")
@classmethod
def _validate_youtube_transcript_concurrent(cls, v: int) -> int:
    """Ensure YouTube transcript concurrency is at least 0 (0 = disabled)."""
    if v < 0:
        raise ValueError("SOW_YOUTUBE_TRANSCRIPT_MAX_CONCURRENT must be >= 0 (0 disables rate limiting)")
    return v
```

> **Note on `MAX_CONCURRENT=0`**: When set to 0, the semaphore is bypassed entirely (no concurrency limit), but min-interval and retry/circuit-breaker logic still apply. This provides an emergency override while preserving the other protections.

### 2. `src/sow_analysis/workers/youtube_transcript.py`

#### New imports

```python
import random
```

Also update the existing `typing` import to include `Callable`:

```python
from typing import Any, Callable, List, Optional
```

> **Why `Callable`?** It is used in the `_YouTubeRateLimiter.call()` method signature. It is not currently imported in `youtube_transcript.py`.

#### 429 error detection helper

```python
def _is_rate_limited_error(e: Exception) -> bool:
    """Check if an exception is caused by YouTube rate limiting (HTTP 429).

    Tries three detection strategies in order:
    1. Check for HTTP 429 status_code on urllib3/requests exceptions.
    2. Check the exception string and its __cause__ chain for "429".
    3. Fall back to False (non-429 error — do not retry).

    The youtube-transcript-api library (via urllib3) raises errors like:
        ResponseError('too many 429 error responses')

    Strategy 1 is more robust; strategy 2 is a pragmatic fallback.
    """
    # Strategy 1: Check for status_code attribute (urllib3 / requests)
    exc = e
    while exc is not None:
        if hasattr(exc, "status_code") and exc.status_code == 429:
            return True
        if hasattr(exc, "code") and exc.code == 429:
            return True
        exc = getattr(exc, "__cause__", None)

    # Strategy 2: String-based fallback
    error_str = str(e)
    if "429" in error_str:
        return True
    cause = e.__cause__
    while cause is not None:
        if "429" in str(cause):
            return True
        cause = cause.__cause__
    return False
```

> **Why the status_code check?** Review feedback noted that string-based "429" detection could theoretically have false positives. Adding an explicit `status_code` check makes the classifier more robust without adding heavy dependencies.

#### `_YouTubeRateLimiter` class

```python
class _YouTubeRateLimiter:
    """Module-level rate limiter for YouTube transcript API calls.

    Provides four layers of protection against YouTube API rate limiting:
    1. Concurrency semaphore (limits simultaneous API calls)
    2. Min-interval throttle (ensures spacing between requests)
    3. Retry with exponential backoff + jitter on HTTP 429
    4. Circuit breaker with auto-recovery after cooldown

    Lazily initializes asyncio primitives on first use (within the event loop).
    """

    def __init__(self):
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._interval_lock: Optional[asyncio.Lock] = None
        self._last_request_time: float = 0.0
        self._consecutive_429_count: int = 0
        self._circuit_open_until: float = 0.0  # monotonic time

    def _ensure_initialized(self) -> None:
        """Lazily initialize asyncio primitives on first use."""
        if self._semaphore is None:
            max_concurrent = settings.SOW_YOUTUBE_TRANSCRIPT_MAX_CONCURRENT
            # MAX_CONCURRENT=0 disables the semaphore entirely
            if max_concurrent > 0:
                self._semaphore = asyncio.Semaphore(max_concurrent)
            self._interval_lock = asyncio.Lock()

    def _is_circuit_open(self) -> bool:
        """Check if the circuit breaker is currently open."""
        return time.monotonic() < self._circuit_open_until

    def _open_circuit(self) -> None:
        """Open the circuit breaker for the configured cooldown period."""
        cooldown = settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN
        self._circuit_open_until = time.monotonic() + cooldown
        logger.warning(
            "YouTube transcript circuit breaker OPENED after %d consecutive 429s, "
            "cooldown %ds — all YouTube transcript fetches will skip to fallback",
            self._consecutive_429_count,
            cooldown,
        )

    def _reset_circuit(self) -> None:
        """Reset the circuit breaker on a successful request."""
        if self._consecutive_429_count > 0:
            self._consecutive_429_count = 0
            self._circuit_open_until = 0.0
            logger.info("YouTube transcript circuit breaker RESET after successful request")

    async def _enforce_min_interval(self) -> None:
        """Sleep if the last request was too recent (min-interval throttle).

        Uses an asyncio.Lock to serialize the timestamp check so that
        concurrent callers are spaced out correctly.
        """
        async with self._interval_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            min_interval = settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS
            if elapsed < min_interval:
                wait = min_interval - elapsed
                logger.debug(
                    "YouTube rate limit: spacing request, sleeping %.2fs", wait
                )
                await asyncio.sleep(wait)
            self._last_request_time = time.monotonic()

    async def call(self, fn: Callable, *, description: str = "") -> Any:
        """Execute a YouTube API call through the rate limiter.

        Args:
            fn: Synchronous callable that performs the YouTube API call.
                Will be run in the default executor.
            description: Human-readable description for logging.

        Returns:
            The result of fn().

        Raises:
            YouTubeTranscriptError: If the circuit breaker is open, or if all
                retries are exhausted on 429, or if fn() raises a non-429 error.
        """
        self._ensure_initialized()

        # Layer 1: Circuit breaker check
        if self._is_circuit_open():
            remaining = self._circuit_open_until - time.monotonic()
            raise YouTubeTranscriptError(
                f"YouTube transcript circuit breaker is open "
                f"(cooldown: {remaining:.0f}s remaining) — skipping {description}"
            )

        loop = asyncio.get_running_loop()
        max_retries = settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES
        base_delay = settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY
        threshold = settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD

        for attempt in range(max_retries + 1):
            # Layer 2: Concurrency semaphore + Layer 3: Min-interval throttle
            if self._semaphore is not None:
                async with self._semaphore:
                    await self._enforce_min_interval()
                    try:
                        result = await loop.run_in_executor(None, fn)
                        # Success — reset circuit breaker
                        self._reset_circuit()
                        return result
                    except Exception as e:
                        if not _is_rate_limited_error(e):
                            # Non-429 error — don't retry, propagate immediately
                            raise

                        # Layer 4: 429 retry with backoff
                        self._consecutive_429_count += 1
                        logger.warning(
                            "YouTube API rate limited (429), attempt %d/%d for %s: %s",
                            attempt + 1,
                            max_retries + 1,
                            description,
                            e,
                        )

                        # Check if circuit breaker should open
                        if self._consecutive_429_count >= threshold:
                            self._open_circuit()
                            raise YouTubeTranscriptError(
                                f"YouTube API rate limited — circuit breaker opened "
                                f"after {self._consecutive_429_count} consecutive 429s: {e}"
                            ) from e

                        # Retry with exponential backoff + jitter (if attempts remain)
                        if attempt < max_retries:
                            delay = min(base_delay * (2 ** attempt), 60.0)
                            delay += random.uniform(0, delay * 0.25)
                            logger.info(
                                "Retrying YouTube API call for %s in %.1fs",
                                description,
                                delay,
                            )
                            await asyncio.sleep(delay)
                            continue

                        # All retries exhausted
                        raise YouTubeTranscriptError(
                            f"YouTube API rate limited after {max_retries + 1} "
                            f"attempts for {description}: {e}"
                        ) from e
            else:
                # MAX_CONCURRENT=0: no semaphore, but still enforce min-interval
                await self._enforce_min_interval()
                try:
                    result = await loop.run_in_executor(None, fn)
                    self._reset_circuit()
                    return result
                except Exception as e:
                    if not _is_rate_limited_error(e):
                        raise

                    self._consecutive_429_count += 1
                    logger.warning(
                        "YouTube API rate limited (429), attempt %d/%d for %s: %s",
                        attempt + 1,
                        max_retries + 1,
                        description,
                        e,
                    )

                    if self._consecutive_429_count >= threshold:
                        self._open_circuit()
                        raise YouTubeTranscriptError(
                            f"YouTube API rate limited — circuit breaker opened "
                            f"after {self._consecutive_429_count} consecutive 429s: {e}"
                        ) from e

                    if attempt < max_retries:
                        delay = min(base_delay * (2 ** attempt), 60.0)
                        delay += random.uniform(0, delay * 0.25)
                        logger.info(
                            "Retrying YouTube API call for %s in %.1fs",
                            description,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        continue

                    raise YouTubeTranscriptError(
                        f"YouTube API rate limited after {max_retries + 1} "
                        f"attempts for {description}: {e}"
                    ) from e

        # Should not reach here, but safety net
        raise YouTubeTranscriptError(
            f"YouTube API rate limiter exhausted all retries for {description}"
        )
```

> **Why `asyncio.get_running_loop()` instead of `get_event_loop()`?** The existing `youtube_transcript.py` uses `get_event_loop()`, but `get_running_loop()` is the modern, recommended API. The spec intentionally upgrades this call for correctness while leaving the other two `get_event_loop()` calls in `_llm_correct()` unchanged.

#### Module-level singleton

```python
_rate_limiter = _YouTubeRateLimiter()
```

#### Integration into `fetch_youtube_transcript()`

Replace the two `loop.run_in_executor(None, ...)` calls with `_rate_limiter.call(...)`.

Because `loop` was only used for `run_in_executor`, the `loop` variable should also be removed from `fetch_youtube_transcript()`.

**Remove this line** from `fetch_youtube_transcript()`:
```python
    loop = asyncio.get_event_loop()
```

**Phase 1 (direct fetch)** — replace:
```python
        transcript = await loop.run_in_executor(None, _fetch_direct)
```
with:
```python
        transcript = await _rate_limiter.call(
            _fetch_direct, description=f"direct fetch for {video_id}"
        )
```

**Phase 2 (list fallback)** — replace:
```python
        transcript = await loop.run_in_executor(None, _fetch_via_list)
```
with:
```python
        transcript = await _rate_limiter.call(
            _fetch_via_list, description=f"list fallback for {video_id}"
        )
```

The existing try/except structure around each phase remains unchanged. When the circuit breaker is open, `_rate_limiter.call()` raises `YouTubeTranscriptError` immediately, which is caught by the Phase 1 except block and falls through to Phase 2. If Phase 2 also raises (circuit still open), the outer except wraps it and the job falls back to Whisper/Qwen3 ASR.

### 3. `.env.example`

Add a new section **after** the existing "YouTube Proxy Configuration" section (after `SOW_YOUTUBE_PROXY_RETRIES`, before `# Docker Build Configuration`):

```bash
# ========================================
# YouTube Transcript Rate Limiting (Optional)
# ========================================

SOW_YOUTUBE_TRANSCRIPT_MAX_CONCURRENT=1
# Maximum concurrent YouTube transcript API calls (default: 1).
# Conservative default prevents IP-level rate limiting from YouTube.
# Increase to 2-3 if using a rotating proxy with multiple IPs.
# Set to 0 to disable concurrency limiting entirely (not recommended).

SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS=3.0
# Minimum seconds between consecutive YouTube API calls (default: 3.0).
# Global throttle that caps request rate regardless of concurrency.
# With max_concurrent=1, this limits throughput to ~20 requests/minute.
# Lower to 2.0 if using a rotating proxy with good IP diversity.

SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES=3
# Retry attempts per YouTube API call on HTTP 429 (default: 3).
# Each retry uses exponential backoff with jitter.

SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY=5.0
# Base delay in seconds for exponential backoff on 429 retries (default: 5.0).
# Actual delay: min(base * 2^attempt, 60) + jitter(0-25%).
# With base=5.0: ~5s, ~10s, ~20s (capped at 60s).

SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD=5
# Consecutive 429 failures before circuit breaker opens (default: 5).
# When open, YouTube transcript fetches are skipped immediately,
# and jobs fall back to Whisper/Qwen3 ASR without hitting YouTube.

SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN=120
# Seconds before circuit breaker auto-recovers (default: 120).
# During cooldown, YouTube transcript fetches are skipped.
# After cooldown, the next fetch attempt is allowed.
```

### 4. `docker-compose.yml`

Add 6 new env vars to the `&common-env` YAML anchor, **after** `SOW_YOUTUBE_PROXY_RETRIES`:

```yaml
  # YouTube Transcript Rate Limiting
  SOW_YOUTUBE_TRANSCRIPT_MAX_CONCURRENT: ${SOW_YOUTUBE_TRANSCRIPT_MAX_CONCURRENT:-1}
  SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS: ${SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS:-3.0}
  SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES: ${SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES:-3}
  SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY: ${SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY:-5.0}
  SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD: ${SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD:-5}
  SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN: ${SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN:-120}
```

### 5. `tests/test_youtube_transcript.py`

Add a `TestYouTubeRateLimiter` test class:

| Test | Description |
|---|---|
| `test_min_interval_enforced` | Two rapid calls → second call waits the min interval (verify `asyncio.sleep` called with >= min_interval) |
| `test_retries_on_429` | fn raises 429 error on first 2 attempts, succeeds on 3rd → verify retried, result returned |
| `test_circuit_breaker_opens_after_threshold` | N consecutive 429s (N=threshold) → circuit opens, verify `_is_circuit_open()` returns True |
| `test_circuit_breaker_blocks_calls_when_open` | When circuit is open, `call()` raises `YouTubeTranscriptError` immediately without calling fn |
| `test_circuit_breaker_closes_after_cooldown` | After cooldown period elapses (mock `time.monotonic`), calls proceed again |
| `test_success_resets_429_count` | After some 429s, a successful call resets `_consecutive_429_count` to 0 |
| `test_non_429_error_not_retried` | fn raises "Subtitles are disabled" error → no retry, exception propagates immediately |
| `test_concurrency_semaphore_limits_parallel` | Multiple concurrent `call()` invocations → only `max_concurrent` run at once |
| `test_max_concurrent_zero_disables_semaphore` | With MAX_CONCURRENT=0, calls proceed without semaphore but min-interval still enforced |
| `test_status_code_429_detected` | Exception with `.status_code = 429` is detected as rate-limited |

Also update existing `TestFetchYoutubeTranscript` tests to ensure they still pass with the rate limiter integrated (mock API doesn't raise 429, so rate limiter is transparent).

> **Implementation note for tests**: The `_YouTubeRateLimiter` is a module-level singleton (`_rate_limiter`). Tests must either:
> 1. Patch `_rate_limiter` with a fresh instance, or
> 2. Use `pytest` fixtures that reset `_rate_limiter._consecutive_429_count`, `_circuit_open_until`, and `_last_request_time` between tests.

## Behavior with Default Settings

| Parameter | Default | Effect |
|---|---|---|
| `SOW_YOUTUBE_TRANSCRIPT_MAX_CONCURRENT` | 1 | Only 1 YouTube API call at a time |
| `SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS` | 3.0 | ~20 requests/minute max throughput |
| `SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES` | 3 | 3 retries per API call on 429 |
| `SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY` | 5.0 | Backoff: ~5s, ~10s, ~20s |
| `SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD` | 5 | Circuit opens after 5 consecutive 429s |
| `SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN` | 120 | 2-minute cooldown when circuit is open |

### Scenario: 354 LRC jobs queued

1. Job 1 acquires semaphore, fetches YouTube transcript (3s min interval enforced).
2. Jobs 2-354 wait at the semaphore.
3. Each job processes sequentially, 3s apart (~18 minutes for all 354 YouTube fetches).
4. If YouTube starts 429-ing:
   - Each call retries up to 3 times with backoff (~5s, ~10s, ~20s).
   - After 5 consecutive 429s, circuit breaker opens.
   - All remaining jobs skip YouTube immediately → fall back to Whisper/Qwen3 ASR.
   - After 120s cooldown, circuit auto-recovers and the next job retries YouTube.

### Interaction with proxy configuration

When `SOW_YOUTUBE_PROXY` is set, the `youtube-transcript-api` library's own `retries_when_blocked` handles 429s with IP rotation. The application-level rate limiter still applies (concurrency + min-interval + circuit breaker), providing defense-in-depth even with a proxy. The retry logic may overlap with the library's retries, but this is acceptable — the application-level retry catches 429s that slip through the library's retry mechanism.

## Testing

```bash
# Run YouTube transcript tests
cd ops/analysis-service && PYTHONPATH=src pytest tests/test_youtube_transcript.py -v

# Run full analysis service test suite
cd ops/analysis-service && PYTHONPATH=src pytest tests/ -v
```

## Changelog from v1

1. **Added `Callable` to typing imports** — was missing in v1 (`youtube_transcript.py` did not import `Callable`).
2. **Added status-code-based 429 detection** — `_is_rate_limited_error()` now checks `.status_code == 429` before falling back to string matching.
3. **Allowed `MAX_CONCURRENT=0` to disable semaphore** — validator changed from `v < 1` to `v < 0`; `_ensure_initialized()` skips semaphore creation when `max_concurrent == 0`.
4. **Fixed `.env.example` insertion point** — clarified to insert after `SOW_YOUTUBE_PROXY_RETRIES` (end of YouTube Proxy section), not an absolute line number.
5. **Used `asyncio.get_running_loop()`** — upgraded from `get_event_loop()` for modern asyncio correctness.
6. **Removed `loop` variable from `fetch_youtube_transcript()`** — it is no longer needed after replacing `run_in_executor` calls.
7. **Added two new test cases**: `test_max_concurrent_zero_disables_semaphore` and `test_status_code_429_detected`.
8. **Added test reset guidance** — noted that module-level singleton requires per-test state cleanup.
