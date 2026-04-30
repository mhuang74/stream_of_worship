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
    SOW_MVSEP_VOCAL_MODEL = 81
    SOW_MVSEP_DEREVERB_MODEL = 0
    SOW_MVSEP_HTTP_TIMEOUT = 60
    SOW_MVSEP_STAGE_TIMEOUT = 300
    SOW_MVSEP_TOTAL_TIMEOUT = 900
    SOW_MVSEP_DAILY_JOB_LIMIT = 50


# Mock the config module before importing mvsep_client
config_module = type(sys)("sow_analysis.config")
config_module.settings = MockSettings()
sys.modules["sow_analysis.config"] = config_module

# Also mock the parent module to prevent other imports
sow_analysis_module = type(sys)("sow_analysis")
sow_analysis_module.config = config_module
sys.modules["sow_analysis"] = sow_analysis_module

# Now import mvsep_client directly
from sow_analysis.services.mvsep_client import (
    MvsepClient,
    MvsepClientError,
    MvsepNonRetriableError,
    MvsepTimeoutError,
)


@pytest.fixture
def client():
    """Create a test MVSEP client."""
    return MvsepClient(
        api_token="test-token",
        enabled=True,
        vocal_model=81,
        dereverb_model=0,
        http_timeout=60,
        stage_timeout=300,
        daily_job_limit=50,
    )


@pytest.fixture
def mock_response():
    """Create a mock HTTP response."""
    response = MagicMock()
    response.json.return_value = {}
    return response


@pytest.mark.asyncio
async def test_submit_success(client, mock_response):
    """Test successful job submission returns job hash."""
    mock_response.json.return_value = {"hash": "abc123"}

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        result = await client._submit_job(
            Path("/tmp/test.mp3"),
            sep_type=40,
            add_opt1=81,
        )

        assert result == "abc123"


@pytest.mark.asyncio
async def test_submit_api_error(client, mock_response):
    """Test API error response raises MvsepClientError."""
    mock_response.json.return_value = {"error": "some_error", "message": "API error"}

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        with pytest.raises(MvsepClientError, match="API error"):
            await client._submit_job(
                Path("/tmp/test.mp3"),
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
                Path("/tmp/test.mp3"),
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
                Path("/tmp/test.mp3"),
                sep_type=40,
                add_opt1=81,
            )

        assert client._disabled is True


def test_is_available_disabled_after_non_retriable(client):
    """Test is_available returns False after _disabled is set."""
    assert client.is_available is True
    client._disabled = True
    assert client.is_available is False


@pytest.mark.asyncio
async def test_poll_done(client, mock_response):
    """Test polling returns data when status is done."""
    mock_response.json.return_value = {
        "status": "done",
        "files": [{"url": "http://example.com/vocals.flac", "name": "vocals.flac"}],
    }

    with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        result = await client._poll_job("abc123")

        assert result["status"] == "done"
        assert "files" in result


@pytest.mark.asyncio
async def test_poll_timeout(client):
    """Test polling raises MvsepTimeoutError after timeout."""
    client.stage_timeout = 0.1  # Very short timeout

    with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value.json.return_value = {"status": "processing"}

        with pytest.raises(MvsepTimeoutError):
            await client._poll_job("abc123")


@pytest.mark.asyncio
async def test_poll_failed_status(client, mock_response):
    """Test polling raises MvsepNonRetriableError on failed status."""
    mock_response.json.return_value = {"status": "failed", "message": "Job failed"}

    with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        with pytest.raises(MvsepNonRetriableError, match="Job failed"):
            await client._poll_job("abc123")


def test_is_available_with_key(client):
    """Test is_available returns True when configured correctly."""
    assert client.is_available is True


def test_is_available_without_key():
    """Test is_available returns False when no API key."""
    client = MvsepClient(api_token="", enabled=True)
    assert client.is_available is False


def test_is_available_daily_limit_exceeded(client):
    """Test is_available returns False when daily limit exceeded."""
    client._daily_job_count = 50  # At limit
    assert client.is_available is False

    client._daily_job_count = 51  # Over limit
    assert client.is_available is False


def test_daily_limit_resets_on_new_utc_day(client):
    """Test daily job count resets on new UTC day."""
    # Set up as if we ran jobs yesterday
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    client._daily_reset_utc = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
    client._daily_job_count = 50  # At limit

    # Check should reset the counter
    assert client._check_daily_limit() is True
    assert client._daily_job_count == 0


@pytest.mark.asyncio
async def test_aclose_closes_httpx_client(client):
    """Test aclose() closes the httpx client."""
    with patch.object(client._client, "aclose", new_callable=AsyncMock) as mock_aclose:
        await client.aclose()
        mock_aclose.assert_called_once()


@pytest.mark.asyncio
async def test_submit_invalid_key_error(client, mock_response):
    """Test invalid key error in response body disables client."""
    mock_response.json.return_value = {"error": "invalid_key", "message": "Invalid API key"}

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        with pytest.raises(MvsepNonRetriableError, match="Invalid API key"):
            await client._submit_job(
                Path("/tmp/test.mp3"),
                sep_type=40,
                add_opt1=81,
            )

        assert client._disabled is True


@pytest.mark.asyncio
async def test_submit_insufficient_credits_error(client, mock_response):
    """Test insufficient credits error in response body disables client."""
    mock_response.json.return_value = {
        "error": "insufficient_credits",
        "message": "Insufficient credits",
    }

    with patch.object(client._client, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        with pytest.raises(MvsepNonRetriableError, match="Insufficient credits"):
            await client._submit_job(
                Path("/tmp/test.mp3"),
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
                Path("/tmp/test.mp3"),
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
                Path("/tmp/test.mp3"),
                sep_type=40,
                add_opt1=81,
            )


@pytest.mark.asyncio
async def test_poll_not_found_status(client, mock_response):
    """Test polling raises MvsepNonRetriableError on not_found status."""
    mock_response.json.return_value = {"status": "not_found"}

    with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        with pytest.raises(MvsepNonRetriableError, match="not found"):
            await client._poll_job("abc123")


@pytest.mark.asyncio
async def test_poll_error_status(client, mock_response):
    """Test polling raises MvsepNonRetriableError on error status."""
    mock_response.json.return_value = {"status": "error", "message": "Server error"}

    with patch.object(client._client, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = mock_response

        with pytest.raises(MvsepNonRetriableError, match="Server error"):
            await client._poll_job("abc123")
