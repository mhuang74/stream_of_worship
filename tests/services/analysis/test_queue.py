"""Tests for job queue."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sow_analysis.models import AnalyzeJobRequest, JobStatus, JobType, LrcJobRequest
from sow_analysis.workers.queue import Job, JobQueue


class TestJobQueue:
    """Test JobQueue class."""

    @pytest.fixture
    async def queue(self):
        """Create a test job queue."""
        with tempfile.TemporaryDirectory() as tmp:
            q = JobQueue(max_concurrent=1, cache_dir=Path(tmp))
            yield q
            q.stop()

    @pytest.mark.asyncio
    async def test_submit_job(self, queue):
        """Test submitting a job."""
        request = AnalyzeJobRequest(
            audio_url="s3://bucket/hash/audio.mp3", content_hash="abc123"
        )

        job = await queue.submit(JobType.ANALYZE, request)

        assert job.id.startswith("job_")
        assert job.type == JobType.ANALYZE
        assert job.status == JobStatus.QUEUED

    @pytest.mark.asyncio
    async def test_get_job(self, queue):
        """Test getting a job by ID."""
        request = AnalyzeJobRequest(
            audio_url="s3://bucket/hash/audio.mp3", content_hash="abc123"
        )

        job = await queue.submit(JobType.ANALYZE, request)
        retrieved = await queue.get_job(job.id)

        assert retrieved is not None
        assert retrieved.id == job.id

    @pytest.mark.asyncio
    async def test_get_missing_job(self, queue):
        """Test getting a non-existent job."""
        job = await queue.get_job("job_nonexistent")
        assert job is None

    @pytest.mark.asyncio
    async def test_submit_lrc_job(self, queue):
        """Test submitting an LRC job."""
        request = LrcJobRequest(
            audio_url="s3://bucket/hash/audio.mp3",
            content_hash="abc123",
            lyrics_text="Line 1\nLine 2",
        )

        job = await queue.submit(JobType.LRC, request)

        assert job.id.startswith("job_")
        assert job.type == JobType.LRC
        assert job.status == JobStatus.QUEUED


class TestLRCJobProcessing:
    """Test LRC job processing (stub behavior)."""

    @pytest.fixture
    async def queue(self):
        """Create a test job queue."""
        with tempfile.TemporaryDirectory() as tmp:
            q = JobQueue(max_concurrent=1, cache_dir=Path(tmp))
            yield q
            q.stop()

    @pytest.mark.asyncio
    async def test_lrc_job_fails_with_not_implemented(self, queue):
        """Test LRC job fails with not implemented error."""
        request = LrcJobRequest(
            audio_url="s3://bucket/hash/audio.mp3",
            content_hash="abc123",
            lyrics_text="Line 1\nLine 2",
        )

        job = await queue.submit(JobType.LRC, request)

        # Process the job directly
        await queue._process_lrc_job(job)

        assert job.status == JobStatus.FAILED
        assert "not yet implemented" in job.error_message
        assert job.stage == "not_implemented"

    @pytest.mark.asyncio
    async def test_lrc_job_with_invalid_request(self, queue):
        """Test LRC job with invalid request type."""
        # Submit as analyze but try to process as LRC
        request = AnalyzeJobRequest(
            audio_url="s3://bucket/hash/audio.mp3", content_hash="abc123"
        )

        job = Job(
            id="job_test123",
            type=JobType.LRC,
            status=JobStatus.PROCESSING,
            request=request,
        )

        await queue._process_lrc_job(job)

        # LRC job should fail with not implemented regardless of request type
        assert job.status == JobStatus.FAILED
        assert "not yet implemented" in job.error_message


class TestJobQueueConcurrency:
    """Test job queue concurrency."""

    @pytest.mark.asyncio
    async def test_max_concurrent_jobs(self):
        """Test max concurrent job limit."""
        with tempfile.TemporaryDirectory() as tmp:
            queue = JobQueue(max_concurrent=2, cache_dir=Path(tmp))

            assert queue.max_concurrent == 2
            assert queue._semaphore._value == 2

            queue.stop()


class TestJobQueueR2:
    """Test R2 initialization."""

    @pytest.mark.asyncio
    async def test_initialize_r2(self):
        """Test initializing R2 client."""
        with tempfile.TemporaryDirectory() as tmp:
            queue = JobQueue(max_concurrent=1, cache_dir=Path(tmp))

            with patch("sow_analysis.workers.queue.R2Client") as mock_r2:
                mock_instance = MagicMock()
                mock_r2.return_value = mock_instance

                queue.initialize_r2("my-bucket", "https://r2.example.com")

                assert queue.r2_client is not None
                mock_r2.assert_called_once_with("my-bucket", "https://r2.example.com")

            queue.stop()
