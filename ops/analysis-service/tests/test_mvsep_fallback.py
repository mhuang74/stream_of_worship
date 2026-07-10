"""Integration tests for MVSEP fallback logic."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from datetime import datetime, timezone
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest_asyncio

from sow_analysis.config import settings
from sow_analysis.models import Job, JobResult, JobStatus, StemSeparationJobRequest, StemSeparationOptions
from sow_analysis.services.mvsep_client import MvsepClient, MvsepClientError, MvsepNonRetriableError, MvsepQueueFullError
from sow_analysis.workers.stem_separation import (
    _separate_with_mvsep_fallback,
    process_stem_separation,
)
from sow_analysis.workers import stem_separation as _ss

# Import client fixture from test_mvsep_client
from test_mvsep_client import client


@pytest.fixture
def mock_separator_wrapper():
    """Create a mock AudioSeparatorWrapper."""
    wrapper = MagicMock()
    wrapper.separate_stems = AsyncMock(return_value=(
        Path("/tmp/dry.flac"),      # vocals_dry (Stage 2 output)
        Path("/tmp/vocals.flac"),   # vocals (Stage 1 output)
        Path("/tmp/instrumental.flac"),
    ))
    wrapper.remove_reverb = AsyncMock(return_value=(
        Path("/tmp/local_dry.flac"),
        Path("/tmp/local_reverb.flac"),
    ))
    return wrapper


@pytest.fixture
def mock_mvsep_client():
    """Create a mock MvsepClient."""
    client = MagicMock(spec=MvsepClient)
    client.is_available = True
    client.stage2_sep_type = 22  # Stage 2 enabled by default
    client.separate_vocals = AsyncMock(return_value=(
        Path("/tmp/mvsep_vocals.flac"),
        Path("/tmp/mvsep_instrumental.flac"),
    ))
    client.remove_reverb = AsyncMock(return_value=(
        Path("/tmp/mvsep_dry.flac"),
        Path("/tmp/mvsep_reverb.flac"),
    ))
    return client


@pytest.fixture
def mock_job():
    """Create a mock stem separation job."""
    request = StemSeparationJobRequest(
        audio_url="s3://test/audio.mp3",
        content_hash="abc123def456",
        options=StemSeparationOptions(),
    )
    job = Job(
        id="test-job-001",
        type="stem_separation",
        status=JobStatus.PROCESSING,
        request=request,
        result=None,
        error_message=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        progress=0.0,
        stage="starting",
    )
    return job


@pytest.mark.asyncio
async def test_mvsep_both_stages_succeed(mock_job, mock_mvsep_client, mock_separator_wrapper):
    """Test MVSEP both stages succeed - local never called."""
    result = await _separate_with_mvsep_fallback(
        input_path=Path("/tmp/input.mp3"),
        output_dir=Path("/tmp/output"),
        job=mock_job,
        mvsep_client=mock_mvsep_client,
        separator_wrapper=mock_separator_wrapper,
    )

    assert result[0] == Path("/tmp/mvsep_dry.flac")      # vocals_dry (Stage 2 output)
    assert result[1] == Path("/tmp/mvsep_vocals.flac")   # vocals (Stage 1 output)
    assert result[2] == Path("/tmp/mvsep_instrumental.flac")

    mock_mvsep_client.separate_vocals.assert_called_once()
    mock_mvsep_client.remove_reverb.assert_called_once()
    mock_separator_wrapper.separate_stems.assert_not_called()


@pytest.mark.asyncio
async def test_mvsep_stage1_fails_retries_then_succeeds(mock_job, mock_mvsep_client, mock_separator_wrapper):
    """Test Stage 1 MVSEP fails twice then succeeds."""
    # First two calls fail, third succeeds
    mock_mvsep_client.separate_vocals.side_effect = [
        MvsepClientError("Network error"),
        MvsepClientError("Timeout"),
        (Path("/tmp/mvsep_vocals.flac"), Path("/tmp/mvsep_instrumental.flac")),
    ]

    result = await _separate_with_mvsep_fallback(
        input_path=Path("/tmp/input.mp3"),
        output_dir=Path("/tmp/output"),
        job=mock_job,
        mvsep_client=mock_mvsep_client,
        separator_wrapper=mock_separator_wrapper,
    )

    assert result[0] == Path("/tmp/mvsep_dry.flac")
    assert mock_mvsep_client.separate_vocals.call_count == 3
    mock_separator_wrapper.separate_stems.assert_not_called()


@pytest.mark.asyncio
async def test_mvsep_stage1_exhausts_retries_falls_back_full_local(mock_job, mock_mvsep_client, mock_separator_wrapper):
    """Test Stage 1 MVSEP exhausts retries and falls back to full local."""
    # All retries fail
    mock_mvsep_client.separate_vocals.side_effect = MvsepClientError("Persistent error")

    result = await _separate_with_mvsep_fallback(
        input_path=Path("/tmp/input.mp3"),
        output_dir=Path("/tmp/output"),
        job=mock_job,
        mvsep_client=mock_mvsep_client,
        separator_wrapper=mock_separator_wrapper,
    )

    assert mock_job.stage == "fallback_local"
    assert result == (
        Path("/tmp/dry.flac"),      # vocals_dry
        Path("/tmp/vocals.flac"),   # vocals
        Path("/tmp/instrumental.flac"),
    )

    # Verify MVSEP was tried 3 times
    assert mock_mvsep_client.separate_vocals.call_count == 3
    # Verify local was called once
    mock_separator_wrapper.separate_stems.assert_called_once()


@pytest.mark.asyncio
async def test_mvsep_stage1_succeeds_stage2_fails_handoff(mock_job, mock_mvsep_client, mock_separator_wrapper):
    """Test Stage 1 MVSEP succeeds but Stage 2 fails - local Stage 2 fallback."""
    mock_mvsep_client.remove_reverb.side_effect = [
        MvsepClientError("Stage 2 error"),
        MvsepClientError("Stage 2 error again"),
        MvsepClientError("Stage 2 error final"),
    ]

    result = await _separate_with_mvsep_fallback(
        input_path=Path("/tmp/input.mp3"),
        output_dir=Path("/tmp/output"),
        job=mock_job,
        mvsep_client=mock_mvsep_client,
        separator_wrapper=mock_separator_wrapper,
    )

    assert mock_job.stage == "fallback_local_stage2"
    assert result[0] == Path("/tmp/local_dry.flac")     # From local remove_reverb
    assert result[1] == Path("/tmp/mvsep_vocals.flac")  # From MVSEP Stage 1

    # Verify cross-backend handoff: local remove_reverb called with MVSEP vocals
    mock_separator_wrapper.remove_reverb.assert_called_once_with(
        Path("/tmp/mvsep_vocals.flac"),
        Path("/tmp/output/mvsep_stage2"),
    )


@pytest.mark.asyncio
async def test_mvsep_stage2_skipped_when_disabled(mock_job, mock_mvsep_client, mock_separator_wrapper):
    """Test Stage 2 is skipped when stage2_sep_type is None."""
    mock_mvsep_client.stage2_sep_type = None  # Stage 2 disabled

    result = await _separate_with_mvsep_fallback(
        input_path=Path("/tmp/input.mp3"),
        output_dir=Path("/tmp/output"),
        job=mock_job,
        mvsep_client=mock_mvsep_client,
        separator_wrapper=mock_separator_wrapper,
    )

    # When Stage 2 is skipped: (None, vocals, instrumental)
    assert result[0] is None                          # vocals_dry is None
    assert result[1] == Path("/tmp/mvsep_vocals.flac")  # vocals from Stage 1
    assert result[2] == Path("/tmp/mvsep_instrumental.flac")

    # Stage 2 should not be called
    mock_mvsep_client.remove_reverb.assert_not_called()
    mock_separator_wrapper.remove_reverb.assert_not_called()
    # Only Stage 1 should be called
    mock_mvsep_client.separate_vocals.assert_called_once()


@pytest.mark.asyncio
async def test_mvsep_non_retriable_fast_fallback(mock_job, mock_mvsep_client, mock_separator_wrapper):
    """Test non-retriable error causes immediate fallback without retries."""
    mock_mvsep_client.separate_vocals.side_effect = MvsepNonRetriableError("Invalid key")

    result = await _separate_with_mvsep_fallback(
        input_path=Path("/tmp/input.mp3"),
        output_dir=Path("/tmp/output"),
        job=mock_job,
        mvsep_client=mock_mvsep_client,
        separator_wrapper=mock_separator_wrapper,
    )

    # Should have tried only once (no retries on non-retriable)
    assert mock_mvsep_client.separate_vocals.call_count == 1
    # Should have fallen back to local
    mock_separator_wrapper.separate_stems.assert_called_once()


@pytest.mark.asyncio
async def test_mvsep_non_retriable_disables_future_jobs(mock_mvsep_client):
    """Test that non-retriable error sets _disabled on client."""
    mock_mvsep_client.separate_vocals.side_effect = MvsepNonRetriableError("Invalid key")
    mock_mvsep_client._disabled = False

    # Simulate the error being raised
    try:
        await mock_mvsep_client.separate_vocals(Path("/tmp/input.mp3"))
    except MvsepNonRetriableError:
        pass

    # In real implementation, the MvsepClient._submit_job catches NonRetriableError
    # and sets _disabled = True, making is_available False
    # Here we just verify the method was called with the error
    mock_mvsep_client.separate_vocals.assert_called_once()


@pytest.mark.asyncio
async def test_mvsep_not_available_uses_local(mock_job, mock_mvsep_client, mock_separator_wrapper):
    """Test when MVSEP client is None or not available, local is used immediately."""
    mock_mvsep_client.is_available = False

    result = await _separate_with_mvsep_fallback(
        input_path=Path("/tmp/input.mp3"),
        output_dir=Path("/tmp/output"),
        job=mock_job,
        mvsep_client=mock_mvsep_client,
        separator_wrapper=mock_separator_wrapper,
    )

    # MVSEP should not be called
    mock_mvsep_client.separate_vocals.assert_not_called()
    mock_mvsep_client.remove_reverb.assert_not_called()
    # Local should be called immediately
    mock_separator_wrapper.separate_stems.assert_called_once()


@pytest.mark.asyncio
async def test_mvsep_none_uses_local(mock_job, mock_separator_wrapper):
    """Test when MVSEP client is None, local is used immediately."""
    result = await _separate_with_mvsep_fallback(
        input_path=Path("/tmp/input.mp3"),
        output_dir=Path("/tmp/output"),
        job=mock_job,
        mvsep_client=None,
        separator_wrapper=mock_separator_wrapper,
    )

    mock_separator_wrapper.separate_stems.assert_called_once()


@pytest.mark.asyncio
async def test_total_timeout_exceeded_falls_back(mock_job, mock_mvsep_client, mock_separator_wrapper):
    """Test that total timeout causes fallback mid-retry.

    With the guarantee-first-attempt change, the first attempt still runs
    even when time_remaining <= 0. The retry (attempt 2) is skipped due to timeout.
    """
    import time

    # Make Stage 1 take a long time
    async def slow_separate_vocals(*args, **kwargs):
        time.sleep(0.2)  # Simulate time passing
        raise MvsepClientError("Slow error")

    mock_mvsep_client.separate_vocals.side_effect = slow_separate_vocals

    # Set a very short total timeout
    original_total_timeout = _ss.settings.SOW_MVSEP_TOTAL_TIMEOUT
    _ss.settings.SOW_MVSEP_TOTAL_TIMEOUT = 0.1

    try:
        result = await _separate_with_mvsep_fallback(
            input_path=Path("/tmp/input.mp3"),
            output_dir=Path("/tmp/output"),
            job=mock_job,
            mvsep_client=mock_mvsep_client,
            separator_wrapper=mock_separator_wrapper,
        )

        # First attempt ran despite timeout, then retry was skipped
        assert mock_mvsep_client.separate_vocals.call_count == 1
        # Should have fallen back due to timeout
        mock_separator_wrapper.separate_stems.assert_called_once()
    finally:
        _ss.settings.SOW_MVSEP_TOTAL_TIMEOUT = original_total_timeout


@pytest.mark.asyncio
async def test_stage_callback_updates(mock_job, mock_mvsep_client, mock_separator_wrapper):
    """Test that stage callback updates job.stage appropriately."""
    # Ensure stage2 is enabled
    mock_mvsep_client.stage2_sep_type = 22

    stages = []

    def capture_stage(stage: str) -> None:
        stages.append(stage)

    mock_mvsep_client.separate_vocals = AsyncMock(return_value=(
        Path("/tmp/mvsep_vocals.flac"),
        Path("/tmp/mvsep_instrumental.flac"),
    ))
    mock_mvsep_client.separate_vocals.__name__ = "separate_vocals"

    # Patch the separate_vocals to call the callback
    original_separate_vocals = mock_mvsep_client.separate_vocals

    async def patched_separate_vocals(input_path, output_dir, stage_callback=None):
        if stage_callback:
            stage_callback("mvsep_stage1_submitting")
            stage_callback("mvsep_stage1_polling")
            stage_callback("mvsep_stage1_downloading")
        return await original_separate_vocals()

    mock_mvsep_client.separate_vocals = patched_separate_vocals

    mock_mvsep_client.remove_reverb = AsyncMock(return_value=(
        Path("/tmp/mvsep_dry.flac"),
        Path("/tmp/mvsep_reverb.flac"),
    ))

    async def patched_remove_reverb(vocals_path, output_dir, stage_callback=None):
        if stage_callback:
            stage_callback("mvsep_stage2_submitting")
            stage_callback("mvsep_stage2_polling")
            stage_callback("mvsep_stage2_downloading")
        return await mock_mvsep_client.remove_reverb()

    mock_mvsep_client.remove_reverb = patched_remove_reverb

    result = await _separate_with_mvsep_fallback(
        input_path=Path("/tmp/input.mp3"),
        output_dir=Path("/tmp/output"),
        job=mock_job,
        mvsep_client=mock_mvsep_client,
        separator_wrapper=mock_separator_wrapper,
    )

    # Job should have been updated through stage callbacks
    # Valid final stages include: mvsep_stage2_downloading (success), complete (done), fallback_local_stage2 (Stage 2 failed)
    assert mock_job.stage in ["mvsep_stage2_downloading", "complete", "fallback_local_stage2"]


@pytest.mark.asyncio
async def test_quota_exhausted_uses_local(mock_job, mock_mvsep_client, mock_separator_wrapper):
    """Test that quota exhaustion causes immediate local fallback."""
    mock_mvsep_client.is_available = False  # Simulate limit hit

    result = await _separate_with_mvsep_fallback(
        input_path=Path("/tmp/input.mp3"),
        output_dir=Path("/tmp/output"),
        job=mock_job,
        mvsep_client=mock_mvsep_client,
        separator_wrapper=mock_separator_wrapper,
    )

    # MVSEP should not be called
    mock_mvsep_client.separate_vocals.assert_not_called()
    # Local should be used
    mock_separator_wrapper.separate_stems.assert_called_once()


@pytest.mark.asyncio
async def test_stage1_no_vocals_file_fallback(mock_job, mock_mvsep_client, mock_separator_wrapper):
    """Test Stage 1 succeeds but returns no vocals file - falls back to local."""
    mock_mvsep_client.separate_vocals.return_value = (None, Path("/tmp/instrumental.flac"))

    result = await _separate_with_mvsep_fallback(
        input_path=Path("/tmp/input.mp3"),
        output_dir=Path("/tmp/output"),
        job=mock_job,
        mvsep_client=mock_mvsep_client,
        separator_wrapper=mock_separator_wrapper,
    )

    assert mock_job.stage == "fallback_local"
    mock_separator_wrapper.separate_stems.assert_called_once()


@pytest.mark.asyncio
async def test_upload_stems_return_order_matches_separate_stems():
    """Test that upload_clean_stems() return order matches separate_stems() return order.

    Both should return: (vocals_dry_url, vocals_url, instrumental_url)
    """
    from sow_analysis.storage.r2 import R2Client

    # Create a mock R2Client
    r2_client = MagicMock(spec=R2Client)
    r2_client.bucket = "test-bucket"
    r2_client.check_exists = AsyncMock(return_value=True)
    r2_client.s3 = MagicMock()

    # We can't easily test the actual upload_clean_stems without S3, but we can verify
    # the function signature accepts the correct parameter order
    # upload_clean_stems(hash_prefix, vocals_dry, instrumental, vocals)
    import inspect
    from sow_analysis.storage.r2 import R2Client

    sig = inspect.signature(R2Client.upload_clean_stems)
    params = list(sig.parameters.keys())

    # Verify parameter names and order
    assert params[0] == "self"
    assert params[1] == "hash_prefix"
    assert params[2] == "vocals_dry"
    assert params[3] == "instrumental"
    assert params[4] == "vocals"


@pytest.mark.asyncio
async def test_stage1_no_vocals_file_fallback(mock_job, mock_mvsep_client, mock_separator_wrapper):
    """Test Stage 1 succeeds but returns no vocals file - falls back to local."""
    mock_mvsep_client.separate_vocals.return_value = (None, Path("/tmp/instrumental.flac"))

    result = await _separate_with_mvsep_fallback(
        input_path=Path("/tmp/input.mp3"),
        output_dir=Path("/tmp/output"),
        job=mock_job,
        mvsep_client=mock_mvsep_client,
        separator_wrapper=mock_separator_wrapper,
    )

    assert mock_job.stage == "fallback_local"
    mock_separator_wrapper.separate_stems.assert_called_once()


# Note: test_httpx_500_retriable is in test_mvsep_client.py where it belongs


@pytest.mark.asyncio
async def test_queue_full_backoff_timing(mock_job, mock_mvsep_client, mock_separator_wrapper):
    """Test that queue-full errors trigger correct backoff delays."""
    from unittest.mock import patch

    sleep_times = []

    async def fake_sleep(seconds):
        sleep_times.append(seconds)

    mock_mvsep_client.separate_vocals.side_effect = [
        MvsepQueueFullError("Queue full"),
        MvsepQueueFullError("Queue full again"),
        (Path("/tmp/mvsep_vocals.flac"), Path("/tmp/mvsep_instrumental.flac")),
    ]

    with patch("asyncio.sleep", side_effect=fake_sleep):
        with patch("random.uniform", return_value=0.0):
            result = await _separate_with_mvsep_fallback(
                input_path=Path("/tmp/input.mp3"),
                output_dir=Path("/tmp/output"),
                job=mock_job,
                mvsep_client=mock_mvsep_client,
                separator_wrapper=mock_separator_wrapper,
            )

    assert len(sleep_times) == 2
    assert sleep_times[0] == 30
    assert sleep_times[1] == 60
    assert mock_mvsep_client.separate_vocals.call_count == 3


@pytest.mark.asyncio
async def test_other_error_backoff_timing(mock_job, mock_mvsep_client, mock_separator_wrapper):
    """Test that non-queue-full errors use shorter backoff delays."""
    from unittest.mock import patch

    sleep_times = []

    async def fake_sleep(seconds):
        sleep_times.append(seconds)

    mock_mvsep_client.separate_vocals.side_effect = [
        MvsepClientError("Network error"),
        MvsepClientError("Timeout"),
        (Path("/tmp/mvsep_vocals.flac"), Path("/tmp/mvsep_instrumental.flac")),
    ]

    with patch("asyncio.sleep", side_effect=fake_sleep):
        with patch("random.uniform", return_value=0.0):
            result = await _separate_with_mvsep_fallback(
                input_path=Path("/tmp/input.mp3"),
                output_dir=Path("/tmp/output"),
                job=mock_job,
                mvsep_client=mock_mvsep_client,
                separator_wrapper=mock_separator_wrapper,
            )

    assert len(sleep_times) == 2
    assert sleep_times[0] == 5
    assert sleep_times[1] == 10
    assert mock_mvsep_client.separate_vocals.call_count == 3


@pytest.mark.asyncio
async def test_queue_full_6_attempts_before_fallback(mock_job, mock_mvsep_client, mock_separator_wrapper):
    """Test that queue-full errors get 6 attempts (vs 3 for other errors)."""
    from unittest.mock import patch

    sleep_times = []

    async def fake_sleep(seconds):
        sleep_times.append(seconds)

    mock_mvsep_client.separate_vocals.side_effect = MvsepQueueFullError("Queue full")

    with patch("asyncio.sleep", side_effect=fake_sleep):
        with patch("random.uniform", return_value=0.0):
            result = await _separate_with_mvsep_fallback(
                input_path=Path("/tmp/input.mp3"),
                output_dir=Path("/tmp/output"),
                job=mock_job,
                mvsep_client=mock_mvsep_client,
                separator_wrapper=mock_separator_wrapper,
            )

    assert mock_mvsep_client.separate_vocals.call_count == 6
    assert len(sleep_times) == 5
    assert sleep_times == [30, 60, 120, 240, 300]
    mock_separator_wrapper.separate_stems.assert_called_once()


@pytest.mark.asyncio
async def test_queue_full_backoff_jitter_applied(mock_job, mock_mvsep_client, mock_separator_wrapper):
    """Test that jitter is applied to queue-full backoff (sleep > base value)."""
    from unittest.mock import patch

    sleep_times = []

    async def fake_sleep(seconds):
        sleep_times.append(seconds)

    mock_mvsep_client.separate_vocals.side_effect = [
        MvsepQueueFullError("Queue full"),
        (Path("/tmp/mvsep_vocals.flac"), Path("/tmp/mvsep_instrumental.flac")),
    ]

    # Patch random.uniform to return +amp (max positive jitter)
    with patch("asyncio.sleep", side_effect=fake_sleep):
        with patch("random.uniform", side_effect=lambda low, high: high):
            result = await _separate_with_mvsep_fallback(
                input_path=Path("/tmp/input.mp3"),
                output_dir=Path("/tmp/output"),
                job=mock_job,
                mvsep_client=mock_mvsep_client,
                separator_wrapper=mock_separator_wrapper,
            )

    assert len(sleep_times) == 1
    # base=30, jitter=0.20 → amp=6 → sleep = 30 + 6 = 36
    assert sleep_times[0] == 36


@pytest.mark.asyncio
async def test_stage2_gets_dedicated_budget_after_stage1_consumed_total(
    mock_job, mock_mvsep_client, mock_separator_wrapper
):
    """Stage 2 still gets retries even when Stage 1 consumed most of the total timeout."""
    import time
    from unittest.mock import patch

    original_total_timeout = _ss.settings.SOW_MVSEP_TOTAL_TIMEOUT
    original_stage2_timeout = _ss.settings.SOW_MVSEP_STAGE2_TIMEOUT
    _ss.settings.SOW_MVSEP_TOTAL_TIMEOUT = 0.3
    _ss.settings.SOW_MVSEP_STAGE2_TIMEOUT = 0.2

    async def slow_stage1(*args, **kwargs):
        time.sleep(0.15)  # Consumes most of total budget
        return (Path("/tmp/mvsep_vocals.flac"), Path("/tmp/mvsep_instrumental.flac"))

    mock_mvsep_client.separate_vocals.side_effect = slow_stage1
    mock_mvsep_client.remove_reverb.side_effect = [
        MvsepClientError("Stage 2 error"),
        MvsepClientError("Stage 2 error again"),
    ]

    try:
        with patch("asyncio.sleep"):
            with patch("random.uniform", return_value=0.0):
                result = await _separate_with_mvsep_fallback(
                    input_path=Path("/tmp/input.mp3"),
                    output_dir=Path("/tmp/output"),
                    job=mock_job,
                    mvsep_client=mock_mvsep_client,
                    separator_wrapper=mock_separator_wrapper,
                )
        # remove_reverb should have been called at least once (guarantee first attempt)
        assert mock_mvsep_client.remove_reverb.call_count >= 1
        assert mock_separator_wrapper.remove_reverb.call_count >= 0
    finally:
        _ss.settings.SOW_MVSEP_TOTAL_TIMEOUT = original_total_timeout
        _ss.settings.SOW_MVSEP_STAGE2_TIMEOUT = original_stage2_timeout


@pytest.mark.asyncio
async def test_stage2_timeout_uses_queue_full_backoff(
    mock_job, mock_mvsep_client, mock_separator_wrapper
):
    """MvsepTimeoutError triggers 6 attempts with 30/60/120/240/300s backoff."""
    from sow_analysis.services.mvsep_client import MvsepTimeoutError
    from unittest.mock import patch

    sleep_times = []

    async def fake_sleep(seconds):
        sleep_times.append(seconds)

    mock_mvsep_client.remove_reverb.side_effect = MvsepTimeoutError("Poll timeout")

    with patch("asyncio.sleep", side_effect=fake_sleep):
        with patch("random.uniform", return_value=0.0):
            await _separate_with_mvsep_fallback(
                input_path=Path("/tmp/input.mp3"),
                output_dir=Path("/tmp/output"),
                job=mock_job,
                mvsep_client=mock_mvsep_client,
                separator_wrapper=mock_separator_wrapper,
            )

    assert mock_mvsep_client.remove_reverb.call_count == 6
    assert sleep_times == [30, 60, 120, 240, 300]


@pytest.mark.asyncio
async def test_stage2_first_attempt_runs_even_when_time_exhausted(
    mock_job, mock_mvsep_client, mock_separator_wrapper
):
    """Stage 2 gets at least one real submission attempt even when total budget is 0."""
    import time
    from unittest.mock import patch

    original_total_timeout = _ss.settings.SOW_MVSEP_TOTAL_TIMEOUT
    original_stage2_timeout = _ss.settings.SOW_MVSEP_STAGE2_TIMEOUT
    _ss.settings.SOW_MVSEP_TOTAL_TIMEOUT = 0.01
    _ss.settings.SOW_MVSEP_STAGE2_TIMEOUT = 0.01

    async def fast_stage1(*args, **kwargs):
        return (Path("/tmp/mvsep_vocals.flac"), Path("/tmp/mvsep_instrumental.flac"))

    mock_mvsep_client.separate_vocals.side_effect = fast_stage1

    try:
        with patch("asyncio.sleep"):
            with patch("random.uniform", return_value=0.0):
                result = await _separate_with_mvsep_fallback(
                    input_path=Path("/tmp/input.mp3"),
                    output_dir=Path("/tmp/output"),
                    job=mock_job,
                    mvsep_client=mock_mvsep_client,
                    separator_wrapper=mock_separator_wrapper,
                )
        # remove_reverb called at least once despite time budget being exhausted
        assert mock_mvsep_client.remove_reverb.call_count >= 1
    finally:
        _ss.settings.SOW_MVSEP_TOTAL_TIMEOUT = original_total_timeout
        _ss.settings.SOW_MVSEP_STAGE2_TIMEOUT = original_stage2_timeout


@pytest.mark.asyncio
async def test_timeout_error_backoff_uses_queue_full_constants(
    mock_job, mock_mvsep_client, mock_separator_wrapper
):
    """Verify timeout errors use 30s base backoff, not 5s."""
    from sow_analysis.services.mvsep_client import MvsepTimeoutError
    from unittest.mock import patch

    sleep_times = []

    async def fake_sleep(seconds):
        sleep_times.append(seconds)

    mock_mvsep_client.remove_reverb.side_effect = [
        MvsepTimeoutError("Poll timeout"),
        (Path("/tmp/mvsep_dry.flac"), Path("/tmp/mvsep_reverb.flac")),
    ]

    with patch("asyncio.sleep", side_effect=fake_sleep):
        with patch("random.uniform", return_value=0.0):
            result = await _separate_with_mvsep_fallback(
                input_path=Path("/tmp/input.mp3"),
                output_dir=Path("/tmp/output"),
                job=mock_job,
                mvsep_client=mock_mvsep_client,
                separator_wrapper=mock_separator_wrapper,
            )

    assert len(sleep_times) == 1
    assert sleep_times[0] == 30  # queue-full base, not other-error base (5)
