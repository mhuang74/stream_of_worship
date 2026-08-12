"""Queue-wiring tests for the beat-grid cache integration.

Exercises ``_process_component_analysis_job`` with ``get_or_detect_beat_grid``
and ``extract_components`` monkeypatched to verify the gate matrix:

  - Helper called when ``snap_to_downbeat=True`` and no ``request.downbeats``.
  - Helper NOT called when ``snap_to_downbeat=False`` (Decision 1 regression guard).
  - Helper NOT called when ``request.downbeats`` provided.
  - ``skip_beat_cache`` forwarded from options.
  - ``force=True`` still consults the beat cache (helper called with skip=False).
  - Helper returning None → ``extract_components`` receives ``downbeats=None``,
    job still completes.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sow_analysis.models import (
    ComponentAnalysisJobRequest,
    ComponentAnalysisOptions,
    Job,
    JobStatus,
    JobType,
)
from sow_analysis.workers.queue import JobQueue


@pytest.fixture
def temp_dir(tmp_path: Path) -> Path:
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


@pytest.fixture
async def job_queue(temp_dir: Path) -> JobQueue:
    queue = JobQueue(
        max_concurrent_local_model=1,
        cache_dir=temp_dir,
        db_path=temp_dir / "jobs.db",
    )
    await queue.initialize()
    # Stub R2 client so _process_component_analysis_job can proceed.
    queue.r2_client = MagicMock()
    queue.r2_client.download_audio = AsyncMock(
        side_effect=lambda url, path: Path(path).write_text("fake audio")
    )
    yield queue
    await queue.stop()


def _make_job(
    content_hash: str = "abc123def456",
    snap_to_downbeat: bool = True,
    downbeats=None,
    skip_beat_cache: bool = False,
    force: bool = False,
) -> Job:
    options = ComponentAnalysisOptions(
        snap_to_downbeat=snap_to_downbeat,
        skip_beat_cache=skip_beat_cache,
        force=force,
    )
    request = ComponentAnalysisJobRequest(
        audio_url="s3://test-bucket/audio.mp3",
        content_hash=content_hash,
        downbeats=downbeats,
        options=options,
    )
    return Job(
        id="test-job-id",
        type=JobType.COMPONENT_ANALYSIS,
        status=JobStatus.PROCESSING,
        request=request,
    )


@pytest.mark.asyncio
async def test_helper_called_when_snap_true_no_downbeats(job_queue: JobQueue) -> None:
    """Helper is invoked when snap_to_downbeat=True and request.downbeats is None."""
    job = _make_job(snap_to_downbeat=True, downbeats=None)

    beat_grid_payload = {"downbeats": [0.0, 4.0, 8.0]}

    with (
        patch(
            "sow_analysis.workers.queue.get_or_detect_beat_grid",
            new_callable=AsyncMock,
            return_value=beat_grid_payload,
        ) as mock_helper,
        patch(
            "sow_analysis.workers.queue.extract_components",
            new_callable=AsyncMock,
            return_value=([], "none"),
        ) as mock_extract,
    ):
        await job_queue._process_component_analysis_job(job)

    mock_helper.assert_called_once()
    # Verify skip_beat_cache forwarded as False (default).
    assert mock_helper.call_args.kwargs.get("skip_beat_cache") is False
    # extract_components should receive the downbeats from the grid.
    assert mock_extract.call_args.kwargs.get("downbeats") == [0.0, 4.0, 8.0]


@pytest.mark.asyncio
async def test_helper_not_called_when_snap_false(job_queue: JobQueue) -> None:
    """Helper is NOT called when snap_to_downbeat=False (Decision 1 guard)."""
    job = _make_job(snap_to_downbeat=False, downbeats=None)

    with (
        patch(
            "sow_analysis.workers.queue.get_or_detect_beat_grid",
            new_callable=AsyncMock,
            return_value={"downbeats": [0.0]},
        ) as mock_helper,
        patch(
            "sow_analysis.workers.queue.extract_components",
            new_callable=AsyncMock,
            return_value=([], "none"),
        ),
    ):
        await job_queue._process_component_analysis_job(job)

    mock_helper.assert_not_called()


@pytest.mark.asyncio
async def test_helper_not_called_when_downbeats_provided(job_queue: JobQueue) -> None:
    """Helper is NOT called when request.downbeats is provided by the caller."""
    job = _make_job(snap_to_downbeat=True, downbeats=[1.0, 5.0, 9.0])

    with (
        patch(
            "sow_analysis.workers.queue.get_or_detect_beat_grid",
            new_callable=AsyncMock,
            return_value={"downbeats": [0.0]},
        ) as mock_helper,
        patch(
            "sow_analysis.workers.queue.extract_components",
            new_callable=AsyncMock,
            return_value=([], "none"),
        ) as mock_extract,
    ):
        await job_queue._process_component_analysis_job(job)

    mock_helper.assert_not_called()
    # extract_components receives the caller-supplied downbeats.
    assert mock_extract.call_args.kwargs.get("downbeats") == [1.0, 5.0, 9.0]


@pytest.mark.asyncio
async def test_skip_beat_cache_forwarded(job_queue: JobQueue) -> None:
    """skip_beat_cache=True is forwarded to the helper."""
    job = _make_job(snap_to_downbeat=True, downbeats=None, skip_beat_cache=True)

    with (
        patch(
            "sow_analysis.workers.queue.get_or_detect_beat_grid",
            new_callable=AsyncMock,
            return_value={"downbeats": [0.0, 4.0]},
        ) as mock_helper,
        patch(
            "sow_analysis.workers.queue.extract_components",
            new_callable=AsyncMock,
            return_value=([], "none"),
        ),
    ):
        await job_queue._process_component_analysis_job(job)

    mock_helper.assert_called_once()
    assert mock_helper.call_args.kwargs.get("skip_beat_cache") is True


@pytest.mark.asyncio
async def test_force_still_consults_beat_cache(job_queue: JobQueue) -> None:
    """force=True does NOT skip the beat cache (core win of this spec)."""
    job = _make_job(snap_to_downbeat=True, downbeats=None, force=True)

    with (
        patch(
            "sow_analysis.workers.queue.get_or_detect_beat_grid",
            new_callable=AsyncMock,
            return_value={"downbeats": [0.0, 4.0]},
        ) as mock_helper,
        patch(
            "sow_analysis.workers.queue.extract_components",
            new_callable=AsyncMock,
            return_value=([], "none"),
        ),
    ):
        await job_queue._process_component_analysis_job(job)

    mock_helper.assert_called_once()
    # force should NOT set skip_beat_cache=True.
    assert mock_helper.call_args.kwargs.get("skip_beat_cache") is False


@pytest.mark.asyncio
async def test_helper_returning_none_job_completes(job_queue: JobQueue) -> None:
    """Helper returning None → extract_components receives downbeats=None, job completes."""
    job = _make_job(snap_to_downbeat=True, downbeats=None)

    with (
        patch(
            "sow_analysis.workers.queue.get_or_detect_beat_grid",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "sow_analysis.workers.queue.extract_components",
            new_callable=AsyncMock,
            return_value=([], "none"),
        ) as mock_extract,
    ):
        await job_queue._process_component_analysis_job(job)

    # extract_components should receive downbeats=None.
    assert mock_extract.call_args.kwargs.get("downbeats") is None
    # Job should still complete (not fail).
    assert job.status == JobStatus.COMPLETED
