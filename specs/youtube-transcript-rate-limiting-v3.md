# YouTube Transcript Rate Limiting — Free-Mode Patient Retry v3

> **Status**: Plan (not yet implemented).
> **Prior**: `specs/youtube-transcript-rate-limiting.md` (v1), `specs/youtube-transcript-rate-limiting-v2.md` (v2 — added the `_YouTubeRateLimiter` circuit breaker).
> **Scope**: Make the YouTube transcript path resilient to IP-level 429s when `SOW_FREE_ONLY_MODE` is on, and fix a breaker-recovery bug.

## Summary

In `SOW_FREE_ONLY_MODE`, the Analysis Service should not abandon the YouTube transcript path after a handful of 429s. The free-mode philosophy ("be patient, wait for quota/rate-limit reset, don't fall back to local ML") currently applies to Qwen3 ASR and MVSEP stem separation but **not** to YouTube transcripts. Additionally, the existing circuit breaker has a recovery bug that can leave it stuck open indefinitely. This plan:

1. Adds a `YouTubeRateLimitedError` subclass to distinguish retryable rate-limit failures from permanent "no transcript" failures.
2. Fixes the circuit breaker so it actually recovers when cooldown expires (resets the 429 counter).
3. Introduces free-mode-aware thresholds, cooldown, retry counts, and min-interval spacing.
4. Adds a free-mode wait loop in `queue.py` Stage 1 that waits for the breaker cooldown (with job heartbeats) and retries YouTube, mirroring the Qwen3 quota-waiter pattern — instead of immediately falling through to Whisper/Qwen3 ASR.

## Problem

### Error Evidence

Production logs (2026-07-11) show 5 different jobs each hitting **one** 429 on the **first attempt** of their direct fetch, then the breaker opening globally:

```
07:26:13 - WARNING - [job_8e68190485a4] YouTube API rate limited (429), attempt 1/4 for direct fetch for hNO5l4mjDHc: ... (Caused by ResponseError('too many 429 error responses'))
07:26:19 - WARNING - [job_7f48bafe4d5f] YouTube API rate limited (429), attempt 1/4 for direct fetch for Za9UdGa-P6g: ...
07:26:26 - WARNING - [job_1dc428f751bb] YouTube API rate limited (429), attempt 1/4 for direct fetch for WQtpV632qyY: ...
07:26:33 - WARNING - [job_d9ee9ef47da8] YouTube API rate limited (429), attempt 1/4 for direct fetch for kszbPoctPbo: ...
07:26:40 - WARNING - [job_4a4e033dbfa2] YouTube API rate limited (429), attempt 1/4 for direct fetch for JsGah6O48ec: ...
07:26:40 - WARNING - [job_4a4e033dbfa2] YouTube transcript circuit breaker OPENED after 5 consecutive 429s, cooldown 120s — all YouTube transcript fetches will skip to fallback
07:26:40 - ERROR   - [job_4a4e033dbfa2] Direct fetch failed for JsGah6O48ec, trying list fallback: YouTube API rate limited — circuit breaker opened after 5 consecutive 429s: ...
07:26:40 - WARNING - [job_4a4e033dbfa2] LRC GENERATION: YouTube transcript path FAILED
07:26:40 - WARNING - [job_4a4e033dbfa2] Reason: YouTube transcript circuit breaker is open (cooldown: 120s remaining) — skipping list fallback for JsGah6O48ec
07:26:40 - WARNING - [job_4a4e033dbfa2] Falling back to LLM-based ASR...
07:26:47 - WARNING - [job_133b72b7ea33] YouTube transcript circuit breaker OPENED after 6 consecutive 429s, cooldown 120s — all YouTube transcript fetches will skip to fallback
```

### Root-Cause Analysis

1. **Global singleton breaker, cross-video counter.** `_rate_limiter` (`youtube_transcript.py:267`) is a module-level singleton. `_consecutive_429_count` is global — not per-video. The queue processes many LRC jobs concurrently; their YouTube fetches are serialized by the semaphore (max_concurrent=1) and 3s min-interval, but each 429 still pumps the same global counter. Five first-attempt 429s from five different videos trips the breaker for **all** videos.

2. **SOW_FREE mode is oblivious to YouTube transcripts.** `SOW_FREE_ONLY_MODE` only governs Qwen3 ASR (`queue.py:1191`) and MVSEP (`stem_separation.py:278`). The YouTube path never reads it. So in free mode, a 120s YouTube cooldown immediately pushes jobs onto the local-Whisper path — the very path free mode is designed to avoid (Whisper needs local GPU).

3. **Circuit breaker cannot recover (bug).** `_consecutive_429_count` is reset **only** on a successful request (`_reset_circuit`, `youtube_transcript.py:141`). Cooldown expiry does **not** reset the counter. After the first trip (count=5), every subsequent 429 — even one — re-trips the breaker instantly (count >= threshold=5). YouTube fetches stay permanently dead until a success occurs, which can't happen because all fetches are skipped during cooldown. The breaker is effectively stuck open for the lifetime of the process once tripped.

4. **List-fallback is gated by the same breaker.** When the direct `fetch()` 429s, the list-fallback (`ytt_api.list()`) is also skipped because it goes through the same `_rate_limiter.call()`. The list endpoint is a different YouTube API surface and may not be rate-limited even when `fetch()` is.

5. **IP-level rate limiting.** The 429s are YouTube IP-level limits. Without `SOW_YOUTUBE_PROXY` (empty by default), all requests come from one IP. The 3s min-interval + max_concurrent=1 already serialize, but YouTube still 429s the spaced-out requests. The robust fix is a rotating proxy; in free mode (no budget for a proxy), the right answer is patience + retry.

## Solution

### Design Principles

- **Consistent with free-mode philosophy.** Free mode waits for quota/rate-limit reset instead of falling back to local ML. Apply the same to YouTube transcripts.
- **Mirror the Qwen3 quota-waiter pattern.** Reuse the proven `queue.py:1190-1216` heartbeat-loop shape for YouTube rate-limit waits.
- **Minimal public surface.** Keep the `_rate_limiter` singleton private; expose a small async helper from `youtube_transcript.py` for the wait loop.
- **Distinguish retryable vs permanent.** A 429 / breaker-open is retryable; "no transcript found" is permanent. Don't wait forever on permanent failures.
- **No new dependencies.** Pure stdlib + existing config patterns.

### Change 1: `YouTubeRateLimitedError` subclass

**File:** `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py` (near line 54)

Add a new error subclass:

```python
class YouTubeRateLimitedError(YouTubeTranscriptError):
    """Raised when YouTube transcript fetch fails due to rate limiting (429)
    or an open circuit breaker. Retryable — caller may wait and retry."""
```

### Change 2: Raise `YouTubeRateLimitedError` from the limiter

**File:** `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py`

Replace the four `raise YouTubeTranscriptError(...)` sites in `_YouTubeRateLimiter.call()` that correspond to rate-limit/breaker conditions with `YouTubeRateLimitedError`:

| Line | Condition | New error |
|---|---|---|
| 186 | Breaker open at entry to `call()` | `YouTubeRateLimitedError` |
| 200 | Breaker re-checked mid-loop | `YouTubeRateLimitedError` |
| 238 | Breaker just opened (count >= threshold) | `YouTubeRateLimitedError` |
| 256 | All retries exhausted on 429 | `YouTubeRateLimitedError` |

Keep `YouTubeTranscriptError` for the "no suitable transcript found" path in `fetch_youtube_transcript` (`youtube_transcript.py:485-568`) — that is a permanent failure, not retryable.

The safety-net raise at line 262 stays as `YouTubeTranscriptError` (should never be reached).

### Change 3: Fix circuit-breaker recovery on cooldown expiry (bug fix)

**File:** `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py:126`

Replace `_is_circuit_open()` so that cooldown expiry resets the 429 counter, allowing the breaker to actually recover:

```python
def _is_circuit_open(self) -> bool:
    """Check if the circuit breaker is currently open.

    If the cooldown has expired, reset the 429 counter so the breaker
    can recover (the next fetch probes YouTube and resets fully on success).
    """
    if self._circuit_open_until == 0.0:
        return False
    if time.monotonic() >= self._circuit_open_until:
        # Cooldown expired — reset so the next 429 doesn't instantly re-trip
        if self._consecutive_429_count > 0:
            logger.info(
                "YouTube transcript circuit breaker cooldown expired — "
                "resetting 429 count (was %d), next fetch will probe YouTube",
                self._consecutive_429_count,
            )
        self._consecutive_429_count = 0
        self._circuit_open_until = 0.0
        return False
    return True
```

This is called from both breaker-check sites (line 184 and line 198) with no other changes needed — the reset happens transparently on the next check after cooldown.

### Change 4: Free-mode threshold/cooldown/retry/min-interval overrides

**File:** `ops/analysis-service/src/sow_analysis/config.py` (after line 227)

Add five new settings fields:

```python
# ── Free-only-mode overrides (patient retry, no local ML fallback) ──
SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD_FREE: int = 10
# Consecutive 429s before breaker opens in free-only mode (vs 5 default).
# Higher because free mode prefers waiting over falling back to local Whisper.

SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN_FREE: int = 300
# Breaker cooldown in seconds in free-only mode (vs 120 default).
# Longer to let YouTube's IP-level rate-limit window pass.

SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES_FREE: int = 5
# Retry attempts per YouTube API call on 429 in free-only mode (vs 3 default).

SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY_FREE: float = 10.0
# Base backoff delay in seconds in free-only mode (vs 5.0 default).
# With base=10: 10s, 20s, 40s, 60s, 60s (capped).

SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS_FREE: float = 15.0
# Min seconds between YouTube API calls in free-only mode (vs 3.0 default).
# More conservative spacing to avoid triggering IP-level 429s.
```

**File:** `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py`

In `_YouTubeRateLimiter.call()` (lines 192-194) and `_enforce_min_interval()` (line 157) and `_open_circuit()` (line 132), select the free-mode value when `settings.SOW_FREE_ONLY_MODE` is on:

```python
# In call():
if settings.SOW_FREE_ONLY_MODE:
    threshold = settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD_FREE
    max_retries = settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES_FREE
    base_delay = settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY_FREE
else:
    threshold = settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD
    max_retries = settings.SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES
    base_delay = settings.SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY

# In _open_circuit():
cooldown = (
    settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN_FREE
    if settings.SOW_FREE_ONLY_MODE
    else settings.SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN
)

# In _enforce_min_interval():
    min_interval = (
        settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS_FREE
        if settings.SOW_FREE_ONLY_MODE
        else settings.SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS
    )
```

where `SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS_FREE = 15.0` (vs 3.0 default).

### Change 5: `try_youtube_transcript_lrc` re-raises `YouTubeRateLimitedError`

**File:** `ops/analysis-service/src/sow_analysis/workers/lrc.py:886-913`

Currently the `try/except (YouTubeTranscriptError, Exception)` at line 907 swallows everything (including rate-limit errors) and returns `None`. Since `YouTubeRateLimitedError` IS-A `YouTubeTranscriptError`, it would be swallowed. Change to re-raise rate-limited errors so `queue.py` can catch them and decide to wait-and-retry (free mode) or fall back (non-free):

```python
from .youtube_transcript import YouTubeRateLimitedError, YouTubeTranscriptError, youtube_transcript_to_lrc

try:
    lrc_lines = await youtube_transcript_to_lrc(...)
    ...
    return output_path, line_count, []
except YouTubeRateLimitedError:
    raise  # let queue.py decide: wait-and-retry (free) or fall back (non-free)
except Exception as e:  # incl. YouTubeTranscriptError("no transcript found")
    logger.warning("LRC GENERATION: YouTube transcript path FAILED")
    logger.warning(f"Reason: {e}")
    logger.warning("Falling back to LLM-based ASR...")
    return None
```

### Change 6: Free-mode wait loop in `queue.py` Stage 1

**File:** `ops/analysis-service/src/sow_analysis/workers/queue.py:1092-1113`

Wrap the existing Stage 1 YouTube call in a retry loop that mirrors the Qwen3 quota-waiter pattern (`queue.py:1190-1216`):

```python
# Stage 1: Try YouTube transcript first — no audio download or stem needed
youtube_lrc_result = None
if request.youtube_url:
    from .lrc import try_youtube_transcript_lrc
    from .youtube_transcript import (
        YouTubeRateLimitedError,
        wait_for_youtube_cooldown_if_open,
    )

    while True:
        await self._update_stage(job, "trying_youtube_transcript", 0.2)
        try:
            youtube_lrc_result = await try_youtube_transcript_lrc(
                request.youtube_url,
                request.lyrics_text,
                request.options,
                lrc_path,
                resolved_language,
            )
            break  # success or permanent failure (None) → exit loop
        except YouTubeRateLimitedError as e:
            if not settings.SOW_FREE_ONLY_MODE:
                # Non-free: don't wait, fall through to Whisper/Qwen3 ASR
                youtube_lrc_result = None
                break
            # Free mode: wait for breaker cooldown with heartbeat, then retry
            await self._update_stage(job, "waiting_for_youtube_rate_limit", 0.2)
            logger.warning(
                "YouTube transcript rate-limited for job %s: %s — waiting for "
                "circuit-breaker cooldown in free-only mode",
                job.id, e,
            )
            while True:
                closed = await wait_for_youtube_cooldown_if_open(
                    max_heartbeat_seconds=60.0,
                    is_cancelled=lambda: job.status == JobStatus.CANCELLED,
                )
                if job.status == JobStatus.CANCELLED:
                    return
                if closed:
                    break
                # Heartbeat: refresh updated_at
                await self._update_stage(job, "waiting_for_youtube_rate_limit", 0.2)
            # Breaker closed — retry YouTube transcript
            continue

    if youtube_lrc_result:
        lrc_path, line_count, whisper_phrases = youtube_lrc_result
        lrc_source = "youtube_transcript"
        job.stage = "youtube_transcript_done"
        logger.info(
            "YouTube transcript succeeded — skipping audio download and stem separation"
        )
```

### Change 7: `wait_for_youtube_cooldown_if_open` helper

**File:** `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py`

Add a small async helper that keeps the `_rate_limiter` singleton private and lets `queue.py` wait without reaching into internals:

```python
async def wait_for_youtube_cooldown_if_open(
    max_heartbeat_seconds: float = 60.0,
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> bool:
    """Block (async) until the YouTube circuit breaker closes, or until cancelled.

    Sleeps in increments of `max_heartbeat_seconds` so the caller can emit
    heartbeats (refresh job.updated_at) between sleeps and check cancellation.

    Args:
        max_heartbeat_seconds: Max seconds to sleep per iteration before
            returning so the caller can heartbeat. Default 60s.
        is_cancelled: Optional callable returning True if the job was cancelled;
            checked between sleeps. If it returns True, this function returns
            immediately with the breaker's current state.

    Returns:
        True when the breaker is closed (cooldown expired or was never open).
        The caller should re-check cancellation before retrying.
    """
    while _rate_limiter.is_circuit_open():
        if is_cancelled is not None and is_cancelled():
            return not _rate_limiter.is_circuit_open()
        remaining = _rate_limiter.remaining_cooldown()
        sleep_for = min(max_heartbeat_seconds, max(remaining, 1.0))
        await asyncio.sleep(sleep_for)
    return True
```

This requires exposing two small accessors on `_YouTubeRateLimiter` (the singleton already tracks the needed state):

```python
def is_circuit_open(self) -> bool:
    """Public accessor — also performs cooldown-expiry reset (see _is_circuit_open)."""
    return self._is_circuit_open()

def remaining_cooldown(self) -> float:
    """Seconds remaining in the current cooldown (0.0 if closed)."""
    if self._circuit_open_until == 0.0:
        return 0.0
    return max(0.0, self._circuit_open_until - time.monotonic())
```

### Change 8: `.env.example` and `docker-compose.yml`

**File:** `ops/analysis-service/.env.example` (near lines 310-318, the free-only-mode docs)

Add the five new env vars with comments documenting the "patient mode" behavior, consistent with the existing Qwen3/MVSEP free-mode docs:

```bash
# ── YouTube transcript free-only-mode overrides ──
# When SOW_FREE_ONLY_MODE=true, these replace the defaults above so the
# service waits through YouTube IP-level 429 cooldowns instead of falling
# back to local Whisper (which needs GPU resources free mode avoids).
SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD_FREE=10
SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN_FREE=300
SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES_FREE=5
SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY_FREE=10.0
SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS_FREE=15.0
```

**File:** `ops/analysis-service/docker-compose.yml` (near line 68)

Add pass-through entries for the five new env vars (matching the existing `SOW_YOUTUBE_TRANSCRIPT_*` pass-through pattern):

```yaml
SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD_FREE: ${SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD_FREE:-10}
SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN_FREE: ${SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN_FREE:-300}
SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES_FREE: ${SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES_FREE:-5}
SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY_FREE: ${SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY_FREE:-10.0}
SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS_FREE: ${SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS_FREE:-15.0}
```

## File Change Summary

| File | Change |
|---|---|
| `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py` | Add `YouTubeRateLimitedError`; replace 4 raise sites; reset counter on cooldown expiry (bug fix); free-mode threshold/retry/cooldown/min-interval selection; add `is_circuit_open()`/`remaining_cooldown()` accessors; add `wait_for_youtube_cooldown_if_open()` helper |
| `ops/analysis-service/src/sow_analysis/workers/lrc.py` | `try_youtube_transcript_lrc` re-raises `YouTubeRateLimitedError` instead of swallowing it |
| `ops/analysis-service/src/sow_analysis/workers/queue.py` | Stage 1 free-mode wait loop with heartbeat, matching Qwen3 quota-waiter pattern |
| `ops/analysis-service/src/sow_analysis/config.py` | Add 5 `*_FREE` settings fields with docs |
| `ops/analysis-service/.env.example` | Document new env vars |
| `ops/analysis-service/docker-compose.yml` | Pass-through for new env vars |
| `ops/analysis-service/tests/` | Breaker recovery on cooldown expiry; `YouTubeRateLimitedError` raised from the right sites; free-mode threshold/cooldown values used; `try_youtube_transcript_lrc` re-raises; free-mode wait loop retries after cooldown closes; non-free mode falls through immediately |

## Testing

```bash
cd ops/analysis-service && uv run --extra dev pytest tests/ -v
```

Test cases to add/extend:

1. **Breaker recovery on cooldown expiry** — simulate 5 consecutive 429s to open the breaker, advance the clock past cooldown, assert `_is_circuit_open()` returns `False` and `_consecutive_429_count` is 0.
2. **`YouTubeRateLimitedError` raised from the right sites** — mock `fn` to raise a 429; assert `call()` raises `YouTubeRateLimitedError` (not `YouTubeTranscriptError`) when breaker opens and when retries are exhausted.
3. **Free-mode threshold/cooldown values** — set `SOW_FREE_ONLY_MODE=true`; assert `call()` uses the `*_FREE` config values (threshold=10, cooldown=300, retries=5, base_delay=10.0, min_interval=8.0).
4. **`try_youtube_transcript_lrc` re-raises** — mock `youtube_transcript_to_lrc` to raise `YouTubeRateLimitedError`; assert `try_youtube_transcript_lrc` propagates it (does not return `None`).
5. **Free-mode wait loop retries** — mock `try_youtube_transcript_lrc` to raise `YouTubeRateLimitedError` once, then succeed; set `SOW_FREE_ONLY_MODE=true`; assert the queue Stage 1 loop waits via `wait_for_youtube_cooldown_if_open` and retries, ultimately producing a YouTube result.
6. **Non-free mode falls through immediately** — same mock but `SOW_FREE_ONLY_MODE=false`; assert Stage 1 does not wait and falls through to Stage 2 (Whisper/Qwen3 ASR).
7. **Cancellation during wait** — set `job.status = CANCELLED` during the wait loop; assert the loop returns early.

## Out of Scope (Deferred)

- **Per-video breaker / sliding time window.** The global counter is retained (with the cooldown-expiry reset fix). A sliding window or per-video tracking is a larger change; the reset fix + free-mode thresholds should suffice for the observed burst pattern.
- **List-fallback retry when direct fetch 429s.** The list endpoint is still gated by the same breaker. Could allow one list attempt per video even when direct 429s, but deferred to keep this change focused.
- **Rotating proxy in free mode.** Free mode has no budget for a proxy; the patient-wait strategy is the free-mode substitute. In non-free mode, configuring `SOW_YOUTUBE_PROXY` remains the robust fix for IP-level 429s.

## Open Questions

1. **Cap on wait attempts.** Should the free-mode wait loop have a max attempts cap (e.g. wait through 3 cooldowns before finally falling back), to avoid a job stuck waiting forever if YouTube is permanently blocked from this IP? Or wait indefinitely (consistent with Qwen3 quota waiter)?
2. **Min-interval free value (15.0s).** Resolved — use 15s (more aggressive than the initial 8s proposal) given the logs show YouTube 429s even at 3s spacing from burst jobs.
