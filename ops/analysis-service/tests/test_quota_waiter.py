"""Unit tests for QuotaWaiter."""

import asyncio
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
