"""Unit tests for MvsepClient."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from datetime import datetime, timezone, timedelta
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

import httpx


# Create mock settings class for tests
class MockSettings:
    """Mock settings for testing."""

    SOW_MVSEP_API_KEY = ""
    SOW_MVSEP_ENABLED = True

    # Stage 1 (Vocal Separation)
    SOW_MVSEP_STAGE1_SEP_TYPE = 48
    SOW_MVSEP_STAGE1_ADD_OPT1 = 11
    SOW_MVSEP_STAGE1_ADD_OPT2 = None

    # Stage 2 (Reverb Removal) — None = skip Stage 2
    SOW_MVSEP_STAGE2_SEP_TYPE = 22
    SOW_MVSEP_STAGE2_ADD_OPT1 = 0
    SOW_MVSEP_STAGE2_ADD_OPT2 = 1

    SOW_MVSEP_HTTP_TIMEOUT = 60
    SOW_MVSEP_STAGE_TIMEOUT = 300
    SOW_MVSEP_STAGE2_TIMEOUT = 900
    SOW_MVSEP_TOTAL_TIMEOUT = 1800
    SOW_MVSEP_MAX_CONCURRENT = 3

    # YouTube Transcript Rate Limiting
    SOW_YOUTUBE_TRANSCRIPT_MAX_CONCURRENT = 1
    SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS = 0.0
    SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES = 0
    SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY = 0.1
    SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD = 99
    SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN = 60

    # YouTube Transcript Free-Only-Mode Overrides
    SOW_FREE_ONLY_MODE = False
    SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD_FREE = 10
    SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN_FREE = 300
    SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES_FREE = 5
    SOW_YOUTUBE_TRANSCRIPT_RETRY_BASE_DELAY_FREE = 10.0
    SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS_FREE = 30.0
    SOW_YOUTUBE_TRANSCRIPT_FREE_MODE_MAX_BREAKER_CYCLES = 3

    # YouTube Proxy
    SOW_YOUTUBE_PROXY = ""
    SOW_YOUTUBE_PROXY_RETRIES = 3


# Mock the config module before importing mvsep_client
import types
config_module = types.ModuleType("sow_analysis.config")
config_module.settings = MockSettings()
sys.modules["sow_analysis.config"] = config_module

# Ensure sow_analysis is importable as a real package but with mocked config
# We need sow_analysis.__path__ set so Python can find subpackages
import sow_analysis
sow_analysis.config = config_module
sys.modules["sow_analysis.config"] = config_module

# Now import mvsep_client — it will resolve the config import from our mock
from sow_analysis.services.mvsep_client import (
    MvsepClient,
    MvsepClientError,
    MvsepNonRetriableError,
    MvsepTimeoutError,
    MvsepQueueFullError,
)


@pytest.fixture
def client(tmp_path):
    """Create a test MVSEP client with a temp audio file."""
    test_audio = tmp_path / "test.mp3"
    test_audio.write_bytes(b"fake audio data")
    client = MvsepClient(
        api_token="test-token",
        enabled=True,
        stage1_sep_type=48,
        stage1_add_opt1=11,
        stage1_add_opt2=None,
        stage2_sep_type=22,
        stage2_add_opt1=0,
        stage2_add_opt2=1,
        http_timeout=60,
        stage_timeout=300,
        max_concurrent=3,
    )
    client._test_audio = test_audio
    return client


@pytest.fixture
def mock_response():
    """Create a mock HTTP response."""
    response = MagicMock()
    response.json.return_value = {}
    return response


@pytest.fixture
def mock_post(client):
    """Create a mock for client._client.post."""
    with patch.object(client._client, "post", new_callable=AsyncMock) as mock:
        yield mock


@pytest.mark.asyncio
async def test_submit_success(client, mock_response):
    """Test successful job submission returns job hash."""
    mock_response.json.return_value = {"success": True, "data": {"hash": "abc123", "link": "https://mvsep.com/..."}}

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        result = await client._submit_job(
            client._test_audio,
            sep_type=48,
            add_opt1=11,
        )

        assert result == "abc123"


@pytest.mark.asyncio
async def test_submit_api_error(client, mock_response):
    """Test API error response raises MvsepClientError."""
    mock_response.json.return_value = {"success": False, "data": {"message": "API error"}}

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        with pytest.raises(MvsepClientError, match="API error"):
            await client._submit_job(
                client._test_audio,
                sep_type=40,
                add_opt1=81,
            )


@pytest.mark.asyncio
async def test_submit_401_raises_non_retriable(client):
    """Test 401 error raises MvsepNonRetriableError and disables client."""
    error_response = MagicMock()
    error_response.status_code = 401
    error_response.text = "Unauthorized"

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.HTTPStatusError(
            "401 Unauthorized",
            request=MagicMock(),
            response=error_response,
        )

        with pytest.raises(MvsepNonRetriableError, match="Authentication failed"):
            await client._submit_job(
                client._test_audio,
                sep_type=40,
                add_opt1=81,
            )

        assert client._disabled is True


@pytest.mark.asyncio
async def test_submit_403_raises_non_retriable(client):
    """Test 403 error raises MvsepNonRetriableError and disables client."""
    error_response = MagicMock()
    error_response.status_code = 403
    error_response.text = "Forbidden"

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.HTTPStatusError(
            "403 Forbidden",
            request=MagicMock(),
            response=error_response,
        )

        with pytest.raises(MvsepNonRetriableError, match="Authentication failed"):
            await client._submit_job(
                client._test_audio,
                sep_type=40,
                add_opt1=81,
            )

        assert client._disabled is True


@pytest.mark.asyncio
async def test_submit_400_queue_full_raises_queue_full_error(client):
    """Test 400 with queue-full message raises MvsepQueueFullError (retriable with longer backoff)."""
    error_response = MagicMock()
    error_response.status_code = 400
    error_response.text = '{"success":false,"errors":["You already have unprocessed file in queue. Please wait before adding new file!"]}'

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.HTTPStatusError(
            "400 Bad Request",
            request=MagicMock(),
            response=error_response,
        )

        with pytest.raises(MvsepQueueFullError, match="queue full"):
            await client._submit_job(
                client._test_audio,
                sep_type=48,
                add_opt1=11,
            )

        assert client._disabled is False


@pytest.mark.asyncio
async def test_submit_400_other_raises_client_error(client):
    """Test 400 without queue-full message raises MvsepClientError (retriable with normal backoff)."""
    error_response = MagicMock()
    error_response.status_code = 400
    error_response.text = '{"success":false,"errors":["Invalid parameter"]}'

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.HTTPStatusError(
            "400 Bad Request",
            request=MagicMock(),
            response=error_response,
        )

        with pytest.raises(MvsepClientError, match="HTTP error 400"):
            await client._submit_job(
                client._test_audio,
                sep_type=48,
                add_opt1=11,
            )

        assert client._disabled is False


def test_is_available_disabled_after_non_retriable(client):
    """Test is_available returns False after _disabled is set."""
    assert client.is_available is True
    client._disabled = True
    assert client.is_available is False


@pytest.mark.asyncio
async def test_poll_done(client, mock_response):
    """Test polling returns data when status is done."""
    mock_response.json.return_value = {
        "success": True,
        "status": "done",
        "data": {"files": [{"url": "http://example.com/vocals.flac", "name": "vocals.flac"}]},
    }

    with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await client._poll_job("abc123")

        assert result["status"] == "done"
        assert "files" in result.get("data", {})


@pytest.mark.asyncio
async def test_poll_timeout(client):
    """Test polling raises MvsepTimeoutError after timeout."""
    client.stage_timeout = 0.1  # Very short timeout

    with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"success": True, "status": "processing"}
        mock_get.return_value = mock_resp

        with pytest.raises(MvsepTimeoutError):
            await client._poll_job("abc123")


@pytest.mark.asyncio
async def test_poll_failed_status(client, mock_response):
    """Test polling raises MvsepNonRetriableError on failed status."""
    mock_response.json.return_value = {"success": True, "status": "failed", "data": {"message": "Job failed"}}

    with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        with pytest.raises(MvsepNonRetriableError, match="Job failed"):
            await client._poll_job("abc123")


def test_is_available_with_key(client):
    """Test is_available returns True when configured correctly."""
    assert client.is_available is True


def test_is_available_without_key(tmp_path):
    """Test is_available returns False when no API key."""
    client = MvsepClient(api_token="", enabled=True)
    assert client.is_available is False


def test_is_available_false_when_quota_exhausted(client):
    """Test is_available returns False when quota exhausted."""
    client._quota_exhausted = True
    assert client.is_available is False


def test_quota_resets_on_new_utc_day(client):
    """Test quota-exhausted flag resets on new UTC day."""
    from datetime import datetime, timezone, timedelta
    client._quota_exhausted = True
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    client._quota_reset_utc = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
    assert client.is_available is True
    assert client._quota_exhausted is False


@pytest.mark.asyncio
async def test_aclose_closes_httpx_client(client):
    """Test aclose() closes the httpx client."""
    with patch.object(client._client, "aclose", new_callable=AsyncMock) as mock_aclose:
        await client.aclose()
        mock_aclose.assert_called_once()


@pytest.mark.asyncio
async def test_submit_invalid_key_error(client, mock_response):
    """Test invalid key error in response body disables client."""
    mock_response.json.return_value = {"success": False, "data": {"message": "Invalid API key"}}

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        with pytest.raises(MvsepNonRetriableError, match="Invalid API key"):
            await client._submit_job(
                client._test_audio,
                sep_type=40,
                add_opt1=81,
            )

        assert client._disabled is True


@pytest.mark.asyncio
async def test_submit_insufficient_credits_error(client, mock_response):
    """Test insufficient credits error in response body disables client."""
    mock_response.json.return_value = {
        "success": False,
        "data": {"message": "Insufficient credits"},
    }

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        with pytest.raises(MvsepNonRetriableError, match="Insufficient credits"):
            await client._submit_job(
                client._test_audio,
                sep_type=40,
                add_opt1=81,
            )

        assert client._disabled is True


@pytest.mark.asyncio
async def test_submit_timeout_error(client):
    """Test timeout exception raises MvsepClientError (retriable)."""
    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.TimeoutException("Request timed out")

        with pytest.raises(MvsepClientError, match="Request timed out"):
            await client._submit_job(
                client._test_audio,
                sep_type=40,
                add_opt1=81,
            )


@pytest.mark.asyncio
async def test_submit_request_error(client):
    """Test request error raises MvsepClientError (retriable)."""
    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.side_effect = httpx.RequestError("Connection failed")

        with pytest.raises(MvsepClientError, match="Connection failed"):
            await client._submit_job(
                client._test_audio,
                sep_type=40,
                add_opt1=81,
            )


@pytest.mark.asyncio
async def test_poll_not_found_status(client, mock_response):
    """Test polling raises MvsepNonRetriableError on not_found status."""
    mock_response.json.return_value = {"success": False, "status": "not_found"}

    with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        with pytest.raises(MvsepNonRetriableError, match="not found"):
            await client._poll_job("abc123")


@pytest.mark.asyncio
async def test_poll_error_status(client, mock_response):
    """Test polling raises MvsepNonRetriableError on error status."""
    mock_response.json.return_value = {"success": False, "status": "error", "data": {"message": "Server error"}}

    with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        with pytest.raises(MvsepNonRetriableError, match="Server error"):
            await client._poll_job("abc123")


@pytest.mark.asyncio
async def test_separate_vocals_handles_other_type(client, tmp_path, mock_response):
    """Test that 'Other' type from MelBand Roformer is correctly identified as instrumental.

    MelBand Roformer (sep_type=48) labels instrumental as 'Other' rather than 'Instrumental'.
    This test verifies the fix for the bug where instrumental files weren't being matched.
    """
    # Simulate the actual API response from MelBand Roformer
    mock_response.json.side_effect = [
        {"success": True, "data": {"hash": "test123", "link": "https://mvsep.com/result"}},
        {
            "success": True,
            "status": "done",
            "data": {
                "hash": "20260430153526-ff12686013-audio.mp3",
                "algorithm": "MelBand Roformer (vocals, instrumental)",
                "output_format": "flac (lossless, 16 bit)",
                "files": [
                    {
                        "type": "Vocals",
                        "url": "https://mvsep.com/storage/processed/20260430153526-ff12686013-audio_melroformer_mt_11_vocals.flac",
                        "download": "audio_melroformer_mt_11_vocals.flac"
                    },
                    {
                        "type": "Other",
                        "url": "https://mvsep.com/storage/processed/20260430153526-ff12686013-audio_melroformer_mt_11_other.flac",
                        "download": "audio_melroformer_mt_11_other.flac"
                    }
                ]
            }
        }
    ]

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
            with patch.object(client, "_download_files", new_callable=AsyncMock) as mock_download:
                # Simulate the downloaded files with the actual filenames
                vocals_file = output_dir / "audio_melroformer_mt_11_vocals.flac"
                other_file = output_dir / "audio_melroformer_mt_11_other.flac"
                vocals_file.write_text("fake vocals")
                other_file.write_text("fake other")
                mock_download.return_value = [vocals_file, other_file]

                mock_post.return_value = mock_response
                mock_get.return_value = mock_response

                result_vocals, result_instrumental = await client.separate_vocals(
                    client._test_audio, output_dir
                )

                assert result_vocals == vocals_file
                assert result_instrumental == other_file, (
                    f"Expected Other file to be identified as instrumental, "
                    f"but got: {result_instrumental}"
                )


@pytest.mark.asyncio
async def test_separate_vocals_semaphore_allows_concurrency(client, tmp_path, mock_response):
    """Test that concurrent separate_vocals calls are allowed up to max_concurrent=3."""
    import asyncio

    in_flight = 0
    max_in_flight = 0
    enter_event = asyncio.Event()

    async def mock_submit_job(*args, **kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        enter_event.set()  # Signal that at least one entered
        await asyncio.sleep(0.05)
        in_flight -= 1
        return "test_hash"

    async def mock_poll_job(job_hash):
        return {
            "success": True,
            "status": "done",
            "data": {"files": []},
        }

    async def mock_download(file_entries, output_dir):
        return []

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    with patch.object(client, "_submit_job", side_effect=mock_submit_job):
        with patch.object(client, "_poll_job", side_effect=mock_poll_job):
            with patch.object(client, "_download_files", side_effect=mock_download):
                # Launch 3 concurrent calls — all should enter concurrently
                results = await asyncio.gather(
                    client.separate_vocals(client._test_audio, output_dir),
                    client.separate_vocals(client._test_audio, output_dir),
                    client.separate_vocals(client._test_audio, output_dir),
                )

    # All should succeed
    assert all(r == (None, None) for r in results)
    # All 3 should have been in flight at the same time
    assert max_in_flight == 3


@pytest.mark.asyncio
async def test_separate_vocals_semaphore_blocks_4th(client, tmp_path, mock_response):
    """Test that a 4th concurrent call blocks until one of the first 3 completes."""
    import asyncio

    in_flight = 0
    max_in_flight = 0
    release = asyncio.Event()

    async def mock_submit_job(*args, **kwargs):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await release.wait()
        in_flight -= 1
        return "test_hash"

    async def mock_poll_job(job_hash):
        return {
            "success": True,
            "status": "done",
            "data": {"files": []},
        }

    async def mock_download(file_entries, output_dir):
        return []

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    with patch.object(client, "_submit_job", side_effect=mock_submit_job):
        with patch.object(client, "_poll_job", side_effect=mock_poll_job):
            with patch.object(client, "_download_files", side_effect=mock_download):
                # Launch 4 concurrent calls
                tasks = [
                    asyncio.create_task(client.separate_vocals(client._test_audio, output_dir))
                    for _ in range(4)
                ]
                # Give time for first 3 to enter (4th should block)
                await asyncio.sleep(0.1)
                # Only 3 should be in flight
                assert max_in_flight == 3
                # Release all
                release.set()
                results = await asyncio.gather(*tasks)

    assert all(r == (None, None) for r in results)
    # Max in-flight never exceeded 3
    assert max_in_flight == 3


@pytest.mark.asyncio
async def test_quota_exhausted_detected_from_success_false(client, mock_post):
    """Test that API response with success=false and quota keywords sets _quota_exhausted."""
    req = httpx.Request("POST", "https://api.mvsep.com/api/create")
    mock_post.return_value = httpx.Response(
        200,
        json={"success": False, "data": {"message": "Daily limit exceeded"}},
        request=req,
    )
    with pytest.raises(MvsepNonRetriableError):
        await client._submit_job(client._test_audio, sep_type=48, add_opt1=11)
    assert client._quota_exhausted is True


@pytest.mark.asyncio
async def test_quota_exhausted_detected_from_400(client, mock_post):
    """Test that HTTP 400 with quota keywords sets _quota_exhausted."""
    req = httpx.Request("POST", "https://api.mvsep.com/api/create")
    mock_post.return_value = httpx.Response(
        400, text="You have exceeded your daily quota", request=req
    )
    with pytest.raises(MvsepNonRetriableError):
        await client._submit_job(client._test_audio, sep_type=48, add_opt1=11)
    assert client._quota_exhausted is True


@pytest.mark.asyncio
async def test_quota_not_triggered_by_generic_error(client, mock_post):
    """Test that non-quota errors don't set _quota_exhausted."""
    req = httpx.Request("POST", "https://api.mvsep.com/api/create")
    mock_post.return_value = httpx.Response(
        200,
        json={"success": False, "data": {"message": "Some other error"}},
        request=req,
    )
    with pytest.raises(MvsepClientError):
        await client._submit_job(client._test_audio, sep_type=48, add_opt1=11)
    assert client._quota_exhausted is False
