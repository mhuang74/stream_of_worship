"""Integration tests for job queue persistence."""

import asyncio
import logging
from pathlib import Path

import pytest

from sow_analysis.models import (
    AnalyzeJobRequest,
    Job,
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
        max_concurrent_local_model=1,
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
        max_concurrent_local_model=1,
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
        max_concurrent_local_model=1,
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
        max_concurrent_local_model=1,
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
        max_concurrent_local_model=1,
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
        max_concurrent_local_model=1,
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
        max_concurrent_local_model=1,
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
        max_concurrent_local_model=1,
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
        max_concurrent_local_model=1,
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
        max_concurrent_local_model=1,
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
        max_concurrent_local_model=1,
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
        max_concurrent_local_model=1,
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


@pytest.mark.asyncio
async def test_clear_queue_cancels_processing_jobs(temp_dir: Path) -> None:
    """Test that clear_queue cancels both QUEUED and PROCESSING jobs."""
    queue = JobQueue(
        max_concurrent_local_model=1,
        cache_dir=temp_dir,
        db_path=temp_dir / "jobs.db",
    )
    await queue.initialize()

    # Submit a queued job
    queued_job = await queue.submit(
        JobType.ANALYZE,
        AnalyzeJobRequest(audio_url="s3://test/queued.mp3", content_hash="queued1"),
    )

    # Submit a job and set it to PROCESSING
    processing_job = await queue.submit(
        JobType.ANALYZE,
        AnalyzeJobRequest(audio_url="s3://test/processing.mp3", content_hash="proc1"),
    )
    processing_job.status = JobStatus.PROCESSING
    processing_job.stage = "analyzing"
    await queue.job_store.update_job(
        processing_job.id, status="processing", stage="analyzing"
    )

    # Clear queue should cancel both
    cancelled = await queue.clear_queue()
    cancelled_ids = {j.id for j in cancelled}
    assert queued_job.id in cancelled_ids
    assert processing_job.id in cancelled_ids

    # Verify status in memory
    assert queued_job.status == JobStatus.CANCELLED
    assert processing_job.status == JobStatus.CANCELLED

    # Verify status in DB
    db_queued = await queue.job_store.get_job(queued_job.id)
    db_processing = await queue.job_store.get_job(processing_job.id)
    assert db_queued.status == JobStatus.CANCELLED
    assert db_processing.status == JobStatus.CANCELLED

    await queue.stop()


@pytest.mark.asyncio
async def test_clear_queue_skips_completed_failed_cancelled(temp_dir: Path) -> None:
    """Test that clear_queue does not affect terminal-state jobs."""
    queue = JobQueue(
        max_concurrent_local_model=1,
        cache_dir=temp_dir,
        db_path=temp_dir / "jobs.db",
    )
    await queue.initialize()

    completed_job = await queue.submit(
        JobType.ANALYZE,
        AnalyzeJobRequest(audio_url="s3://test/comp.mp3", content_hash="comp1"),
    )
    completed_job.status = JobStatus.COMPLETED
    completed_job.stage = "complete"
    await queue.job_store.update_job(completed_job.id, status="completed", stage="complete")

    cancelled_job = await queue.submit(
        JobType.ANALYZE,
        AnalyzeJobRequest(audio_url="s3://test/canc.mp3", content_hash="canc1"),
    )
    cancelled_job.status = JobStatus.CANCELLED
    cancelled_job.stage = "cancelled"
    await queue.job_store.update_job(cancelled_job.id, status="cancelled", stage="cancelled")

    # Clear queue should not cancel these
    cancelled = await queue.clear_queue()
    cancelled_ids = {j.id for j in cancelled}
    assert completed_job.id not in cancelled_ids
    assert cancelled_job.id not in cancelled_ids

    assert completed_job.status == JobStatus.COMPLETED
    assert cancelled_job.status == JobStatus.CANCELLED

    await queue.stop()


@pytest.mark.asyncio
async def test_waiting_job_recovered_as_queued(temp_dir: Path) -> None:
    """Test that a WAITING job is recovered as QUEUED on restart."""
    db_path = temp_dir / "jobs.db"

    # Create queue and submit job
    queue1 = JobQueue(
        max_concurrent_local_model=1,
        cache_dir=temp_dir,
        db_path=db_path,
    )
    await queue1.initialize()

    request = AnalyzeJobRequest(
        audio_url="s3://test-bucket/audio.mp3",
        content_hash="wait123",
    )

    job = await queue1.submit(JobType.ANALYZE, request)

    # Manually set job to WAITING (simulating it was dequeued but not started)
    await queue1.job_store.update_job(job.id, status="waiting", stage="waiting")

    # Stop queue
    await queue1.stop()

    # Create second queue
    queue2 = JobQueue(
        max_concurrent_local_model=1,
        cache_dir=temp_dir,
        db_path=db_path,
    )
    await queue2.initialize()

    # Job should be recovered as QUEUED (not WAITING)
    recovered = await queue2.get_job(job.id)
    assert recovered is not None
    assert recovered.status == JobStatus.QUEUED
    assert recovered.stage == "requeued"
    assert recovered.progress == 0.0

    await queue2.stop()


@pytest.mark.asyncio
async def test_cancel_waiting_job_no_warning(temp_dir: Path) -> None:
    """Test that cancelling a WAITING job returns no warning (unlike PROCESSING)."""
    queue = JobQueue(
        max_concurrent_local_model=1,
        cache_dir=temp_dir,
        db_path=temp_dir / "jobs.db",
    )
    await queue.initialize()

    request = AnalyzeJobRequest(
        audio_url="s3://test/wait.mp3",
        content_hash="cancel_wait1",
    )

    job = await queue.submit(JobType.ANALYZE, request)

    # Set job to WAITING
    job.status = JobStatus.WAITING
    job.stage = "waiting"
    await queue.job_store.update_job(job.id, status="waiting", stage="waiting")

    # Cancel the WAITING job
    cancelled_job, warning = await queue.cancel_job(job.id)

    assert cancelled_job is not None
    assert cancelled_job.status == JobStatus.CANCELLED
    # No warning for WAITING jobs
    assert warning is None

    await queue.stop()


@pytest.mark.asyncio
async def test_cancel_processing_job_has_warning(temp_dir: Path) -> None:
    """Test that cancelling a PROCESSING job returns a warning (contrast with WAITING)."""
    queue = JobQueue(
        max_concurrent_local_model=1,
        cache_dir=temp_dir,
        db_path=temp_dir / "jobs.db",
    )
    await queue.initialize()

    request = AnalyzeJobRequest(
        audio_url="s3://test/proc.mp3",
        content_hash="cancel_proc1",
    )

    job = await queue.submit(JobType.ANALYZE, request)

    # Set job to PROCESSING
    job.status = JobStatus.PROCESSING
    job.stage = "analyzing"
    await queue.job_store.update_job(job.id, status="processing", stage="analyzing")

    # Cancel the PROCESSING job
    cancelled_job, warning = await queue.cancel_job(job.id)

    assert cancelled_job is not None
    assert cancelled_job.status == JobStatus.CANCELLED
    # Warning for PROCESSING jobs
    assert warning is not None
    assert "PROCESSING" in warning

    await queue.stop()


@pytest.mark.asyncio
async def test_clear_queue_cancels_waiting(temp_dir: Path) -> None:
    """Test that clear_queue cancels WAITING jobs."""
    queue = JobQueue(
        max_concurrent_local_model=1,
        cache_dir=temp_dir,
        db_path=temp_dir / "jobs.db",
    )
    await queue.initialize()

    # Submit a job and set it to WAITING
    waiting_job = await queue.submit(
        JobType.ANALYZE,
        AnalyzeJobRequest(audio_url="s3://test/waiting.mp3", content_hash="clear_wait1"),
    )
    waiting_job.status = JobStatus.WAITING
    waiting_job.stage = "waiting"
    await queue.job_store.update_job(waiting_job.id, status="waiting", stage="waiting")

    # Clear queue should cancel the WAITING job
    cancelled = await queue.clear_queue()
    cancelled_ids = {j.id for j in cancelled}
    assert waiting_job.id in cancelled_ids

    # Verify status in memory
    assert waiting_job.status == JobStatus.CANCELLED

    # Verify status in DB
    db_waiting = await queue.job_store.get_job(waiting_job.id)
    assert db_waiting.status == JobStatus.CANCELLED

    await queue.stop()


@pytest.mark.asyncio
async def test_job_set_to_waiting_on_dequeue(temp_dir: Path) -> None:
    """Test that a job is set to WAITING after dequeue but before semaphore.

    We verify that _process_job_with_semaphore sets WAITING status before
    calling the processor. We use a mock processor that blocks on a semaphore
    so the WAITING state is observable.
    """
    queue = JobQueue(
        max_concurrent_local_model=1,
        cache_dir=temp_dir,
        db_path=temp_dir / "jobs.db",
    )
    await queue.initialize()

    request = AnalyzeJobRequest(
        audio_url="s3://test/dequeue.mp3",
        content_hash="dequeue1",
    )
    job = await queue.submit(JobType.ANALYZE, request)

    # Manually invoke _process_job_with_semaphore and check that WAITING is set
    # before the processor runs. We use an event to synchronize: the test
    # checks the status after _process_job_with_semaphore sets WAITING but
    # before the processor (which we mock) runs.
    import unittest.mock

    status_before_processor = []

    async def mock_processor(j: Job) -> None:
        status_before_processor.append(j.status)

    # Patch the analysis processor path
    with unittest.mock.patch.object(queue, "_process_analysis_job", side_effect=mock_processor):
        # Acquire the semaphore first so the job blocks on it
        async with queue._local_model_semaphore:
            # Start processing in background
            task = asyncio.create_task(queue._process_job_with_semaphore(job))
            # Give it time to set WAITING and block on the semaphore
            await asyncio.sleep(0.1)
            # Job should be WAITING (set before semaphore acquisition)
            assert job.status == JobStatus.WAITING
            # Release the semaphore so the processor can run
        # Wait for the task to complete
        await task

    # The processor should have seen the job as WAITING (before it sets PROCESSING)
    # Actually, the processor sets PROCESSING as its first action, so the status
    # captured inside the mock is WAITING (the state just before the processor runs).
    # But since our mock doesn't set PROCESSING, the status stays WAITING.
    assert len(status_before_processor) == 1

    await queue.stop()


@pytest.mark.asyncio
async def test_log_queue_state_shows_waiting(temp_dir: Path) -> None:
    """Test that _log_queue_state() output includes 'waiting:N' count."""
    queue = JobQueue(
        max_concurrent_local_model=1,
        cache_dir=temp_dir,
        db_path=temp_dir / "jobs.db",
    )
    await queue.initialize()

    # Submit jobs and set one to WAITING
    waiting_job = await queue.submit(
        JobType.ANALYZE,
        AnalyzeJobRequest(audio_url="s3://test/wait.mp3", content_hash="log_wait1"),
    )
    waiting_job.status = JobStatus.WAITING
    waiting_job.stage = "waiting"

    # Capture log output
    import logging
    import io

    log_stream = io.StringIO()
    handler = logging.StreamHandler(log_stream)
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)

    logger = logging.getLogger("sow_analysis.workers.queue")
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    try:
        queue._log_queue_state()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)

    # Check that the log output contains "waiting:"
    log_text = log_stream.getvalue()
    assert "waiting:" in log_text
    assert "waiting:1" in log_text

    await queue.stop()
