"""Shared LLM rate-limit retry utility.

Provides exponential backoff with jitter for LLM API calls (OpenAI-compatible)
that receive HTTP 429 rate-limit responses. Used by both ``_llm_correct``
(YouTube transcript path) and ``_llm_align`` (Whisper/Qwen3 ASR path) to
avoid falling back to expensive local ASR when transient rate limits occur.

Also provides a module-level concurrency semaphore (``_llm_semaphore``) that
limits the number of simultaneous LLM calls across all LRC jobs, preventing
self-inflicted ``concurrent_budget_exceeded`` 429s.
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


async def _call_llm_with_rate_limit_retry(
    sync_fn: Callable[[], str],
    *,
    description: str,
    loop: Optional[asyncio.AbstractEventLoop] = None,
) -> str:
    """Run a synchronous LLM call with rate-limit retry logic.

    Args:
        sync_fn: Synchronous callable that performs the OpenAI SDK call
            (with ``max_retries=0`` on the client to disable SDK-level retries).
        description: Human-readable description for logging.
        loop: Optional event loop. If None, uses ``asyncio.get_event_loop()``.

    Returns:
        The LLM response text.

    Raises:
        The last exception if all retries are exhausted or a non-429 error occurs.
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
        try:
            return await loop.run_in_executor(None, sync_fn)
        except Exception as e:
            if not _is_llm_rate_limited_error(e):
                # Non-429 error — propagate immediately
                raise

            last_exception = e

            # Don't sleep after the last attempt
            if attempt >= max_attempts - 1:
                break

            # Check if we have budget remaining for another retry
            elapsed = time.monotonic() - start_time
            remaining_budget = total_timeout - elapsed
            if remaining_budget <= 0:
                logger.warning(
                    "LLM rate-limit retry budget exhausted for %s "
                    "(%.1fs elapsed, %.1fs budget) — giving up",
                    description,
                    elapsed,
                    total_timeout,
                )
                break

            # Extract provider retry guidance
            retry_after = _extract_retry_after(e)
            provider_base, provider_max = _extract_backoff_config(e)

            # Use provider's backoff config if available, else settings
            effective_base = provider_base if provider_base != base_delay else base_delay
            effective_max = provider_max if provider_max != max_delay else max_delay

            # Compute backoff delay
            # Exponential: min(base * 2^attempt, max_delay) + jitter (0-25%)
            exp_delay = min(effective_base * (2 ** attempt), effective_max)

            # Respect retry_after as minimum delay
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

            await asyncio.sleep(delay)

    # All attempts exhausted or budget exceeded
    elapsed = time.monotonic() - start_time
    if last_exception is not None:
        raise last_exception
    raise RuntimeError(
        f"LLM rate-limit retry exhausted for {description} "
        f"after {max_attempts} attempts ({elapsed:.1f}s elapsed)"
    )
