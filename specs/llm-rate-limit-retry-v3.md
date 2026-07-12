# LLM API Rate-Limit Retry Enhancement — v3

> **Status**: Ready for Implementation
> **Related**:
> - `specs/llm-rate-limit-retry-v2.md` (parent spec; this doc supersedes it)
> - `specs/analysis-service-rate-limit-timeout-tuning-v1.md` (YouTube fetch path precedent)
> - `specs/llm-rate-limit-retry-v1.md` (original 429 retry with exponential backoff)

## Summary

Following an operational-robustness review of v2, this v3 spec addresses the same two root causes (524 not retried, thundering-herd 429s) but **broadens scope** to fix two serious production side-effects discovered during review:

1. **Semaphore held during backoff sleeps** — with SOW_LLM_MAX_CONCURRENT=3, a single 524 retry_after=120 holds a slot for 2 minutes of idle sleep, collapsing effective throughput. v3 moves semaphore management **inside** `_call_llm_with_rate_limit_retry` so slots are only held during the actual HTTP call.
2. **Caller boilerplate** — `_llm_correct` and `_llm_align` both manually acquire/release the semaphore around the retry wrapper. v3 eliminates this duplication by making the wrapper self-contained.

The v3 design is: **one public async function that callers invoke directly; it internally handles semaphore acquisition, min-interval pacing, retry loop, and backoff sleeps.**

## Problems (unchanged from v2)

### Issue 1: Transient 5xx errors (notably Cloudflare 524) are not retried

`_call_llm_with_rate_limit_retry` only retries when `_is_llm_rate_limited_error(e)` returns `True`. A 524 matches none of the 429-only checks, so it propagates immediately. The YouTube transcript path falls back to expensive ASR despite having 8 retry attempts and 300s of timeout budget unused.

### Issue 2: LLM calls spaced too closely → avoidable concurrent_budget_exceeded 429s

`SOW_LLM_MAX_CONCURRENT=3` matches the provider's slot budget, but it is a concurrency limiter, not a rate limiter. When one job's HTTP response returns and the slot is released, a waiting job fires within milliseconds before the provider's `in_flight` counter has decremented.

## Design Principles (unchanged from v2)

1. **Retry transient 5xx errors** with the same exponential backoff used for 429s, honoring the provider's `retry_after` field. Reuse the existing 8-attempt / 300s budget.
2. **Pace LLM HTTP calls** with a module-level min-interval throttle, mirroring the proven `_enforce_min_interval()` pattern from the YouTube transcript rate limiter.
3. **Do not hold the semaphore during backoff sleeps** — the slot should only cover the actual HTTP request/response window.

## Architecture: Unified Entry Point

### Current (v2) Call Pattern

```
_llm_correct / _llm_align
  ├── await _acquire_llm_slot()          # caller manages semaphore
  ├── try:
  │     return await _call_llm_with_rate_limit_retry(sync_fn)
  │  finally:
  │     _release_llm_slot()              # held entire duration
```

### New (v3) Call Pattern

```
_llm_correct / _llm_align
  └── return await call_llm_with_retry(sync_fn, description=...)   # one call
```

`call_llm_with_retry` internally:
1. Enforces min-interval throttle
2. Acquires semaphore slot
3. Runs `sync_fn` via `run_in_executor`
4. On 429 or retryable 5xx: releases slot, sleeps (backoff), loops to step 1
5. On success or non-retryable error: releases slot and returns/raises

## Detailed Changes

### Part A — Add retryable 5xx detection

**File: `ops/analysis-service/src/sow_analysis/workers/llm_rate_limit.py`**

#### A.1 Add `_is_llm_retryable_error(e: Exception) -> bool`

Sibling to `_is_llm_rate_limited_error`. Uses the same exception-cause-chain walk pattern (visited set + `__cause__`/`__context__` traversal). Detection strategies:

1. **Status code check**: `getattr(exc, "status_code", None)` in `{500, 502, 503, 504, 520, 521, 522, 523, 524, 529}` (also check `getattr(exc, "code", None)` for symmetry with the 429 path)
2. **Type-name check**: `type(exc).__name__` in `{"APITimeoutError", "APIConnectionError"}` (OpenAI SDK exception types). **Excludes `APIStatusError`** — it is a broad base class that includes 400 Bad Request.
3. **Provider JSON body check**: parse the exception message/body via `_extract_json_from_text`, then check for `"retryable": true`. The 524 response body has `retryable` at the top level, and `_extract_json_from_text` already handles the OpenAI SDK's `"Error code: 524 - {...}"` format.

Returns `False` for parse errors (`ValueError`, `json.JSONDecodeError`) and other non-transient errors.

#### A.2 Update the retry loop to include 5xx

In the unified `call_llm_with_retry` function (see Part C), the propagation logic changes from:

```python
if not _is_llm_rate_limited_error(e):
    raise
```

to:

```python
is_rate_limit = _is_llm_rate_limited_error(e)
is_retryable = _is_llm_retryable_error(e)
if not (is_rate_limit or is_retryable):
    raise
```

#### A.3 Differentiate log messages

- Existing (429 path): `"LLM rate limited (429) for %s, attempt %d/%d, backing off %.1fs ..."`
- New (5xx path, when `is_retryable and not is_rate_limit`): `"LLM transient error (%d) for %s, attempt %d/%d, backing off %.1fs ..."` where `%d` is the actual status code (e.g., 524).

Both share the same backoff parameters. The log line includes the status code so operators can distinguish 524 from 503 at a glance.

### Part B — Add min-interval throttle

**File: `ops/analysis-service/src/sow_analysis/workers/llm_rate_limit.py`**

#### B.1 Add module-level throttle state

```python
_llm_interval_lock: Optional[asyncio.Lock] = None
_llm_last_request_time: float = 0.0
```

Lazy-initialize the `asyncio.Lock` on first use (same pattern as `_llm_semaphore`).

#### B.2 Add `_enforce_llm_min_interval()`

Mirror `_enforce_min_interval()` in `youtube_transcript.py`, but with jitter applied to the **target interval** (not the wait delta) to avoid `wait <= 0` edge cases:

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

#### B.3 Add config setting

**File: `ops/analysis-service/src/sow_analysis/config.py`** — Add near `SOW_LLM_MAX_CONCURRENT`:

```python
SOW_LLM_MIN_INTERVAL_SECONDS: float = 2.0
# Minimum gap (seconds) between consecutive LLM HTTP calls across all jobs.
# Compensates for provider-side in_flight accounting lag. This throttle
# fires while holding the LLM semaphore slot, so it paces active requests
# without blocking idle jobs. Set to 0 to disable.
```

Default rationale: 2.0s gives ~1-2s headroom for provider in_flight accounting to settle. At 3 slots, sustained throughput is ~0.5 req/s per slot (1.5 req/s total), well below provider capacity.

### Part C — Refactor: unified `call_llm_with_retry` entry point

**File: `ops/analysis-service/src/sow_analysis/workers/llm_rate_limit.py`**

#### C.1 New public function `call_llm_with_retry`

Replace the existing `_call_llm_with_rate_limit_retry` + manual semaphore pattern with a single self-contained async function:

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
    - Min-interval pacing between requests
    - Semaphore-guarded concurrency
    - Exponential backoff with jitter for 429 and transient 5xx errors
    - Budget enforcement (max attempts + wall-clock timeout)
    """
    if loop is None:
        loop = asyncio.get_event_loop()

    max_attempts = settings.SOW_LLM_RATE_LIMIT_MAX_RETRIES
    total_timeout = settings.SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS
    base_delay = settings.SOW_LLM_RATE_LIMIT_BASE_DELAY
    max_delay = settings.SOW_LLM_RATE_LIMIT_MAX_DELAY

    start_time = time.monotonic()
    last_exception: Optional[Exception] = None

    for attempt in range(max_attempts):
        # Pace requests (module-level min-interval throttle)
        await _enforce_llm_min_interval()

        # Acquire concurrency slot (only held for the actual HTTP call)
        await _acquire_llm_slot()
        try:
            return await loop.run_in_executor(None, sync_fn)
        except Exception as e:
            is_rate_limit = _is_llm_rate_limited_error(e)
            is_retryable = _is_llm_retryable_error(e)
            if not (is_rate_limit or is_retryable):
                raise  # Non-retriable error — propagate immediately

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
            await asyncio.sleep(delay)
            # Do NOT acquire here — the next loop iteration will call
            # _enforce_llm_min_interval then _acquire_llm_slot normally
        finally:
            # Release slot if we haven't already released it for backoff.
            # This covers the success path and the immediate-raise path.
            _release_llm_slot()

    # All attempts exhausted or budget exceeded
    elapsed = time.monotonic() - start_time
    if last_exception is not None:
        raise last_exception
    raise RuntimeError(
        f"LLM retry exhausted for {description} after {max_attempts} attempts ({elapsed:.1f}s elapsed)"
    )
```

**Key semantics of the `finally` block:**

The `finally` releases the slot on:
- **Success path** — return happens inside `try`, `finally` runs, releases slot.
- **Immediate-raise path** — non-retryable exception inside `try`, `finally` runs, releases slot.
- **Retry path** — we explicitly `_release_llm_slot()` before `await asyncio.sleep(delay)`. The subsequent `finally` runs and calls `_release_llm_slot()` again. `asyncio.Semaphore.release()` is idempotent for over-release **only up to the initial value**, but calling it twice without matching acquires would over-release.

**Solution: track whether we released for backoff, and skip the finally release in that case.**

Use a local flag:

```python
released_for_backoff = False
try:
    return await loop.run_in_executor(None, sync_fn)
except Exception as e:
    # ... retry logic ...
    _release_llm_slot()
    released_for_backoff = True
    await asyncio.sleep(delay)
finally:
    if not released_for_backoff:
        _release_llm_slot()
```

This ensures exactly one release per acquire.

#### C.2 Helper `_extract_status_code(e: Exception) -> Optional[int]`

Walk the exception chain (same pattern as `_is_llm_rate_limited_error`) and return the first `status_code` or `code` attribute found. Used for the 5xx log message.

#### C.3 Deprecate old exports

Keep `_call_llm_with_rate_limit_retry`, `_acquire_llm_slot`, and `_release_llm_slot` as **deprecated aliases** that raise `DeprecationWarning` and delegate to `call_llm_with_retry`. This prevents breaking any external call sites that might exist outside the known two (`_llm_correct`, `_llm_align`).

```python
import warnings

async def _call_llm_with_rate_limit_retry(*args, **kwargs):
    warnings.warn(
        "_call_llm_with_rate_limit_retry is deprecated; use call_llm_with_retry",
        DeprecationWarning,
        stacklevel=2,
    )
    return await call_llm_with_retry(*args, **kwargs)
```

### Part D — Update callers

#### D.1 `_llm_correct` in `youtube_transcript.py`

Replace the manual semaphore pattern:

```python
from .llm_rate_limit import call_llm_with_retry, _is_llm_rate_limited_error

# ... inside _llm_correct ...
response_text = await call_llm_with_retry(
    _call_llm,
    description=f"LLM correction ({effective_model})",
    loop=loop,
)
```

Remove `_acquire_llm_slot` and `_release_llm_slot` imports.

#### D.2 `_llm_align` in `lrc.py`

Replace the manual semaphore pattern:

```python
from .llm_rate_limit import call_llm_with_retry, _is_llm_rate_limited_error

# ... inside _llm_align ...
response_text = await call_llm_with_retry(
    _call_llm,
    description=f"LLM alignment ({effective_model})",
    loop=loop,
)
```

Remove `_acquire_llm_slot` and `_release_llm_slot` imports.

**No behavioral change in `_llm_align`'s outer retry loop** — it catches all exceptions and continues retrying, same as before. The spec scope explicitly keeps this unchanged per product direction.

### Part E — Update tests

#### E.1 `tests/test_llm_rate_limit.py`

1. `_is_llm_retryable_error` returns `True` for a 524-flavored exception built from the exact response body in the production log (status_code=524, `retryable: true`)
2. `_is_llm_retryable_error` returns `True` for `openai.APITimeoutError` / `APIConnectionError` instances (mock or real)
3. `_is_llm_retryable_error` returns `True` for a 503 status with `"retryable": true` in body
4. `_is_llm_retryable_error` returns `False` for `ValueError("LLM returned empty alignment")` and `json.JSONDecodeError`
5. `_is_llm_retryable_error` returns `False` for `APIStatusError` with status_code=400 (broad base class excluded)
6. `call_llm_with_retry` retries on a 524 and succeeds on a later attempt, honoring `retry_after` as minimum delay
7. `call_llm_with_retry` releases the semaphore before `asyncio.sleep` during backoff (verify via mock: `_release_llm_slot` called before `asyncio.sleep`, semaphore value incremented)
8. `call_llm_with_retry` still propagates immediately on a non-retriable error (e.g., 400 Bad Request)
9. `_enforce_llm_min_interval` sleeps when called twice within `SOW_LLM_MIN_INTERVAL_SECONDS`
10. `_enforce_llm_min_interval` is a no-op when `SOW_LLM_MIN_INTERVAL_SECONDS == 0`
11. `call_llm_with_retry` calls `_enforce_llm_min_interval` before each acquisition (verify call count via spy)
12. Deprecation warnings are raised by `_call_llm_with_rate_limit_retry`, `_acquire_llm_slot`, and `_release_llm_slot`
13. Concurrency semaphore still limits parallel calls when using `call_llm_with_retry`

#### E.2 `tests/test_youtube_transcript.py`

14. When `_llm_correct`'s `call_llm_with_retry` raises a 524 on the first call and succeeds on the second, `youtube_transcript_to_lrc` completes without raising (uses existing semaphore-disabled test pattern)
15. When `call_llm_with_retry` exhausts all retries on a 524, the outer `_llm_correct` raises `YouTubeTranscriptError` with message containing "LLM correction failed"

#### E.3 `tests/test_lrc.py` (if exists)

16. `_llm_align` integration with `call_llm_with_retry` — a 524 on first attempt, success on second, returns aligned lines

## Files to Modify

| File | Change |
|------|--------|
| `ops/analysis-service/src/sow_analysis/workers/llm_rate_limit.py` | **Major refactor**: add `_is_llm_retryable_error`, `_enforce_llm_min_interval`, `_extract_status_code`, module state (`_llm_interval_lock`, `_llm_last_request_time`), new `call_llm_with_retry` function, deprecate old exports |
| `ops/analysis-service/src/sow_analysis/config.py` | Add `SOW_LLM_MIN_INTERVAL_SECONDS = 2.0` |
| `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py` | Replace manual acquire/release with `call_llm_with_retry` (remove `_acquire_llm_slot`, `_release_llm_slot` imports and calls) |
| `ops/analysis-service/src/sow_analysis/workers/lrc.py` | Replace manual acquire/release with `call_llm_with_retry` (remove `_acquire_llm_slot`, `_release_llm_slot` imports and calls) |
| `ops/analysis-service/tests/test_llm_rate_limit.py` | Add all new tests; update existing tests for new function names and semaphore-internals behavior |
| `ops/analysis-service/tests/test_youtube_transcript.py` | Add 524 retry path test |

**No changes** in `routes/health.py` (excluded by design, same as v2).

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
- `"LLM min-interval throttle: spacing request, sleeping X.XXs"` debug messages appear when many jobs compete
- 429 `concurrent_budget_exceeded` errors drop significantly
- No immediate `"Falling back to LLM-based ASR"` on first 524

## Migration Notes

### For downstream code (future PRs)

Any new code that needs LLM rate-limit retry should use:

```python
from sow_analysis.workers.llm_rate_limit import call_llm_with_retry

result = await call_llm_with_retry(sync_fn, description="...")
```

Instead of the old 4-step pattern (acquire → call wrapper → release → handle exceptions).

### Rollback safety

The deprecated exports (`_call_llm_with_rate_limit_retry`, `_acquire_llm_slot`, `_release_llm_slot`) remain functional. If a critical bug is found in `call_llm_with_retry`, callers can be reverted to the old pattern in `youtube_transcript.py` and `lrc.py` without touching `llm_rate_limit.py`.

## Changelog from v2 to v3

| Aspect | v2 | v3 |
|--------|-----|-----|
| Scope | 4 files (llm_rate_limit.py, config.py, 2 test files) | 6 files (same + youtube_transcript.py, lrc.py) |
| Semaphore management | Caller manually acquires/releases | Handled internally by `call_llm_with_retry` |
| Semaphore held during backoff | Yes (serious throughput collapse) | No (released before `asyncio.sleep`) |
| Entry point | `_call_llm_with_rate_limit_retry` | `call_llm_with_retry` (new unified API) |
| Old exports | Kept as-is (no deprecation) | Kept with `DeprecationWarning` |
| Jitter application | Applied to wait delta (could be ≤0) | Applied to target interval (always positive wait) |
| 5xx type-name check | Included `APIStatusError` (too broad) | Excludes `APIStatusError`; only `APITimeoutError`, `APIConnectionError` |
| `_llm_align` outer loop | Spec said "no changes needed" | Explicitly kept unchanged (product decision) |
