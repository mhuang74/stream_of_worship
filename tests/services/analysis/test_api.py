"""Tests for FastAPI endpoints."""

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sow_analysis.main import app
from sow_analysis.models import JobStatus, JobType
from sow_analysis.routes.jobs import set_job_queue
from sow_analysis.workers.queue import Job, JobQueue


@pytest.fixture
def mock_job_queue():
    """Create a mock job queue."""
    queue = MagicMock(spec=JobQueue)

    # Make async methods return coroutines
    async def mock_submit(*args, **kwargs):
        now = datetime.now(timezone.utc)
        return Job(
            id="job_abc123",
            type=JobType.ANALYZE,
            status=JobStatus.QUEUED,
            request=MagicMock(),
            created_at=now,
            updated_at=now,
        )

    async def mock_get_job(job_id):
        if job_id == "job_abc123":
            now = datetime.now(timezone.utc)
            return Job(
                id="job_abc123",
                type=JobType.ANALYZE,
                status=JobStatus.PROCESSING,
                request=MagicMock(),
                created_at=now,
                updated_at=now,
                progress=0.5,
                stage="analyzing",
            )
        return None

    queue.submit = mock_submit
    queue.get_job = mock_get_job

    set_job_queue(queue)
    yield queue
    set_job_queue(None)


@pytest.fixture
def client(mock_job_queue):
    """Create a test client."""
    return TestClient(app)


@pytest.fixture(autouse=True)
def mock_settings():
    """Mock settings with API key."""
    with patch(
        "sow_analysis.routes.jobs.settings",
        SOW_ANALYSIS_API_KEY="test-api-key",
    ):
        with patch(
            "sow_analysis.routes.health.settings",
            SOW_ANALYSIS_API_KEY="test-api-key",
            SOW_R2_BUCKET="test-bucket",
            SOW_R2_ENDPOINT_URL="",
            CACHE_DIR="/tmp/test-cache",
        ):
            yield


class TestHealthEndpoint:
    """Test health check endpoint."""

    def test_health_check(self, client):
        """Test health check returns healthy."""
        response = client.get("/api/v1/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data
        assert "services" in data

    def test_root_endpoint(self, client):
        """Test root endpoint."""
        response = client.get("/")

        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "version" in data


class TestJobsEndpoints:
    """Test jobs API endpoints."""

    def test_submit_analysis_job(self, client, mock_job_queue):
        """Test submitting analysis job."""
        response = client.post(
            "/api/v1/jobs/analyze",
            json={
                "audio_url": "s3://bucket/hash/audio.mp3",
                "content_hash": "abc123",
            },
            headers={"Authorization": "Bearer test-api-key"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == "job_abc123"
        assert data["status"] == "queued"

    def test_submit_analysis_job_no_auth(self, client):
        """Test submitting without auth fails."""
        response = client.post(
            "/api/v1/jobs/analyze",
            json={
                "audio_url": "s3://bucket/hash/audio.mp3",
                "content_hash": "abc123",
            },
        )

        assert response.status_code == 401

    def test_submit_analysis_job_wrong_auth(self, client):
        """Test submitting with wrong auth fails."""
        response = client.post(
            "/api/v1/jobs/analyze",
            json={
                "audio_url": "s3://bucket/hash/audio.mp3",
                "content_hash": "abc123",
            },
            headers={"Authorization": "Bearer wrong-key"},
        )

        assert response.status_code == 401

    def test_submit_lrc_job(self, client, mock_job_queue):
        """Test submitting LRC job."""

        async def mock_submit_lrc(*args, **kwargs):
            now = datetime.now(timezone.utc)
            return Job(
                id="job_def456",
                type=JobType.LRC,
                status=JobStatus.QUEUED,
                request=MagicMock(),
                created_at=now,
                updated_at=now,
            )

        mock_job_queue.submit = mock_submit_lrc

        response = client.post(
            "/api/v1/jobs/lrc",
            json={
                "audio_url": "s3://bucket/hash/audio.mp3",
                "content_hash": "abc123",
                "lyrics_text": "Line 1\nLine 2",
            },
            headers={"Authorization": "Bearer test-api-key"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == "job_def456"
        assert data["job_type"] == "lrc"

    def test_get_job_status(self, client, mock_job_queue):
        """Test getting job status."""

        async def mock_get_job(job_id):
            now = datetime.now(timezone.utc)
            return Job(
                id="job_abc123",
                type=JobType.ANALYZE,
                status=JobStatus.PROCESSING,
                request=MagicMock(),
                created_at=now,
                updated_at=now,
                progress=0.5,
                stage="analyzing",
            )

        mock_job_queue.get_job = mock_get_job

        response = client.get(
            "/api/v1/jobs/job_abc123",
            headers={"Authorization": "Bearer test-api-key"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == "job_abc123"
        assert data["status"] == "processing"
        assert data["progress"] == 0.5
        assert data["stage"] == "analyzing"

    def test_get_job_status_not_found(self, client, mock_job_queue):
        """Test getting non-existent job."""

        async def mock_get_job_not_found(job_id):
            return None

        mock_job_queue.get_job = mock_get_job_not_found

        response = client.get(
            "/api/v1/jobs/job_missing",
            headers={"Authorization": "Bearer test-api-key"},
        )

        assert response.status_code == 404

    def test_get_job_status_no_queue_initialized(self, client):
        """Test error when queue not initialized."""
        set_job_queue(None)

        response = client.get(
            "/api/v1/jobs/job_abc123",
            headers={"Authorization": "Bearer test-api-key"},
        )

        assert response.status_code == 500

    def test_submit_analysis_with_options(self, client, mock_job_queue):
        """Test submitting analysis with custom options."""
        response = client.post(
            "/api/v1/jobs/analyze",
            json={
                "audio_url": "s3://bucket/hash/audio.mp3",
                "content_hash": "abc123",
                "options": {
                    "generate_stems": False,
                    "stem_model": "demucs",
                    "force": True,
                },
            },
            headers={"Authorization": "Bearer test-api-key"},
        )

        assert response.status_code == 200
