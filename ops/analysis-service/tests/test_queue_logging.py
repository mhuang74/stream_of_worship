"""Tests for JobQueue quiescent-state detection and log suppression."""

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from sow_analysis.models import AnalyzeJobRequest, Job, JobStatus, JobType
from sow_analysis.workers.queue import JobQueue


def _make_job(
    job_id: str,
    status: JobStatus,
    stage: str = "",
    job_type: JobType = JobType.STEM_SEPARATION,
    updated_at: datetime | None = None,
) -> Job:
    request = AnalyzeJobRequest(audio_url="http://example.com/a.mp3", content_hash="abc")
    return Job(
        id=job_id,
        type=job_type,
        status=status,
        request=request,
        stage=stage,
        updated_at=updated_at or datetime.now(timezone.utc),
    )


@pytest.fixture
def job_queue(tmp_path):
    return JobQueue(max_concurrent_local_model=1, cache_dir=tmp_path, db_path=tmp_path / "jobs.db")


# ── _is_quota_wait_quiescent() tests ──


def test_is_quota_wait_quiescent_all_processing_quota_wait(job_queue):
    """3 PROCESSING jobs with quota-wait stages -> True."""
    now = datetime.now(timezone.utc)
    for i in range(3):
        job_queue._jobs[f"job_{i}"] = _make_job(
            f"job_{i}",
            JobStatus.PROCESSING,
            stage="waiting_for_mvsep_quota_reset",
            updated_at=now,
        )
    assert job_queue._is_quota_wait_quiescent() is True


def test_is_quota_wait_quiescent_mixed_stages(job_queue):
    """One quota-waiting, one PROCESSING with stage='analyzing' -> False."""
    now = datetime.now(timezone.utc)
    job_queue._jobs["job_a"] = _make_job(
        "job_a", JobStatus.PROCESSING, stage="waiting_for_mvsep_quota_reset", updated_at=now
    )
    job_queue._jobs["job_b"] = _make_job(
        "job_b", JobStatus.PROCESSING, stage="analyzing", updated_at=now
    )
    assert job_queue._is_quota_wait_quiescent() is False


def test_is_quota_wait_quiescent_with_queued_job(job_queue):
    """Quota-waiting jobs plus a QUEUED job -> False."""
    now = datetime.now(timezone.utc)
    job_queue._jobs["job_a"] = _make_job(
        "job_a", JobStatus.PROCESSING, stage="waiting_for_mvsep_quota_reset", updated_at=now
    )
    job_queue._jobs["job_b"] = _make_job("job_b", JobStatus.QUEUED, updated_at=now)
    assert job_queue._is_quota_wait_quiescent() is False


def test_is_quota_wait_quiescent_empty_queue(job_queue):
    """No jobs -> False."""
    assert job_queue._is_quota_wait_quiescent() is False


def test_is_quota_wait_quiescent_only_finished_jobs(job_queue):
    """Only COMPLETED/FAILED -> False (nothing active)."""
    now = datetime.now(timezone.utc)
    job_queue._jobs["job_a"] = _make_job("job_a", JobStatus.COMPLETED, updated_at=now)
    job_queue._jobs["job_b"] = _make_job(
        "job_b", JobStatus.FAILED, updated_at=now - timedelta(seconds=999)
    )
    assert job_queue._is_quota_wait_quiescent() is False


def test_is_quota_wait_quiescent_qwen3_asr_stage(job_queue):
    """Qwen3 ASR quota-wait stage also counts as quiescent."""
    now = datetime.now(timezone.utc)
    job_queue._jobs["job_a"] = _make_job(
        "job_a",
        JobStatus.PROCESSING,
        stage="waiting_for_qwen3_asr_quota_reset",
        updated_at=now,
    )
    assert job_queue._is_quota_wait_quiescent() is True


def test_is_quota_wait_quiescent_finished_plus_quota_wait(job_queue):
    """Finished jobs are ignored; one active quota-wait job -> True."""
    now = datetime.now(timezone.utc)
    job_queue._jobs["job_done"] = _make_job("job_done", JobStatus.COMPLETED, updated_at=now)
    job_queue._jobs["job_wait"] = _make_job(
        "job_wait", JobStatus.PROCESSING, stage="waiting_for_mvsep_quota_reset", updated_at=now
    )
    assert job_queue._is_quota_wait_quiescent() is True


# ─_log_queue_state() suppression tests ──


def test_log_queue_state_suppressed_during_quiescence(job_queue, caplog):
    """During quiescence, log is suppressed until interval elapses."""
    now = datetime.now(timezone.utc)
    job_queue._jobs["job_a"] = _make_job(
        "job_a", JobStatus.PROCESSING, stage="waiting_for_mvsep_quota_reset", updated_at=now
    )
    caplog.set_level(logging.INFO, logger="sow_analysis.workers.queue")

    # Set last quiescent log time to "now" so suppression is active
    import time as time_mod

    job_queue._last_quiescent_log_time = time_mod.monotonic()

    job_queue._log_queue_state()
    queue_logs = [r for r in caplog.records if "Queue state" in r.message]
    assert len(queue_logs) == 0  # suppressed


def test_log_queue_state_emits_after_interval_during_quiescence(job_queue, caplog):
    """After interval elapses during quiescence, log is emitted once."""
    now = datetime.now(timezone.utc)
    job_queue._jobs["job_a"] = _make_job(
        "job_a", JobStatus.PROCESSING, stage="waiting_for_mvsep_quota_reset", updated_at=now
    )
    caplog.set_level(logging.INFO, logger="sow_analysis.workers.queue")

    import time as time_mod

    # Set last quiescent log far in the past so interval has elapsed
    job_queue._last_quiescent_log_time = time_mod.monotonic() - 99999

    job_queue._log_queue_state()
    queue_logs = [r for r in caplog.records if "Queue state" in r.message]
    assert len(queue_logs) == 1  # emitted


def test_log_queue_state_resumes_after_quiescence(job_queue, caplog):
    """After quiescence ends, next call logs immediately and resets timer."""
    now = datetime.now(timezone.utc)
    job_queue._jobs["job_a"] = _make_job(
        "job_a", JobStatus.PROCESSING, stage="analyzing", updated_at=now
    )
    caplog.set_level(logging.INFO, logger="sow_analysis.workers.queue")

    import time as time_mod

    # Simulate having been in quiescence
    job_queue._last_quiescent_log_time = time_mod.monotonic()

    # Now not quiescent (stage is "analyzing") -> should log immediately
    job_queue._log_queue_state()
    queue_logs = [r for r in caplog.records if "Queue state" in r.message]
    assert len(queue_logs) == 1
    # Timer should be reset to 0.0
    assert job_queue._last_quiescent_log_time == 0.0


def test_log_queue_state_no_jobs_no_log(job_queue, caplog):
    """Empty queue -> no log (early return)."""
    caplog.set_level(logging.INFO, logger="sow_analysis.workers.queue")
    job_queue._log_queue_state()
    queue_logs = [r for r in caplog.records if "Queue state" in r.message]
    assert len(queue_logs) == 0
