"""Tests for job queue."""

import asyncio
import logging
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
from sow_analysis.models import (
    AnalyzeJobRequest,
    EmbeddingJobRequest,
    FastAnalyzeJobRequest,
    ForcedAlignmentJobRequest,
    JobStatus,
    JobType,
    LrcJobRequest,
    LrcOptions,
    StemSeparationJobRequest,
)
from sow_analysis.workers.queue import (
    FINISHED_JOB_MEMORY_RETENTION_SECONDS,
    Job,
    JobQueue,
    _compute_lrc_cache_key,
)


def _queue_state_messages(caplog):
    return [record.message for record in caplog.records if "Queue state:" in record.message]


def _make_analysis_job(job_id: str, status: JobStatus) -> Job:
    return Job(
        id=job_id,
        type=JobType.ANALYZE,
        status=status,
        request=AnalyzeJobRequest(audio_url=f"s3://bucket/{job_id}.mp3", content_hash=job_id),
    )


def _make_embedding_job(job_id: str, status: JobStatus) -> Job:
    return Job(
        id=job_id,
        type=JobType.EMBEDDING,
        status=status,
        request=EmbeddingJobRequest(
            song_id=job_id,
            title="Test Song",
            content_hash=job_id,
        ),
    )


def _make_job_for_type(job_type: JobType, job_id: str, status: JobStatus) -> Job:
    """Build a minimal valid Job for any JobType."""
    if job_type == JobType.ANALYZE:
        return _make_analysis_job(job_id, status)
    if job_type == JobType.EMBEDDING:
        return _make_embedding_job(job_id, status)
    if job_type == JobType.LRC:
        return Job(
            id=job_id,
            type=job_type,
            status=status,
            request=LrcJobRequest(
                audio_url=f"s3://bucket/{job_id}.mp3",
                content_hash=job_id,
                lyrics_text="测试歌词",
            ),
        )
    if job_type == JobType.STEM_SEPARATION:
        return Job(
            id=job_id,
            type=job_type,
            status=status,
            request=StemSeparationJobRequest(
                audio_url=f"s3://bucket/{job_id}.mp3",
                content_hash=job_id,
            ),
        )
    if job_type == JobType.FORCED_ALIGNMENT:
        return Job(
            id=job_id,
            type=job_type,
            status=status,
            request=ForcedAlignmentJobRequest(
                audio_url=f"s3://bucket/{job_id}.mp3",
                content_hash=job_id,
                lyrics_text="测试歌词",
            ),
        )
    if job_type == JobType.FAST_ANALYZE:
        return Job(
            id=job_id,
            type=job_type,
            status=status,
            request=FastAnalyzeJobRequest(
                audio_url=f"s3://bucket/{job_id}.mp3",
                content_hash=job_id,
            ),
        )
    raise ValueError(f"Unsupported JobType: {job_type}")


class TestJobQueue:
    """Test JobQueue class."""

    @pytest.fixture
    async def queue(self):
        """Create a test job queue."""
        with tempfile.TemporaryDirectory() as tmp:
            q = JobQueue(max_concurrent_local_model=1, cache_dir=Path(tmp))
            yield q
            await q.stop()

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


class TestJobQueueStateLogging:
    """Test queue state logging suppression and emission rules."""

    def test_log_queue_state_skips_empty_queue(self, tmp_path, caplog):
        queue = JobQueue(max_concurrent_local_model=1, cache_dir=tmp_path)

        with caplog.at_level(logging.INFO, logger="sow_analysis.workers.queue"):
            queue._log_queue_state()

        assert _queue_state_messages(caplog) == []

    def test_log_queue_state_skips_completed_only_queue(self, tmp_path, caplog):
        queue = JobQueue(max_concurrent_local_model=1, cache_dir=tmp_path)
        job = _make_analysis_job("job_completed", JobStatus.COMPLETED)
        queue._jobs[job.id] = job

        with caplog.at_level(logging.INFO, logger="sow_analysis.workers.queue"):
            queue._log_queue_state()

        assert _queue_state_messages(caplog) == []

    def test_log_queue_state_emits_for_queued_job(self, tmp_path, caplog):
        queue = JobQueue(max_concurrent_local_model=1, cache_dir=tmp_path)
        job = _make_analysis_job("job_queued", JobStatus.QUEUED)
        queue._jobs[job.id] = job

        with caplog.at_level(logging.INFO, logger="sow_analysis.workers.queue"):
            queue._log_queue_state()

        messages = _queue_state_messages(caplog)
        assert len(messages) == 1
        assert "ANALYZE[queued:1,waiting:0,processing:0,completed:0,failed:0]" in messages[0]
        assert "ANALYZE queued=[" in messages[0]

    def test_log_queue_state_emits_for_processing_job(self, tmp_path, caplog):
        queue = JobQueue(max_concurrent_local_model=1, cache_dir=tmp_path)
        job = _make_analysis_job("job_processing", JobStatus.PROCESSING)
        queue._jobs[job.id] = job

        with caplog.at_level(logging.INFO, logger="sow_analysis.workers.queue"):
            queue._log_queue_state()

        messages = _queue_state_messages(caplog)
        assert len(messages) == 1
        assert "ANALYZE[queued:0,waiting:0,processing:1,completed:0,failed:0]" in messages[0]
        assert "ANALYZE processing=" in messages[0]

    def test_log_queue_state_emits_for_recent_failed_job(self, tmp_path, caplog):
        queue = JobQueue(max_concurrent_local_model=1, cache_dir=tmp_path)
        job = _make_analysis_job("job_failed", JobStatus.FAILED)
        queue._jobs[job.id] = job

        with caplog.at_level(logging.INFO, logger="sow_analysis.workers.queue"):
            queue._log_queue_state()

        messages = _queue_state_messages(caplog)
        assert len(messages) == 1
        assert "ANALYZE[queued:0,waiting:0,processing:0,completed:0,failed:1]" in messages[0]

    def test_log_queue_state_skips_stale_failed_job(self, tmp_path, caplog):
        queue = JobQueue(max_concurrent_local_model=1, cache_dir=tmp_path)
        job = _make_analysis_job("job_stale_failed", JobStatus.FAILED)
        job.updated_at = datetime.now(timezone.utc) - timedelta(
            seconds=FINISHED_JOB_MEMORY_RETENTION_SECONDS + 1
        )
        queue._jobs[job.id] = job

        with caplog.at_level(logging.INFO, logger="sow_analysis.workers.queue"):
            queue._log_queue_state()

        assert _queue_state_messages(caplog) == []

    def test_log_queue_state_handles_embedding_queued_job(self, tmp_path, caplog):
        """Regression: EMBEDDING jobs must not cause KeyError in _log_queue_state."""
        queue = JobQueue(max_concurrent_local_model=1, cache_dir=tmp_path)
        job = _make_embedding_job("job_emb_queued", JobStatus.QUEUED)
        queue._jobs[job.id] = job

        with caplog.at_level(logging.INFO, logger="sow_analysis.workers.queue"):
            queue._log_queue_state()

        messages = _queue_state_messages(caplog)
        assert len(messages) == 1
        assert "EMBEDDING[queued:1,waiting:0,processing:0,completed:0,failed:0]" in messages[0]
        assert "EMBEDDING queued=[" in messages[0]

    def test_log_queue_state_handles_embedding_processing_job(self, tmp_path, caplog):
        """Regression: EMBEDDING processing jobs must not cause KeyError."""
        queue = JobQueue(max_concurrent_local_model=1, cache_dir=tmp_path)
        job = _make_embedding_job("job_emb_processing", JobStatus.PROCESSING)
        queue._jobs[job.id] = job

        with caplog.at_level(logging.INFO, logger="sow_analysis.workers.queue"):
            queue._log_queue_state()

        messages = _queue_state_messages(caplog)
        assert len(messages) == 1
        assert "EMBEDDING[queued:0,waiting:0,processing:1,completed:0,failed:0]" in messages[0]
        assert "EMBEDDING processing=" in messages[0]


    @pytest.mark.parametrize("job_type", list(JobType))
    def test_log_queue_state_covers_every_job_type(self, tmp_path, caplog, job_type):
        """Future-proof: every JobType member renders without raising."""
        queue = JobQueue(max_concurrent_local_model=1, cache_dir=tmp_path)
        job = _make_job_for_type(job_type, f"job_{job_type.value}", JobStatus.QUEUED)
        queue._jobs[job.id] = job

        with caplog.at_level(logging.INFO, logger="sow_analysis.workers.queue"):
            queue._log_queue_state()

        messages = _queue_state_messages(caplog)
        assert len(messages) == 1
        assert f"{job_type.name}[queued:1,waiting:0,processing:0,completed:0,failed:0]" in messages[0]
        assert f"{job_type.name} queued=[" in messages[0]


class TestLRCJobProcessing:
    """Test LRC job processing."""

    @pytest.fixture
    async def queue(self):
        """Create a test job queue."""
        with tempfile.TemporaryDirectory() as tmp:
            q = JobQueue(max_concurrent_local_model=1, cache_dir=Path(tmp))
            yield q
            await q.stop()

    @pytest.mark.asyncio
    async def test_lrc_job_uses_cache_with_text(self, queue):
        """Test LRC job with cached text rewrites official lyrics.lrc."""
        request = LrcJobRequest(
            audio_url="s3://bucket/hash/audio.mp3",
            content_hash="abc123def456",
            lyrics_text="Line 1\nLine 2",
        )

        # Pre-populate cache with text
        cache_key = _compute_lrc_cache_key(request.content_hash, request.lyrics_text, "en")
        queue.cache_manager.save_lrc_result(
            cache_key,
            {
                "lrc_url": "s3://bucket/abc123def456/lyrics.lrc",
                "line_count": 2,
                "lrc_source": "whisper_asr",
                "lrc_text": "[00:00.00] Line 1\n[00:05.00] Line 2\n",
            },
        )

        queue.r2_client = MagicMock()
        queue.r2_client.head_object = AsyncMock(return_value={"ETag": '"etag"'})
        queue.r2_client.upload_official_lrc = AsyncMock(
            return_value="s3://bucket/abc123def456/lyrics.lrc"
        )

        job = await queue.submit(JobType.LRC, request)
        await queue._process_lrc_job(job)

        assert job.status == JobStatus.COMPLETED
        assert job.stage == "cached"
        assert job.result.line_count == 2
        queue.r2_client.upload_official_lrc.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lrc_job_uploads_official_lrc(self, queue):
        """New LRC jobs upload only to the official lyrics.lrc path."""
        request = LrcJobRequest(
            audio_url="s3://bucket/hash/audio.mp3",
            content_hash="abc123def456",
            lyrics_text="Amazing grace",
            options=LrcOptions(language="en", use_qwen3_asr=False, use_vocals_stem=False),
        )
        job = Job(
            id="job_test_lrc_upload_alias",
            type=JobType.LRC,
            status=JobStatus.QUEUED,
            request=request,
        )
        queue.r2_client = MagicMock()
        queue.r2_client.download_audio = AsyncMock()
        queue.r2_client.head_object = AsyncMock(return_value={"ETag": '"etag"'})
        queue.r2_client.upload_official_lrc = AsyncMock(
            return_value="s3://bucket/abc123def456/lyrics.lrc"
        )

        async def generate_lrc(_audio_path, _lyrics_text, _options, output_path, **_kwargs):
            output_path.write_text("[00:00.00] Amazing grace\n")
            return output_path, 1, []

        with patch("sow_analysis.workers.queue.generate_lrc", new=generate_lrc):
            await queue._process_lrc_job(job)

        assert job.status == JobStatus.COMPLETED
        assert job.result.lrc_url == "s3://bucket/abc123def456/lyrics.lrc"
        queue.r2_client.upload_official_lrc.assert_awaited_once_with(
            "abc123def456", ANY, expected_etag="etag"
        )

    @pytest.mark.asyncio
    async def test_lrc_job_cache_hit_with_text_rewrites_official(self, queue):
        """Cache hit with cached text rewrites official lyrics.lrc with ETag check."""
        request = LrcJobRequest(
            audio_url="s3://bucket/hash/audio.mp3",
            content_hash="abc123def456",
            lyrics_text="Line 1\nLine 2",
        )

        cache_key = _compute_lrc_cache_key(request.content_hash, request.lyrics_text, "en")
        queue.cache_manager.save_lrc_result(
            cache_key,
            {
                "lrc_url": "s3://bucket/abc123def456/lyrics.lrc",
                "line_count": 2,
                "lrc_source": "whisper_asr",
                "lrc_text": "[00:00.00] Line 1\n[00:05.00] Line 2\n",
            },
        )

        queue.r2_client = MagicMock()
        queue.r2_client.head_object = AsyncMock(return_value={"ETag": '"etag"'})
        queue.r2_client.upload_official_lrc = AsyncMock(
            return_value="s3://bucket/abc123def456/lyrics.lrc"
        )

        job = await queue.submit(JobType.LRC, request)
        await queue._process_lrc_job(job)

        assert job.status == JobStatus.COMPLETED
        assert job.stage == "cached"
        queue.r2_client.upload_official_lrc.assert_awaited_once_with(
            "abc123def456", ANY, expected_etag="etag"
        )

    @pytest.mark.asyncio
    async def test_lrc_job_cache_hit_metadata_only_ignored(self, queue):
        """Metadata-only legacy cache entry is ignored and regenerated."""
        request = LrcJobRequest(
            audio_url="s3://bucket/hash/audio.mp3",
            content_hash="abc123def456",
            lyrics_text="Amazing grace",
            options=LrcOptions(language="en", use_qwen3_asr=False, use_vocals_stem=False),
        )

        cache_key = _compute_lrc_cache_key(request.content_hash, request.lyrics_text, "en")
        queue.cache_manager.save_lrc_result(
            cache_key,
            {"lrc_url": "s3://bucket/abc123def456/lyrics.lrc", "line_count": 1},
        )

        queue.r2_client = MagicMock()
        queue.r2_client.head_object = AsyncMock(return_value={"ETag": '"etag"'})
        queue.r2_client.upload_official_lrc = AsyncMock(
            return_value="s3://bucket/abc123def456/lyrics.lrc"
        )
        queue.r2_client.download_audio = AsyncMock()

        async def generate_lrc(_audio_path, _lyrics_text, _options, output_path, **_kwargs):
            output_path.write_text("[00:00.00] Amazing grace\n")
            return output_path, 1, []

        job = Job(
            id="job_test_cache_ignore",
            type=JobType.LRC,
            status=JobStatus.QUEUED,
            request=request,
        )

        with patch("sow_analysis.workers.queue.generate_lrc", new=generate_lrc):
            await queue._process_lrc_job(job)

        assert job.status == JobStatus.COMPLETED
        assert job.stage == "complete"
        queue.r2_client.upload_official_lrc.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_lrc_job_stale_object_fails(self, queue):
        """If lyrics.lrc ETag changes after job start, LRC job fails."""
        from sow_analysis.storage.r2 import StaleObjectError

        request = LrcJobRequest(
            audio_url="s3://bucket/hash/audio.mp3",
            content_hash="abc123def456",
            lyrics_text="Amazing grace",
            options=LrcOptions(language="en", use_qwen3_asr=False, use_vocals_stem=False),
        )
        queue.r2_client = MagicMock()
        queue.r2_client.head_object = AsyncMock(return_value={"ETag": '"oldetag"'})
        queue.r2_client.upload_official_lrc = AsyncMock(
            side_effect=StaleObjectError("lyrics.lrc was modified")
        )
        queue.r2_client.download_audio = AsyncMock()

        async def generate_lrc(_audio_path, _lyrics_text, _options, output_path, **_kwargs):
            output_path.write_text("[00:00.00] Amazing grace\n")
            return output_path, 1, []

        job = Job(
            id="job_test_stale",
            type=JobType.LRC,
            status=JobStatus.QUEUED,
            request=request,
        )

        with patch("sow_analysis.workers.queue.generate_lrc", new=generate_lrc):
            await queue._process_lrc_job(job)

        assert job.status == JobStatus.FAILED
        assert "stale_object" in job.stage
        assert "modified" in job.error_message

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

            await queue.stop()


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

            await queue.stop()
