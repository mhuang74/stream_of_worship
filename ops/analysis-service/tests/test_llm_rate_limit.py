"""Tests for the LLM rate-limit retry utility."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from sow_analysis.workers.llm_rate_limit import (
    _acquire_llm_slot,
    _call_llm_with_rate_limit_retry,
    _extract_retry_after,
    _is_llm_rate_limited_error,
    _llm_semaphore,
    _release_llm_slot,
)


class FakeRateLimitError(Exception):
    """Simulates an OpenAI SDK RateLimitError."""

    def __init__(self, message="Rate limit exceeded", status_code=429, response=None):
        self.status_code = status_code
        self.response = response
        super().__init__(message)


def _make_429_response(body_text: str):
    """Create a mock response object with .text attribute."""
    mock = MagicMock()
    mock.text = body_text
    return mock


class TestIsLlmRateLimitedError:
    """Tests for _is_llm_rate_limited_error()."""

    def test_status_code_429_detected(self):
        """OpenAI SDK RateLimitError with status_code=429 detected."""
        err = FakeRateLimitError(status_code=429)
        assert _is_llm_rate_limited_error(err) is True

    def test_string_fallback_429(self):
        """Exception with '429' in message detected."""
        assert _is_llm_rate_limited_error(Exception("Error code: 429")) is True

    def test_concurrent_budget_exceeded(self):
        """Exception with 'concurrent_budget_exceeded' detected."""
        assert _is_llm_rate_limited_error(
            Exception("concurrent_budget_exceeded: 3/3 slots in use")
        ) is True

    def test_rate_limit_error_in_message(self):
        """Exception with 'rate_limit' in message detected."""
        assert _is_llm_rate_limited_error(
            Exception("rate_limit_error: too many requests")
        ) is True

    def test_non_429_not_detected(self):
        """Non-429 exception not detected."""
        assert _is_llm_rate_limited_error(Exception("Authentication failed")) is False

    def test_cause_chain_429(self):
        """429 detected in __cause__ chain."""
        cause = FakeRateLimitError(status_code=429)
        err = Exception("Wrapped error")
        err.__cause__ = cause
        assert _is_llm_rate_limited_error(err) is True

    def test_type_name_rate_limit_error(self):
        """Exception with 'RateLimitError' in type name detected."""

        class MyRateLimitError(Exception):
            pass

        assert _is_llm_rate_limited_error(MyRateLimitError("some error")) is True


class TestExtractRetryAfter:
    """Tests for _extract_retry_after()."""

    def test_parses_retry_after_from_response(self):
        """Parses retry_after from error response body."""
        body = '{"error": {"retry_after": 1.0, "retryable": true}}'
        response = _make_429_response(body)
        err = FakeRateLimitError(response=response)
        assert _extract_retry_after(err) == 1.0

    def test_falls_back_to_strategy_suggested_delay(self):
        """Falls back to retry_strategy.suggested_initial_delay_s."""
        body = '{"error": {"retry_strategy": {"suggested_initial_delay_s": 2.5}}}'
        response = _make_429_response(body)
        err = FakeRateLimitError(response=response)
        assert _extract_retry_after(err) == 2.5

    def test_returns_none_when_no_retry_info(self):
        """Returns None when no retry info present."""
        body = '{"error": {"message": "some other error"}}'
        response = _make_429_response(body)
        err = FakeRateLimitError(response=response)
        assert _extract_retry_after(err) is None

    def test_parses_from_sdk_error_message(self):
        """Parses retry_after from SDK error message string format."""
        # OpenAI SDK format: "Error code: 429 - {'error': {'retry_after': 3.0}}"
        err = Exception("Error code: 429 - {'error': {'retry_after': 3.0}}")
        assert _extract_retry_after(err) == 3.0

    def test_parses_full_openrouter_response(self):
        """Parses the full OpenRouter 429 response from the spec."""
        body = (
            '{"error": {"type": "rate_limit_error", "code": "concurrent_budget_exceeded", '
            '"message": "Concurrent limit reached", "retry_after": 1.0, '
            '"retry_strategy": {"type": "concurrent_drain", '
            '"suggested_initial_delay_s": 1.0, "max_delay_s": 30.0, '
            '"backoff": "exponential", "backoff_base": 2.0, "jitter": true}, '
            '"retryable": true}}'
        )
        response = _make_429_response(body)
        err = FakeRateLimitError(response=response)
        assert _extract_retry_after(err) == 1.0


class TestCallLlmWithRateLimitRetry:
    """Tests for _call_llm_with_rate_limit_retry()."""

    @pytest.fixture(autouse=True)
    def _setup_settings(self):
        """Patch settings for all tests in this class."""
        with patch("sow_analysis.workers.llm_rate_limit.settings") as mock_settings:
            mock_settings.SOW_LLM_RATE_LIMIT_MAX_RETRIES = 8
            mock_settings.SOW_LLM_RATE_LIMIT_BASE_DELAY = 2.0
            mock_settings.SOW_LLM_RATE_LIMIT_MAX_DELAY = 30.0
            mock_settings.SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS = 300
            mock_settings.SOW_LLM_MAX_CONCURRENT = 3
            yield

    @pytest.mark.asyncio
    async def test_retries_on_429_then_succeeds(self):
        """Mock SDK call fails 2x with 429, succeeds on 3rd → result returned."""
        call_count = 0

        def sync_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise FakeRateLimitError(status_code=429)
            return "success"

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            sleep_calls.append(duration)
            await original_sleep(0)

        with patch("sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep):
            result = await _call_llm_with_rate_limit_retry(
                sync_fn, description="test"
            )

        assert result == "success"
        assert call_count == 3
        assert len(sleep_calls) == 2

    @pytest.mark.asyncio
    async def test_respects_retry_after(self):
        """Backoff delay >= retry_after from response."""
        body = '{"error": {"retry_after": 5.0}}'
        response = _make_429_response(body)
        call_count = 0

        def sync_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise FakeRateLimitError(response=response, status_code=429)
            return "ok"

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            sleep_calls.append(duration)
            await original_sleep(0)

        with patch("sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep):
            result = await _call_llm_with_rate_limit_retry(
                sync_fn, description="test"
            )

        assert result == "ok"
        assert len(sleep_calls) == 1
        # Delay should be >= retry_after (5.0), plus jitter
        assert sleep_calls[0] >= 5.0

    @pytest.mark.asyncio
    async def test_non_429_propagates(self):
        """Non-429 error raised immediately, no retry."""
        call_count = 0

        def sync_fn():
            nonlocal call_count
            call_count += 1
            raise ValueError("Authentication failed")

        with pytest.raises(ValueError, match="Authentication failed"):
            await _call_llm_with_rate_limit_retry(sync_fn, description="test")

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_exhausts_retries(self):
        """All attempts fail with 429 → raises after max_retries."""
        call_count = 0

        def sync_fn():
            nonlocal call_count
            call_count += 1
            raise FakeRateLimitError(status_code=429)

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            sleep_calls.append(duration)
            await original_sleep(0)

        with patch("sow_analysis.workers.llm_rate_limit.settings") as mock_settings:
            mock_settings.SOW_LLM_RATE_LIMIT_MAX_RETRIES = 3
            mock_settings.SOW_LLM_RATE_LIMIT_BASE_DELAY = 0.01
            mock_settings.SOW_LLM_RATE_LIMIT_MAX_DELAY = 0.1
            mock_settings.SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS = 300
            mock_settings.SOW_LLM_MAX_CONCURRENT = 3

            with patch(
                "sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep
            ):
                with pytest.raises(FakeRateLimitError):
                    await _call_llm_with_rate_limit_retry(
                        sync_fn, description="test"
                    )

        assert call_count == 3
        assert len(sleep_calls) == 2  # sleeps between attempts, not after last

    @pytest.mark.asyncio
    async def test_timeout_budget_exceeded(self):
        """Retries stop after total_timeout wall-clock budget."""
        call_count = 0

        def sync_fn():
            nonlocal call_count
            call_count += 1
            raise FakeRateLimitError(status_code=429)

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            sleep_calls.append(duration)
            await original_sleep(0)

        with patch("sow_analysis.workers.llm_rate_limit.settings") as mock_settings:
            mock_settings.SOW_LLM_RATE_LIMIT_MAX_RETRIES = 100
            mock_settings.SOW_LLM_RATE_LIMIT_BASE_DELAY = 0.01
            mock_settings.SOW_LLM_RATE_LIMIT_MAX_DELAY = 0.1
            mock_settings.SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS = 0  # No budget
            mock_settings.SOW_LLM_MAX_CONCURRENT = 3

            with patch(
                "sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep
            ):
                with pytest.raises(FakeRateLimitError):
                    await _call_llm_with_rate_limit_retry(
                        sync_fn, description="test"
                    )

        # With 0 budget, should try once, then give up (no sleep)
        assert call_count == 1
        assert len(sleep_calls) == 0

    @pytest.mark.asyncio
    async def test_backoff_increases_exponentially(self):
        """Verify delay sequence is roughly base * 2^n."""
        delays = []

        def sync_fn():
            raise FakeRateLimitError(status_code=429)

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            sleep_calls.append(duration)
            await original_sleep(0)

        with patch("sow_analysis.workers.llm_rate_limit.settings") as mock_settings:
            mock_settings.SOW_LLM_RATE_LIMIT_MAX_RETRIES = 5
            mock_settings.SOW_LLM_RATE_LIMIT_BASE_DELAY = 2.0
            mock_settings.SOW_LLM_RATE_LIMIT_MAX_DELAY = 30.0
            mock_settings.SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS = 300
            mock_settings.SOW_LLM_MAX_CONCURRENT = 3

            with patch(
                "sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep
            ):
                with pytest.raises(FakeRateLimitError):
                    await _call_llm_with_rate_limit_retry(
                        sync_fn, description="test"
                    )

        # 4 sleeps for 5 attempts
        assert len(sleep_calls) == 4
        # Each delay should be roughly base * 2^attempt (plus jitter 0-25%)
        # attempt 0: ~2.0, attempt 1: ~4.0, attempt 2: ~8.0, attempt 3: ~16.0
        # Verify increasing trend
        assert sleep_calls[1] > sleep_calls[0]
        assert sleep_calls[2] > sleep_calls[1]
        assert sleep_calls[3] > sleep_calls[2]
        # Verify rough magnitude (delay = base * 2^attempt + 0-25% jitter)
        # So delay ranges from exp_delay to exp_delay * 1.25
        assert 2.0 <= sleep_calls[0] <= 2.0 * 1.25 + 0.1
        assert 4.0 * 0.8 <= sleep_calls[1] <= 4.0 * 1.25 + 0.1
        assert 8.0 * 0.8 <= sleep_calls[2] <= 8.0 * 1.25 + 0.1
        assert 16.0 * 0.8 <= sleep_calls[3] <= 16.0 * 1.25 + 0.1


class TestConcurrencySemaphore:
    """Tests for the LLM concurrency semaphore."""

    @pytest.mark.asyncio
    async def test_semaphore_limits_parallel_calls(self):
        """Semaphore blocks when max_concurrent reached."""
        import sow_analysis.workers.llm_rate_limit as mod

        # Reset module-level semaphore
        mod._llm_semaphore = None

        with patch("sow_analysis.workers.llm_rate_limit.settings") as mock_settings:
            mock_settings.SOW_LLM_MAX_CONCURRENT = 1

            current_concurrent = 0
            max_observed = 0

            async def task():
                nonlocal current_concurrent, max_observed
                await _acquire_llm_slot()
                try:
                    current_concurrent += 1
                    max_observed = max(max_observed, current_concurrent)
                    await asyncio.sleep(0.05)
                    current_concurrent -= 1
                finally:
                    _release_llm_slot()

            tasks = [task() for _ in range(5)]
            await asyncio.gather(*tasks)

        assert max_observed == 1

        # Cleanup
        mod._llm_semaphore = None

    @pytest.mark.asyncio
    async def test_max_concurrent_zero_disables_semaphore(self):
        """With max_concurrent=0, semaphore is disabled (no limit)."""
        import sow_analysis.workers.llm_rate_limit as mod

        mod._llm_semaphore = None

        with patch("sow_analysis.workers.llm_rate_limit.settings") as mock_settings:
            mock_settings.SOW_LLM_MAX_CONCURRENT = 0

            await _acquire_llm_slot()
            _release_llm_slot()

            assert mod._llm_semaphore is None

        mod._llm_semaphore = None
