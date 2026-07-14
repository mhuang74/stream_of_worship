"""Shared LLM rate-limit retry utility.

Provides exponential backoff with jitter for LLM API calls (OpenAI-compatible)
that receive HTTP 429 rate-limit responses or transient 5xx errors (notably
Cloudflare 524). Used by both ``_llm_correct`` (YouTube transcript path) and
``_llm_align`` (Whisper/Qwen3 ASR path) to avoid falling back to expensive
local ASR when transient errors occur.

Also provides:
- A module-level concurrency semaphore (``_llm_semaphore``) that limits the
  number of simultaneous LLM calls across all LRC jobs, preventing
  self-inflicted ``concurrent_budget_exceeded`` 429s.
- A module-level min-interval throttle (``_enforce_llm_min_interval``) that
  paces LLM HTTP calls to compensate for provider-side in_flight accounting
  lag. Fires after acquiring the semaphore slot, so only active (slot-holding)
  jobs are paced.

The unified entry point ``call_llm_with_retry`` handles semaphore management,
throttling, and retry/backoff internally. Callers simply pass a sync callable
and receive the result (or the last exception on exhaustion).
"""

import asyncio
import json
import logging
import random
import time
from typing import Any, Callable, Optional

from ..config import settings

logger = logging.getLogger(__name__)

# Module-level LLM concurrency limiter.
# Lazily initialized on first use within the event loop.
_llm_semaphore: Optional[asyncio.Semaphore] = None

# Module-level min-interval throttle state.
# Lazily initialized on first use within the event loop.
_llm_interval_lock: Optional[asyncio.Lock] = None
_llm_last_request_time: float = 0.0


async def _acquire_llm_slot() -> None:
    """Acquire a concurrency slot before making an LLM call.

    Uses a module-level semaphore initialized to ``SOW_LLM_MAX_CONCURRENT``.
    When ``SOW_LLM_MAX_CONCURRENT`` is 0, the semaphore is disabled (no limit).
    """
    global _llm_semaphore
    if _llm_semaphore is None:
        max_concurrent = settings.SOW_LLM_MAX_CONCURRENT
        if max_concurrent > 0:
            _llm_semaphore = asyncio.Semaphore(max_concurrent)
        else:
            _llm_semaphore = None  # disabled
    if _llm_semaphore is not None:
        await _llm_semaphore.acquire()


def _release_llm_slot() -> None:
    """Release a concurrency slot after an LLM call completes."""
    if _llm_semaphore is not None:
        _llm_semaphore.release()


def _is_llm_rate_limited_error(e: Exception) -> bool:
    """Detect whether an exception is an LLM 429 rate-limit error.

    Detection strategies (in order):
    1. Check ``status_code == 429`` on the exception and its
       ``__cause__``/``__context__`` chain (OpenAI SDK's
       ``RateLimitError`` has ``.status_code``).
    2. Check exception type name for ``RateLimitError`` or ``rate_limit``.
    3. String fallback: check for "429", "rate_limit", "rate_limit_error",
       "concurrent_budget_exceeded" in the exception message and cause chain.
    """
    visited: set[int] = set()
    exc: Optional[Exception] = e

    # Strategy 1: Check status_code attribute
    while exc is not None and id(exc) not in visited:
        visited.add(id(exc))
        status_code = getattr(exc, "status_code", None)
        if status_code == 429:
            return True
        code = getattr(exc, "code", None)
        if code == 429:
            return True
        exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)

    # Strategy 2: Check exception type name
    visited = set()
    exc = e
    while exc is not None and id(exc) not in visited:
        visited.add(id(exc))
        type_name = type(exc).__name__
        if "RateLimitError" in type_name or "rate_limit" in type_name.lower():
            return True
        exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)

    # Strategy 3: String-based fallback
    rate_limit_markers = (
        "429",
        "rate_limit",
        "rate_limit_error",
        "concurrent_budget_exceeded",
    )
    visited = set()
    exc = e
    while exc is not None and id(exc) not in visited:
        visited.add(id(exc))
        error_str = str(exc)
        for marker in rate_limit_markers:
            if marker in error_str:
                return True
        exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)

    return False


def _is_llm_retryable_error(e: Exception) -> bool:
    """Detect whether an exception is a transient 5xx error worth retrying.

    Short-circuit: returns ``False`` immediately if the exception (or any
    exception in its cause chain) is a 429 rate-limit error. This prevents
    429s from being misclassified/logged as "retryable 5xx".

    Detection strategies (after the short-circuit):
    1. Status code check: ``status_code`` or ``code`` attribute in
       ``{500, 502, 503, 504, 520, 521, 522, 523, 524, 529}``.
    2. Type-name check: ``type(exc).__name__`` in
       ``{"APITimeoutError", "APIConnectionError"}`` (OpenAI SDK exception
       types). Excludes ``APIStatusError`` (broad base class that includes
       400 Bad Request). Does NOT explicitly exclude ``RateLimitError`` —
       the 429 short-circuit above already handles that case.
    3. Provider JSON body check: parse the exception message/body via
       ``_extract_json_from_text``, then check for ``"retryable": true``.

    Returns ``False`` for parse errors (``ValueError``, ``json.JSONDecodeError``)
    and other non-transient errors.
    """
    # Short-circuit: if this is a 429 rate-limit error, it's not a "retryable 5xx"
    if _is_llm_rate_limited_error(e):
        return False

    # Also short-circuit on any status_code==429 / code==429 in the chain
    visited: set[int] = set()
    exc: Optional[Exception] = e
    while exc is not None and id(exc) not in visited:
        visited.add(id(exc))
        for attr in ("status_code", "code"):
            val = getattr(exc, attr, None)
            if val is not None:
                try:
                    if int(val) == 429:
                        return False
                except (TypeError, ValueError):
                    pass
        exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)

    retryable_status_codes = {500, 502, 503, 504, 520, 521, 522, 523, 524, 529}

    # Strategy 1: Check status_code / code attribute
    visited = set()
    exc = e
    while exc is not None and id(exc) not in visited:
        visited.add(id(exc))
        for attr in ("status_code", "code"):
            val = getattr(exc, attr, None)
            if val is not None:
                try:
                    if int(val) in retryable_status_codes:
                        return True
                except (TypeError, ValueError):
                    pass
        exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)

    # Strategy 2: Check exception type name (OpenAI SDK exception types)
    retryable_type_names = {"APITimeoutError", "APIConnectionError"}
    visited = set()
    exc = e
    while exc is not None and id(exc) not in visited:
        visited.add(id(exc))
        type_name = type(exc).__name__
        if type_name in retryable_type_names:
            return True
        exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)

    # Strategy 3: Provider JSON body check for "retryable": true
    visited = set()
    exc = e
    while exc is not None and id(exc) not in visited:
        visited.add(id(exc))

        body = None
        response = getattr(exc, "response", None)
        if response is not None:
            body = getattr(response, "text", None)
        if body is None:
            body = getattr(exc, "body", None)
        if body is None:
            body = str(exc)

        data = None
        if isinstance(body, dict):
            data = body
        elif isinstance(body, (str, bytes)):
            text = body if isinstance(body, str) else body.decode("utf-8", errors="replace")
            try:
                data = _extract_json_from_text(text)
            except (ValueError, json.JSONDecodeError):
                data = None

        if isinstance(data, dict):
            error_obj = data.get("error", data)
            if isinstance(error_obj, dict):
                retryable = error_obj.get("retryable")
                if retryable is True:
                    return True

        exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)

    return False


def _extract_status_code(e: Exception) -> Optional[int]:
    """Extract the HTTP status code from an exception's cause chain.

    Walks the exception chain (same pattern as ``_is_llm_rate_limited_error``)
    and returns the first ``status_code`` or ``code`` attribute found.
    Used for the 5xx log message.
    """
    visited: set[int] = set()
    exc: Optional[Exception] = e
    while exc is not None and id(exc) not in visited:
        visited.add(id(exc))
        status_code = getattr(exc, "status_code", None)
        if status_code is not None:
            try:
                return int(status_code)
            except (TypeError, ValueError):
                pass
        code = getattr(exc, "code", None)
        if code is not None:
            try:
                return int(code)
            except (TypeError, ValueError):
                pass
        exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)
    return None


def _extract_retry_after(e: Exception) -> Optional[float]:
    """Parse the ``retry_after`` field from a 429 error response body.

    The OpenAI SDK's ``RateLimitError`` includes the JSON response body.
    The OpenRouter response includes::

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

    Returns ``retry_after`` if present, else
    ``retry_strategy.suggested_initial_delay_s``, else ``None``.
    """
    visited: set[int] = set()
    exc: Optional[Exception] = e

    while exc is not None and id(exc) not in visited:
        visited.add(id(exc))

        # Try to get the response body from various attributes
        body = None
        response = getattr(exc, "response", None)
        if response is not None:
            # httpx.Response object — .text or .json()
            body = getattr(response, "text", None)

        # Some SDK versions store the body in .body or .error
        if body is None:
            body = getattr(exc, "body", None)
        if body is None:
            # The error message itself may contain the JSON body
            body = str(exc)

        retry_after = _parse_retry_after_from_body(body)
        if retry_after is not None:
            return retry_after

        exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)

    return None


def _parse_retry_after_from_body(body: Any) -> Optional[float]:
    """Parse retry_after from a response body (string, dict, or bytes)."""
    data = None

    if isinstance(body, dict):
        data = body
    elif isinstance(body, (str, bytes)):
        text = body if isinstance(body, str) else body.decode("utf-8", errors="replace")
        # Try to extract JSON from the error message
        # The OpenAI SDK error message often looks like:
        # "Error code: 429 - {'error': {'retry_after': 1.0, ...}}"
        data = _extract_json_from_text(text)
    elif hasattr(body, "__dict__"):
        data = body.__dict__

    if data is None:
        return None

    # Navigate the error structure
    error_obj = data.get("error", data) if isinstance(data, dict) else None
    if not isinstance(error_obj, dict):
        return None

    # Direct retry_after
    retry_after = error_obj.get("retry_after")
    if retry_after is not None:
        try:
            return float(retry_after)
        except (TypeError, ValueError):
            pass

    # Fallback: retry_strategy.suggested_initial_delay_s
    retry_strategy = error_obj.get("retry_strategy")
    if isinstance(retry_strategy, dict):
        suggested = retry_strategy.get("suggested_initial_delay_s")
        if suggested is not None:
            try:
                return float(suggested)
            except (TypeError, ValueError):
                pass

    return None


def _extract_json_from_text(text: str) -> Optional[dict]:
    """Extract a JSON dict from text that may contain embedded JSON.

    Handles formats like:
    - "Error code: 429 - {'error': {...}}"
    - Full JSON string
    - Python dict repr with single quotes
    """
    # Try direct JSON parse first
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # Try to find JSON/dict in the text after " - " separator
    # (OpenAI SDK format: "Error code: 429 - {...}")
    for sep in (" - ", ": "):
        if sep in text:
            _, _, rest = text.partition(sep)
            rest = rest.strip()
            # Try JSON parse
            try:
                return json.loads(rest)
            except (json.JSONDecodeError, TypeError):
                pass
            # Try Python dict literal (single quotes) via ast
            try:
                import ast

                parsed = ast.literal_eval(rest)
                if isinstance(parsed, dict):
                    return parsed
            except (ValueError, SyntaxError):
                pass

    return None


def _extract_backoff_config(e: Exception) -> tuple[float, float]:
    """Extract (backoff_base, max_delay) from the error's retry_strategy.

    Returns (settings defaults) if not found.
    """
    default_base = settings.SOW_LLM_RATE_LIMIT_BASE_DELAY
    default_max = settings.SOW_LLM_RATE_LIMIT_MAX_DELAY

    visited: set[int] = set()
    exc: Optional[Exception] = e

    while exc is not None and id(exc) not in visited:
        visited.add(id(exc))

        body = None
        response = getattr(exc, "response", None)
        if response is not None:
            body = getattr(response, "text", None)
        if body is None:
            body = getattr(exc, "body", None)
        if body is None:
            body = str(exc)

        data = None
        if isinstance(body, dict):
            data = body
        elif isinstance(body, (str, bytes)):
            text = body if isinstance(body, str) else body.decode("utf-8", errors="replace")
            data = _extract_json_from_text(text)

        if isinstance(data, dict):
            error_obj = data.get("error", data)
            if isinstance(error_obj, dict):
                retry_strategy = error_obj.get("retry_strategy")
                if isinstance(retry_strategy, dict):
                    backoff_base = retry_strategy.get("backoff_base")
                    max_delay = retry_strategy.get("max_delay_s")
                    result_base = default_base
                    result_max = default_max
                    if backoff_base is not None:
                        try:
                            result_base = float(backoff_base)
                        except (TypeError, ValueError):
                            pass
                    if max_delay is not None:
                        try:
                            result_max = float(max_delay)
                        except (TypeError, ValueError):
                            pass
                    return (result_base, result_max)

        exc = getattr(exc, "__cause__", None) or getattr(exc, "__context__", None)

    return (default_base, default_max)


async def _enforce_llm_min_interval() -> None:
    """Sleep if the last LLM HTTP request was too recent.

    Mirrors ``_enforce_min_interval()`` in ``youtube_transcript.py``, but with
    jitter applied to the **target interval** (not the wait delta) to avoid
    ``wait <= 0`` edge cases.

    Fires after acquiring the LLM semaphore slot, so only active (slot-holding)
    jobs are paced. Idle jobs waiting on the semaphore do not consume throttle
    budget.
    """
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

    Args:
        sync_fn: Synchronous callable that performs the OpenAI SDK call
            (with ``max_retries=0`` on the client to disable SDK-level retries).
        description: Human-readable description for logging.
        loop: Optional event loop. If None, uses ``asyncio.get_running_loop()``.

    Returns:
        The LLM response text.

    Raises:
        The last exception if all retries are exhausted or a non-retryable
        error occurs.
    """
    if loop is None:
        loop = asyncio.get_running_loop()

    # These are global defaults covering both the YouTube-transcript LLM correction
    # step (_llm_correct) and the ASR-fallback LLM alignment step (_llm_align).
    # Provider-reported retry_strategy.max_delay_s from the OpenRouter error body
    # overrides max_delay dynamically when present.
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

            # Check budget (env-overridable via SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS)
            elapsed = time.monotonic() - start_time
            remaining_budget = total_timeout - elapsed
            if remaining_budget <= 0:
                logger.warning(
                    "LLM retry budget exhausted for %s (%.1fs elapsed, %.1fs budget) — giving up "
                    "(raise SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS to extend)",
                    description,
                    elapsed,
                    total_timeout,
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
                    description,
                    attempt + 1,
                    max_attempts,
                    delay,
                    f"{retry_after:.1f}s" if retry_after else "None",
                    remaining_budget,
                    e,
                )
            else:
                logger.warning(
                    "LLM transient error (%s) for %s, attempt %d/%d, "
                    "backing off %.1fs (retry_after=%s, budget remaining: %.1fs): %s",
                    status_code,
                    description,
                    attempt + 1,
                    max_attempts,
                    delay,
                    f"{retry_after:.1f}s" if retry_after else "None",
                    remaining_budget,
                    e,
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
        f"LLM retry exhausted for {description} after {max_attempts} attempts "
        f"({elapsed:.1f}s elapsed)"
    )
