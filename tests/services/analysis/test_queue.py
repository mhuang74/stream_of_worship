"""Tests for job queue."""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sow_analysis.models import AnalyzeJobRequest, JobStatus, JobType, LrcJobRequest, LrcOptions
from sow_analysis.workers.queue import Job, JobQueue, _compute_lrc_cache_key


class TestJobQueue:
    """Test JobQueue class."""

    @pytest.fixture
    async def queue(self):
        """Create a test job queue."""
        with tempfile.TemporaryDirectory() as tmp:
            q = JobQueue(max_concurrent_local_model=1, cache_dir=Path(tmp))
            yield q
            q.stop()

    @pytest.mark.asyncio
    async def test_submit_job(self, queue):
        """Test submitting a job."""
        request = AnalyzeJobRequest(audio_url="s3://bucket/hash/audio.mp3", content_hash="abc123")

        job = await queue.submit(JobType.ANALYZE, request)

        assert job.id.startswith("job_")
        assert job.type == JobType.ANALYZE
        assert job.status == JobStatus.QUEUED

    @pytest.mark.asyncio
    async def test_get_job(self, queue):
        """Test getting a job by ID."""
        request = AnalyzeJobRequest(audio_url="s3://bucket/hash/audio.mp3", content_hash="abc123")

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
    """Test LRC job processing."""

    @pytest.fixture
    async def queue(self):
        """Create a test job queue."""
        with tempfile.TemporaryDirectory() as tmp:
            q = JobQueue(max_concurrent_local_model=1, cache_dir=Path(tmp))
            yield q
            q.stop()

    @pytest.mark.asyncio
    async def test_lrc_job_uses_cache(self, queue):
        """Test LRC job returns cached result when available."""
        request = LrcJobRequest(
            audio_url="s3://bucket/hash/audio.mp3",
            content_hash="abc123def456",
            lyrics_text="Line 1\nLine 2",
        )

        # Pre-populate cache with correct composite key
        cache_key = _compute_lrc_cache_key(request.content_hash, request.lyrics_text)
        queue.cache_manager.save_lrc_result(
            cache_key,
            {"lrc_url": "s3://bucket/abc123def456/lyrics.lrc", "line_count": 2},
        )

        job = await queue.submit(JobType.LRC, request)
        await queue._process_lrc_job(job)

        assert job.status == JobStatus.COMPLETED
        assert job.stage == "cached"
        assert job.result.line_count == 2

    @pytest.mark.asyncio
    async def test_lrc_job_with_invalid_request(self, queue):
        """Test LRC job with invalid request type fails gracefully."""
        # Submit as analyze but try to process as LRC
        request = AnalyzeJobRequest(audio_url="s3://bucket/hash/audio.mp3", content_hash="abc123")

        job = Job(
            id="job_test123",
            type=JobType.LRC,
            status=JobStatus.PROCESSING,
            request=request,
        )

        await queue._process_lrc_job(job)

        # LRC job should fail with invalid request type error
        assert job.status == JobStatus.FAILED
        assert "Invalid request type" in job.error_message

    @pytest.mark.asyncio
    async def test_lrc_child_stem_wait_stops_when_parent_cancelled(self, queue):
        """LRC cancellation during child stem wait does not continue into transcription."""
        request = LrcJobRequest(
            audio_url="s3://bucket/hash/audio.mp3",
            content_hash="abc123def456",
            lyrics_text="Line 1\nLine 2",
            options=LrcOptions(force=True, use_qwen3_asr=False),
        )
        job = Job(
            id="job_cancel_parent",
            type=JobType.LRC,
            status=JobStatus.PROCESSING,
            request=request,
        )
        queue._jobs[job.id] = job

        async def download_audio(_url, path):
            path.write_bytes(b"audio")

        queue.r2_client = MagicMock()
        queue.r2_client.download_audio = AsyncMock(side_effect=download_audio)
        generate_lrc = AsyncMock()

        async def cancel_parent(_delay):
            job.status = JobStatus.CANCELLED
            job.stage = "cancelled"

        with (
            patch(
                "sow_analysis.workers.stem_separation.get_vocals_dry_url",
                new=AsyncMock(return_value=None),
            ),
            patch("sow_analysis.workers.queue.generate_lrc", new=generate_lrc),
            patch(
                "sow_analysis.workers.queue.asyncio.sleep",
                new=AsyncMock(side_effect=cancel_parent),
            ),
        ):
            await queue._process_lrc_job(job)

        assert job.status == JobStatus.CANCELLED
        generate_lrc.assert_not_awaited()


class TestJobQueueConcurrency:
    """Test job queue concurrency."""

    @pytest.mark.asyncio
    async def test_max_concurrent_jobs(self):
        """Test max concurrent job limit."""
        with tempfile.TemporaryDirectory() as tmp:
            queue = JobQueue(max_concurrent_local_model=2, cache_dir=Path(tmp))

            assert queue.max_concurrent_local_model == 2

            queue.stop()


class TestJobQueueR2:
    """Test R2 initialization."""

    @pytest.mark.asyncio
    async def test_initialize_r2(self):
        """Test initializing R2 client."""
        with tempfile.TemporaryDirectory() as tmp:
            queue = JobQueue(max_concurrent_local_model=1, cache_dir=Path(tmp))

            with patch("sow_analysis.workers.queue.R2Client") as mock_r2:
                mock_instance = MagicMock()
                mock_r2.return_value = mock_instance

                queue.initialize_r2("my-bucket", "https://r2.example.com")

                assert queue.r2_client is not None
                mock_r2.assert_called_once_with("my-bucket", "https://r2.example.com")

            queue.stop()
