# LLM API Rate-Limit Retry Enhancement — v1

> **Status**: Draft  
> **Related**: `specs/youtube-transcript-rate-limiting-v2.md` (YouTube transcript fetch rate limiting — this spec covers the LLM call rate limiting, a separate concern)

## Summary

Enhance the Analysis Service's handling of LLM API rate limiting (HTTP 429) so that transient rate-limit errors during the YouTube transcription LLM correction step do not cause the LRC job to fall back to expensive local ASR. The system should retry patiently with exponential backoff, respecting the provider's retry guidance, before giving up on the LLM-based path.

## Problem

### Error Evidence

Production logs show the YouTube transcript path failing due to an LLM 429 from OpenRouter:

```
sow_analysis.workers.lrc - WARNING - [job_95cf5477ed84]
LRC GENERATION: YouTube transcript path FAILED
Reason: LLM correction failed: Error code: 429 - {
  'error': {
    'type': 'rate_limit_error',
    'code': 'concurrent_budget_exceeded',
    'message': 'Concurrent limit reached for Qwen/Qwen3.6-35B-A3B: 3/3 slots in use. ...',
    'retry_after': 1.0,
    'retry_strategy': {
      'type': 'concurrent_drain',
      'suggested_initial_delay_s': 1.0,
      'max_delay_s': 30.0,
      'backoff': 'exponential',
      'backoff_base': 2.0,
      'jitter': True
    },
    'retryable': True,
    'context': {'budget': 3, 'in_flight': 3, 'model': 'Qwen/Qwen3.6-35B-A3B', 'limit_type': 'concurrent'}
  }
}
Falling back to LLM-based ASR...
```

### Root Cause

The LLM correction step (`_llm_correct()` in `youtube_transcript.py:571-631`) has **zero retry logic**. A single 429 from the LLM provider immediately raises `YouTubeTranscriptError`, which `try_youtube_transcript_lrc()` (`lrc.py:873-879`) catches and returns `None`, triggering the Tier 2/Tier 3 ASR fallback cascade.

The 429 response body includes rich retry guidance (`retry_after: 1.0`, exponential backoff strategy, `retryable: true`) that is completely ignored.

### Current LLM Call Sites

There are two LLM call sites in the LRC pipeline, both using the OpenAI Python SDK:

| Call Site | File | Retries | Backoff | Issue |
|---|---|---|---|---|
| `_llm_correct()` | `youtube_transcript.py:571` | **None** | N/A | Single 429 kills the entire YouTube transcript path → ASR fallback |
| `_llm_align()` | `lrc.py:556` | 3 | **None** (immediate retry, no sleep) | Retrying instantly worsens rate limiting; used in Tier 2 (Qwen3 ASR) and Tier 3 (Whisper) |

Neither OpenAI client sets `max_retries=`, so the SDK's built-in retry (default 2) is implicit and not configurable.

### The Three-Tier LRC Fallback Cascade

```
_process_lrc_job() [queue.py:870]
│
├─ Tier 1: YouTube transcript + LLM correction
│   └─ try_youtube_transcript_lrc() [lrc.py:825]
│       ├─ fetch_youtube_transcript() — has good 429 handling (_YouTubeRateLimiter)
│       ├─ _llm_correct() — NO retry on 429 ← THIS IS THE FAILURE POINT
│       ├─ success → DONE (lrc_source="youtube_transcript")
│       └─ failure → returns None → falls through to Tier 2
│
├─ Tier 2: DashScope Qwen3 ASR (cloud) + LLM alignment
│   └─ generate_lrc_from_qwen3_asr() [lrc.py:764]
│       ├─ Qwen3AsrClient.transcribe() — has 3 retries with backoff
│       ├─ _llm_align() — 3 retries, NO backoff sleep
│       ├─ success → DONE (lrc_source="qwen3_asr")
│       └─ failure → falls through to Tier 3
│
└─ Tier 3: Local Whisper ASR + LLM alignment (EXPENSIVE)
    └─ generate_lrc() [lrc.py:882]
        ├─ _run_whisper_transcription() — local model, CPU-intensive
        ├─ _llm_align() — 3 retries, NO backoff sleep
        └─ success → DONE (lrc_source="whisper_asr")
```

**Local ASR (Tier 3) is very expensive** — it runs Whisper on CPU, downloads audio from R2, and requires the local-model semaphore. The user's requirement is clear: we would rather spend more time retrying the LLM than fall back to ASR.

## Design

### Principle

If the YouTube transcript fetch succeeds but the LLM correction fails due to rate limiting, retry patiently (up to 5 minutes) with exponential backoff before giving up. Only if all retries are exhausted should the job fall back to ASR. Non-rate-limit errors (auth failures, parse errors) should propagate according to existing behavior.

### 1. New Module: `workers/llm_rate_limit.py`

A shared LLM rate-limit retry utility used by both `_llm_correct` and `_llm_align`.

#### `_is_llm_rate_limited_error(e: Exception) -> bool`

Detects whether an exception is an LLM 429 rate-limit error. Detection strategies (in order):

1. Check `status_code == 429` on the exception and its `__cause__`/`__context__` chain (OpenAI SDK's `RateLimitError` has `.status_code`).
2. Check exception type name for `RateLimitError` or `rate_limit`.
3. String fallback: check for "429", "rate_limit", "rate_limit_error", "concurrent_budget_exceeded" in the exception message and cause chain.

#### `_extract_retry_after(e: Exception) -> Optional[float]`

Parses the `retry_after` field from the 429 error response body. The OpenAI SDK's `RateLimitError` includes the JSON response body. The OpenRouter response includes:

```json
{
  "retry_after": 1.0,
  "retry_strategy": {
    "suggested_initial_delay_s": 1.0,
    "max_delay_s": 30.0,
    "backoff": "exponential",
    "backoff_base": 2.0,
    "jitter": true
  }
}
```

Returns `retry_after` if present, else `retry_strategy.suggested_initial_delay_s`, else `None`.

#### `_call_llm_with_rate_limit_retry(sync_fn, *, description, loop) -> str`

An async wrapper that:

- Runs `sync_fn` (the OpenAI SDK call, with `max_retries=0` on the client to disable SDK-level retries) via `loop.run_in_executor`
- On 429: computes backoff delay using:
  - `retry_after` from the response body as the minimum delay (provider-suggested)
  - Exponential backoff: `min(base * 2^attempt, max_delay)` + jitter (0-25%)
  - Respects provider's `retry_strategy.backoff_base` and `retry_strategy.max_delay_s` when available
- Sleeps via `await asyncio.sleep(delay)` — releases the executor thread during backoff
- Continues retrying until either `max_attempts` exhausted or `total_timeout` wall-clock budget exceeded
- On non-429 exceptions: re-raises immediately (caller handles its own retry for parse/validation errors)
- Logs each retry attempt with attempt number, delay, and remaining budget

#### Module-level LLM concurrency limiter

```python
_llm_semaphore: Optional[asyncio.Semaphore] = None

async def _acquire_llm_slot() -> None:
    """Acquire a concurrency slot before making an LLM call."""
    global _llm_semaphore
    if _llm_semaphore is None:
        _llm_semaphore = asyncio.Semaphore(settings.SOW_LLM_MAX_CONCURRENT)
    await _llm_semaphore.acquire()

def _release_llm_slot() -> None:
    """Release a concurrency slot after an LLM call completes."""
    if _llm_semaphore is not None:
        _llm_semaphore.release()
```

Both `_llm_correct` and `_llm_align` acquire a slot before calling the LLM and release it after (in a `try/finally`). This serializes LLM calls to stay within the provider's concurrency budget (3 slots in the log), preventing self-inflicted `concurrent_budget_exceeded` 429s when multiple LRC jobs run simultaneously.

### 2. Config Additions: `config.py`

Add new settings in the LLM Configuration section (after `SOW_LLM_MODEL`):

| Setting | Type | Default | Purpose |
|---|---|---|---|
| `SOW_LLM_MAX_CONCURRENT` | `int` | `3` | Module-level semaphore limiting concurrent LLM calls across all LRC jobs. Matches the provider's concurrency budget (3 slots in the log). Prevents self-inflicted 429s from multiple overlapping jobs. Set to 0 to disable. |
| `SOW_LLM_RATE_LIMIT_MAX_RETRIES` | `int` | `8` | Max retry attempts on 429. Increased from the current implicit 3 to be more patient. |
| `SOW_LLM_RATE_LIMIT_BASE_DELAY` | `float` | `2.0` | Base delay in seconds for exponential backoff on 429 retries. |
| `SOW_LLM_RATE_LIMIT_MAX_DELAY` | `float` | `30.0` | Cap on backoff delay. Matches provider's `max_delay_s: 30.0`. |
| `SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS` | `int` | `300` | Total wall-clock budget for 429 retries (5 minutes). If all retries are consumed within this budget but keep getting 429, give up. Prevents infinite waiting while being patient enough for concurrent slots to drain. |

### 3. Fix `_llm_correct()` in `youtube_transcript.py` (lines 571-631)

**This is the primary fix — currently zero retry on 429.**

Rewrite `_llm_correct` to:

- Set `max_retries=0` on the OpenAI client (we handle retry ourselves for finer control over backoff and provider retry guidance)
- Acquire an LLM concurrency slot via `_acquire_llm_slot()` / `_release_llm_slot()` before/after the call
- Wrap the call in `_call_llm_with_rate_limit_retry()` from the new shared module
- On 429: retry with exponential backoff + jitter, respecting `retry_after` from the response body, up to `SOW_LLM_RATE_LIMIT_MAX_RETRIES` attempts or `SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS` wall-clock budget
- On non-429 error: raise `YouTubeTranscriptError` immediately (same as current behavior)
- On success after retries: return the response text
- On exhaustion: raise `YouTubeTranscriptError("LLM correction failed after N rate-limit retries (budget: Xs)")`

This means transient 429s (like the `concurrent_budget_exceeded` in the log) will be retried for up to 5 minutes before giving up, instead of failing instantly.

### 4. Fix `_llm_align()` in `lrc.py` (lines 556-675)

**Currently 3 retries with NO backoff sleep between attempts.**

Modify the retry loop:

- Set `max_retries=0` on the OpenAI client
- Acquire an LLM concurrency slot via `_acquire_llm_slot()` / `_release_llm_slot()` before/after each LLM call attempt
- Split the `except Exception` branch (line 671) into two:
  - **429 / rate-limit errors**: retry with exponential backoff + jitter (using the shared utility), respecting `retry_after`, with the 5-minute budget and up to `SOW_LLM_RATE_LIMIT_MAX_RETRIES` attempts. Log clearly that this is a rate limit and we're backing off.
  - **Other errors**: keep existing behavior (immediate retry, up to 3 attempts for parse/validation errors)
- Keep `json.JSONDecodeError` and `ValueError` handling the same (these are parse/validation errors, not rate limits — immediate retry is fine)
- The `max_retries` parameter default stays at 3 for parse-error retries; rate-limit retries use `settings.SOW_LLM_RATE_LIMIT_MAX_RETRIES` separately

### 5. Tests

#### `tests/test_llm_rate_limit.py` (new)

Unit tests for the shared utility:

- `test_is_llm_rate_limited_error_status_code` — OpenAI SDK `RateLimitError` with `status_code=429` detected
- `test_is_llm_rate_limited_error_string_fallback` — exception with "429" in message detected
- `test_is_llm_rate_limited_error_concurrent_budget` — exception with "concurrent_budget_exceeded" detected
- `test_is_llm_rate_limited_error_non_429` — non-429 exception not detected
- `test_extract_retry_after_from_response` — parses `retry_after` from error body
- `test_extract_retry_after_from_strategy` — falls back to `retry_strategy.suggested_initial_delay_s`
- `test_extract_retry_after_none` — returns None when no retry info present
- `test_call_llm_retries_on_429_then_succeeds` — mock SDK call fails 2x with 429, succeeds on 3rd → result returned, sleep called
- `test_call_llm_respects_retry_after` — backoff delay >= `retry_after` from response
- `test_call_llm_non_429_propagates` — non-429 error raised immediately, no retry
- `test_call_llm_exhausts_retries` — all attempts fail with 429 → raises after `max_retries`
- `test_call_llm_timeout_budget_exceeded` — retries stop after `total_timeout` wall-clock budget
- `test_call_llm_backoff_increases_exponentially` — verify delay sequence is roughly `base * 2^n`
- `test_concurrency_semaphore_limits_parallel_calls` — semaphore blocks when `max_concurrent` reached

#### `tests/test_youtube_transcript.py` (additions)

- `test_llm_correct_retries_on_429` — mock OpenAI client raises 429 twice, succeeds third → `_llm_correct` returns result
- `test_llm_correct_429_exhausts_retries` — mock OpenAI client always raises 429 → `YouTubeTranscriptError` raised
- `test_llm_correct_non_429_no_retry` — mock OpenAI client raises non-429 → `YouTubeTranscriptError` raised immediately, no retry
- `test_llm_correct_respects_retry_after` — verify backoff sleep respects `retry_after` from response body

#### `tests/integration/test_lrc_worker.py` (additions)

- `test_llm_align_retries_on_429_with_backoff` — mock OpenAI client raises 429, verify `asyncio.sleep` called with backoff delay before retry
- `test_llm_align_429_then_parse_error` — 429 on first attempt, JSON parse error on second → retries with backoff for 429, then retries immediately for parse error
- `test_llm_align_non_429_error_no_backoff` — non-429 error → no backoff sleep, immediate retry

## Behavior Change Summary

| Scenario | Before | After |
|---|---|---|
| LLM 429 during `_llm_correct` (YouTube path) | Fails immediately → fallback to Whisper ASR | Retries for up to 5 min with backoff → likely succeeds, avoids ASR |
| LLM 429 during `_llm_align` (Whisper/Qwen3 path) | 3 immediate retries (worsens rate limit) → failure | Backoff with jitter, up to 8 retries/5 min → likely succeeds |
| Multiple concurrent LRC jobs | All hit LLM simultaneously → 429 cascade | Semaphore limits to 3 concurrent → avoids self-inflicted 429 |
| Non-429 LLM error (auth, parse) | Propagates immediately | Unchanged — propagates immediately |

## Files Modified

| File | Change |
|---|---|
| `ops/analysis-service/src/sow_analysis/config.py` | Add 5 new `SOW_LLM_RATE_LIMIT_*` and `SOW_LLM_MAX_CONCURRENT` settings |
| `ops/analysis-service/src/sow_analysis/workers/llm_rate_limit.py` | **New file** (~120 lines): `_is_llm_rate_limited_error`, `_extract_retry_after`, `_call_llm_with_rate_limit_retry`, concurrency semaphore |
| `ops/analysis-service/src/sow_analysis/workers/youtube_transcript.py` | Rewrite `_llm_correct()` to use shared retry utility (~30 lines changed) |
| `ops/analysis-service/src/sow_analysis/workers/lrc.py` | Modify `_llm_align()` retry loop to add backoff on 429 (~20 lines changed) |
| `ops/analysis-service/tests/test_llm_rate_limit.py` | **New file**: unit tests for shared utility |
| `ops/analysis-service/tests/test_youtube_transcript.py` | Add tests for `_llm_correct` 429 retry behavior |
| `ops/analysis-service/tests/integration/test_lrc_worker.py` | Add tests for `_llm_align` 429 backoff behavior |

## Verification

```bash
# Run all analysis service tests
cd ops/analysis-service && PYTHONPATH=src pytest tests/ -v

# Run specific test files
cd ops/analysis-service && PYTHONPATH=src pytest tests/test_llm_rate_limit.py -v
cd ops/analysis-service && PYTHONPATH=src pytest tests/test_youtube_transcript.py -v
cd ops/analysis-service && PYTHONPATH=src pytest tests/integration/test_lrc_worker.py -v
```
