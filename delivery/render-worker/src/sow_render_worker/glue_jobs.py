"""Durable Glue (AI transition) job spine for the render worker.

One Lambda invocation handles exactly one SQS delivery, so a duplicate
delivery must never redo work. The exact call order implemented here is:

1. ``claim_glue_job(conn, job_id, user_id)`` — atomic claim. ``(None, _)`` is
   an unknown owner/job pair; ``claimed_now=False`` means this delivery must
   read the persisted state instead of running the phase machine again.
2. Redelivery rules for ``claimed_now=False``: a terminal phase is returned
   untouched; ``uploading``/``composing`` without a complete durable success
   record is the *ambiguous provider outcome* (terminal ``failed`` with
   ``GLUE_PROVIDER_UNKNOWN`` and ``retry_kind="paid"``, no provider call, no
   refund); ``uploading``/``composing`` with a persisted raw response is left
   for free staging recovery; any other in-flight phase is left running.
3. ``mode="staging"`` (retry-processing recovery) and any delivery that finds
   the persisted phase at ``storing-response``/``staging`` take the free path:
   verify the persisted raw response metadata, move to ``staging`` and hand
   off to ``glue_staging.finalize_staging`` (issue #318). It never calls the
   provider.
4. ``mode="generate"`` (SQS/default): re-read the Songset Items, rebuild the
   canonical descriptor from fresh inputs and compare it to the persisted
   ``descriptor_key``/``descriptor_json`` plus the persisted item timestamps.
   Stale or cancelled work finishes ``cancelled`` (``GLUE_INPUTS_CHANGED``)
   without spend.
5. Resolve the provider stage (injected seam, else
   ``glue_provider.create_provider_stage``) and require a configured provider
   key *before* building excerpts. Missing configuration raises
   ``GlueConfigurationError`` and leaves the job pre-contact.
6. Build both canonical excerpts with ``prepare_excerpt`` (side A then B) and
   heartbeat after every stage boundary.
7. Deadline gate (``GLUE_EXECUTION_DEADLINE``, ``retry_kind="free"``,
   reservation released) before the first provider request.
8. ``record_provider_contact(...)`` compare-and-set; ``False`` means
   cancellation won, so the job finishes ``cancelled`` and no provider call is
   made. The returned ``True`` consumes the paid reservation.
9. ``provider_stage(job, conn, *, a_excerpt, b_excerpt, work_dir, log_event)``
   (issue #317) performs upload/compose/raw persistence and leaves the job in
   ``storing-response``; issue #318 continues from there into ``staging``.

Errors are mapped to terminal phases with sanitized ``error_detail`` and the
correct ``retry_kind``: ``free`` for pre-contact infrastructure failures,
``paid`` for anything after the provider contact compare-and-set. The bounded
free-retry policy (initial + 3, full jitter in ``[0, 1s]``, ``[0, 2s]``,
``[0, 4s]``) wraps DB/R2/local-decode operations only and is never applied to a
provider request. Configuration absence is raised, never turned into a
pretend success.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import random
import re
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from sow_render_worker.asset_fetcher import AssetFetcher
from sow_render_worker.db import (
    GLUE_TERMINAL_PHASES,
    GlueJob,
    claim_glue_job,
    finish_glue_job,
    get_glue_job,
    heartbeat_glue_job,
    read_songset_items_for_glue,
    record_provider_contact,
    set_glue_phase,
)
from sow_render_worker.glue_descriptor import build_descriptor
from sow_render_worker.glue_excerpts import (
    GlueAudioUnavailable,
    GlueExcerptTooShort,
    PreparedExcerpt,
    canonical_steps_from_arrangement,
    prepare_excerpt,
)

logger = logging.getLogger(__name__)

GLUE_NOT_CONFIGURED = "GLUE_NOT_CONFIGURED"
GLUE_EXECUTION_DEADLINE = "GLUE_EXECUTION_DEADLINE"
GLUE_PROVIDER_UNKNOWN = "GLUE_PROVIDER_UNKNOWN"
GLUE_WORKER_FAILED = "GLUE_WORKER_FAILED"
GLUE_INPUTS_CHANGED = "GLUE_INPUTS_CHANGED"
GLUE_EXCERPT_TOO_SHORT = "GLUE_EXCERPT_TOO_SHORT"
GLUE_AUDIO_UNAVAILABLE = "GLUE_AUDIO_UNAVAILABLE"

# Provider phases stored on the job; upload-a/upload-b both map to `uploading`.
_IN_FLIGHT_PROVIDER_PHASES = frozenset({"uploading", "composing"})
_FIRST_UPLOAD_PROVIDER_PHASE = "upload-a"

# Spec §2.5 provider deadline reserves: connect + read + persistence margin.
UPLOAD_DEADLINE_RESERVE_MS = 990_000
COMPOSE_DEADLINE_RESERVE_MS = 1_890_000

# Free infrastructure retries: initial attempt + one per entry, full jitter.
FREE_RETRY_DELAY_CAPS_SECONDS = (1.0, 2.0, 4.0)

# Module-level indirections so tests can patch them without touching stdlib.
_sleep = time.sleep

_LOG_FIELD_ALLOWLIST = (
    "jobId",
    "userId",
    "songsetId",
    "fromItemId",
    "toItemId",
    "takeId",
    "attemptId",
    "descriptorKey",
    "phase",
    "providerStatus",
    "cacheHit",
    "retryCount",
    # Logged on terminal failure so the GluePaidFailure metric filter can match
    # `"retryKind": "paid"`; without it the documented alarm never fired.
    "retryKind",
    "durationMs",
    "errorCode",
    "mode",
    "operation",
    "delayMs",
    "remainingMs",
    "status",
    "reason",
)

# Anything that could leak a URL, a filesystem path, a secret or a stack.
_UNSAFE_DETAIL_PATTERN = re.compile(
    r"(https?://|/|\\\\|api[_-]?key|secret|token|bearer|password|traceback|\bat\s+\w+\()",
    re.IGNORECASE,
)
_MAX_DETAIL_LENGTH = 200


class GlueConfigurationError(RuntimeError):
    """Worker configuration needed for a paid Glue stage is absent."""

    code = GLUE_NOT_CONFIGURED

    def __init__(self, message: str = "Glue provider is not configured") -> None:
        super().__init__(message)


@dataclass
class GlueJobError(Exception):
    """Typed Glue failure carrying the stable client code and retry class."""

    code: str
    retry_kind: str
    detail: str | None = None

    def __init__(self, code: str, retry_kind: str, detail: str | None = None) -> None:
        super().__init__(code, retry_kind, detail)
        self.code = code
        self.retry_kind = retry_kind
        self.detail = detail

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.code} ({self.retry_kind})"


class _StaleJunction(Exception):
    """Saved inputs no longer produce the persisted descriptor."""


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------


def _log_event(event: str, *, level: str = "info", **fields: Any) -> None:
    """Emit one structured JSON line for a stable Glue event name."""
    payload: dict[str, Any] = {"event": event}
    for key, value in fields.items():
        if value is None:
            continue
        if key in _LOG_FIELD_ALLOWLIST or key in ("errorType", "errorCode"):
            payload[key] = value
    logger.log(
        getattr(logging, level.upper(), logging.INFO),
        json.dumps(payload, default=str, ensure_ascii=False, sort_keys=True),
    )


def _job_logger(job_id: str, user_id: int) -> Callable[..., None]:
    def log_event(event: str, *, level: str = "info", **fields: Any) -> None:
        _log_event(event, level=level, jobId=job_id, userId=user_id, **fields)

    return log_event


def _sanitize_detail(detail: object, *, fallback: str) -> str:
    """Collapse a failure detail to a short, leak-free sentence."""
    if not isinstance(detail, str):
        return fallback
    text = " ".join(detail.split())
    if not text:
        return fallback
    if _UNSAFE_DETAIL_PATTERN.search(text):
        return fallback
    return text[:_MAX_DETAIL_LENGTH]


# ---------------------------------------------------------------------------
# Deadline and free retries
# ---------------------------------------------------------------------------


def _remaining_ms(lambda_context: Any) -> Optional[int]:
    """Remaining Lambda budget in ms, or None for local runs."""
    if lambda_context is None:
        return None
    getter = getattr(lambda_context, "get_remaining_time_in_millis", None)
    if not callable(getter):
        return None
    try:
        return int(getter())
    except Exception:  # pragma: no cover - defensive around test doubles
        return None


def require_deadline(lambda_context: Any, stage: str) -> Optional[int]:
    """Gate a provider stage on its full reserve; None means skipped locally.

    ``stage`` is ``"upload"`` (990 s) or ``"compose"`` (1890 s). Issues #317
    re-checks the compose reserve immediately before compose.
    """
    reserve = {
        "upload": UPLOAD_DEADLINE_RESERVE_MS,
        "compose": COMPOSE_DEADLINE_RESERVE_MS,
    }.get(stage)
    if reserve is None:
        raise ValueError(f"Unknown Glue provider stage: {stage!r}")
    remaining = _remaining_ms(lambda_context)
    if remaining is None:
        return None
    if remaining < reserve:
        raise GlueJobError(
            GLUE_EXECUTION_DEADLINE,
            "free",
            f"Execution budget exhausted before {stage}",
        )
    return remaining


def _free_retry(
    operation: Callable[[], Any],
    *,
    label: str,
    log: Callable[..., None],
) -> Any:
    """Run an infrastructure operation with initial + 3 full-jitter retries.

    Never wrap a provider request with this: an ambiguous paid request must not
    be repeated automatically.
    """
    attempt = 0
    while True:
        try:
            return operation()
        except (GlueExcerptTooShort, GlueConfigurationError, GlueJobError):
            raise
        except Exception as exc:
            if attempt >= len(FREE_RETRY_DELAY_CAPS_SECONDS):
                raise
            delay_cap = FREE_RETRY_DELAY_CAPS_SECONDS[attempt]
            delay = random.uniform(0, delay_cap)
            attempt += 1
            log(
                "glue.retry",
                level="warning",
                operation=label,
                retryCount=attempt,
                delayMs=round(delay * 1000, 3),
                errorType=type(exc).__name__,
            )
            _sleep(delay)


@contextlib.contextmanager
def _atomic(conn: Any) -> Iterator[None]:
    """Toggle autocommit for one transaction, mirroring db.py's idiom."""
    original_autocommit = conn.autocommit
    try:
        conn.autocommit = False
        yield
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.autocommit = original_autocommit


def _release_unconsumed_reservation(conn: Any, job: GlueJob) -> bool:
    """Release a still-reserved paid attempt; no-op once consumed/released.

    The guard is ``state = 'reserved'`` so a consumed quota slot can never be
    refunded here. Runs in one transaction across the ledger row and the job
    column, matching the webapp's reservation transaction.
    """
    now = datetime.now(timezone.utc)
    released = False
    with _atomic(conn):
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE glue_paid_attempts SET state = %s, released_at = %s "
                "WHERE glue_job_id = %s AND user_id = %s AND state = %s",
                ("released", now, job.id, job.user_id, "reserved"),
            )
            released = bool(cur.rowcount)
            cur.execute(
                "UPDATE glue_jobs SET paid_reservation_state = %s, updated_at = %s "
                "WHERE id = %s AND user_id = %s AND paid_reservation_state = %s",
                ("released", now, job.id, job.user_id, "reserved"),
            )
            released = bool(cur.rowcount) or released
    return released


# ---------------------------------------------------------------------------
# Terminal transitions
# ---------------------------------------------------------------------------


def _finish_terminal(
    conn: Any,
    job: GlueJob,
    *,
    phase: str,
    code: Optional[str],
    retry_kind: Optional[str],
    detail: Optional[str],
    release: bool,
    log: Callable[..., None],
    event: str,
) -> None:
    """Finish a job once: terminal phase + cleared active + reservation state."""
    if release:
        # A never-consumed reservation is refundable even if a concurrent
        # cancellation already made the job terminal; the SQL is guarded on
        # state='reserved', so a consumed slot is never refunded.
        try:
            _release_unconsumed_reservation(conn, job)
        except Exception:
            logger.exception("Failed to release Glue paid reservation")
    with contextlib.suppress(Exception):
        current = get_glue_job(conn, job.id, job.user_id)
        if current is not None and current.phase in GLUE_TERMINAL_PHASES:
            log(
                event,
                level="warning",
                phase=current.phase,
                errorCode=current.error_code,
                status="already-terminal",
                retryKind=retry_kind,
            )
            return
    try:
        finish_glue_job(
            conn,
            job.id,
            job.user_id,
            phase=phase,
            error_code=code,
            error_detail=_sanitize_detail(detail, fallback=code or "Glue job stopped"),
            retry_kind=retry_kind,
        )
    except Exception:
        logger.exception("Failed to finish Glue job")
        raise
    log(
        event,
        level="warning" if phase != "ready" else "info",
        phase=phase,
        errorCode=code,
        status="finished",
        retryKind=retry_kind,
    )


def _handle_failure(
    conn: Any,
    job: GlueJob,
    exc: BaseException,
    *,
    contacted: bool,
    log: Callable[..., None],
) -> None:
    """Map a worker exception to the terminal failure vocabulary."""
    if isinstance(exc, GlueConfigurationError):
        raise exc
    retry_kind = "paid" if contacted else "free"
    if isinstance(exc, GlueJobError):
        _finish_terminal(
            conn,
            job,
            phase="failed",
            code=exc.code,
            retry_kind=exc.retry_kind,
            detail=exc.detail,
            release=not contacted,
            log=log,
            event="glue.job.failed",
        )
        return
    if isinstance(exc, GlueExcerptTooShort):
        _finish_terminal(
            conn,
            job,
            phase="failed",
            code=GLUE_EXCERPT_TOO_SHORT,
            retry_kind="none",
            detail=str(exc),
            release=not contacted,
            log=log,
            event="glue.job.failed",
        )
        return
    if isinstance(exc, GlueAudioUnavailable):
        _finish_terminal(
            conn,
            job,
            phase="failed",
            code=GLUE_AUDIO_UNAVAILABLE,
            retry_kind=retry_kind,
            detail=str(exc),
            release=not contacted,
            log=log,
            event="glue.job.failed",
        )
        return
    logger.exception(
        "Unexpected Glue worker failure",
        extra={"jobId": job.id, "userId": job.user_id, "phase": job.phase},
    )
    _finish_terminal(
        conn,
        job,
        phase="failed",
        code=GLUE_WORKER_FAILED,
        retry_kind=retry_kind,
        detail=type(exc).__name__,
        release=not contacted,
        log=log,
        event="glue.job.failed",
    )


def _heartbeat(conn: Any, job: GlueJob, log: Callable[..., None], *, stage: str) -> None:
    """Heartbeat after a stage boundary with the bounded free-retry policy.

    If every retry fails the error propagates to the failure mapper so the job
    never keeps running without a live heartbeat.
    """
    try:
        _free_retry(
            lambda: heartbeat_glue_job(conn, job.id, job.user_id),
            label=f"heartbeat_{stage}",
            log=log,
        )
    except Exception:
        logger.exception("Failed to heartbeat Glue job")
        log("glue.heartbeat.failed", level="warning", phase=stage)
        raise


# ---------------------------------------------------------------------------
# Input revalidation
# ---------------------------------------------------------------------------


def _timestamps_match(expected: Optional[datetime], actual: Any) -> bool:
    if expected is None:
        return True
    if not isinstance(actual, datetime):
        return False
    left = expected.replace(tzinfo=timezone.utc) if expected.tzinfo is None else expected
    right = actual.replace(tzinfo=timezone.utc) if actual.tzinfo is None else actual
    return left == right


@dataclass(frozen=True)
class _JunctionInputs:
    from_item: dict[str, Any]
    to_item: dict[str, Any]
    from_steps: list[list[float]] | None
    to_steps: list[list[float]] | None
    descriptor: dict[str, Any]
    canonical_json: str
    descriptor_key: str


def _rebuild_junction(items: list[dict[str, Any]], job: GlueJob) -> _JunctionInputs:
    """Recompute the descriptor from fresh item facts; raise when stale."""
    positions = {row["id"]: index for index, row in enumerate(items)}
    from_index = positions.get(job.from_item_id)
    to_index = positions.get(job.to_item_id)
    if from_index is None or to_index is None:
        raise _StaleJunction("Junction items are no longer in the Songset")
    if to_index != from_index + 1:
        raise _StaleJunction("Junction items are no longer adjacent")
    from_item = items[from_index]
    to_item = items[to_index]
    for item in (from_item, to_item):
        if item.get("deleted_at") is not None:
            raise _StaleJunction("Recording was deleted")
        if not item.get("recording_hash_prefix") or not item.get("recording_content_hash"):
            raise _StaleJunction("Recording is unavailable")
    if not _timestamps_match(job.expected_from_updated_at, from_item.get("updated_at")):
        raise _StaleJunction("Outgoing item changed")
    if not _timestamps_match(job.expected_to_updated_at, to_item.get("updated_at")):
        raise _StaleJunction("Incoming item changed")

    tempo = to_item.get("tempo_bpm")
    if not isinstance(tempo, (int, float)) or isinstance(tempo, bool) or tempo <= 0:
        raise _StaleJunction("Incoming tempo analysis is unavailable")
    beats = from_item.get("glue_beats") or 4
    try:
        from_steps = canonical_steps_from_arrangement(
            from_item.get("arrangement_steps"),
            from_item.get("sections"),
            from_item.get("beats"),
            from_item.get("duration_seconds"),
        )
        to_steps = canonical_steps_from_arrangement(
            to_item.get("arrangement_steps"),
            to_item.get("sections"),
            to_item.get("beats"),
            to_item.get("duration_seconds"),
        )
        descriptor, canonical, key = build_descriptor(
            beats=beats,
            to_tempo_bpm=float(tempo),
            from_side={
                "contentHash": from_item["recording_content_hash"],
                "steps": from_steps,
            },
            to_side={"contentHash": to_item["recording_content_hash"], "steps": to_steps},
        )
    except (ValueError, TypeError) as exc:
        raise _StaleJunction("Saved arrangement or tempo analysis is stale") from exc
    return _JunctionInputs(
        from_item=from_item,
        to_item=to_item,
        from_steps=from_steps,
        to_steps=to_steps,
        descriptor=descriptor,
        canonical_json=canonical,
        descriptor_key=key,
    )


# ---------------------------------------------------------------------------
# Provider seam
# ---------------------------------------------------------------------------


def _load_provider_stage(*, lambda_context: Any = None) -> Callable[..., None]:
    """Lazily build the production provider stage (issue #317).

    The factory gets the Lambda context so the HTTP transport can cap its
    read timeouts to the actual remaining budget; it returns the stage
    callable with the spine's exact ``provider_stage`` signature.
    """
    try:
        from sow_render_worker.glue_provider import create_provider_stage
    except (ImportError, AttributeError) as exc:
        raise GlueConfigurationError(
            "Glue provider stage is not available in this worker build"
        ) from exc
    return create_provider_stage(lambda_context=lambda_context)


def _provider_key_present() -> bool:
    return bool(os.environ.get("SOW_ELEVENLABS_API_KEY"))


def _resolve_provider_stage(
    provider_stage: Optional[Callable[..., None]],
    *,
    lambda_context: Any = None,
) -> Callable[..., None]:
    """Injected fake, else the production stage plus a configured key."""
    if provider_stage is not None:
        return provider_stage
    if not _provider_key_present():
        raise GlueConfigurationError("SOW_ELEVENLABS_API_KEY is not configured")
    return _load_provider_stage(lambda_context=lambda_context)


def _load_staging_stage() -> Callable[..., None]:
    """Lazily import the free staging recovery stage (issue #318)."""
    from sow_render_worker.glue_staging import finalize_staging

    return finalize_staging


def _make_work_dir(job: GlueJob) -> Path:
    return Path(tempfile.mkdtemp(prefix=f"sow-glue-{job.id[:8]}-"))


def _current_provider_contacted(conn: Any, job: GlueJob) -> bool:
    """Read the live provider_contacted flag; the claimed row may be stale."""
    try:
        current = get_glue_job(conn, job.id, job.user_id)
    except Exception:
        logger.exception("Failed to read Glue provider contact state")
        return bool(job.provider_contacted)
    if current is None:
        return bool(job.provider_contacted)
    return bool(current.provider_contacted)


def _has_durable_provider_success(job: GlueJob) -> bool:
    return bool(job.raw_response_r2_key and job.raw_response_sha256 and job.raw_response_bytes)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def execute_glue_job(
    job_id: str,
    user_id: int,
    conn: Any,
    lambda_context: Any = None,
    mode: str = "generate",
    provider_stage: Optional[Callable[..., None]] = None,
    asset_fetcher: Any = None,
) -> None:
    """Run one Glue job delivery (see the module docstring for call order)."""
    if mode not in ("generate", "staging"):
        raise ValueError(f"Unknown Glue job mode: {mode!r}")

    log = _job_logger(job_id, user_id)
    log("glue.job.invoked", mode=mode, remainingMs=_remaining_ms(lambda_context))

    job, claimed_now = _free_retry(
        lambda: claim_glue_job(conn, job_id, user_id),
        label="claim",
        log=log,
    )
    if job is None:
        log("glue.job.missing", level="warning", status="unknown-job")
        return
    if job.phase in GLUE_TERMINAL_PHASES:
        log("glue.job.redelivered", phase=job.phase, status="terminal")
        return
    # claim_glue_job preserves an in-flight provider phase for a stale
    # heartbeat; that is the ambiguous-redelivery case, never a resume.
    if job.phase in _IN_FLIGHT_PROVIDER_PHASES:
        _handle_redelivery(conn, job, log)
        return
    if job.phase in ("storing-response", "staging"):
        # A durable post-provider phase is free staging recovery, never a
        # second provider request (issue #318 fills the staging body).
        _run_staging_mode(conn, job, log, asset_fetcher=asset_fetcher)
        return
    if not claimed_now:
        # An explicit retry-processing delivery may run free staging recovery
        # even when another delivery set the phase milliseconds ago; it never
        # calls the provider and #318's staging transaction is idempotent.
        if mode == "staging" and job.phase in ("storing-response", "staging"):
            log("glue.job.redelivered", phase=job.phase, status="explicit-staging")
        else:
            log("glue.job.redelivered", phase=job.phase, status="in-progress")
            return

    log(
        "glue.job.claimed",
        phase=job.phase,
        fromItemId=job.from_item_id,
        toItemId=job.to_item_id,
        descriptorKey=job.descriptor_key,
        attemptId=job.attempt_id,
    )
    if mode == "staging":
        _run_staging_mode(conn, job, log, asset_fetcher=asset_fetcher)
        return
    _run_generate_mode(
        conn,
        job,
        log,
        lambda_context=lambda_context,
        provider_stage=provider_stage,
        asset_fetcher=asset_fetcher,
    )


def _handle_redelivery(conn: Any, job: GlueJob, log: Callable[..., None]) -> None:
    """Duplicate delivery: never redo work or repeat a provider request."""
    if job.phase in GLUE_TERMINAL_PHASES:
        log("glue.job.redelivered", phase=job.phase, status="terminal")
        return
    if job.phase in _IN_FLIGHT_PROVIDER_PHASES:
        if _has_durable_provider_success(job):
            log(
                "glue.job.redelivered",
                phase=job.phase,
                status="durable-response",
                reason="free-staging-recovery",
            )
            return
        _finish_terminal(
            conn,
            job,
            phase="failed",
            code=GLUE_PROVIDER_UNKNOWN,
            retry_kind="paid",
            detail="Provider outcome is unknown; a new attempt may be charged",
            release=False,
            log=log,
            event="glue.provider.ambiguous",
        )
        return
    log("glue.job.redelivered", phase=job.phase, status="in-progress")


def _run_generate_mode(
    conn: Any,
    job: GlueJob,
    log: Callable[..., None],
    *,
    lambda_context: Any,
    provider_stage: Optional[Callable[..., None]],
    asset_fetcher: Any,
) -> None:
    contacted = False
    work_dir: Optional[Path] = None
    try:
        items = _free_retry(
            lambda: read_songset_items_for_glue(conn, job.songset_id),
            label="read_items",
            log=log,
        )
        _heartbeat(conn, job, log, stage="preparing")

        try:
            inputs = _rebuild_junction(items, job)
        except _StaleJunction as exc:
            _finish_stale(conn, job, log, reason=str(exc))
            return
        if (
            inputs.descriptor_key != job.descriptor_key
            or inputs.canonical_json != job.descriptor_json
        ):
            _finish_stale(conn, job, log, reason="Descriptor changed")
            return
        _heartbeat(conn, job, log, stage="validated")

        stage = _resolve_provider_stage(provider_stage, lambda_context=lambda_context)
        if asset_fetcher is None:
            asset_fetcher = AssetFetcher()
            initializer = getattr(asset_fetcher, "initialize", None)
            if callable(initializer):
                initializer()

        work_dir = _make_work_dir(job)
        a_excerpt = _prepare_side(
            conn, job, inputs, side="A", asset_fetcher=asset_fetcher,
            work_dir=work_dir, log=log,
        )
        _heartbeat(conn, job, log, stage="excerpt-a")
        b_excerpt = _prepare_side(
            conn, job, inputs, side="B", asset_fetcher=asset_fetcher,
            work_dir=work_dir, log=log,
        )
        _heartbeat(conn, job, log, stage="excerpt-b")

        require_deadline(lambda_context, "upload")
        _heartbeat(conn, job, log, stage="deadline-gate")

        # A resumed job (free retry of a definitely-unsent failure) already won
        # this compare-and-set on its earlier delivery; its reservation is
        # consumed and its persisted uploads are reusable. The CAS is a
        # cancellation gate, not a per-delivery token, so re-losing it on a
        # row that is already contacted would wrongly cancel paid work.
        already_contacted = bool(_current_provider_contacted(conn, job))
        if already_contacted:
            contacted = True
            log(
                "glue.provider.contact.reused",
                phase=job.phase,
                providerStatus=job.provider_status,
                status="resume",
            )
        elif not record_provider_contact(
            conn, job.id, job.user_id, _FIRST_UPLOAD_PROVIDER_PHASE
        ):
            _finish_terminal(
                conn,
                job,
                phase="cancelled",
                code=None,
                retry_kind=None,
                detail=None,
                release=True,
                log=log,
                event="glue.job.cancelled",
            )
            return
        else:
            contacted = True
            # Exactly one event per paid attempt: this is the moment the
            # reservation is consumed and provider_contacted flips true.
            log(
                "glue.provider.contact.started",
                phase=job.phase,
                attemptId=job.attempt_id,
                operation="provider",
            )
        _heartbeat(conn, job, log, stage="uploading")

        stage(
            job,
            conn,
            a_excerpt=a_excerpt,
            b_excerpt=b_excerpt,
            work_dir=work_dir,
            log_event=log,
        )
        _after_provider_stage(
            conn,
            job,
            log,
            work_dir=work_dir,
            asset_fetcher=asset_fetcher,
        )
    except GlueConfigurationError:
        raise
    except Exception as exc:
        _handle_failure(conn, job, exc, contacted=contacted, log=log)
    finally:
        if work_dir is not None:
            shutil.rmtree(work_dir, ignore_errors=True)


def _prepare_side(
    conn: Any,
    job: GlueJob,
    inputs: _JunctionInputs,
    *,
    side: str,
    asset_fetcher: Any,
    work_dir: Path,
    log: Callable[..., None],
) -> PreparedExcerpt:
    item = inputs.from_item if side == "A" else inputs.to_item
    steps = inputs.from_steps if side == "A" else inputs.to_steps
    return _free_retry(
        lambda: prepare_excerpt(
            conn=conn,
            user_id=job.user_id,
            side=side,
            recording_hash_prefix=item["recording_hash_prefix"],
            content_hash=item["recording_content_hash"],
            steps=steps,
            asset_fetcher=asset_fetcher,
            work_dir=work_dir,
        ),
        label=f"prepare_excerpt_{side}",
        log=log,
    )


def _after_provider_stage(
    conn: Any,
    job: GlueJob,
    log: Callable[..., None],
    *,
    work_dir: Optional[Path] = None,
    asset_fetcher: Any = None,
) -> None:
    """Boundary after the provider stage: heartbeat, then free staging.

    The provider stage (issue #317) leaves the job in ``storing-response`` with
    the raw response persisted. Issue #318 continues from there into staging
    via ``glue_staging.finalize_staging``. Until that module exists in this
    worker build the job simply stops at ``storing-response`` with a live
    heartbeat: absent staging is never a job failure, because retry-processing
    can resume it later for free.
    """
    _heartbeat(conn, job, log, stage="storing-response")
    try:
        current = get_glue_job(conn, job.id, job.user_id)
    except Exception:
        logger.exception("Failed to read Glue job after the provider stage")
        return
    if current is None:
        return
    if current.phase in _IN_FLIGHT_PROVIDER_PHASES:
        if _has_durable_provider_success(current):
            # The stage persisted and recorded the raw response but could not
            # advance the phase (for example, the row went inactive for one
            # statement). Durable bytes are the only authorization for free
            # recovery, so route to staging instead of declaring ambiguity.
            log(
                "glue.response.persisted",
                phase=current.phase,
                status="durable-response",
            )
            if set_glue_phase(conn, job.id, job.user_id, "storing-response") is None:
                log("glue.staging.unavailable", level="warning", phase=current.phase)
                return
            current = get_glue_job(conn, job.id, job.user_id)
            if current is None or current.phase not in ("storing-response", "staging"):
                return
        else:
            # The stage returned without a durable success record: a request may
            # have been sent, so never repeat it automatically.
            _finish_terminal(
                conn,
                job,
                phase="failed",
                code=GLUE_PROVIDER_UNKNOWN,
                retry_kind="paid",
                detail="Provider stage returned without a durable response",
                release=False,
                log=log,
                event="glue.provider.ambiguous",
            )
            return
    log("glue.provider.stage.complete", phase=current.phase, providerStatus=current.provider_status)
    if current.phase not in ("storing-response", "staging"):
        return

    # Free staging continuation. The module is issue #318's; until it exists in
    # this worker build the job stops here with a live heartbeat and remains
    # recoverable by id through retry-processing — never a failure.
    try:
        stage = _load_staging_stage()
    except (ImportError, AttributeError):
        _heartbeat(conn, job, log, stage="storing-response")
        log(
            "glue.staging.unavailable",
            level="warning",
            phase="storing-response",
            status="free-recovery-available",
        )
        return
    try:
        # Hand staging the fresh row: it carries the persisted raw-response
        # key/checksum/byte count that authorizes free recovery.
        stage(
            conn,
            current,
            log,
            work_dir=work_dir,
            asset_fetcher=asset_fetcher,
        )
    except GlueConfigurationError:
        raise
    except Exception as exc:
        # Staging is free recovery: never paid, and a consumed reservation is
        # never refunded by the guarded release path.
        _handle_failure(conn, job, exc, contacted=False, log=log)


def _finish_stale(conn: Any, job: GlueJob, log: Callable[..., None], *, reason: str) -> None:
    _finish_terminal(
        conn,
        job,
        phase="cancelled",
        code=GLUE_INPUTS_CHANGED,
        retry_kind="none",
        detail=reason,
        release=not job.provider_contacted,
        log=log,
        event="glue.job.stale",
    )


def _run_staging_mode(
    conn: Any,
    job: GlueJob,
    log: Callable[..., None],
    *,
    asset_fetcher: Any = None,
) -> None:
    """Free staging recovery entry: never calls the provider stage."""
    contacted = bool(job.provider_contacted)
    try:
        if not _has_durable_provider_success(job):
            _finish_terminal(
                conn,
                job,
                phase="failed",
                code=GLUE_PROVIDER_UNKNOWN,
                retry_kind="paid",
                detail="No verified provider response is persisted",
                release=False,
                log=log,
                event="glue.provider.ambiguous",
            )
            return

        _free_retry(
            lambda: set_glue_phase(conn, job.id, job.user_id, "staging"),
            label="enter_staging",
            log=log,
        )
        _heartbeat(conn, job, log, stage="staging")

        try:
            stage = _load_staging_stage()
        except (ImportError, AttributeError):
            logger.exception("Glue staging stage is not available")
            _finish_terminal(
                conn,
                job,
                phase="failed",
                code=GLUE_WORKER_FAILED,
                retry_kind="free",
                detail="Staging recovery is unavailable",
                release=False,
                log=log,
                event="glue.job.failed",
            )
            return

        work_dir = _make_work_dir(job)
        try:
            stage(conn, job, log, work_dir=work_dir, asset_fetcher=asset_fetcher)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)
        log("glue.staging.recovered", phase="staging")
    except GlueConfigurationError:
        raise
    except Exception as exc:
        _handle_failure(conn, job, exc, contacted=contacted, log=log)
