"""Shared quota waiter for free-only patient mode.

One instance per API type (MVSEP, DashScope). Service-wide singleton shared
by all jobs. When quota is exhausted, jobs call ``wait()`` to block until the
quota resets (UTC daily) or the job is cancelled.

The ``wait()`` method does a 1-second-granularity loop. Each tick:
1. Checks the cancellation callback -> returns False if cancelled
2. Directly calls ``is_available`` (the ``probe_fn``) in-process -> returns True
   if available
3. Falls through to ``asyncio.wait_for(_event.wait(), timeout=1.0)`` for the
   next tick

This means every job independently self-checks every 1s. The poller background
task is a shared optimization that proactively sets the event on detection —
but even if it crashes, each job's own 1s tick will eventually discover
availability (after the next UTC midnight, when ``is_available`` returns True).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class QuotaWaiter:
    """Block jobs until free-tier API quota resets.

    One instance per API type (MVSEP, DashScope). Service-wide singleton.

    Args:
        name: Human-readable name for logging (e.g., "mvsep", "qwen3").
        probe_fn: Callable returning True when quota is available.
            Typically ``lambda: client.is_available`` (since ``is_available``
            is a property, a lambda or bound method must be passed, not the
            property value itself).
        poll_interval: Seconds between poller background checks.
    """

    def __init__(
        self,
        name: str,
        probe_fn: Callable[[], bool],
        poll_interval: int,
    ) -> None:
        self._name = name
        self._probe_fn = probe_fn
        self._poll_interval = poll_interval
        self._event: asyncio.Event = asyncio.Event()
        self._event.set()  # Start in "available" state — waiters resume immediately
        self._poller_task: Optional[asyncio.Task] = None
        self._waiting_jobs: set[str] = set()
        self._last_log_time: float = 0.0

    async def mark_exhausted(self) -> None:
        """Called when a job detects quota exhaustion. Clears event, starts poller."""
        self._event.clear()
        logger.info(
            "QuotaWaiter[%s]: quota marked exhausted; %d jobs currently waiting",
            self._name,
            len(self._waiting_jobs),
        )
        self._start_poller()

    async def wait(
        self,
        job,
        cancel_fn: Callable[[], bool],
        max_wait_seconds: int = 60,
    ) -> bool:
        """Block until quota available, job cancelled, or max_wait_seconds elapsed.

        Self-checks ``is_available`` every 1s. Runs for at most
        ``max_wait_seconds`` iterations of the 1s loop. Returns True if
        available, False if cancelled or max_wait_seconds elapsed.

        The ``job`` parameter is used for periodic logging: every 30s, logs
        the count of waiting jobs and a sample of job IDs, so operators can
        see what's blocked.

        Callers should loop on ``wait()`` with ``max_wait_seconds=60``,
        calling ``_update_stage`` between iterations as a heartbeat.
        """
        job_id = getattr(job, "id", str(job))
        self._waiting_jobs.add(job_id)
        try:
            for _ in range(max_wait_seconds):
                if cancel_fn():
                    return False
                # Clear event before checking probe to prevent race condition:
                # if poller (or another waiter) sets the event between probe()
                # and clear(), we'd incorrectly discard it and block for 1s.
                self._event.clear()
                if self._probe_fn():
                    self._event.set()
                    return True
                # Periodic logging every 30s
                now = time.monotonic()
                if now - self._last_log_time >= 30.0:
                    self._last_log_time = now
                    sample = list(self._waiting_jobs)[:5]
                    logger.info(
                        "QuotaWaiter[%s]: %d jobs waiting for quota reset (sample: %s)",
                        self._name,
                        len(self._waiting_jobs),
                        sample,
                    )
                try:
                    await asyncio.wait_for(self._event.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
            # max_wait_seconds elapsed without availability
            return False
        finally:
            self._waiting_jobs.discard(job_id)

    def _start_poller(self) -> None:
        """Lazily start background poller task. Guards against double-start."""
        if self._poller_task is not None and not self._poller_task.done():
            return
        self._poller_task = asyncio.create_task(self._poll_loop())

    async def _poll_loop(self) -> None:
        """Background task: every poll_interval, call probe_fn. Set event when True.

        Wrapped in try/except Exception: log + continue. Never crashes.
        """
        while True:
            try:
                await asyncio.sleep(self._poll_interval)
                if self._probe_fn():
                    logger.info(
                        "QuotaWaiter[%s]: poller detected quota available; "
                        "unblocking %d waiting jobs",
                        self._name,
                        len(self._waiting_jobs),
                    )
                    self._event.set()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(
                    "QuotaWaiter[%s]: poller error (will continue): %s",
                    self._name,
                    exc,
                )

    async def stop(self) -> None:
        """Called during shutdown. Cancels poller task."""
        if self._poller_task is not None:
            self._poller_task.cancel()
            try:
                await self._poller_task
            except asyncio.CancelledError:
                pass
            self._poller_task = None
        logger.info("QuotaWaiter[%s]: stopped", self._name)
