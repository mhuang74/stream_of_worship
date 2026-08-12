"""Reusable step timing context manager for job processing transparency.

Wraps any processing step with start/end/elapsed log traces at INFO level.
Automatically inherits the job_id context from ``logging_config.job_id_ctx``
so traces are prefixed with ``[job_id]`` by ``JobIdFormatter``.
"""

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field


@dataclass
class StepHeartbeat:
    """Returned by step_timer() for manual heartbeat calls inside the with-block.

    Attributes:
        step_name: Human-readable name of the step.
        start: Monotonic start time of the step.
        log: Logger instance used for output.
        _heartbeat_interval: Minimum seconds between heartbeat lines. 0 disables.
        _last_beat: Timestamp of the last emitted heartbeat (throttle state).
    """

    step_name: str
    start: float
    log: logging.Logger
    _heartbeat_interval: float = field(default=0.0, repr=False)
    _last_beat: float = field(default=0.0, repr=False)

    def heartbeat(self, detail: str = "") -> None:
        """Log a 'still running' heartbeat line. Throttled to heartbeat_interval.

        Args:
            detail: Optional human-readable progress detail (e.g. "component 3/8").
        """
        if self._heartbeat_interval <= 0:
            return
        now = time.time()
        if now - self._last_beat < self._heartbeat_interval:
            return
        self._last_beat = now
        elapsed = now - self.start
        msg = f"Step heartbeat: {self.step_name} ({elapsed:.1f}s elapsed"
        if detail:
            msg += f" — {detail}"
        msg += ")"
        self.log.info(msg)


@contextmanager
def step_timer(
    step_name: str,
    log: logging.Logger,
    heartbeat_interval: float = 0.0,
) -> Iterator[StepHeartbeat]:
    """Context manager that logs start, end, and elapsed time of a step.

    Usage::

        with step_timer("Audio download", logger):
            await r2_client.download_audio(url, path)

    With heartbeat support::

        with step_timer("Component extraction", logger, heartbeat_interval=30) as hb:
            for i, comp in enumerate(components, 1):
                compute(comp)
                hb.heartbeat(f"component {i}/{len(components)}")

    Logs at INFO level:
        - ``Step started: {step_name}``
        - ``Step completed: {step_name} ({elapsed:.2f}s)``

    On exception, logs at ERROR level before re-raising:
        - ``Step failed: {step_name} ({elapsed:.2f}s) — {error}``

    Args:
        step_name: Human-readable name of the processing step.
        log: Logger instance to use for output.
        heartbeat_interval: If > 0, enable manual heartbeat() calls from inside
            the block. Does NOT auto-beat; callers must call .heartbeat() in
            their loop. Default 0 (disabled for backward compat).
    """
    start = time.time()
    hb = StepHeartbeat(
        step_name=step_name,
        start=start,
        log=log,
        _heartbeat_interval=heartbeat_interval,
    )
    log.info(f"Step started: {step_name}")
    try:
        yield hb
    except Exception as exc:
        elapsed = time.time() - start
        log.error(f"Step failed: {step_name} ({elapsed:.2f}s) — {exc}")
        raise
    else:
        elapsed = time.time() - start
        log.info(f"Step completed: {step_name} ({elapsed:.2f}s)")
