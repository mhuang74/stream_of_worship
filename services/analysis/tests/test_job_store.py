"""Unit tests for JobStore."""

import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from sow_analysis.models import (
    AnalyzeJobRequest,
    AnalyzeOptions,
    Job,
    JobStatus,
    JobType,
    LrcJobRequest,
    LrcOptions,
    Section,
)
from sow_analysis.storage.cache import CacheManager
from sow_analysis.storage.db import JobStore


@pytest.fixture
def temp_cache_dir(tmp_path: Path) -> Path:
    """Create a temporary cache directory."""
    return tmp_path / "cache"


@pytest.fixture
def cache_manager(temp_cache_dir: Path) -> CacheManager:
    """Create a cache manager for testing."""
    return CacheManager(temp_cache_dir)


@pytest.fixture
def temp_db_path(tmp_path: Path) -> Path:
    """Create a temporary database path."""
    return tmp_path / "test_jobs.db"


@pytest.fixture
async def job_store(temp_db_path: Path, cache_manager: CacheManager) -> JobStore:
    """Create a JobStore instance for testing."""
    store = JobStore(temp_db_path)
    store.set_cache_manager(cache_manager)
    await store.initialize()
    yield store
    await store.close()


@pytest.mark.asyncio
async def test_initialize_creates_tables(temp_db_path: Path) -> None:
    """Verify DB initialization creates tables."""
    store = JobStore(temp_db_path)
    await store.initialize()
    await store.close()

    # Verify database file exists
    assert temp_db_path.exists()

    # Reopen and verify we can query
    store2 = JobStore(temp_db_path)
    await store2.initialize()

    # Should have no jobs initially
    jobs = await store2.list_jobs()
    assert jobs == []

    await store2.close()


@pytest.mark.asyncio
async def test_insert_and_get_analyze_job(job_store: JobStore) -> None:
    """Test round-trip for analyze job."""
    request = AnalyzeJobRequest(
        audio_url="s3://test-bucket/audio.mp3",
        content_hash="abc123",
        options=AnalyzeOptions(generate_stems=True),
    )

    job = Job(
        id="job_test123",
        type=JobType.ANALYZE,
        status=JobStatus.QUEUED,
        request=request,
    )

    await job_store.insert_job(job)

    # Retrieve and verify
    retrieved = await job_store.get_job("job_test123")
    assert retrieved is not None
    assert retrieved.id == "job_test123"
    assert retrieved.type == JobType.ANALYZE
    assert retrieved.status == JobStatus.QUEUED
    assert isinstance(retrieved.request, AnalyzeJobRequest)
    assert retrieved.request.audio_url == "s3://test-bucket/audio.mp3"
    assert retrieved.request.options.generate_stems


@pytest.mark.asyncio
async def test_insert_and_get_lrc_job(job_store: JobStore) -> None:
    """Test round-trip for LRC job."""
    request = LrcJobRequest(
        audio_url="s3://test-bucket/audio.mp3",
        content_hash="def456",
        lyrics_text="Verse 1\nChorus",
        options=LrcOptions(whisper_model="large-v3"),
    )

    job = Job(
        id="job_lrc456",
        type=JobType.LRC,
        status=JobStatus.QUEUED,
        request=request,
    )

    await job_store.insert_job(job)

    # Retrieve and verify
    retrieved = await job_store.get_job("job_lrc456")
    assert retrieved is not None
    assert retrieved.id == "job_lrc456"
    assert retrieved.type == JobType.LRC
    assert retrieved.status == JobStatus.QUEUED
    assert isinstance(retrieved.request, LrcJobRequest)
    assert retrieved.request.lyrics_text == "Verse 1\nChorus"


@pytest.mark.asyncio
async def test_update_job_status(job_store: JobStore) -> None:
    """Verify status transitions persist."""
    request = AnalyzeJobRequest(
        audio_url="s3://test-bucket/audio.mp3",
        content_hash="abc123",
    )

    job = Job(
        id="job_status_test",
        type=JobType.ANALYZE,
        status=JobStatus.QUEUED,
        request=request,
    )

    await job_store.insert_job(job)

    # Update to PROCESSING
    await job_store.update_job("job_status_test", status="processing", progress=0.5)

    retrieved = await job_store.get_job("job_status_test")
    assert retrieved.status == JobStatus.PROCESSING
    assert retrieved.progress == 0.5
    assert retrieved.updated_at > retrieved.created_at

    # Update to COMPLETED
    from sow_analysis.models import JobResult

    result = JobResult(tempo_bpm=120, musical_key="C")
    await job_store.update_job(
        "job_status_test",
        status="completed",
        progress=1.0,
        result_json=result.model_dump_json(),
    )

    retrieved = await job_store.get_job("job_status_test")
    assert retrieved.status == JobStatus.COMPLETED
    assert retrieved.progress == 1.0
    assert retrieved.result is not None
    assert retrieved.result.tempo_bpm == 120


@pytest.mark.asyncio
async def test_get_interrupted_jobs(job_store: JobStore) -> None:
    """Verify only QUEUED/PROCESSING jobs are returned."""
    requests = [
        AnalyzeJobRequest(
            audio_url=f"s3://test/bucket/audio{i}.mp3",
            content_hash=f"hash{i}",
        )
        for i in range(6)
    ]

    # Create jobs with different statuses
    await job_store.insert_job(Job(id="job_queued", type=JobType.ANALYZE, status=JobStatus.QUEUED, request=requests[0]))
    await job_store.insert_job(Job(id="job_processing", type=JobType.ANALYZE, status=JobStatus.PROCESSING, request=requests[1]))
    await job_store.insert_job(Job(id="job_completed1", type=JobType.ANALYZE, status=JobStatus.COMPLETED, request=requests[2]))
    await job_store.insert_job(Job(id="job_completed2", type=JobType.LRC, status=JobStatus.COMPLETED, request=LrcJobRequest(audio_url="s3://test/lrc.mp3", content_hash="lrc1", lyrics_text="lyrics")))
    await job_store.insert_job(Job(id="job_failed1", type=JobType.ANALYZE, status=JobStatus.FAILED, request=requests[3], error_message="Error"))
    await job_store.insert_job(Job(id="job_failed2", type=JobType.LRC, status=JobStatus.FAILED, request=LrcJobRequest(audio_url="s3://test/lrc2.mp3", content_hash="lrc2", lyrics_text="lyrics"), error_message="Error"))

    # Get interrupted jobs
    interrupted = await job_store.get_interrupted_jobs()

    assert len(interrupted) == 2
    interrupted_ids = {job.id for job in interrupted}
    assert interrupted_ids == {"job_queued", "job_processing"}


@pytest.mark.asyncio
async def test_purge_old_jobs(job_store: JobStore) -> None:
    """Verify old completed/failed jobs are deleted but recent ones kept."""
    now = datetime.now(timezone.utc)

    # Create old completed job (>7 days ago)
    old_completed = Job(
        id="job_old_completed",
        type=JobType.ANALYZE,
        status=JobStatus.COMPLETED,
        request=AnalyzeJobRequest(audio_url="s3://old.mp3", content_hash="old"),
        created_at=now - timedelta(days=10),
        updated_at=now - timedelta(days=10),
    )

    # Create old failed job (>7 days ago)
    old_failed = Job(
        id="job_old_failed",
        type=JobType.ANALYZE,
        status=JobStatus.FAILED,
        request=AnalyzeJobRequest(audio_url="s3://old2.mp3", content_hash="old2"),
        created_at=now - timedelta(days=8),
        updated_at=now - timedelta(days=8),
    )

    # Create recent completed job (<7 days ago)
    recent_completed = Job(
        id="job_recent_completed",
        type=JobType.ANALYZE,
        status=JobStatus.COMPLETED,
        request=AnalyzeJobRequest(audio_url="s3://recent.mp3", content_hash="recent"),
        created_at=now - timedelta(days=2),
        updated_at=now - timedelta(days=2),
    )

    # Create queued job (should NOT be purged regardless of age)
    old_queued = Job(
        id="job_old_queued",
        type=JobType.ANALYZE,
        status=JobStatus.QUEUED,
        request=AnalyzeJobRequest(audio_url="s3://old_queued.mp3", content_hash="old_q"),
        created_at=now - timedelta(days=10),
        updated_at=now - timedelta(days=10),
    )

    for job in [old_completed, old_failed, recent_completed, old_queued]:
        await job_store.insert_job(job)

    # Purge jobs older than 7 days
    purged = await job_store.purge_old_jobs(max_age_days=7)

    assert purged == 2

    # Verify old completed/failed jobs are gone
    assert await job_store.get_job("job_old_completed") is None
    assert await job_store.get_job("job_old_failed") is None

    # Verify recent completed job remains
    assert await job_store.get_job("job_recent_completed") is not None

    # Verify queued job remains (never purged)
    assert await job_store.get_job("job_old_queued") is not None


@pytest.mark.asyncio
async def test_purge_preserves_active_jobs(job_store: JobStore) -> None:
    """Verify QUEUED/PROCESSING jobs are never purged regardless of age."""
    now = datetime.now(timezone.utc)

    # Create very old queued and processing jobs
    old_queued = Job(
        id="job_ancient_queued",
        type=JobType.ANALYZE,
        status=JobStatus.QUEUED,
        request=AnalyzeJobRequest(audio_url="s3://ancient_queued.mp3", content_hash="ancient_q"),
        created_at=now - timedelta(days=100),
        updated_at=now - timedelta(days=100),
    )

    old_processing = Job(
        id="job_ancient_processing",
        type=JobType.ANALYZE,
        status=JobStatus.PROCESSING,
        request=AnalyzeJobRequest(audio_url="s3://ancient_proc.mp3", content_hash="ancient_p"),
        created_at=now - timedelta(days=100),
        updated_at=now - timedelta(days=100),
    )

    for job in [old_queued, old_processing]:
        await job_store.insert_job(job)

    # Purge everything older than 7 days
    purged = await job_store.purge_old_jobs(max_age_days=7)

    # Should not have purged anything (QUEUED/PROCESSING are never purged)
    assert purged == 0

    # Verify both jobs still exist
    assert await job_store.get_job("job_ancient_queued") is not None
    assert await job_store.get_job("job_ancient_processing") is not None


@pytest.mark.asyncio
async def test_list_jobs_with_filters(job_store: JobStore) -> None:
    """Test listing jobs with status and type filters."""
    # Create jobs of various types and statuses
    await job_store.insert_job(Job(id="job_a_q", type=JobType.ANALYZE, status=JobStatus.QUEUED, request=AnalyzeJobRequest(audio_url="s3://a1.mp3", content_hash="a1")))
    await job_store.insert_job(Job(id="job_a_p", type=JobType.ANALYZE, status=JobStatus.PROCESSING, request=AnalyzeJobRequest(audio_url="s3://a2.mp3", content_hash="a2")))
    await job_store.insert_job(Job(id="job_a_c", type=JobType.ANALYZE, status=JobStatus.COMPLETED, request=AnalyzeJobRequest(audio_url="s3://a3.mp3", content_hash="a3")))
    await job_store.insert_job(Job(id="job_l_q", type=JobType.LRC, status=JobStatus.QUEUED, request=LrcJobRequest(audio_url="s3://l1.mp3", content_hash="l1", lyrics_text="lyrics")))
    await job_store.insert_job(Job(id="job_l_c", type=JobType.LRC, status=JobStatus.COMPLETED, request=LrcJobRequest(audio_url="s3://l2.mp3", content_hash="l2", lyrics_text="lyrics")))

    # List all jobs
    all_jobs = await job_store.list_jobs()
    assert len(all_jobs) == 5

    # Filter by status
    queued_jobs = await job_store.list_jobs(status=JobStatus.QUEUED)
    assert len(queued_jobs) == 2
    assert {job.id for job in queued_jobs} == {"job_a_q", "job_l_q"}

    # Filter by type
    analyze_jobs = await job_store.list_jobs(job_type=JobType.ANALYZE)
    assert len(analyze_jobs) == 3
    assert {job.id for job in analyze_jobs} == {"job_a_q", "job_a_p", "job_a_c"}

    # Filter by both status and type
    completed_analyze = await job_store.list_jobs(status=JobStatus.COMPLETED, job_type=JobType.ANALYZE)
    assert len(completed_analyze) == 1
    assert completed_analyze[0].id == "job_a_c"

    # Test limit
    limited = await job_store.list_jobs(limit=2)
    assert len(limited) == 2


@pytest.mark.asyncio
async def test_get_nonexistent_job(job_store: JobStore) -> None:
    """Test getting a non-existent job returns None."""
    result = await job_store.get_job("job_does_not_exist")
    assert result is None
