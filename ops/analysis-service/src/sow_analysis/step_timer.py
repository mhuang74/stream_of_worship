"""Reusable step timing context manager for job processing transparency.

Wraps any processing step with start/end/elapsed log traces at INFO level.
Automatically inherits the job_id context from ``logging_config.job_id_ctx``
so traces are prefixed with ``[job_id]`` by ``JobIdFormatter``.
"""

import logging
import time
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def step_timer(step_name: str, log: logging.Logger) -> Iterator[None]:
    """Context manager that logs start, end, and elapsed time of a step.

    Usage::

        with step_timer("Audio download", logger):
            await r2_client.download_audio(url, path)

    Logs at INFO level:
        - ``Step started: {step_name}``
        - ``Step completed: {step_name} ({elapsed:.2f}s)``

    On exception, logs at ERROR level before re-raising:
        - ``Step failed: {step_name} ({elapsed:.2f}s) — {error}``

    Args:
        step_name: Human-readable name of the processing step.
        log: Logger instance to use for output.
    """
    start = time.time()
    log.info(f"Step started: {step_name}")
    try:
        yield
    except Exception as exc:
        elapsed = time.time() - start
        log.error(f"Step failed: {step_name} ({elapsed:.2f}s) — {exc}")
        raise
    else:
        elapsed = time.time() - start
        log.info(f"Step completed: {step_name} ({elapsed:.2f}s)")
