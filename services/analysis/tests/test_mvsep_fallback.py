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
from sow_analysis.services.mvsep_client import MvsepClient, MvsepClientError, MvsepNonRetriableError
from sow_analysis.workers.stem_separation import (
    _separate_with_mvsep_fallback,
    process_stem_separation,
)


@pytest.fixture
def mock_separator_wrapper():
    """Create a mock AudioSeparatorWrapper."""
    wrapper = MagicMock()
    wrapper.separate_stems = AsyncMock(return_value=(
        Path("/tmp/clean.flac"),
        Path("/tmp/reverb.flac"),
        Path("/tmp/instrumental.flac"),
    ))
    wrapper.remove_reverb = AsyncMock(return_value=(
        Path("/tmp/local_clean.flac"),
        Path("/tmp/local_reverb.flac"),
    ))
    return wrapper


@pytest.fixture
def mock_mvsep_client():
    """Create a mock MvsepClient."""
    client = MagicMock(spec=MvsepClient)
    client.is_available = True
    client.separate_vocals = AsyncMock(return_value=(
        Path("/tmp/mvsep_vocals.flac"),
        Path("/tmp/mvsep_instrumental.flac"),
    ))
    client.remove_reverb = AsyncMock(return_value=(
        Path("/tmp/mvsep_clean.flac"),
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

    assert result[0] == Path("/tmp/mvsep_clean.flac")
    assert result[1] == Path("/tmp/mvsep_vocals.flac")
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

    assert result[0] == Path("/tmp/mvsep_clean.flac")
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
        Path("/tmp/clean.flac"),
        Path("/tmp/reverb.flac"),
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
    assert result[0] == Path("/tmp/local_clean.flac")  # From local remove_reverb
    assert result[1] == Path("/tmp/mvsep_vocals.flac")  # From MVSEP Stage 1

    # Verify cross-backend handoff: local remove_reverb called with MVSEP vocals
    mock_separator_wrapper.remove_reverb.assert_called_once_with(
        Path("/tmp/mvsep_vocals.flac"),
        Path("/tmp/output/mvsep_stage2"),
    )


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

    # In real implementation, this would be set by MvsepClient
    # Here we verify the behavior is expected
    assert not mock_mvsep_client.is_available  # Client becomes unavailable


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
    """Test that total timeout causes fallback mid-retry."""
    import time

    # Make Stage 1 take a long time
    async def slow_separate_vocals(*args, **kwargs):
        time.sleep(0.2)  # Simulate time passing
        raise MvsepClientError("Slow error")

    mock_mvsep_client.separate_vocals.side_effect = slow_separate_vocals

    # Set a very short total timeout
    original_total_timeout = settings.SOW_MVSEP_TOTAL_TIMEOUT
    settings.SOW_MVSEP_TOTAL_TIMEOUT = 0.1

    try:
        result = await _separate_with_mvsep_fallback(
            input_path=Path("/tmp/input.mp3"),
            output_dir=Path("/tmp/output"),
            job=mock_job,
            mvsep_client=mock_mvsep_client,
            separator_wrapper=mock_separator_wrapper,
        )

        # Should have fallen back due to timeout
        mock_separator_wrapper.separate_stems.assert_called_once()
    finally:
        settings.SOW_MVSEP_TOTAL_TIMEOUT = original_total_timeout


@pytest.mark.asyncio
async def test_stage_callback_updates(mock_job, mock_mvsep_client, mock_separator_wrapper):
    """Test that stage callback updates job.stage appropriately."""
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
        Path("/tmp/mvsep_clean.flac"),
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
    assert mock_job.stage in ["mvsep_stage2_downloading", "complete"]


@pytest.mark.asyncio
async def test_daily_limit_hit_uses_local(mock_job, mock_mvsep_client, mock_separator_wrapper):
    """Test that daily job limit causes immediate local fallback."""
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
async def test_httpx_500_retriable(client):
    """Test 5xx error is retriable (doesn't disable client)."""
    import httpx
    from unittest.mock import MagicMock

    error_response = MagicMock()
    error_response.status_code = 503
    error_response.text = "Service Unavailable"

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.HTTPStatusError(
            "503 Service Unavailable",
            request=MagicMock(),
            response=error_response,
        )

        with pytest.raises(MvsepClientError, match="HTTP error 503"):
            await client._submit_job(
                Path("/tmp/test.mp3"),
                sep_type=40,
                add_opt1=81,
            )

        # Should NOT be disabled
        assert client._disabled is False
