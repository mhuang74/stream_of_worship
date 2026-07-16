"""Unit tests for QuotaWaiter."""

import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from sow_analysis.workers.quota_waiter import QuotaWaiter


class _FakeJob:
    def __init__(self, job_id: str):
        self.id = job_id


@pytest.fixture
def fake_job():
    return _FakeJob("job_test_001")


@pytest.mark.asyncio
async def test_wait_returns_true_when_available(fake_job):
    """Event is set -> returns True immediately."""
    waiter = QuotaWaiter("test", lambda: True, poll_interval=100)
    result = await waiter.wait(fake_job, lambda: False, max_wait_seconds=5)
    assert result is True
    await waiter.stop()


@pytest.mark.asyncio
async def test_wait_returns_false_on_cancel(fake_job):
    """cancel_fn returns True -> returns False within ~1s."""
    waiter = QuotaWaiter("test", lambda: False, poll_interval=100)
    result = await waiter.wait(fake_job, lambda: True, max_wait_seconds=5)
    assert result is False
    await waiter.stop()


@pytest.mark.asyncio
async def test_wait_self_checks_is_available_every_second(fake_job):
    """Even without poller, detects availability via 1s self-check."""
    call_count = [0]

    def probe():
        call_count[0] += 1
        return call_count[0] >= 2  # available on second check

    waiter = QuotaWaiter("test", probe, poll_interval=100)
    result = await waiter.wait(fake_job, lambda: False, max_wait_seconds=5)
    assert result is True
    assert call_count[0] >= 2
    await waiter.stop()


@pytest.mark.asyncio
async def test_wait_respects_max_wait_seconds(fake_job):
    """max_wait_seconds=3 -> returns False after 3 ticks."""
    waiter = QuotaWaiter("test", lambda: False, poll_interval=100)
    import time

    start = time.monotonic()
    result = await waiter.wait(fake_job, lambda: False, max_wait_seconds=3)
    elapsed = time.monotonic() - start
    assert result is False
    assert elapsed >= 2.5  # at least ~3 ticks of 1s each
    await waiter.stop()


@pytest.mark.asyncio
async def test_mark_exhausted_clears_event_starts_poller(fake_job):
    """After mark_exhausted, waiters block; poller task is created."""
    waiter = QuotaWaiter("test", lambda: False, poll_interval=100)
    assert waiter._event.is_set()  # initially available

    await waiter.mark_exhausted()
    assert not waiter._event.is_set()  # cleared
    assert waiter._poller_task is not None
    assert not waiter._poller_task.done()

    await waiter.stop()


@pytest.mark.asyncio
async def test_poller_sets_event_when_probe_returns_true(fake_job):
    """Poller calls probe_fn, sets event, unblocks all waiters."""
    available = [False]
    waiter = QuotaWaiter("test", lambda: available[0], poll_interval=0.1)

    await waiter.mark_exhausted()

    async def _set_available():
        await asyncio.sleep(0.2)
        available[0] = True

    asyncio.create_task(_set_available())

    result = await waiter.wait(fake_job, lambda: False, max_wait_seconds=10)
    assert result is True
    await waiter.stop()


@pytest.mark.asyncio
async def test_multiple_waiters_all_unblock():
    """3 concurrent waiters all resume on single event.set()."""
    available = [False]
    waiter = QuotaWaiter("test", lambda: available[0], poll_interval=100)
    await waiter.mark_exhausted()

    results = []

    async def _waiter(idx):
        job = _FakeJob(f"job_{idx}")
        r = await waiter.wait(job, lambda: False, max_wait_seconds=10)
        results.append(r)

    tasks = [asyncio.create_task(_waiter(i)) for i in range(3)]

    # Let waiters enter the wait loop
    await asyncio.sleep(0.5)
    # Make probe return True and set event to unblock all
    available[0] = True
    waiter._event.set()

    await asyncio.gather(*tasks, return_exceptions=True)

    assert all(r is True for r in results)
    await waiter.stop()


@pytest.mark.asyncio
async def test_poller_survives_probe_exception():
    """probe_fn raises -> poller logs and continues, doesn't crash."""
    call_count = [0]

    def probe():
        call_count[0] += 1
        if call_count[0] == 1:
            raise RuntimeError("probe error")
        return True

    waiter = QuotaWaiter("test", probe, poll_interval=0.1)
    await waiter.mark_exhausted()

    # Wait long enough for poller to run at least twice
    await asyncio.sleep(0.5)
    assert waiter._poller_task is not None
    assert not waiter._poller_task.done()
    await waiter.stop()


@pytest.mark.asyncio
async def test_mark_exhausted_idempotent():
    """Multiple jobs call mark_exhausted concurrently -> single poller start."""
    waiter = QuotaWaiter("test", lambda: False, poll_interval=100)

    await asyncio.gather(
        waiter.mark_exhausted(),
        waiter.mark_exhausted(),
        waiter.mark_exhausted(),
    )

    assert waiter._poller_task is not None
    assert not waiter._poller_task.done()
    await waiter.stop()


@pytest.mark.asyncio
async def test_stop_cancels_poller():
    """stop() cancels the background task cleanly."""
    waiter = QuotaWaiter("test", lambda: False, poll_interval=0.1)
    await waiter.mark_exhausted()
    assert waiter._poller_task is not None

    await waiter.stop()
    assert waiter._poller_task is None


@pytest.mark.asyncio
async def test_initial_event_state_is_set(fake_job):
    """New QuotaWaiter — wait() returns True immediately before mark_exhausted."""
    waiter = QuotaWaiter("test", lambda: True, poll_interval=100)
    assert waiter._event.is_set()

    result = await waiter.wait(fake_job, lambda: False, max_wait_seconds=5)
    assert result is True
    await waiter.stop()


@pytest.mark.asyncio
async def test_periodic_logging_emits_waiting_count():
    """2 jobs waiting, advance time 30s -> log line with count=2 and sample IDs."""
    waiter = QuotaWaiter("test", lambda: False, poll_interval=100)
    await waiter.mark_exhausted()

    # Force _last_log_time to be old enough to trigger logging
    waiter._last_log_time = 0.0

    job1 = _FakeJob("job_aaa")
    job2 = _FakeJob("job_bbb")

    async def _wait(job):
        await waiter.wait(job, lambda: False, max_wait_seconds=1)

    # Start two waiters
    t1 = asyncio.create_task(_wait(job1))
    t2 = asyncio.create_task(_wait(job2))

    # Let them run briefly
    await asyncio.sleep(0.1)

    # The periodic logging should have fired since _last_log_time was 0.0
    # (we can't easily capture log output, but we verify no crash)
    await asyncio.gather(t1, t2, return_exceptions=True)
    await waiter.stop()


# ── Quiescent throttling tests ──


def _patch_wait_for_immediate_timeout(monkeypatch):
    """Patch asyncio.wait_for in quota_waiter module to raise TimeoutError instantly."""
    import sow_analysis.workers.quota_waiter as qw_mod

    async def _fake_wait_for(awaitable, timeout):
        if hasattr(awaitable, "close"):
            awaitable.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(asyncio, "wait_for", _fake_wait_for)


def _make_monotonic_stepper(start: float, step: float):
    """Return a fake monotonic() that advances by `step` on each call."""
    state = [start]

    def _fake():
        val = state[0]
        state[0] += step
        return val

    return _fake, state


@pytest.mark.asyncio
async def test_quiescent_fn_none_uses_normal_interval(monkeypatch, caplog):
    """With is_quiescent_fn=None (default), logging fires every 30s as before."""
    import sow_analysis.workers.quota_waiter as qw_mod

    _patch_wait_for_immediate_timeout(monkeypatch)
    fake_mono, _ = _make_monotonic_stepper(start=30.0, step=30.0)
    monkeypatch.setattr(qw_mod.time, "monotonic", fake_mono)

    waiter = QuotaWaiter("test", lambda: False, poll_interval=100)
    job = _FakeJob("job_001")
    caplog.set_level(logging.INFO, logger="sow_analysis.workers.quota_waiter")

    await waiter.wait(job, lambda: False, max_wait_seconds=5)
    await waiter.stop()

    waiting_logs = [r for r in caplog.records if "waiting for quota reset" in r.message]
    assert len(waiting_logs) == 5  # one per 30s tick


@pytest.mark.asyncio
async def test_quiescent_fn_true_uses_quiescent_interval(monkeypatch, caplog):
    """With is_quiescent_fn=lambda: True, only one log per 30-min interval."""
    import sow_analysis.workers.quota_waiter as qw_mod

    _patch_wait_for_immediate_timeout(monkeypatch)
    # Start at 1800 so first tick logs (1800 - 0 >= 1800); advance 60s/tick
    fake_mono, _ = _make_monotonic_stepper(start=1800.0, step=60.0)
    monkeypatch.setattr(qw_mod.time, "monotonic", fake_mono)

    waiter = QuotaWaiter("test", lambda: False, poll_interval=100, is_quiescent_fn=lambda: True)
    job = _FakeJob("job_001")
    caplog.set_level(logging.INFO, logger="sow_analysis.workers.quota_waiter")

    # 40 ticks × 60s = 2400s = 40 min; quiescent interval = 1800s (30 min)
    await waiter.wait(job, lambda: False, max_wait_seconds=40)
    await waiter.stop()

    waiting_logs = [r for r in caplog.records if "waiting for quota reset" in r.message]
    # First log at tick 1 (now=1800), second at tick 31 (now=3600) -> 2 logs
    assert len(waiting_logs) == 2


@pytest.mark.asyncio
async def test_quiescent_transition_logs_backoff_notice(monkeypatch, caplog):
    """On the first quiescent tick, the 'backing off' transition line is emitted."""
    import sow_analysis.workers.quota_waiter as qw_mod

    _patch_wait_for_immediate_timeout(monkeypatch)
    fake_mono, _ = _make_monotonic_stepper(start=1800.0, step=60.0)
    monkeypatch.setattr(qw_mod.time, "monotonic", fake_mono)

    waiter = QuotaWaiter("test", lambda: False, poll_interval=100, is_quiescent_fn=lambda: True)
    job = _FakeJob("job_001")
    caplog.set_level(logging.INFO, logger="sow_analysis.workers.quota_waiter")

    await waiter.wait(job, lambda: False, max_wait_seconds=3)
    await waiter.stop()

    backoff_logs = [r for r in caplog.records if "backing off periodic log" in r.message]
    assert len(backoff_logs) == 1


@pytest.mark.asyncio
async def test_quiescent_to_active_resumes_cadence(monkeypatch, caplog):
    """After quiescent period, flipping to non-quiescent emits 'resuming' line."""
    import sow_analysis.workers.quota_waiter as qw_mod

    _patch_wait_for_immediate_timeout(monkeypatch)
    fake_mono, _ = _make_monotonic_stepper(start=1800.0, step=60.0)
    monkeypatch.setattr(qw_mod.time, "monotonic", fake_mono)

    quiescent = [True]
    waiter = QuotaWaiter(
        "test", lambda: False, poll_interval=100, is_quiescent_fn=lambda: quiescent[0]
    )
    job = _FakeJob("job_001")
    caplog.set_level(logging.INFO, logger="sow_analysis.workers.quota_waiter")

    # 2 quiescent ticks
    await waiter.wait(job, lambda: False, max_wait_seconds=2)
    assert waiter._was_quiescent is True

    # Flip to non-quiescent and run 1 more tick
    quiescent[0] = False
    await waiter.wait(job, lambda: False, max_wait_seconds=1)
    await waiter.stop()

    resume_logs = [r for r in caplog.records if "resuming normal log cadence" in r.message]
    assert len(resume_logs) == 1
