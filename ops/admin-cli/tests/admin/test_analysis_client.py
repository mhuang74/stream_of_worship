"""Tests for AnalysisClient service."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from stream_of_worship.admin.services.analysis import (
    AnalysisClient,
    AnalysisResult,
    AnalysisServiceError,
    JobInfo,
)


@pytest.fixture
def api_key_env(monkeypatch):
    """Set the required API key environment variable."""
    monkeypatch.setenv("SOW_ANALYSIS_API_KEY", "test-api-key")


class TestAnalysisClientInit:
    """Tests for AnalysisClient construction."""

    def test_creates_with_api_key(self, api_key_env):
        """Creates successfully when API key is set."""
        client = AnalysisClient("http://localhost:8000")
        assert client.base_url == "http://localhost:8000"
        assert client.timeout == 30

    def test_stores_base_url_and_timeout(self, api_key_env):
        """Base URL and timeout are stored as attributes."""
        client = AnalysisClient("http://example.com", timeout=60)
        assert client.base_url == "http://example.com"
        assert client.timeout == 60

    def test_strips_trailing_slash(self, api_key_env):
        """Trailing slash is stripped from base URL."""
        client = AnalysisClient("http://localhost:8000/")
        assert client.base_url == "http://localhost:8000"

    def test_raises_without_api_key(self, monkeypatch):
        """Raises ValueError when SOW_ANALYSIS_API_KEY is not set."""
        monkeypatch.delenv("SOW_ANALYSIS_API_KEY", raising=False)

        with pytest.raises(ValueError, match="SOW_ANALYSIS_API_KEY"):
            AnalysisClient("http://localhost:8000")


class TestHealthCheck:
    """Tests for AnalysisClient.health_check."""

    @patch("stream_of_worship.admin.services.analysis.requests.get")
    def test_success(self, mock_get, api_key_env):
        """Returns health data on successful request."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"status": "healthy"}
        mock_get.return_value = mock_response

        client = AnalysisClient("http://localhost:8000")
        result = client.health_check()

        assert result["status"] == "healthy"
        mock_get.assert_called_once_with(
            "http://localhost:8000/api/v1/health",
            timeout=30,
        )

    @patch("stream_of_worship.admin.services.analysis.requests.get")
    def test_connection_error(self, mock_get, api_key_env):
        """Raises AnalysisServiceError on connection failure."""
        mock_get.side_effect = requests.exceptions.ConnectionError("Connection refused")

        client = AnalysisClient("http://localhost:8000")
        with pytest.raises(AnalysisServiceError, match="Cannot connect"):
            client.health_check()


class TestSubmitAnalysis:
    """Tests for AnalysisClient.submit_analysis."""

    @patch("stream_of_worship.admin.services.analysis.requests.post")
    def test_success(self, mock_post, api_key_env):
        """Returns JobInfo on successful submission."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "job_id": "job-123",
            "status": "queued",
            "job_type": "analysis",
            "progress": 0.0,
        }
        mock_post.return_value = mock_response

        client = AnalysisClient("http://localhost:8000")
        job = client.submit_analysis(
            audio_url="s3://bucket/audio.mp3",
            content_hash="abc123",
        )

        assert job.job_id == "job-123"
        assert job.status == "queued"
        assert job.job_type == "analysis"

    @patch("stream_of_worship.admin.services.analysis.requests.post")
    def test_options_passed_correctly(self, mock_post, api_key_env):
        """Options are passed in request body."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"job_id": "job-123", "status": "queued", "job_type": "analysis"}
        mock_post.return_value = mock_response

        client = AnalysisClient("http://localhost:8000")
        client.submit_analysis(
            audio_url="s3://bucket/audio.mp3",
            content_hash="abc123",
            generate_stems=False,
            force=True,
        )

        call_args = mock_post.call_args
        payload = call_args.kwargs["json"]
        assert payload["audio_url"] == "s3://bucket/audio.mp3"
        assert payload["content_hash"] == "abc123"
        assert payload["options"]["generate_stems"] is False
        assert payload["options"]["force"] is True

    @patch("stream_of_worship.admin.services.analysis.requests.post")
    def test_connection_error(self, mock_post, api_key_env):
        """Raises AnalysisServiceError on connection failure."""
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection refused")

        client = AnalysisClient("http://localhost:8000")
        with pytest.raises(AnalysisServiceError, match="Cannot connect"):
            client.submit_analysis("s3://bucket/audio.mp3", "abc123")

    @patch("stream_of_worship.admin.services.analysis.requests.post")
    def test_401_unauthorized(self, mock_post, api_key_env):
        """Raises AnalysisServiceError with status_code 401 on auth failure."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_post.return_value = mock_response

        client = AnalysisClient("http://localhost:8000")
        with pytest.raises(AnalysisServiceError) as exc_info:
            client.submit_analysis("s3://bucket/audio.mp3", "abc123")

        assert exc_info.value.status_code == 401
        assert "Authentication failed" in str(exc_info.value)

    @patch("stream_of_worship.admin.services.analysis.requests.post")
    def test_500_server_error(self, mock_post, api_key_env):
        """Raises AnalysisServiceError on server error."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "500 Server Error"
        )
        mock_post.return_value = mock_response

        client = AnalysisClient("http://localhost:8000")
        with pytest.raises(AnalysisServiceError):
            client.submit_analysis("s3://bucket/audio.mp3", "abc123")


class TestGetJob:
    """Tests for AnalysisClient.get_job."""

    @patch("stream_of_worship.admin.services.analysis.requests.get")
    def test_queued_job(self, mock_get, api_key_env):
        """Returns JobInfo for queued job."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "job_id": "job-123",
            "status": "queued",
            "job_type": "analysis",
            "progress": 0.0,
        }
        mock_get.return_value = mock_response

        client = AnalysisClient("http://localhost:8000")
        job = client.get_job("job-123")

        assert job.job_id == "job-123"
        assert job.status == "queued"

    @patch("stream_of_worship.admin.services.analysis.requests.get")
    def test_completed_job_with_result(self, mock_get, api_key_env):
        """Returns JobInfo with AnalysisResult for completed job."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "job_id": "job-123",
            "status": "completed",
            "job_type": "analysis",
            "progress": 1.0,
            "result": {
                "duration_seconds": 245.3,
                "tempo_bpm": 128.5,
                "musical_key": "G",
                "musical_mode": "major",
                "stems_url": "s3://bucket/stems/",
            },
        }
        mock_get.return_value = mock_response

        client = AnalysisClient("http://localhost:8000")
        job = client.get_job("job-123")

        assert job.status == "completed"
        assert job.result is not None
        assert job.result.tempo_bpm == 128.5
        assert job.result.musical_key == "G"
        assert job.result.stems_url == "s3://bucket/stems/"

    @patch("stream_of_worship.admin.services.analysis.requests.get")
    def test_failed_job(self, mock_get, api_key_env):
        """Returns JobInfo with error_message for failed job."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "job_id": "job-123",
            "status": "failed",
            "job_type": "analysis",
            "error_message": "Analysis failed: out of memory",
        }
        mock_get.return_value = mock_response

        client = AnalysisClient("http://localhost:8000")
        job = client.get_job("job-123")

        assert job.status == "failed"
        assert job.error_message == "Analysis failed: out of memory"

    @patch("stream_of_worship.admin.services.analysis.requests.get")
    def test_404_not_found(self, mock_get, api_key_env):
        """Raises AnalysisServiceError with status_code 404."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_get.return_value = mock_response

        client = AnalysisClient("http://localhost:8000")
        with pytest.raises(AnalysisServiceError) as exc_info:
            client.get_job("job-nonexistent")

        assert exc_info.value.status_code == 404
        assert "Job not found" in str(exc_info.value)

    @patch("stream_of_worship.admin.services.analysis.requests.get")
    def test_401_unauthorized(self, mock_get, api_key_env):
        """Raises AnalysisServiceError with status_code 401."""
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_get.return_value = mock_response

        client = AnalysisClient("http://localhost:8000")
        with pytest.raises(AnalysisServiceError) as exc_info:
            client.get_job("job-123")

        assert exc_info.value.status_code == 401


class TestWaitForCompletion:
    """Tests for AnalysisClient.wait_for_completion."""

    @patch("stream_of_worship.admin.services.analysis.AnalysisClient.get_job")
    def test_completes_immediately(self, mock_get_job, api_key_env):
        """Returns immediately if job is already completed."""
        mock_get_job.return_value = JobInfo(
            job_id="job-123",
            status="completed",
            job_type="analysis",
            progress=1.0,
        )

        client = AnalysisClient("http://localhost:8000")
        job = client.wait_for_completion("job-123")

        assert job.status == "completed"
        mock_get_job.assert_called_once_with("job-123")

    @patch("stream_of_worship.admin.services.analysis.time.sleep")
    @patch("stream_of_worship.admin.services.analysis.AnalysisClient.get_job")
    def test_polls_until_complete(self, mock_get_job, mock_sleep, api_key_env):
        """Polls until job completes."""
        mock_get_job.side_effect = [
            JobInfo(job_id="job-123", status="processing", job_type="analysis", progress=0.3),
            JobInfo(job_id="job-123", status="processing", job_type="analysis", progress=0.7),
            JobInfo(job_id="job-123", status="completed", job_type="analysis", progress=1.0),
        ]

        client = AnalysisClient("http://localhost:8000")
        job = client.wait_for_completion("job-123", poll_interval=1.0, timeout=30.0)

        assert job.status == "completed"
        assert mock_get_job.call_count == 3
        assert mock_sleep.call_count == 2

    @patch("stream_of_worship.admin.services.analysis.time.sleep")
    @patch("stream_of_worship.admin.services.analysis.AnalysisClient.get_job")
    def test_returns_on_failure(self, mock_get_job, mock_sleep, api_key_env):
        """Returns when job fails."""
        mock_get_job.side_effect = [
            JobInfo(job_id="job-123", status="processing", job_type="analysis"),
            JobInfo(
                job_id="job-123",
                status="failed",
                job_type="analysis",
                error_message="Out of memory",
            ),
        ]

        client = AnalysisClient("http://localhost:8000")
        job = client.wait_for_completion("job-123")

        assert job.status == "failed"
        assert job.error_message == "Out of memory"

    @patch("stream_of_worship.admin.services.analysis.time.sleep")
    @patch("stream_of_worship.admin.services.analysis.time.time")
    @patch("stream_of_worship.admin.services.analysis.AnalysisClient.get_job")
    def test_timeout(self, mock_get_job, mock_time, mock_sleep, api_key_env):
        """Raises AnalysisServiceError on timeout."""
        mock_time.side_effect = [0, 5, 10, 15]  # Simulates time passing
        mock_get_job.return_value = JobInfo(
            job_id="job-123", status="processing", job_type="analysis"
        )

        client = AnalysisClient("http://localhost:8000")
        with pytest.raises(AnalysisServiceError, match="Timed out"):
            client.wait_for_completion("job-123", poll_interval=5.0, timeout=10.0)

    @patch("stream_of_worship.admin.services.analysis.time.sleep")
    @patch("stream_of_worship.admin.services.analysis.AnalysisClient.get_job")
    def test_callback_invoked(self, mock_get_job, mock_sleep, api_key_env):
        """Callback is called with JobInfo each iteration."""
        mock_get_job.side_effect = [
            JobInfo(job_id="job-123", status="processing", job_type="analysis", progress=0.5),
            JobInfo(job_id="job-123", status="completed", job_type="analysis", progress=1.0),
        ]

        callback_calls = []

        def callback(job):
            callback_calls.append(job)

        client = AnalysisClient("http://localhost:8000")
        client.wait_for_completion("job-123", callback=callback)

        assert len(callback_calls) == 2
        assert callback_calls[0].progress == 0.5
        assert callback_calls[1].progress == 1.0


class TestParseJobResponse:
    """Tests for AnalysisClient._parse_job_response."""

    def test_minimal_response(self):
        """Parses minimal job response."""
        data = {
            "job_id": "job-123",
            "status": "queued",
            "job_type": "analysis",
        }

        job = AnalysisClient._parse_job_response(data)

        assert job.job_id == "job-123"
        assert job.status == "queued"
        assert job.job_type == "analysis"
        assert job.result is None

    def test_full_response_with_result(self):
        """Parses full response with analysis results."""
        data = {
            "job_id": "job-123",
            "status": "completed",
            "job_type": "analysis",
            "progress": 1.0,
            "stage": "done",
            "created_at": "2024-01-15T10:00:00",
            "updated_at": "2024-01-15T10:05:00",
            "result": {
                "duration_seconds": 245.3,
                "tempo_bpm": 128.5,
                "musical_key": "G",
                "musical_mode": "major",
                "key_confidence": 0.87,
                "loudness_db": -8.2,
                "beats": [0.0, 0.5, 1.0],
                "downbeats": [0.0, 2.0],
                "sections": [{"start": 0.0, "end": 60.0}],
                "embeddings_shape": [768, 512],
                "stems_url": "s3://bucket/stems/",
            },
        }

        job = AnalysisClient._parse_job_response(data)

        assert job.job_id == "job-123"
        assert job.status == "completed"
        assert job.progress == 1.0
        assert job.stage == "done"
        assert job.created_at == "2024-01-15T10:00:00"
        assert job.updated_at == "2024-01-15T10:05:00"

        result = job.result
        assert result is not None
        assert result.duration_seconds == 245.3
        assert result.tempo_bpm == 128.5
        assert result.musical_key == "G"
        assert result.musical_mode == "major"
        assert result.key_confidence == 0.87
        assert result.loudness_db == -8.2
        assert result.beats == [0.0, 0.5, 1.0]
        assert result.downbeats == [0.0, 2.0]
        assert result.sections == [{"start": 0.0, "end": 60.0}]
        assert result.embeddings_shape == [768, 512]
        assert result.stems_url == "s3://bucket/stems/"

    def test_null_result(self):
        """Handles null result field."""
        data = {
            "job_id": "job-123",
            "status": "failed",
            "job_type": "analysis",
            "result": None,
        }

        job = AnalysisClient._parse_job_response(data)

        assert job.status == "failed"
        assert job.result is None


class TestAnalysisResult:
    """Tests for AnalysisResult dataclass."""

    def test_defaults(self):
        """All fields default to None."""
        result = AnalysisResult()
        assert result.duration_seconds is None
        assert result.tempo_bpm is None
        assert result.musical_key is None
        assert result.stems_url is None

    def test_full_fields(self):
        """All fields can be set."""
        result = AnalysisResult(
            duration_seconds=245.3,
            tempo_bpm=128.5,
            musical_key="G",
            musical_mode="major",
            key_confidence=0.87,
            loudness_db=-8.2,
            beats=[0.0, 0.5, 1.0],
            downbeats=[0.0, 2.0],
            sections=[{"start": 0.0, "end": 60.0}],
            embeddings_shape=[768, 512],
            stems_url="s3://bucket/stems/",
        )
        assert result.duration_seconds == 245.3
        assert result.tempo_bpm == 128.5


class TestJobInfo:
    """Tests for JobInfo dataclass."""

    def test_defaults(self):
        """Optional fields have defaults."""
        job = JobInfo(job_id="job-123", status="queued", job_type="analysis")
        assert job.progress == 0.0
        assert job.stage == ""
        assert job.error_message is None
        assert job.result is None

    def test_with_result(self):
        """Can include AnalysisResult."""
        result = AnalysisResult(tempo_bpm=128.5)
        job = JobInfo(
            job_id="job-123",
            status="completed",
            job_type="analysis",
            result=result,
        )
        assert job.result is not None
        assert job.result.tempo_bpm == 128.5
