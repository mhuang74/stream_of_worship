# LLM API Rate-Limit Retry Enhancement — v4

> **Status**: Ready for Implementation
> **Related**:
> - `specs/llm-rate-limit-retry-v3.md` (parent spec; this doc supersedes it)
> - `specs/llm-rate-limit-retry-v2.md` (original 429 + 5xx retry design)
> - `specs/llm-rate-limit-retry-v1.md` (introduced 429 retry with exponential backoff)
> - `specs/analysis-service-rate-limit-timeout-tuning-v1.md` (YouTube fetch path precedent)

## Summary

Following a review of v3, this v4 spec preserves the two core fixes — (1) retry
transient 5xx errors (notably Cloudflare 524), and (2) pace LLM HTTP calls with
a min-interval throttle — plus the unified entry-point refactor
(`call_llm_with_retry`). It reconciles seven issues surfaced during v3 review:

1. **Throttle placement**: `_enforce_llm_min_interval()` is called **after**
   `_acquire_llm_slot()`, so the throttle paces only active (slot-holding)
   jobs and matches the proven YouTube transcript pattern
   (`youtube_transcript.py:297-299`).
2. **Aliases removed**: `_call_llm_with_rate_limit_retry`, `_acquire_llm_slot`,
   `_release_llm_slot` are **deleted**, not deprecated. Both known callers
   migrate in the same PR; no external consumers exist.
3. **429 short-circuit**: `_is_llm_retryable_error` returns `False` immediately
   when `status_code == 429`, preventing 429s from being misclassified/logged
   as "retryable 5xx".
4. **Exhausted-retry logging**: `_llm_correct` distinguishes
   "transient-error retries exhausted" from other failures in the raised
   `YouTubeTranscriptError` message.
5. **Test semaphore handling**: tests patch `_acquire_llm_slot`/
   `_release_llm_slot` to no-ops (moved inside the wrapper, callers no longer
   patch them).
6. **Event loop API**: switch `asyncio.get_event_loop()` →
   `asyncio.get_running_loop()` (Python 3.12+ readiness).
7. **Type-name check**: rely on the 429 status-code short-circuit alone; do not
   add an explicit `RateLimitError` exclusion to strategy 2.

## Problems (unchanged from v2/v3)

### Issue 1: Transient 5xx errors (notably Cloudflare 524) are not retried

`_call_llm_with_rate_limit_retry` only retries when
`_is_llm_rate_limited_error(e)` returns `True`. A 524 matches none of the
429-only checks, so it propagates immediately. The YouTube transcript path falls
back to expensive ASR despite having 8 retry attempts and 300s of timeout
budget unused.

### Issue 2: LLM calls spaced too closely → avoidable concurrent_budget_exceeded 429s

`SOW_LLM_MAX_CONCURRENT=3` matches the provider's slot budget, but it is a
concurrency limiter, not a rate limiter. When one job's HTTP response returns
and the slot is released, a waiting job fires within milliseconds before the
provider's `in_flight` counter has decremented.

### Issue 3 (new in v3): Semaphore held during backoff sleep

With `SOW_LLM_MAX_CONCURRENT=3`, a single 524 `retry_after=120` holds a slot
for 2 minutes of idle sleep, collapsing effective throughput from 3 → 2 slots.
v4 moves semaphore management **inside** `call_llm_with_retry` so slots are
released before `asyncio.sleep(delay)` during backoff.

## Design Principles

1. **Retry transient 5xx errors** with the same exponential backoff used for
   429s, honoring the provider's `retry_after` field. Reuse the existing
   8-attempt / 300s budget.
2. **Pace LLM HTTP calls** with a module-level min-interval throttle, mirroring
   the proven `_enforce_min_interval()` pattern from the YouTube transcript
   rate limiter.
3. **Do not hold the semaphore during backoff sleeps** — the slot covers the
   throttle wait + actual HTTP request/response window only.
4. **Throttle paces only active (slot-holding) jobs.** The throttle fires
   after `_acquire_llm_slot()` succeeds, so idle jobs waiting on the semaphore
   do not consume throttle budget. This matches the YouTube transcript pattern
   at `youtube_transcript.py:297-299`.

## Architecture: Unified Entry Point

### New Call Pattern

```
_llm_correct / _llm_align
  └── return await call_llm_with_retry(sync_fn, description=...)   # one call
```

`call_llm_with_retry` internally, per attempt:
1. Acquires semaphore slot (`_acquire_llm_slot`)
2. Enforces min-interval throttle (`_enforce_llm_min_interval`)
3. Runs `sync_fn` via `run_in_executor`
4. On 429 or retryable 5xx: releases slot, sleeps (backoff), loops to step 1
5. On success or non-retryable error: releases slot and returns/raises

Old symbols `_call_llm_with_rate_limit_retry`, `_acquire_llm_slot`,
`_release_llm_slot` are **removed** (not kept as deprecated aliases).

## Detailed Changes

### Part A — Add retryable 5xx detection

**File: `ops/analysis-service/src/sow_analysis/workers/llm_rate_limit.py`**

#### A.1 Add `_is_llm_retryable_error(e: Exception) -> bool`

Sibling to `_is_llm_rate_limited_error`. Uses the same exception-cause-chain
walk pattern (visited set + `__cause__`/`__context__` traversal).

**Short-circuit (new in v4):** at the top of the function, return `False`
immediately if `_is_llm_rate_limited_error(e)` returns `True` OR if any exception
in the chain has `status_code == 429` / `code == 429`. This prevents a 429 from
being misclassified as a "retryable 5xx" and logged with the 5xx message.

Detection strategies (after the short-circuit):

1. **Status code check**: `getattr(exc, "status_code", None)` in
   `{500, 502, 503, 504, 520, 521, 522, 523, 524, 529}` (also check
   `getattr(exc, "code", None)` for symmetry with the 429 path).
2. **Type-name check**: `type(exc).__name__` in
   `{"APITimeoutError", "APIConnectionError"}` (OpenAI SDK exception types).
   **Excludes `APIStatusError`** — it is a broad base class that includes
   400 Bad Request. Does NOT explicitly exclude `RateLimitError` — the status-code
   short-circuit above already handles that case.
3. **Provider JSON body check**: parse the exception message/body via
   `_extract_json_from_text`, then check for `"retryable": true`. The 524
   response body has `retryable` at the top level, and `_extract_json_from_text`
   already handles the OpenAI SDK's `"Error code: 524 - {...}"` format.

Returns `False` for parse errors (`ValueError`, `json.JSONDecodeError`) and
other non-transient errors.

#### A.2 Update the retry loop to include 5xx

In the unified `call_llm_with_retry` function (see Part C), the propagation
logic is:

```python
is_rate_limit = _is_llm_rate_limited_error(e)
is_retryable = _is_llm_retryable_error(e)
if not (is_rate_limit or is_retryable):
    raise
```

#### A.3 Differentiate log messages

- Existing (429 path): `"LLM rate limited (429) for %s, attempt %d/%d, backing off %.1fs ..."`
- New (5xx path, when `is_retryable and not is_rate_limit`):
  `"LLM transient error (%d) for %s, attempt %d/%d, backing off %.1fs ..."`
  where `%d` is the actual status code (e.g., 524).

Both share the same backoff parameters.

### Part B — Add min-interval throttle

**File: `ops/analysis-service/src/sow_analysis/workers/llm_rate_limit.py`**

#### B.1 Add module-level throttle state

```python
_llm_interval_lock: Optional[asyncio.Lock] = None
_llm_last_request_time: float = 0.0
```

Lazy-initialize the `asyncio.Lock` on first use (same pattern as
`_llm_semaphore`).

#### B.2 Add `_enforce_llm_min_interval()`

Mirror `_enforce_min_interval()` in `youtube_transcript.py:224-248`, but with
jitter applied to the **target interval** (not the wait delta) to avoid
`wait <= 0` edge cases (v3's jitter placement bug fix preserved):

```python
async def _enforce_llm_min_interval() -> None:
    """Sleep if the last LLM HTTP request was too recent."""
    global _llm_interval_lock, _llm_last_request_time

    if _llm_interval_lock is None:
        _llm_interval_lock = asyncio.Lock()

    min_interval = settings.SOW_LLM_MIN_INTERVAL_SECONDS
    if min_interval <= 0:
        return

    async with _llm_interval_lock:
        now = time.monotonic()
        elapsed = now - _llm_last_request_time
        # Apply jitter to the target interval (not the delta)
        jitter = min_interval * 0.25
        effective_min = random.uniform(min_interval - jitter, min_interval + jitter)
        wait = effective_min - elapsed
        if wait > 0:
            logger.debug("LLM min-interval throttle: spacing request, sleeping %.2fs", wait)
            await asyncio.sleep(wait)
        _llm_last_request_time = time.monotonic()
```

#### B.3 Placement: AFTER acquire (v4 decision)

**v4 decision**: `_enforce_llm_min_interval` is called **after**
`_acquire_llm_slot()` succeeds (see §C.1). Rationale:

- Matches the YouTube transcript pattern at `youtube_transcript.py:297-299`,
  where the throttle fires inside the semaphore-guarded block.
- Only active (slot-holding) jobs are paced; idle jobs waiting on the
  semaphore do not consume throttle budget.
- The throttle wait occupies a slot, but slot occupation during a ~2s throttle
  is acceptable — the catastrophic case (Issue 3) was the backoff sleep
  (up to 120s), which v4 still addresses by releasing the slot before
  `asyncio.sleep(delay)`.

The v3 comment claiming "fires while holding the LLM semaphore slot, so it
paces active requests without blocking idle jobs" is now **accurate** in v4.

#### B.4 Add config setting

**File: `ops/analysis-service/src/sow_analysis/config.py`** — Add near
`SOW_LLM_MAX_CONCURRENT`:

```python
SOW_LLM_MIN_INTERVAL_SECONDS: float = 2.0
# Minimum gap (seconds) between consecutive LLM HTTP calls across all jobs.
# Compensates for provider-side in_flight accounting lag. This throttle fires
# after acquiring the LLM semaphore slot, so it paces active requests without
# blocking idle jobs. Set to 0 to disable.
```

### Part C — Refactor: unified `call_llm_with_retry` entry point

**File: `ops/analysis-service/src/sow_analysis/workers/llm_rate_limit.py`**

#### C.1 New public function `call_llm_with_retry`

Replace `_call_llm_with_rate_limit_retry` entirely. Use
`asyncio.get_running_loop()` instead of `asyncio.get_event_loop()` (v4
decision: Python 3.12+ readiness).

```python
async def call_llm_with_retry(
    sync_fn: Callable[[], str],
    *,
    description: str,
    loop: Optional[asyncio.AbstractEventLoop] = None,
) -> str:
    """Run a synchronous LLM call with rate-limit retry, concurrency, and pacing.

    This is the unified entry point for all LLM calls in the analysis service.
    It internally handles:
    - Semaphore-guarded concurrency (slot acquired/released inside this function)
    - Min-interval pacing between requests (fires after slot acquisition)
    - Exponential backoff with jitter for 429 and transient 5xx errors
    - Budget enforcement (max attempts + wall-clock timeout)

    Critical invariant: the semaphore slot is RELEASED before asyncio.sleep
    during backoff, so other jobs can use the slot during our backoff.
    """
    if loop is None:
        loop = asyncio.get_running_loop()

    max_attempts = settings.SOW_LLM_RATE_LIMIT_MAX_RETRIES
    total_timeout = settings.SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS
    base_delay = settings.SOW_LLM_RATE_LIMIT_BASE_DELAY
    max_delay = settings.SOW_LLM_RATE_LIMIT_MAX_DELAY

    start_time = time.monotonic()
    last_exception: Optional[Exception] = None

    for attempt in range(max_attempts):
        # Acquire concurrency slot. Throttle fires AFTER acquisition (v4)
        # so only active jobs are paced.
        await _acquire_llm_slot()

        released_for_backoff = False
        try:
            # Pace the request — fires while holding the slot
            await _enforce_llm_min_interval()

            return await loop.run_in_executor(None, sync_fn)
        except Exception as e:
            is_rate_limit = _is_llm_rate_limited_error(e)
            is_retryable = _is_llm_retryable_error(e)
            if not (is_rate_limit or is_retryable):
                raise  # Non-retryable error — propagate immediately

            last_exception = e

            # Don't sleep after the last attempt
            if attempt >= max_attempts - 1:
                break

            # Check budget
            elapsed = time.monotonic() - start_time
            remaining_budget = total_timeout - elapsed
            if remaining_budget <= 0:
                logger.warning(
                    "LLM retry budget exhausted for %s (%.1fs elapsed, %.1fs budget) — giving up",
                    description, elapsed, total_timeout,
                )
                break

            # Extract provider retry guidance
            retry_after = _extract_retry_after(e)
            provider_base, provider_max = _extract_backoff_config(e)
            effective_base = provider_base if provider_base != base_delay else base_delay
            effective_max = provider_max if provider_max != max_delay else max_delay

            # Compute backoff delay
            exp_delay = min(effective_base * (2 ** attempt), effective_max)
            if retry_after is not None:
                delay = max(retry_after, exp_delay)
            else:
                delay = exp_delay

            # Add jitter (0-25%)
            jitter = random.uniform(0, delay * 0.25)
            delay += jitter

            # Don't exceed remaining budget
            if delay > remaining_budget:
                delay = remaining_budget

            status_code = _extract_status_code(e) or 0
            if is_rate_limit:
                logger.warning(
                    "LLM rate limited (429) for %s, attempt %d/%d, "
                    "backing off %.1fs (retry_after=%s, budget remaining: %.1fs): %s",
                    description, attempt + 1, max_attempts, delay,
                    f"{retry_after:.1f}s" if retry_after else "None",
                    remaining_budget, e,
                )
            else:
                logger.warning(
                    "LLM transient error (%s) for %s, attempt %d/%d, "
                    "backing off %.1fs (retry_after=%s, budget remaining: %.1fs): %s",
                    status_code, description, attempt + 1, max_attempts, delay,
                    f"{retry_after:.1f}s" if retry_after else "None",
                    remaining_budget, e,
                )

            # CRITICAL: release the semaphore BEFORE sleeping so other jobs
            # can use the slot during our backoff
            _release_llm_slot()
            released_for_backoff = True
            await asyncio.sleep(delay)
            # Next loop iteration: _acquire_llm_slot -> _enforce_llm_min_interval -> run
        finally:
            # Release slot if we haven't already released it for backoff.
            # Covers the success path and the immediate-raise path.
            if not released_for_backoff:
                _release_llm_slot()

    # All attempts exhausted or budget exceeded
    elapsed = time.monotonic() - start_time
    if last_exception is not None:
        raise last_exception
    raise RuntimeError(
        f"LLM retry exhausted for {description} after {max_attempts} attempts ({elapsed:.1f}s elapsed)"
    )
```

#### C.2 Helper `_extract_status_code(e: Exception) -> Optional[int]`

Walk the exception chain (same pattern as `_is_llm_rate_limited_error`) and
return the first `status_code` or `code` attribute found. Used for the 5xx log
message.

#### C.3 Remove old symbols (v4 decision)

**Delete** the following:
- `_call_llm_with_rate_limit_retry` (replaced by `call_llm_with_retry`)
- `_acquire_llm_slot` — kept as an **internal** helper (callers from outside
  the module must not use it directly; it is not exported).
- `_release_llm_slot` — same: kept internal.

Both known callers (`_llm_correct`, `_llm_align`) migrate in the same PR (Part D),
so removing the public `_call_llm_with_rate_limit_retry` name is safe. The
internal `_acquire_llm_slot`/`_release_llm_slot` helpers remain for use by
`call_llm_with_retry` and are not imported by any caller after migration.

### Part D — Update callers

#### D.1 `_llm_correct` in `youtube_transcript.py`

Replace the manual semaphore pattern (lines 751-808) with:

```python
from .llm_rate_limit import call_llm_with_retry, _is_llm_rate_limited_error, _is_llm_retryable_error

# ... inside _llm_correct ...
logger.info(f"Calling LLM ({effective_model}) for YouTube transcript correction")
try:
    return await call_llm_with_retry(
        _call_llm,
        description=f"LLM correction ({effective_model})",
        loop=loop,
    )
except Exception as e:
    # v4 decision: differentiate exhausted-retry failures
    if _is_llm_rate_limited_error(e):
        raise YouTubeTranscriptError(
            f"LLM correction failed after rate-limit retries: {e}"
        ) from e
    if _is_llm_retryable_error(e):
        raise YouTubeTranscriptError(
            f"LLM correction failed after transient-error retries: {e}"
        ) from e
    raise YouTubeTranscriptError(f"LLM correction failed: {e}") from e
```

Remove `_acquire_llm_slot` and `_release_llm_slot` imports and calls. Also
update `loop = asyncio.get_event_loop()` → `asyncio.get_running_loop()`.

#### D.2 `_llm_align` in `lrc.py`

Replace the manual semaphore pattern (lines 636-660) with:

```python
from .llm_rate_limit import call_llm_with_retry, _is_llm_rate_limited_error, _is_llm_retryable_error

# ... inside _llm_align ...
for attempt in range(max_retries):
    try:
        logger.info(f"LLM alignment attempt {attempt + 1}/{max_retries}")
        attempt_start = time.time()

        response_text = await call_llm_with_retry(
            _call_llm,
            description=f"LLM alignment ({effective_model})",
            loop=loop,
        )
        # ... rest unchanged ...
```

Remove `_acquire_llm_slot` and `_release_llm_slot` imports and calls. Update
`asyncio.get_event_loop()` → `asyncio.get_running_loop()` at `lrc.py:604`.

**No behavioral change in `_llm_align`'s outer retry loop** — it catches all
exceptions and continues retrying, same as before.

### Part E — Update tests

#### E.1 `tests/test_llm_rate_limit.py`

1. `_is_llm_retryable_error` returns `True` for a 524-flavored exception built
   from the exact response body in the production log (status_code=524,
   `retryable: true`)
2. `_is_llm_retryable_error` returns `True` for `openai.APITimeoutError` /
   `APIConnectionError` instances (mock or real)
3. `_is_llm_retryable_error` returns `True` for a 503 status with
   `"retryable": true` in body
4. `_is_llm_retryable_error` returns `False` for
   `ValueError("LLM returned empty alignment")` and `json.JSONDecodeError`
5. `_is_llm_retryable_error` returns `False` for `APIStatusError` with
   status_code=400 (broad base class excluded)
6. **v4 new**: `_is_llm_retryable_error` returns `False` immediately for
   status_code=429 (short-circuit — even though strategy 2's type-name set
   does not explicitly exclude `RateLimitError`)
7. `call_llm_with_retry` retries on a 524 and succeeds on a later attempt,
   honoring `retry_after` as minimum delay
8. **v4 new**: `call_llm_with_retry` releases the semaphore before
   `asyncio.sleep` during backoff (verify via mock:
   `_release_llm_slot` called before `asyncio.sleep`; semaphore value
   incremented)
9. `call_llm_with_retry` still propagates immediately on a non-retryable
   error (e.g., 400 Bad Request)
10. `_enforce_llm_min_interval` sleeps when called twice within
    `SOW_LLM_MIN_INTERVAL_SECONDS`
11. `_enforce_llm_min_interval` is a no-op when
    `SOW_LLM_MIN_INTERVAL_SECONDS == 0`
12. **v4 new**: `call_llm_with_retry` calls `_enforce_llm_min_interval`
    AFTER `_acquire_llm_slot` (verify call order via spy: acquire → throttle →
    run_in_executor)
13. `call_llm_with_retry` uses `asyncio.get_running_loop()` when `loop=None`
14. **v4 new**: importing `_call_llm_with_rate_limit_retry` raises
    `ImportError` (symbol removed; not just deprecated)
15. Concurrency semaphore still limits parallel calls when using
    `call_llm_with_retry`

**v4 test semaphore handling**: tests patch `_acquire_llm_slot` and
`_release_llm_slot` to no-ops (since they are now called inside
`call_llm_with_retry` and the old caller-level patch pattern no longer
applies). Set `SOW_LLM_MAX_CONCURRENT=0` as an alternative if cleaner per test.

#### E.2 `tests/test_youtube_transcript.py`

16. When `_llm_correct`'s `call_llm_with_retry` raises a 524 on the first
    call and succeeds on the second, `youtube_transcript_to_lrc` completes
    without raising (mock `_acquire_llm_slot`/`_release_llm_slot` to no-ops)
17. When `call_llm_with_retry` exhausts all retries on a 524, the outer
    `_llm_correct` raises `YouTubeTranscriptError` **with message containing
    "transient-error retries"** (v4 differentiated message — not the generic
    "LLM correction failed")
18. When `call_llm_with_retry` exhausts all retries on a 429, the outer
    `_llm_correct` raises `YouTubeTranscriptError` with message containing
    "rate-limit retries" (existing message preserved)

#### E.3 `tests/test_lrc.py`

19. `_llm_align` integration with `call_llm_with_retry` — a 524 on first
    attempt, success on second, returns aligned lines

## Files to Modify

| File | Change |
|------|--------|
| `ops/analysis-service/src/sow_analysis/workers/llm_rate_limit.py` | **Major refactor**: add `_is_llm_retryable_error` (with 429 short-circuit), `_enforce_llm_min_interval`, `_extract_status_code`, module state (`_llm_interval_lock`, `_llm_last_request_time`), new `call_llm_with_retry` function (throttle-after-acquire, release-before-backoff-sleep, `get_running_loop`), remove `_call_llm_with_rate_limit_retry` symbol (keep `_acquire_llm_slot`/`_release_llm_slot` internal) |
| `ops/analysis-service/src/sow_analysis/config.py` | Add `SOW_LLM_MIN_INTERVAL_SECONDS = 2.0` |
| `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py` | Replace manual acquire/release with `call_llm_with_retry`; switch to `get_running_loop`; differentiate exhausted-retry `YouTubeTranscriptError` message |
| `ops/analysis-service/src/sow_analysis/workers/lrc.py` | Replace manual acquire/release with `call_llm_with_retry`; switch to `get_running_loop` |
| `ops/analysis-service/tests/test_llm_rate_limit.py` | Add all new tests; patch `_acquire_llm_slot`/`_release_llm_slot` as no-ops |
| `ops/analysis-service/tests/test_youtube_transcript.py` | Add 524 retry path test; assert differentiated error message |

**No changes** in `routes/health.py`.

## What Does NOT Change

- `SOW_LLM_MAX_CONCURRENT = 3` (semaphore still matches provider budget)
- `SOW_LLM_RATE_LIMIT_MAX_RETRIES = 8`
- `SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS = 300`
- `SOW_LLM_RATE_LIMIT_BASE_DELAY = 2.0` / `SOW_LLM_RATE_LIMIT_MAX_DELAY = 30.0`
- `_extract_retry_after` / `_extract_backoff_config` (already handle top-level `retry_after`)
- Health check at `routes/health.py:68`
- The three-tier LRC fallback cascade (Tier 1 YouTube → Tier 2 Qwen3 ASR → Tier 3 Whisper)
- `_llm_align` outer retry loop behavior (still retries all exceptions up to 3 times)

## Verification

```bash
cd ops/analysis-service

# Run LLM rate-limit tests (Parts A, B, C)
uv run --extra dev pytest tests/test_llm_rate_limit.py -v

# Run YouTube transcript tests (Part D end-to-end)
uv run --extra dev pytest tests/test_youtube_transcript.py -v

# Run LRC tests (Part D end-to-end)
uv run --extra dev pytest tests/test_lrc.py -v

# Run full test suite
uv run --extra dev pytest tests/ -v
```

After deployment, verify in logs:

- 524 errors produce `"LLM transient error (524) for ..."` messages
- `"LLM min-interval throttle: spacing request, sleeping X.XXs"` debug messages
  appear when many jobs compete
- 429 `concurrent_budget_exceeded` errors drop significantly
- No immediate `"Falling back to LLM-based ASR"` on first 524
- Exhausted-retry `YouTubeTranscriptError` messages say "transient-error
  retries" (5xx) vs "rate-limit retries" (429) for operator triage

## Changelog from v3 to v4

| Aspect | v3 | v4 |
|--------|----|----|
| Throttle placement | Ambiguous (§C.1 said before-acquire; §B.3 comment said after-acquire) | After-acquire (matches YouTube pattern) |
| Old symbols | Deprecated aliases raising `DeprecationWarning` | `_call_llm_with_rate_limit_retry` deleted; `_acquire_llm_slot`/`_release_llm_slot` kept internal |
| 429 in `_is_llm_retryable_error` | Not explicitly handled (could misclassify 429 as 5xx log) | Explicit short-circuit returns `False` for status_code==429 |
| Exhausted-retry logging | Generic `YouTubeTranscriptError` | Differentiated: "rate-limit retries" vs "transient-error retries" |
| Test semaphore patching | Relied on v2's "semaphore-disabled test pattern" (caller-level) | Patch `_acquire_llm_slot`/`_release_llm_slot` as no-ops (now internal to wrapper) |
| `asyncio.get_event_loop()` | Retained | Switched to `asyncio.get_running_loop()` |
| `RateLimitError` explicit exclusion | N/A | Relies on 429 status-code short-circuit alone |
