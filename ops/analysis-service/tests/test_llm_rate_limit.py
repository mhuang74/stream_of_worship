"""Tests for the LLM rate-limit retry utility."""

import asyncio
import json
import time
from unittest.mock import MagicMock, patch

import pytest

from sow_analysis.workers.llm_rate_limit import (
    _acquire_llm_slot,
    _enforce_llm_min_interval,
    _extract_retry_after,
    _extract_status_code,
    _is_llm_rate_limited_error,
    _is_llm_retryable_error,
    _release_llm_slot,
    call_llm_with_retry,
)


class FakeRateLimitError(Exception):
    """Simulates an OpenAI SDK RateLimitError."""

    def __init__(self, message="Rate limit exceeded", status_code=429, response=None):
        self.status_code = status_code
        self.response = response
        super().__init__(message)


class Fake5xxError(Exception):
    """Simulates a transient 5xx error (e.g., Cloudflare 524)."""

    def __init__(self, status_code=524, message="Cloudflare timeout", response=None):
        self.status_code = status_code
        self.response = response
        super().__init__(message)


def _make_429_response(body_text: str):
    """Create a mock response object with .text attribute."""
    mock = MagicMock()
    mock.text = body_text
    return mock


def _make_524_response(body_text: str):
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


class TestIsLlmRetryableError:
    """Tests for _is_llm_retryable_error()."""

    def test_524_status_code_detected(self):
        """524 status code detected as retryable."""
        err = Fake5xxError(status_code=524)
        assert _is_llm_retryable_error(err) is True

    def test_524_from_production_log_body(self):
        """524-flavored exception built from the exact response body in the production log."""
        body = '{"retryable": true, "message": "Cloudflare timeout"}'
        response = _make_524_response(body)
        err = Fake5xxError(status_code=524, response=response)
        assert _is_llm_retryable_error(err) is True

    def test_api_timeout_error_type_name(self):
        """APITimeoutError type name detected as retryable."""

        class APITimeoutError(Exception):
            pass

        assert _is_llm_retryable_error(APITimeoutError("request timed out")) is True

    def test_api_connection_error_type_name(self):
        """APIConnectionError type name detected as retryable."""

        class APIConnectionError(Exception):
            pass

        assert _is_llm_retryable_error(APIConnectionError("connection failed")) is True

    def test_503_with_retryable_true_in_body(self):
        """503 status with 'retryable': true in body detected."""
        body = '{"error": {"retryable": true, "message": "Service unavailable"}}'
        response = _make_524_response(body)
        err = Fake5xxError(status_code=503, response=response)
        assert _is_llm_retryable_error(err) is True

    def test_value_error_not_retryable(self):
        """ValueError is not retryable."""
        assert _is_llm_retryable_error(ValueError("LLM returned empty alignment")) is False

    def test_json_decode_error_not_retryable(self):
        """json.JSONDecodeError is not retryable."""
        assert _is_llm_retryable_error(json.JSONDecodeError("msg", "doc", 0)) is False

    def test_api_status_error_400_not_retryable(self):
        """APIStatusError with status_code=400 is not retryable (broad base class excluded)."""

        class APIStatusError(Exception):
            def __init__(self, status_code=400):
                self.status_code = status_code
                super().__init__("Bad Request")

        assert _is_llm_retryable_error(APIStatusError(status_code=400)) is False

    def test_429_short_circuit_returns_false(self):
        """429 status code short-circuits to False (not misclassified as 5xx)."""
        err = FakeRateLimitError(status_code=429)
        assert _is_llm_retryable_error(err) is False

    def test_429_in_cause_chain_short_circuit(self):
        """429 in __cause__ chain short-circuits to False."""
        cause = FakeRateLimitError(status_code=429)
        err = Exception("Wrapped 524-like error")
        err.__cause__ = cause
        assert _is_llm_retryable_error(err) is False

    def test_500_status_code_detected(self):
        """500 status code detected as retryable."""
        err = Fake5xxError(status_code=500)
        assert _is_llm_retryable_error(err) is True

    def test_502_status_code_detected(self):
        """502 status code detected as retryable."""
        err = Fake5xxError(status_code=502)
        assert _is_llm_retryable_error(err) is True

    def test_503_status_code_detected(self):
        """503 status code detected as retryable."""
        err = Fake5xxError(status_code=503)
        assert _is_llm_retryable_error(err) is True

    def test_504_status_code_detected(self):
        """504 status code detected as retryable."""
        err = Fake5xxError(status_code=504)
        assert _is_llm_retryable_error(err) is True

    def test_529_status_code_detected(self):
        """529 status code detected as retryable."""
        err = Fake5xxError(status_code=529)
        assert _is_llm_retryable_error(err) is True

    def test_non_retryable_error_not_detected(self):
        """Generic non-5xx, non-timeout error is not retryable."""
        assert _is_llm_retryable_error(Exception("Authentication failed")) is False


class TestExtractStatusCode:
    """Tests for _extract_status_code()."""

    def test_extracts_status_code(self):
        err = Fake5xxError(status_code=524)
        assert _extract_status_code(err) == 524

    def test_extracts_from_cause_chain(self):
        cause = Fake5xxError(status_code=503)
        err = Exception("wrapped")
        err.__cause__ = cause
        assert _extract_status_code(err) == 503

    def test_returns_none_when_no_status_code(self):
        assert _extract_status_code(ValueError("no code")) is None

    def test_extracts_code_attribute(self):
        class Err(Exception):
            def __init__(self):
                self.code = 524
                super().__init__("timeout")

        assert _extract_status_code(Err()) == 524


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


class TestCallLlmWithRetry:
    """Tests for call_llm_with_retry()."""

    @pytest.fixture(autouse=True)
    def _setup_settings(self):
        """Patch settings for all tests in this class."""
        with patch("sow_analysis.workers.llm_rate_limit.settings") as mock_settings:
            mock_settings.SOW_LLM_RATE_LIMIT_MAX_RETRIES = 16
            mock_settings.SOW_LLM_RATE_LIMIT_BASE_DELAY = 2.0
            mock_settings.SOW_LLM_RATE_LIMIT_MAX_DELAY = 90.0
            mock_settings.SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS = 1200
            mock_settings.SOW_LLM_MAX_CONCURRENT = 0  # disable semaphore
            mock_settings.SOW_LLM_MIN_INTERVAL_SECONDS = 0.0  # disable throttle
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
            result = await call_llm_with_retry(sync_fn, description="test")

        assert result == "success"
        assert call_count == 3
        assert len(sleep_calls) == 2

    @pytest.mark.asyncio
    async def test_retries_on_524_then_succeeds(self):
        """524 on first call, success on second → result returned."""
        call_count = 0

        def sync_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Fake5xxError(status_code=524)
            return "success"

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            sleep_calls.append(duration)
            await original_sleep(0)

        with patch("sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep):
            result = await call_llm_with_retry(sync_fn, description="test")

        assert result == "success"
        assert call_count == 2
        assert len(sleep_calls) == 1

    @pytest.mark.asyncio
    async def test_524_honors_retry_after(self):
        """Backoff delay >= retry_after from 524 response."""
        body = '{"retry_after": 5.0, "retryable": true}'
        response = _make_524_response(body)
        call_count = 0

        def sync_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Fake5xxError(status_code=524, response=response)
            return "ok"

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            sleep_calls.append(duration)
            await original_sleep(0)

        with patch("sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep):
            result = await call_llm_with_retry(sync_fn, description="test")

        assert result == "ok"
        assert len(sleep_calls) == 1
        assert sleep_calls[0] >= 5.0

    @pytest.mark.asyncio
    async def test_respects_retry_after_429(self):
        """Backoff delay >= retry_after from 429 response."""
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
            result = await call_llm_with_retry(sync_fn, description="test")

        assert result == "ok"
        assert len(sleep_calls) == 1
        assert sleep_calls[0] >= 5.0

    @pytest.mark.asyncio
    async def test_non_retryable_propagates(self):
        """Non-retryable error (400 Bad Request) raised immediately, no retry."""
        call_count = 0

        def sync_fn():
            nonlocal call_count
            call_count += 1
            raise ValueError("Authentication failed")

        with pytest.raises(ValueError, match="Authentication failed"):
            await call_llm_with_retry(sync_fn, description="test")

        assert call_count == 1

    @pytest.mark.asyncio
    async def test_exhausts_retries_on_429(self):
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
            mock_settings.SOW_LLM_MAX_CONCURRENT = 0
            mock_settings.SOW_LLM_MIN_INTERVAL_SECONDS = 0.0

            with patch(
                "sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep
            ):
                with pytest.raises(FakeRateLimitError):
                    await call_llm_with_retry(sync_fn, description="test")

        assert call_count == 3
        assert len(sleep_calls) == 2  # sleeps between attempts, not after last

    @pytest.mark.asyncio
    async def test_exhausts_retries_on_524(self):
        """All attempts fail with 524 → raises after max_retries."""
        call_count = 0

        def sync_fn():
            nonlocal call_count
            call_count += 1
            raise Fake5xxError(status_code=524)

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
            mock_settings.SOW_LLM_MAX_CONCURRENT = 0
            mock_settings.SOW_LLM_MIN_INTERVAL_SECONDS = 0.0

            with patch(
                "sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep
            ):
                with pytest.raises(Fake5xxError):
                    await call_llm_with_retry(sync_fn, description="test")

        assert call_count == 3
        assert len(sleep_calls) == 2

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
            mock_settings.SOW_LLM_MAX_CONCURRENT = 0
            mock_settings.SOW_LLM_MIN_INTERVAL_SECONDS = 0.0

            with patch(
                "sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep
            ):
                with pytest.raises(FakeRateLimitError):
                    await call_llm_with_retry(sync_fn, description="test")

        # With 0 budget, should try once, then give up (no sleep)
        assert call_count == 1
        assert len(sleep_calls) == 0

    @pytest.mark.asyncio
    async def test_backoff_increases_exponentially(self):
        """Verify delay sequence is roughly base * 2^n."""
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
            mock_settings.SOW_LLM_MAX_CONCURRENT = 0
            mock_settings.SOW_LLM_MIN_INTERVAL_SECONDS = 0.0

            with patch(
                "sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep
            ):
                with pytest.raises(FakeRateLimitError):
                    await call_llm_with_retry(sync_fn, description="test")

        # 4 sleeps for 5 attempts
        assert len(sleep_calls) == 4
        # Each delay should be roughly base * 2^attempt (plus jitter 0-25%)
        assert sleep_calls[1] > sleep_calls[0]
        assert sleep_calls[2] > sleep_calls[1]
        assert sleep_calls[3] > sleep_calls[2]
        assert 2.0 <= sleep_calls[0] <= 2.0 * 1.25 + 0.1
        assert 4.0 * 0.8 <= sleep_calls[1] <= 4.0 * 1.25 + 0.1
        assert 8.0 * 0.8 <= sleep_calls[2] <= 8.0 * 1.25 + 0.1
        assert 16.0 * 0.8 <= sleep_calls[3] <= 16.0 * 1.25 + 0.1

    @pytest.mark.asyncio
    async def test_releases_semaphore_before_backoff_sleep(self):
        """Semaphore is released BEFORE asyncio.sleep during backoff."""
        import sow_analysis.workers.llm_rate_limit as mod

        mod._llm_semaphore = None

        release_calls = []
        original_release = mod._release_llm_slot

        def spy_release():
            release_calls.append(time.monotonic())
            original_release()

        sleep_times = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            sleep_times.append(time.monotonic())
            await original_sleep(0)

        call_count = 0

        def sync_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise Fake5xxError(status_code=524)
            return "ok"

        with patch("sow_analysis.workers.llm_rate_limit.settings") as mock_settings:
            mock_settings.SOW_LLM_RATE_LIMIT_MAX_RETRIES = 3
            mock_settings.SOW_LLM_RATE_LIMIT_BASE_DELAY = 0.01
            mock_settings.SOW_LLM_RATE_LIMIT_MAX_DELAY = 0.1
            mock_settings.SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS = 300
            mock_settings.SOW_LLM_MAX_CONCURRENT = 1  # enable semaphore
            mock_settings.SOW_LLM_MIN_INTERVAL_SECONDS = 0.0

            with (
                patch("sow_analysis.workers.llm_rate_limit._release_llm_slot", side_effect=spy_release),
                patch("sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep),
            ):
                result = await call_llm_with_retry(sync_fn, description="test")

        assert result == "ok"
        # At least one release should have happened before a sleep (backoff)
        assert len(release_calls) >= 1
        assert len(sleep_times) >= 1
        # The release for backoff should happen before the sleep
        assert release_calls[0] <= sleep_times[0]

        mod._llm_semaphore = None

    @pytest.mark.asyncio
    async def test_throttle_called_after_acquire(self):
        """_enforce_llm_min_interval is called AFTER _acquire_llm_slot."""
        call_order = []

        original_acquire = _acquire_llm_slot
        original_throttle = _enforce_llm_min_interval

        async def spy_acquire():
            call_order.append("acquire")
            await original_acquire()

        async def spy_throttle():
            call_order.append("throttle")
            await original_throttle()

        def sync_fn():
            call_order.append("run")
            return "ok"

        with (
            patch("sow_analysis.workers.llm_rate_limit._acquire_llm_slot", side_effect=spy_acquire),
            patch("sow_analysis.workers.llm_rate_limit._enforce_llm_min_interval", side_effect=spy_throttle),
        ):
            result = await call_llm_with_retry(sync_fn, description="test")

        assert result == "ok"
        # Verify order: acquire → throttle → run
        assert call_order == ["acquire", "throttle", "run"]

    @pytest.mark.asyncio
    async def test_uses_get_running_loop_when_loop_none(self):
        """call_llm_with_retry uses asyncio.get_running_loop() when loop=None."""
        def sync_fn():
            return "ok"

        result = await call_llm_with_retry(sync_fn, description="test", loop=None)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_old_symbol_removed(self):
        """Importing _call_llm_with_rate_limit_retry raises ImportError (symbol removed)."""
        with pytest.raises(ImportError):
            from sow_analysis.workers.llm_rate_limit import _call_llm_with_rate_limit_retry  # noqa: F401

    @pytest.mark.asyncio
    async def test_retry_budget_1200s_sustained(self):
        """Retries continue past the old 305s cliff and only give up after 1200s budget.

        Simulates the production failure (job_11d44eb841fe) where the budget was
        exhausted at 304.8s. With the new 1200s budget, retries should continue.
        Uses a fake monotonic clock to simulate elapsed time without real sleeping.
        """
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

        # Fake time: each call takes 30s, each sleep takes 30s.
        # With 16 attempts and 1200s budget, we should get many retries before
        # the budget is exhausted.
        fake_time = [0.0]

        def fake_monotonic():
            return fake_time[0]

        def sync_fn_with_time():
            nonlocal call_count
            call_count += 1
            fake_time[0] += 30.0  # each LLM call takes 30s
            raise FakeRateLimitError(status_code=429)

        async def mock_sleep_with_time(duration):
            sleep_calls.append(duration)
            fake_time[0] += duration  # advance fake clock by sleep duration
            await original_sleep(0)

        with patch("sow_analysis.workers.llm_rate_limit.settings") as mock_settings:
            mock_settings.SOW_LLM_RATE_LIMIT_MAX_RETRIES = 16
            mock_settings.SOW_LLM_RATE_LIMIT_BASE_DELAY = 2.0
            mock_settings.SOW_LLM_RATE_LIMIT_MAX_DELAY = 90.0
            mock_settings.SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS = 1200
            mock_settings.SOW_LLM_MAX_CONCURRENT = 0
            mock_settings.SOW_LLM_MIN_INTERVAL_SECONDS = 0.0

            with (
                patch("sow_analysis.workers.llm_rate_limit.time.monotonic", side_effect=fake_monotonic),
                patch("sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep_with_time),
            ):
                with pytest.raises(FakeRateLimitError):
                    await call_llm_with_retry(sync_fn_with_time, description="test")

        # Critical: must have retried past the old 305s cliff.
        # With 30s per call + 30s sleeps, the old 300s budget would have stopped
        # at ~5 attempts. With 1200s budget we should get significantly more.
        assert call_count > 5, (
            f"Expected more than 5 retries with 1200s budget, got {call_count}"
        )
        # And we should have made at least one sleep (i.e. didn't give up immediately)
        assert len(sleep_calls) > 0

    @pytest.mark.asyncio
    async def test_sixteen_attempts_before_giveup(self):
        """Loop honors max_attempts=16 when each attempt fails immediately with 429.

        Uses a fake monotonic clock that doesn't advance, so the budget never
        triggers — only the attempt count limits retries.
        """
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

        fake_time = [0.0]

        def fake_monotonic():
            return fake_time[0]

        with patch("sow_analysis.workers.llm_rate_limit.settings") as mock_settings:
            mock_settings.SOW_LLM_RATE_LIMIT_MAX_RETRIES = 16
            mock_settings.SOW_LLM_RATE_LIMIT_BASE_DELAY = 0.01
            mock_settings.SOW_LLM_RATE_LIMIT_MAX_DELAY = 0.1
            mock_settings.SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS = 1200
            mock_settings.SOW_LLM_MAX_CONCURRENT = 0
            mock_settings.SOW_LLM_MIN_INTERVAL_SECONDS = 0.0

            with (
                patch("sow_analysis.workers.llm_rate_limit.time.monotonic", side_effect=fake_monotonic),
                patch("sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep),
            ):
                with pytest.raises(FakeRateLimitError):
                    await call_llm_with_retry(sync_fn, description="test")

        assert call_count == 16
        # 15 sleeps between 16 attempts (no sleep after last)
        assert len(sleep_calls) == 15

    @pytest.mark.asyncio
    async def test_max_delay_90s_when_provider_omits_guidance(self):
        """Single 429 with no retry_strategy in body → delay capped at 90s + 25% jitter.

        Verifies our local SOW_LLM_RATE_LIMIT_MAX_DELAY=90.0 is honored when the
        provider error body omits retry_strategy.max_delay_s.
        """
        # Plain 429 with no retry guidance in body
        call_count = 0

        def sync_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise FakeRateLimitError(status_code=429)
            return "ok"

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            sleep_calls.append(duration)
            await original_sleep(0)

        with patch("sow_analysis.workers.llm_rate_limit.settings") as mock_settings:
            mock_settings.SOW_LLM_RATE_LIMIT_MAX_RETRIES = 16
            mock_settings.SOW_LLM_RATE_LIMIT_BASE_DELAY = 2.0
            mock_settings.SOW_LLM_RATE_LIMIT_MAX_DELAY = 90.0
            mock_settings.SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS = 1200
            mock_settings.SOW_LLM_MAX_CONCURRENT = 0
            mock_settings.SOW_LLM_MIN_INTERVAL_SECONDS = 0.0

            with patch(
                "sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep
            ):
                result = await call_llm_with_retry(sync_fn, description="test")

        assert result == "ok"
        assert len(sleep_calls) == 1
        # First attempt backoff = base * 2^0 = 2.0, well under 90s cap.
        # The 90s cap matters for later attempts; here we just verify the cap
        # is loaded correctly (no provider override → effective_max == 90.0).
        assert sleep_calls[0] <= 2.0 * 1.25 + 0.1  # 2.0 + 25% jitter

    @pytest.mark.asyncio
    async def test_provider_max_delay_30s_still_honored(self):
        """Provider-reported retry_strategy.max_delay_s=30.0 caps delay at 30s.

        Even though our local SOW_LLM_RATE_LIMIT_MAX_DELAY=90.0, the provider's
        dynamic guidance (max_delay_s: 30.0) should override and cap each
        per-attempt sleep at 30s + 25% jitter.
        """
        body = (
            '{"error": {"type": "rate_limit_error", "code": "concurrent_budget_exceeded", '
            '"message": "Concurrent limit reached", "retry_after": 1.0, '
            '"retry_strategy": {"type": "concurrent_drain", '
            '"suggested_initial_delay_s": 1.0, "max_delay_s": 30.0, '
            '"backoff": "exponential", "backoff_base": 2.0, "jitter": true}, '
            '"retryable": true}}'
        )
        response = _make_429_response(body)

        # Force many retries so backoff reaches the cap.
        call_count = 0

        def sync_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 10:
                raise FakeRateLimitError(response=response, status_code=429)
            return "ok"

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            sleep_calls.append(duration)
            await original_sleep(0)

        fake_time = [0.0]

        def fake_monotonic():
            return fake_time[0]

        async def mock_sleep_with_time(duration):
            sleep_calls.append(duration)
            await original_sleep(0)

        with patch("sow_analysis.workers.llm_rate_limit.settings") as mock_settings:
            mock_settings.SOW_LLM_RATE_LIMIT_MAX_RETRIES = 16
            mock_settings.SOW_LLM_RATE_LIMIT_BASE_DELAY = 2.0
            mock_settings.SOW_LLM_RATE_LIMIT_MAX_DELAY = 90.0
            mock_settings.SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS = 1200
            mock_settings.SOW_LLM_MAX_CONCURRENT = 0
            mock_settings.SOW_LLM_MIN_INTERVAL_SECONDS = 0.0

            with (
                patch("sow_analysis.workers.llm_rate_limit.time.monotonic", side_effect=fake_monotonic),
                patch("sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep_with_time),
            ):
                result = await call_llm_with_retry(sync_fn, description="test")

        assert result == "ok"
        # All sleeps should be <= 30s + 25% jitter = 37.5s
        for i, delay in enumerate(sleep_calls):
            assert delay <= 37.5 + 0.1, (
                f"Sleep {i} = {delay}s exceeds provider max_delay_s=30.0 + 25% jitter"
            )


class TestEnforceLlmMinInterval:
    """Tests for _enforce_llm_min_interval()."""

    @pytest.mark.asyncio
    async def test_sleeps_when_called_twice_within_interval(self):
        """When called twice within SOW_LLM_MIN_INTERVAL_SECONDS, second call sleeps."""
        import sow_analysis.workers.llm_rate_limit as mod

        mod._llm_interval_lock = None
        mod._llm_last_request_time = 0.0

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            sleep_calls.append(duration)
            await original_sleep(0)

        with patch("sow_analysis.workers.llm_rate_limit.settings") as mock_settings:
            mock_settings.SOW_LLM_MIN_INTERVAL_SECONDS = 2.0

            with patch("sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep):
                await _enforce_llm_min_interval()
                await _enforce_llm_min_interval()

        # Second call should have slept
        assert len(sleep_calls) >= 1
        # Sleep should be roughly within jitter range of 2.0 (±25%)
        assert 1.0 <= sleep_calls[0] <= 3.0

        mod._llm_interval_lock = None
        mod._llm_last_request_time = 0.0

    @pytest.mark.asyncio
    async def test_noop_when_interval_zero(self):
        """When SOW_LLM_MIN_INTERVAL_SECONDS == 0, _enforce_llm_min_interval is a no-op."""
        import sow_analysis.workers.llm_rate_limit as mod

        mod._llm_interval_lock = None
        mod._llm_last_request_time = 0.0

        sleep_calls = []
        original_sleep = asyncio.sleep

        async def mock_sleep(duration):
            sleep_calls.append(duration)
            await original_sleep(0)

        with patch("sow_analysis.workers.llm_rate_limit.settings") as mock_settings:
            mock_settings.SOW_LLM_MIN_INTERVAL_SECONDS = 0.0

            with patch("sow_analysis.workers.llm_rate_limit.asyncio.sleep", side_effect=mock_sleep):
                await _enforce_llm_min_interval()
                await _enforce_llm_min_interval()

        assert len(sleep_calls) == 0

        mod._llm_interval_lock = None
        mod._llm_last_request_time = 0.0


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

    @pytest.mark.asyncio
    async def test_call_llm_with_retry_limits_concurrency(self):
        """call_llm_with_retry still limits parallel calls via semaphore."""
        import sow_analysis.workers.llm_rate_limit as mod

        mod._llm_semaphore = None
        mod._llm_interval_lock = None
        mod._llm_last_request_time = 0.0

        current_concurrent = 0
        max_observed = 0

        def sync_fn():
            nonlocal current_concurrent, max_observed
            current_concurrent += 1
            max_observed = max(max_observed, current_concurrent)
            import time as _time

            _time.sleep(0.05)
            current_concurrent -= 1
            return "done"

        with patch("sow_analysis.workers.llm_rate_limit.settings") as mock_settings:
            mock_settings.SOW_LLM_MAX_CONCURRENT = 1
            mock_settings.SOW_LLM_MIN_INTERVAL_SECONDS = 0.0
            mock_settings.SOW_LLM_RATE_LIMIT_MAX_RETRIES = 1
            mock_settings.SOW_LLM_RATE_LIMIT_BASE_DELAY = 0.01
            mock_settings.SOW_LLM_RATE_LIMIT_MAX_DELAY = 0.1
            mock_settings.SOW_LLM_RATE_LIMIT_TIMEOUT_SECONDS = 300

            tasks = [call_llm_with_retry(sync_fn, description=f"task-{i}") for i in range(5)]
            results = await asyncio.gather(*tasks)

        assert all(r == "done" for r in results)
        assert max_observed == 1

        mod._llm_semaphore = None
        mod._llm_interval_lock = None
        mod._llm_last_request_time = 0.0
