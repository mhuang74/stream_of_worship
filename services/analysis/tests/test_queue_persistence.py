"""Integration tests for job queue persistence."""

import asyncio
from pathlib import Path

import pytest

from sow_analysis.models import (
    AnalyzeJobRequest,
    JobStatus,
    JobType,
)
from sow_analysis.storage.cache import CacheManager
from sow_analysis.workers.queue import JobQueue


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    """Create a temporary directory for tests."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


@pytest.fixture
async def job_queue(temp_dir: Path) -> JobQueue:
    """Create a JobQueue instance for testing."""
    queue = JobQueue(
        max_concurrent_analysis=1,
        max_concurrent_lrc=1,
        cache_dir=temp_dir,
        db_path=temp_dir / "jobs.db",
    )
    await queue.initialize()
    yield queue
    await queue.stop()


@pytest.mark.asyncio
async def test_job_survives_queue_restart(temp_dir: Path) -> None:
    """Test that a submitted job survives queue restart."""
    db_path = temp_dir / "jobs.db"

    # Create first queue and submit a job
    queue1 = JobQueue(
        max_concurrent_analysis=1,
        max_concurrent_lrc=1,
        cache_dir=temp_dir,
        db_path=db_path,
    )
    await queue1.initialize()

    request = AnalyzeJobRequest(
        audio_url="s3://test-bucket/audio.mp3",
        content_hash="test123",
    )

    job = await queue1.submit(JobType.ANALYZE, request)
    assert job.status == JobStatus.QUEUED

    # Stop first queue
    await queue1.stop()

    # Create second queue (simulating service restart)
    queue2 = JobQueue(
        max_concurrent_analysis=1,
        max_concurrent_lrc=1,
        cache_dir=temp_dir,
        db_path=db_path,
    )
    await queue2.initialize()

    # Job should be recovered in the new queue
    recovered_job = await queue2.get_job(job.id)
    assert recovered_job is not None
    assert recovered_job.id == job.id
    assert recovered_job.status == JobStatus.QUEUED
    assert recovered_job.stage == "requeued"

    await queue2.stop()


@pytest.mark.asyncio
async def test_completed_job_queryable_after_memory_eviction(temp_dir: Path) -> None:
    """Test that completed jobs are queryable from DB after being evicted from memory."""
    queue = JobQueue(
        max_concurrent_analysis=1,
        max_concurrent_lrc=1,
        cache_dir=temp_dir,
        db_path=temp_dir / "jobs.db",
    )
    await queue.initialize()

    request = AnalyzeJobRequest(
        audio_url="s3://test-bucket/audio.mp3",
        content_hash="test456",
    )

    # Submit a job
    job = await queue.submit(JobType.ANALYZE, request)

    # Manually set job to COMPLETED to simulate completion
    job.status = JobStatus.COMPLETED
    job.progress = 1.0
    job.stage = "complete"
    await queue.job_store.update_job(
        job.id,
        status="completed",
        progress=1.0,
        stage="complete",
    )

    # Job is in memory and in DB
    assert job.id in queue._jobs
    from_db = await queue.job_store.get_job(job.id)
    assert from_db is not None
    assert from_db.status == JobStatus.COMPLETED

    # Remove from in-memory cache (simulating memory eviction)
    queue._jobs.pop(job.id, None)

    # Job should still be queryable via get_job which falls back to DB
    retrieved = await queue.get_job(job.id)
    assert retrieved is not None
    assert retrieved.id == job.id
    assert retrieved.status == JobStatus.COMPLETED

    await queue.stop()


@pytest.mark.asyncio
async def test_multiple_interrupted_jobs_recovered(temp_dir: Path) -> None:
    """Test that multiple interrupted jobs are recovered correctly."""
    db_path = temp_dir / "jobs.db"

    # Create queue and submit multiple jobs
    queue1 = JobQueue(
        max_concurrent_analysis=1,
        max_concurrent_lrc=1,
        cache_dir=temp_dir,
        db_path=db_path,
    )
    await queue1.initialize()

    requests = [
        AnalyzeJobRequest(
            audio_url=f"s3://test/job{i}.mp3",
            content_hash=f"hash{i}",
        )
        for i in range(5)
    ]

    job_ids = []
    for i, request in enumerate(requests):
        job = await queue1.submit(JobType.ANALYZE, request)
        job_ids.append(job.id)

    # Stop queue (simulating crash with jobs in queue)
    await queue1.stop()

    # Create second queue
    queue2 = JobQueue(
        max_concurrent_analysis=1,
        max_concurrent_lrc=1,
        cache_dir=temp_dir,
        db_path=db_path,
    )
    await queue2.initialize()

    # All jobs should be recovered
    for job_id in job_ids:
        job = await queue2.get_job(job_id)
        assert job is not None
        assert job.status == JobStatus.QUEUED
        assert job.stage == "requeued"

    await queue2.stop()


@pytest.mark.asyncio
async def test_processing_job_recovered_as_queued(temp_dir: Path) -> None:
    """Test that a PROCESSING job is recovered as QUEUED on restart."""
    db_path = temp_dir / "jobs.db"

    # Create queue and submit job
    queue1 = JobQueue(
        max_concurrent_analysis=1,
        max_concurrent_lrc=1,
        cache_dir=temp_dir,
        db_path=db_path,
    )
    await queue1.initialize()

    request = AnalyzeJobRequest(
        audio_url="s3://test-bucket/audio.mp3",
        content_hash="proc123",
    )

    job = await queue1.submit(JobType.ANALYZE, request)

    # Manually set job to PROCESSING (simulating it was processing when stopped)
    await queue1.job_store.update_job(job.id, status="processing", stage="analyzing")

    # Stop queue
    await queue1.stop()

    # Create second queue
    queue2 = JobQueue(
        max_concurrent_analysis=1,
        max_concurrent_lrc=1,
        cache_dir=temp_dir,
        db_path=db_path,
    )
    await queue2.initialize()

    # Job should be recovered as QUEUED (not PROCESSING)
    recovered = await queue2.get_job(job.id)
    assert recovered is not None
    assert recovered.status == JobStatus.QUEUED
    assert recovered.stage == "requeued"
    assert recovered.progress == 0.0

    await queue2.stop()


@pytest.mark.asyncio
async def test_completed_failed_jobs_not_requeued(temp_dir: Path) -> None:
    """Test that COMPLETED and FAILED jobs are not requeued on restart."""
    db_path = temp_dir / "jobs.db"

    # Create queue and submit jobs
    queue1 = JobQueue(
        max_concurrent_analysis=1,
        max_concurrent_lrc=1,
        cache_dir=temp_dir,
        db_path=db_path,
    )
    await queue1.initialize()

    request1 = AnalyzeJobRequest(
        audio_url="s3://test/comp.mp3",
        content_hash="comp123",
    )
    request2 = AnalyzeJobRequest(
        audio_url="s3://test/fail.mp3",
        content_hash="fail456",
    )

    job1 = await queue1.submit(JobType.ANALYZE, request1)
    job2 = await queue1.submit(JobType.ANALYZE, request2)

    # Set one job to COMPLETED and one to FAILED
    await queue1.job_store.update_job(job1.id, status="completed")
    await queue1.job_store.update_job(job2.id, status="failed", error_message="test error")

    # Stop queue
    await queue1.stop()

    # Create second queue
    queue2 = JobQueue(
        max_concurrent_analysis=1,
        max_concurrent_lrc=1,
        cache_dir=temp_dir,
        db_path=db_path,
    )
    await queue2.initialize()

    # Jobs should still exist but not be in the queue
    recovered1 = await queue2.get_job(job1.id)
    recovered2 = await queue2.get_job(job2.id)

    assert recovered1 is not None
    assert recovered1.status == JobStatus.COMPLETED

    assert recovered2 is not None
    assert recovered2.status == JobStatus.FAILED
    assert recovered2.error_message == "test error"

    # They should not be in the processing queue
    assert job1.id not in queue2._jobs
    assert job2.id not in queue2._jobs

    await queue2.stop()


@pytest.mark.asyncio
async def test_old_jobs_purged_on_startup(temp_dir: Path) -> None:
    """Test that old completed/failed jobs are purged on startup."""
    db_path = temp_dir / "jobs.db"

    # Create queue
    queue1 = JobQueue(
        max_concurrent_analysis=1,
        max_concurrent_lrc=1,
        cache_dir=temp_dir,
        db_path=db_path,
    )
    await queue1.initialize()

    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)

    # Create old completed job
    old_completed = await queue1.submit(
        JobType.ANALYZE,
        AnalyzeJobRequest(audio_url="s3://old.mp3", content_hash="old"),
    )
    await queue1.job_store.update_job(old_completed.id, status="completed")
    # Manually update timestamp to simulate old job
    await queue1.job_store._db.execute(
        "UPDATE jobs SET created_at = ?, updated_at = ? WHERE id = ?",
        ((now - timedelta(days=10)).isoformat(), (now - timedelta(days=10)).isoformat(), old_completed.id),
    )
    await queue1.job_store._db.commit()

    # Create recent completed job
    recent_completed = await queue1.submit(
        JobType.ANALYZE,
        AnalyzeJobRequest(audio_url="s3://recent.mp3", content_hash="recent"),
    )
    await queue1.job_store.update_job(recent_completed.id, status="completed")

    await queue1.stop()

    # Create second queue with purge on startup
    queue2 = JobQueue(
        max_concurrent_analysis=1,
        max_concurrent_lrc=1,
        cache_dir=temp_dir,
        db_path=db_path,
    )
    await queue2.initialize()

    # Old completed job should be purged
    assert await queue2.get_job(old_completed.id) is None

    # Recent completed job should still exist
    retrieved = await queue2.get_job(recent_completed.id)
    assert retrieved is not None
    assert retrieved.status == JobStatus.COMPLETED

    await queue2.stop()
