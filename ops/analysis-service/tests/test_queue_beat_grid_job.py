"""Queue tests for the standalone ``beat_grid`` job type.

Exercises ``_process_beat_grid_job`` with ``get_or_detect_beat_grid``
monkeypatched:

  - Grid returned → job COMPLETED, ``job.result.beats``/``downbeats`` populated,
    and the job row is persisted with type ``beat_grid`` (the SQLite CHECK
    constraint must accept it, otherwise restart recovery never sees it).
  - Grid is None → job FAILED with the madmom error message.
  - ``skip_beat_cache`` forwarded from the request.
  - Persisted request/result round-trip through ``_row_to_job``.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sow_analysis.models import (
    BeatGridJobRequest,
    Job,
    JobStatus,
    JobType,
)
from sow_analysis.storage.db import JobStore
from sow_analysis.workers.queue import JobQueue


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


@pytest.fixture
async def job_queue(temp_dir: Path) -> JobQueue:
    queue = JobQueue(
        max_concurrent_analyze=1,
        cache_dir=temp_dir,
        db_path=temp_dir / "jobs.db",
    )
    await queue.initialize()
    # Stub R2 client so _process_beat_grid_job can download audio.
    queue.r2_client = MagicMock()
    queue.r2_client.download_audio = AsyncMock(
        side_effect=lambda url, path: Path(path).write_text("fake audio")
    )
    yield queue
    await queue.stop()


def _make_job(
    content_hash: str = "abc123def456",
    skip_beat_cache: bool = False,
    job_id: str = "test-beat-grid-job",
) -> Job:
    request = BeatGridJobRequest(
        audio_url="s3://test-bucket/audio.mp3",
        content_hash=content_hash,
        skip_beat_cache=skip_beat_cache,
    )
    return Job(
        id=job_id,
        type=JobType.BEAT_GRID,
        status=JobStatus.PROCESSING,
        request=request,
    )


@pytest.mark.asyncio
async def test_job_completes_with_beats(job_queue: JobQueue) -> None:
    """A detected grid lands in job.result and the job completes."""
    job = _make_job()

    with patch(
        "sow_analysis.workers.queue.get_or_detect_beat_grid",
        new_callable=AsyncMock,
        return_value={"beats": [[0.5, 1], [1.0, 2]], "downbeats": [0.5]},
    ) as mock_helper:
        await job_queue._process_beat_grid_job(job)

    mock_helper.assert_called_once()
    assert mock_helper.call_args.kwargs.get("content_hash") == "abc123def456"
    assert job.status == JobStatus.COMPLETED
    assert job.stage == "complete"
    assert job.result is not None
    # madmom's [[time, position], ...] is flattened to timestamps for JobResult.
    assert job.result.beats == [0.5, 1.0]
    assert job.result.downbeats == [0.5]
    # No component work happened.
    assert job.result.components is None


@pytest.mark.asyncio
async def test_grid_miss_fails_job(job_queue: JobQueue) -> None:
    """A None grid fails the job with the madmom error message."""
    job = _make_job()

    with patch(
        "sow_analysis.workers.queue.get_or_detect_beat_grid",
        new_callable=AsyncMock,
        return_value=None,
    ):
        await job_queue._process_beat_grid_job(job)

    assert job.status == JobStatus.FAILED
    assert job.error_message == "madmom beat detection failed (see sow-downbeats logs)"


@pytest.mark.asyncio
async def test_skip_beat_cache_forwarded(job_queue: JobQueue) -> None:
    """skip_beat_cache=True from the request is forwarded to the helper."""
    job = _make_job(skip_beat_cache=True)

    with patch(
        "sow_analysis.workers.queue.get_or_detect_beat_grid",
        new_callable=AsyncMock,
        return_value={"beats": [[0.5, 1]], "downbeats": []},
    ) as mock_helper:
        await job_queue._process_beat_grid_job(job)

    assert mock_helper.call_args.kwargs.get("skip_beat_cache") is True


@pytest.mark.asyncio
async def test_job_persisted_and_restored(temp_dir: Path) -> None:
    """A beat_grid job persists (CHECK constraint accepts the type) and reloads."""
    db_path = temp_dir / "jobs.db"

    queue = JobQueue(max_concurrent_analyze=1, cache_dir=temp_dir, db_path=db_path)
    await queue.initialize()
    queue.r2_client = MagicMock()
    queue.r2_client.download_audio = AsyncMock(
        side_effect=lambda url, path: Path(path).write_text("fake audio")
    )

    job = _make_job(job_id="job_persist_bg", content_hash="hash-for-persist")
    await queue.job_store.insert_job(job)

    with patch(
        "sow_analysis.workers.queue.get_or_detect_beat_grid",
        new_callable=AsyncMock,
        return_value={"beats": [[1.5, 1], [2.5, 2]], "downbeats": [1.5]},
    ):
        await queue._process_beat_grid_job(job)

    # The row must exist: insert_job would have raised IntegrityError on a table
    # whose CHECK does not list 'beat_grid'.
    stored = await queue.job_store.get_job("job_persist_bg")
    assert stored is not None
    assert stored.type == JobType.BEAT_GRID
    assert stored.status == JobStatus.COMPLETED
    assert stored.result.beats == [1.5, 2.5]

    # Restart recovery path: a fresh store reads the type back without ValueError.
    fresh_store = JobStore(db_path)
    await fresh_store.initialize()
    reloaded = await fresh_store.get_job("job_persist_bg")
    assert reloaded is not None
    assert isinstance(reloaded.request, BeatGridJobRequest)
    assert reloaded.request.content_hash == "hash-for-persist"
    assert reloaded.result.beats == [1.5, 2.5]

    await queue.stop()
