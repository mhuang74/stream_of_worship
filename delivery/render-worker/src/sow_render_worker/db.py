import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)


TOTAL_PHASES = 5

PHASE_ORDER = [
    "preparing",
    "mixing_audio",
    "rendering_frames",
    "encoding_video",
    "uploading",
]

ORPHANED_JOB_THRESHOLD_MINUTES = 15

STALE_JOB_THRESHOLD_SECONDS = 120

LIKELY_DEAD_THRESHOLD_SECONDS = 60

VALID_FONT_FAMILIES = {
    "lxgw_wenkai_tc",
    "chocolate_classical_sans",
    "chiron_goround_tc",
    "noto_serif_tc",
}


def _normalize_font_family(value: object) -> str:
    if isinstance(value, str) and value in VALID_FONT_FAMILIES:
        return value
    if value is not None:
        logger.warning("Unknown font_family=%r; falling back to noto_serif_tc", value)
    return "noto_serif_tc"


@dataclass
class RenderJob:
    id: str
    songset_id: str
    user_id: int
    status: str
    phase: Optional[str] = None
    phase_index: Optional[int] = None
    total_phases: Optional[int] = None
    percent_complete: float = 0.0
    estimated_seconds_left: Optional[float] = None
    elapsed_seconds: Optional[float] = None
    error_message: Optional[str] = None
    estimated_total_seconds: Optional[float] = None
    total_duration_seconds: Optional[float] = None
    started_at: Optional[datetime] = None
    template: str = "dark"
    resolution: str = "720p"
    audio_enabled: bool = True
    video_enabled: bool = True
    font_size_preset: str = "M"
    font_family: str = "noto_serif_tc"
    include_title_card: bool = False
    title_card_duration_seconds: Optional[float] = None
    title_card_lines: Optional[list[str]] = None
    mp3_r2_key: Optional[str] = None
    mp4_r2_key: Optional[str] = None
    chapters_r2_key: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


@dataclass
class RenderProgress:
    phase: Optional[str] = None
    phase_index: Optional[int] = None
    total_phases: Optional[int] = None
    estimated_total_seconds: Optional[float] = None
    total_duration_seconds: Optional[float] = None
    started_at: Optional[datetime] = None
    elapsed_seconds: Optional[float] = None
    percent_complete: Optional[float] = None
    estimated_seconds_left: Optional[float] = None


# ---------------------------------------------------------------------------
# Glue (AI transition) job / take / excerpt models — ADR-0014
#
# JSON text columns are exposed verbatim as str: the worker owns canonical
# serialization (glue_descriptor) and must never re-encode stored text.
# ---------------------------------------------------------------------------

GLUE_ACTIVE_PHASES = (
    "queued",
    "preparing",
    "uploading",
    "composing",
    "storing-response",
    "staging",
)

GLUE_TERMINAL_PHASES = ("ready", "failed", "cancelled", "dispatch-failed")

# The two phases where the paid response is already stored and only free local
# decoding/QA remains. Staging may finish from either one.
GLUE_STAGING_PHASES = ("storing-response", "staging")

# A duplicate queue delivery during an in-flight phase must not redo provider
# work. Only an absent or stale heartbeat makes a non-queued active job
# claimable again; the phase itself is preserved so execute_glue_job can apply
# the ambiguous-redelivery rule instead of calling the provider.
GLUE_CLAIM_STALE_SECONDS = 120


def _uuid_str(value: Any) -> Optional[str]:
    """Normalize psycopg2 uuid.UUID values (and None) to plain text."""
    if value is None:
        return None
    return str(value)


@dataclass
class GlueJob:
    id: str
    user_id: int
    songset_id: str
    from_item_id: str
    to_item_id: str
    client_request_id: str
    take_mode: str
    descriptor_key: str
    descriptor_json: str
    phase: str
    active: bool
    dispatch_state: str
    attempt_id: str
    pending_take_id: str
    take_ordinal: int
    seed: int
    expected_from_updated_at: Optional[datetime] = None
    expected_to_updated_at: Optional[datetime] = None
    dispatch_attempt: int = 0
    paid_reservation_id: Optional[str] = None
    paid_reservation_state: Optional[str] = None
    provider_contacted: bool = False
    provider_phase: Optional[str] = None
    provider_status: Optional[int] = None
    retry_kind: Optional[str] = None
    resulting_take_id: Optional[str] = None
    active_take_id: Optional[str] = None
    raw_response_r2_key: Optional[str] = None
    raw_response_sha256: Optional[str] = None
    raw_response_bytes: Optional[int] = None
    error_code: Optional[str] = None
    error_detail: Optional[str] = None
    heartbeat_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None


@dataclass
class GlueTake:
    id: str
    user_id: int
    descriptor_key: str
    descriptor_json: str
    ordinal: int
    seed: int
    status: str
    r2_key: Optional[str] = None
    seconds: Optional[float] = None
    raw_response_r2_key: Optional[str] = None
    raw_response_sha256: Optional[str] = None
    raw_response_bytes: Optional[int] = None
    metrics_json: str = "{}"
    reasons_json: str = "[]"
    error_code: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class GlueExcerpt:
    user_id: int
    logical_key: str
    side: str
    seconds: float
    content_hash: str
    phase: str
    version: int = 2
    canonical_steps_json: Optional[str] = None
    audio_sha256: Optional[str] = None
    elevenlabs_song_id: Optional[str] = None
    rejection_code: Optional[str] = None
    safe_failure_detail: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


def _row_to_glue_job(row: dict[str, Any]) -> GlueJob:
    return GlueJob(
        id=_uuid_str(row["id"]) or "",
        user_id=row["user_id"],
        songset_id=row["songset_id"],
        from_item_id=row["from_item_id"],
        to_item_id=row["to_item_id"],
        client_request_id=_uuid_str(row["client_request_id"]) or "",
        take_mode=row["take_mode"],
        descriptor_key=row["descriptor_key"],
        descriptor_json=row["descriptor_json"],
        phase=row["phase"],
        active=row["active"],
        dispatch_state=row["dispatch_state"],
        attempt_id=_uuid_str(row["attempt_id"]) or "",
        pending_take_id=_uuid_str(row["pending_take_id"]) or "",
        take_ordinal=row["take_ordinal"],
        seed=row["seed"],
        expected_from_updated_at=row.get("expected_from_updated_at"),
        expected_to_updated_at=row.get("expected_to_updated_at"),
        dispatch_attempt=row.get("dispatch_attempt") or 0,
        paid_reservation_id=_uuid_str(row.get("paid_reservation_id")),
        paid_reservation_state=row.get("paid_reservation_state"),
        provider_contacted=row.get("provider_contacted", False),
        provider_phase=row.get("provider_phase"),
        provider_status=row.get("provider_status"),
        retry_kind=row.get("retry_kind"),
        resulting_take_id=_uuid_str(row.get("resulting_take_id")),
        active_take_id=_uuid_str(row.get("active_take_id")),
        raw_response_r2_key=row.get("raw_response_r2_key"),
        raw_response_sha256=row.get("raw_response_sha256"),
        raw_response_bytes=row.get("raw_response_bytes"),
        error_code=row.get("error_code"),
        error_detail=row.get("error_detail"),
        heartbeat_at=row.get("heartbeat_at"),
        created_at=row.get("created_at"),
        started_at=row.get("started_at"),
        updated_at=row.get("updated_at"),
        finished_at=row.get("finished_at"),
    )


def _row_to_glue_take(row: dict[str, Any]) -> GlueTake:
    return GlueTake(
        id=_uuid_str(row["id"]) or "",
        user_id=row["user_id"],
        descriptor_key=row["descriptor_key"],
        descriptor_json=row["descriptor_json"],
        ordinal=row["ordinal"],
        seed=row["seed"],
        status=row["status"],
        r2_key=row.get("r2_key"),
        seconds=row.get("seconds"),
        raw_response_r2_key=row.get("raw_response_r2_key"),
        raw_response_sha256=row.get("raw_response_sha256"),
        raw_response_bytes=row.get("raw_response_bytes"),
        metrics_json=row.get("metrics_json") or "{}",
        reasons_json=row.get("reasons_json") or "[]",
        error_code=row.get("error_code"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _row_to_glue_excerpt(row: dict[str, Any]) -> GlueExcerpt:
    return GlueExcerpt(
        user_id=row["user_id"],
        logical_key=row["logical_key"],
        side=row["side"],
        seconds=row["seconds"],
        content_hash=row["content_hash"],
        phase=row["phase"],
        version=row.get("version") or 2,
        canonical_steps_json=row.get("canonical_steps_json"),
        audio_sha256=row.get("audio_sha256"),
        elevenlabs_song_id=row.get("elevenlabs_song_id"),
        rejection_code=row.get("rejection_code"),
        safe_failure_detail=row.get("safe_failure_detail"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def get_phase_index(phase: str) -> int:
    if phase == "completed":
        return TOTAL_PHASES
    try:
        return PHASE_ORDER.index(phase)
    except ValueError:
        return -1


def _row_to_render_job(row: dict[str, Any]) -> RenderJob:
    title_card_lines_raw = row.get("title_card_lines")
    title_card_lines = None
    if title_card_lines_raw:
        try:
            parsed = json.loads(title_card_lines_raw)
            title_card_lines = parsed if parsed else None
        except json.JSONDecodeError:
            logger.warning("Failed to parse title_card_lines JSON: %s", title_card_lines_raw)
            title_card_lines = None

    return RenderJob(
        id=row["id"],
        songset_id=row["songset_id"],
        user_id=row["user_id"],
        status=row["status"],
        phase=row.get("phase"),
        phase_index=row.get("phase_index"),
        total_phases=row.get("total_phases"),
        percent_complete=row.get("percent_complete") or 0.0,
        estimated_seconds_left=row.get("estimated_seconds_left"),
        elapsed_seconds=row.get("elapsed_seconds"),
        error_message=row.get("error_message"),
        estimated_total_seconds=row.get("estimated_total_seconds"),
        total_duration_seconds=row.get("total_duration_seconds"),
        started_at=row.get("started_at"),
        template=row.get("template") or "dark",
        resolution=row.get("resolution") or "720p",
        audio_enabled=row.get("audio_enabled", True),
        video_enabled=row.get("video_enabled", True),
        font_size_preset=row.get("font_size_preset") or "M",
        font_family=_normalize_font_family(row.get("font_family")),
        include_title_card=row.get("include_title_card", False),
        title_card_duration_seconds=row.get("title_card_duration_seconds"),
        title_card_lines=title_card_lines,
        mp3_r2_key=row.get("mp3_r2_key"),
        mp4_r2_key=row.get("mp4_r2_key"),
        chapters_r2_key=row.get("chapters_r2_key"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
        completed_at=row.get("completed_at"),
    )


def get_connection(database_url: Optional[str] = None) -> psycopg2.extensions.connection:
    url = database_url or os.environ.get("SOW_DATABASE_URL")
    if not url:
        raise ValueError("SOW_DATABASE_URL is required")
    conn = psycopg2.connect(
        url,
        keepalives=1,
        keepalives_idle=60,
        keepalives_interval=10,
        keepalives_count=5,
    )
    conn.autocommit = True
    return conn


def get_render_job(
    conn: psycopg2.extensions.connection,
    job_id: str,
    user_id: int,
) -> Optional[RenderJob]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM render_jobs WHERE id = %s AND user_id = %s",
            (job_id, user_id),
        )
        row = cur.fetchone()
    if not row:
        return None
    return _row_to_render_job(row)


def start_render_job(
    conn: psycopg2.extensions.connection,
    job_id: str,
    user_id: int,
) -> Optional[RenderJob]:
    now = datetime.now(timezone.utc)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "UPDATE render_jobs SET status = %s, started_at = COALESCE(started_at, %s), updated_at = %s "
            "WHERE id = %s AND user_id = %s AND status = %s "
            "RETURNING *",
            ("running", now, now, job_id, user_id, "queued"),
        )
        row = cur.fetchone()
    if not row:
        return None
    return _row_to_render_job(row)


def reclaim_stale_job(
    conn: psycopg2.extensions.connection,
    job_id: str,
    user_id: int,
    stale_threshold_seconds: int = STALE_JOB_THRESHOLD_SECONDS,
) -> Optional[RenderJob]:
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(seconds=stale_threshold_seconds)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM render_jobs WHERE id = %s AND user_id = %s AND status = %s",
            (job_id, user_id, "running"),
        )
        row = cur.fetchone()
        if not row:
            return None

        job = _row_to_render_job(row)
        if job.updated_at is None:
            return None

        aware_updated = (
            job.updated_at.replace(tzinfo=timezone.utc)
            if job.updated_at.tzinfo is None
            else job.updated_at
        )
        if aware_updated >= threshold:
            return None

        cur.execute(
            "UPDATE render_jobs "
            "SET status = %s, error_message = %s, updated_at = %s, "
            "    started_at = NULL, phase = %s, phase_index = %s, percent_complete = %s "
            "WHERE id = %s AND user_id = %s AND status = %s "
            "RETURNING *",
            ("queued", None, now, "preparing", 0, 0, job_id, user_id, "running"),
        )
        row = cur.fetchone()

    if not row:
        return None
    return _row_to_render_job(row)


def reclaim_likely_dead_job(
    conn: psycopg2.extensions.connection,
    job_id: str,
    user_id: int,
) -> Optional[RenderJob]:
    return reclaim_stale_job(conn, job_id, user_id, stale_threshold_seconds=LIKELY_DEAD_THRESHOLD_SECONDS)


def update_render_progress(
    conn: psycopg2.extensions.connection,
    job_id: str,
    user_id: int,
    progress: RenderProgress,
) -> Optional[RenderJob]:
    now = datetime.now(timezone.utc)
    updates: list[str] = []
    params: list[Any] = []

    if progress.phase is not None:
        updates.append("phase = %s")
        params.append(progress.phase)
        updates.append("phase_index = %s")
        params.append(get_phase_index(progress.phase))

    if progress.estimated_total_seconds is not None:
        updates.append("estimated_total_seconds = %s")
        params.append(progress.estimated_total_seconds)

    if progress.total_duration_seconds is not None:
        updates.append("total_duration_seconds = %s")
        params.append(progress.total_duration_seconds)

    if progress.started_at is not None:
        updates.append("started_at = %s")
        params.append(progress.started_at)

    if progress.elapsed_seconds is not None:
        updates.append("elapsed_seconds = %s")
        params.append(progress.elapsed_seconds)

    if progress.percent_complete is not None:
        updates.append("percent_complete = %s")
        params.append(progress.percent_complete)

    if progress.estimated_seconds_left is not None:
        updates.append("estimated_seconds_left = %s")
        params.append(progress.estimated_seconds_left)

    if not updates:
        return get_render_job(conn, job_id, user_id)

    updates.append("updated_at = %s")
    params.append(now)

    params.extend([job_id, user_id])

    sql = f"UPDATE render_jobs SET {', '.join(updates)} WHERE id = %s AND user_id = %s AND status = 'running' RETURNING *"

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    if not row:
        return None

    logger.debug(
        "Progress update for job %s: phase=%s (%d/%d), elapsed=%.1fs, percent=%.1f%%, est_remaining=%s",
        job_id,
        progress.phase,
        progress.phase_index if progress.phase_index is not None else get_phase_index(progress.phase or ""),
        progress.total_phases,
        progress.elapsed_seconds or 0,
        progress.percent_complete or 0,
        f"{progress.estimated_seconds_left:.0f}s" if progress.estimated_seconds_left is not None else "N/A",
    )

    return _row_to_render_job(row)


def complete_render_job(
    conn: psycopg2.extensions.connection,
    job_id: str,
    user_id: int,
    mp3_r2_key: Optional[str] = None,
    mp4_r2_key: Optional[str] = None,
    chapters_r2_key: Optional[str] = None,
) -> Optional[RenderJob]:
    now = datetime.now(timezone.utc)

    job = get_render_job(conn, job_id, user_id)
    if not job:
        return None

    final_elapsed_seconds = None
    if job.started_at and now:
        aware_started = (
            job.started_at.replace(tzinfo=timezone.utc)
            if job.started_at.tzinfo is None
            else job.started_at
        )
        delta = now - aware_started
        final_elapsed_seconds = delta.total_seconds()

    original_autocommit = conn.autocommit
    try:
        conn.autocommit = False
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE render_jobs SET "
                "  status = %s, phase = %s, phase_index = %s, percent_complete = %s, "
                "  elapsed_seconds = %s, "
                "  mp3_r2_key = %s, mp4_r2_key = %s, chapters_r2_key = %s, "
                "  completed_at = %s, updated_at = %s "
                "WHERE id = %s AND user_id = %s AND status = %s "
                "RETURNING *",
                (
                    "completed",
                    "completed",
                    TOTAL_PHASES,
                    100,
                    final_elapsed_seconds,
                    mp3_r2_key,
                    mp4_r2_key,
                    chapters_r2_key,
                    now,
                    now,
                    job_id,
                    user_id,
                    "running",
                ),
            )
            row = cur.fetchone()
        if not row:
            conn.rollback()
            return None
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE songsets SET last_completed_render_job_id = %s, updated_at = %s "
                "WHERE id = %s",
                (job_id, now, row["songset_id"]),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.autocommit = original_autocommit
    return _row_to_render_job(row)


def fail_render_job(
    conn: psycopg2.extensions.connection,
    job_id: str,
    user_id: int,
    error_message: str,
) -> Optional[RenderJob]:
    now = datetime.now(timezone.utc)
    original_autocommit = conn.autocommit
    try:
        conn.autocommit = False
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE render_jobs SET status = %s, error_message = %s, updated_at = %s "
                "WHERE id = %s AND user_id = %s AND status IN %s "
                "RETURNING *",
                ("failed", error_message, now, job_id, user_id, ("running", "queued")),
            )
            row = cur.fetchone()
        if not row:
            conn.rollback()
            return None
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE songsets SET last_failed_render_job_id = %s, updated_at = %s "
                "WHERE id = %s",
                (job_id, now, row["songset_id"]),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.autocommit = original_autocommit
    return _row_to_render_job(row)


def recover_orphaned_jobs(
    conn: psycopg2.extensions.connection,
    threshold_minutes: int = ORPHANED_JOB_THRESHOLD_MINUTES,
) -> int:
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(minutes=threshold_minutes)

    original_autocommit = conn.autocommit
    try:
        conn.autocommit = False
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE render_jobs SET status = %s, error_message = %s, updated_at = %s "
                "WHERE status = %s AND updated_at < %s "
                "RETURNING id, songset_id",
                ("failed", f"Job timed out after {threshold_minutes} minutes without progress", now, "running", threshold),
            )
            affected = cur.fetchall()

        if affected:
            with conn.cursor() as cur:
                for row in affected:
                    cur.execute(
                        "UPDATE songsets SET last_failed_render_job_id = %s, updated_at = %s "
                        "WHERE id = %s",
                        (row["id"], now, row["songset_id"]),
                    )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.autocommit = original_autocommit

    return len(affected)


# ---------------------------------------------------------------------------
# Glue (AI transition) helpers — ADR-0014
# ---------------------------------------------------------------------------


def get_glue_job(
    conn: psycopg2.extensions.connection,
    job_id: str,
    user_id: int,
) -> Optional[GlueJob]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM glue_jobs WHERE id = %s AND user_id = %s",
            (job_id, user_id),
        )
        row = cur.fetchone()
    if not row:
        return None
    return _row_to_glue_job(row)


def claim_glue_job(
    conn: psycopg2.extensions.connection,
    job_id: str,
    user_id: int,
) -> tuple[Optional[GlueJob], bool]:
    """Atomically claim a dispatchable Glue job for this delivery.

    A queued job transitions to ``preparing``. A job already in an in-flight
    active phase keeps its phase: only an absent/stale heartbeat makes it
    claimable again, so a duplicate delivery during upload/compose returns
    ``claimed_now=False`` and the caller must read the stored durable state
    instead of redoing provider work.

    Returns ``(job, claimed_now)``; ``job`` is ``None`` only when no row
    matches the owner/job pair.
    """
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(seconds=GLUE_CLAIM_STALE_SECONDS)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "UPDATE glue_jobs SET "
            "phase = CASE WHEN phase = %s THEN %s ELSE phase END, "
            "started_at = COALESCE(started_at, %s), heartbeat_at = %s, updated_at = %s "
            "WHERE id = %s AND user_id = %s AND phase = ANY(%s) "
            "AND (heartbeat_at IS NULL OR heartbeat_at < %s) "
            "RETURNING *",
            (
                "queued",
                "preparing",
                now,
                now,
                now,
                job_id,
                user_id,
                list(GLUE_ACTIVE_PHASES),
                stale_before,
            ),
        )
        row = cur.fetchone()
        if row:
            return _row_to_glue_job(row), True
        cur.execute(
            "SELECT * FROM glue_jobs WHERE id = %s AND user_id = %s",
            (job_id, user_id),
        )
        row = cur.fetchone()
    if not row:
        return None, False
    return _row_to_glue_job(row), False


# Columns a worker may set through set_glue_phase. Anything else (id, owner,
# immutable take identity, descriptor) is a programming error, not data.
GLUE_JOB_MUTABLE_FIELDS = frozenset(
    {
        "dispatch_state",
        "dispatch_attempt",
        "attempt_id",
        "paid_reservation_id",
        "paid_reservation_state",
        "provider_contacted",
        "provider_phase",
        "provider_status",
        "retry_kind",
        "resulting_take_id",
        "active_take_id",
        "raw_response_r2_key",
        "raw_response_sha256",
        "raw_response_bytes",
        "error_code",
        "error_detail",
        "heartbeat_at",
        "started_at",
        "finished_at",
    }
)


def set_glue_phase(
    conn: psycopg2.extensions.connection,
    job_id: str,
    user_id: int,
    phase: str,
    *,
    active: Optional[bool] = None,
    **fields: Any,
) -> Optional[GlueJob]:
    """Guarded phase/progress update; returns the updated job or None.

    Only an active job can move through non-terminal phases. Terminal phases
    must clear ``active`` in the same statement (use finish_glue_job), which
    the database enforces with glue_jobs_active_phase_check.
    """
    unknown = set(fields) - GLUE_JOB_MUTABLE_FIELDS
    if unknown:
        raise ValueError(f"Unknown glue_jobs fields: {sorted(unknown)}")

    now = datetime.now(timezone.utc)
    updates = ["phase = %s"]
    params: list[Any] = [phase]
    if active is not None:
        updates.append("active = %s")
        params.append(active)
    for name, value in fields.items():
        updates.append(f"{name} = %s")
        params.append(value)
    updates.append("updated_at = %s")
    params.append(now)
    params.extend([job_id, user_id])

    sql = (
        f"UPDATE glue_jobs SET {', '.join(updates)} "
        "WHERE id = %s AND user_id = %s AND active "
        "RETURNING *"
    )
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    if not row:
        return None
    return _row_to_glue_job(row)


def heartbeat_glue_job(
    conn: psycopg2.extensions.connection,
    job_id: str,
    user_id: int,
) -> bool:
    now = datetime.now(timezone.utc)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "UPDATE glue_jobs SET heartbeat_at = %s, updated_at = %s "
            "WHERE id = %s AND user_id = %s AND active "
            "RETURNING id",
            (now, now, job_id, user_id),
        )
        row = cur.fetchone()
    return row is not None


# Provider phases are stored in provider_phase; the job phase column only
# accepts the job phase vocabulary, so map to the owning job phase.
_GLUE_PROVIDER_JOB_PHASE = {
    "upload-a": "uploading",
    "upload-b": "uploading",
    "compose": "composing",
}


def record_provider_contact(
    conn: psycopg2.extensions.connection,
    job_id: str,
    user_id: int,
    provider_phase: str,
) -> bool:
    """Consume the reservation and record contact in one compare-and-set.

    This is the competing transition against cancellation: it succeeds only
    while ``provider_contacted`` is false and the job is not cancelled. Returns
    False when cancellation (or another worker attempt) won, in which case no
    reservation was consumed and the caller must not send a provider request.
    """
    job_phase = _GLUE_PROVIDER_JOB_PHASE.get(provider_phase)
    if job_phase is None:
        raise ValueError(f"Unknown provider phase: {provider_phase!r}")

    now = datetime.now(timezone.utc)
    original_autocommit = conn.autocommit
    try:
        conn.autocommit = False
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "UPDATE glue_jobs SET "
                "provider_contacted = true, paid_reservation_state = %s, "
                "phase = %s, provider_phase = %s, updated_at = %s "
                "WHERE id = %s AND user_id = %s "
                "AND provider_contacted = false AND phase <> %s AND active "
                "RETURNING id",
                ("consumed", job_phase, provider_phase, now, job_id, user_id, "cancelled"),
            )
            row = cur.fetchone()
        if not row:
            conn.rollback()
            return False
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE glue_paid_attempts SET state = %s, consumed_at = %s "
                "WHERE glue_job_id = %s AND user_id = %s",
                ("consumed", now, job_id, user_id),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.autocommit = original_autocommit
    return True


def record_glue_raw_response(
    conn: psycopg2.extensions.connection,
    job_id: str,
    user_id: int,
    *,
    r2_key: str,
    sha256: str,
    byte_count: int,
) -> Optional[GlueJob]:
    """Persist the immutable raw compose response metadata exactly once.

    The guard ``raw_response_r2_key IS NULL`` makes the write idempotent across
    retries and duplicate deliveries: an already stored response is never
    overwritten (raw objects are immutable, spec §2.2/§2.4). Returns the
    updated job, or None when the job is unknown, owned by someone else, or
    already has a recorded response.
    """
    if not r2_key:
        raise ValueError("record_glue_raw_response requires an r2_key")
    if not sha256:
        raise ValueError("record_glue_raw_response requires a sha256")
    if byte_count <= 0:
        raise ValueError("record_glue_raw_response requires a positive byte_count")
    now = datetime.now(timezone.utc)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "UPDATE glue_jobs SET "
            "raw_response_r2_key = %s, raw_response_sha256 = %s, "
            "raw_response_bytes = %s, updated_at = %s "
            "WHERE id = %s AND user_id = %s AND raw_response_r2_key IS NULL "
            "RETURNING *",
            (r2_key, sha256, byte_count, now, job_id, user_id),
        )
        row = cur.fetchone()
    if not row:
        return None
    return _row_to_glue_job(row)


def set_glue_job_dispatch_failed(
    conn: psycopg2.extensions.connection,
    job_id: str,
    user_id: int,
    *,
    attempt_id: Optional[str] = None,
) -> Optional[GlueJob]:
    """Mark a job dispatch-failed (free redispatch path).

    ``attempt_id`` guards against a late HTTP retry resetting a job a worker
    already claimed. The paid reservation is released by the webapp route in
    its own transaction; this helper only moves the job/terminal columns.
    """
    now = datetime.now(timezone.utc)
    sql = (
        "UPDATE glue_jobs SET "
        "phase = %s, active = %s, dispatch_state = %s, "
        "error_code = %s, retry_kind = %s, finished_at = %s, updated_at = %s "
        "WHERE id = %s AND user_id = %s AND phase = ANY(%s)"
    )
    params: list[Any] = [
        "dispatch-failed",
        False,
        "failed",
        "GLUE_DISPATCH_FAILED",
        "free",
        now,
        now,
        job_id,
        user_id,
        ["queued", "dispatch-failed"],
    ]
    if attempt_id is not None:
        sql += " AND attempt_id = %s"
        params.append(attempt_id)
    sql += " RETURNING *"

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    if not row:
        return None
    return _row_to_glue_job(row)


def finish_glue_job(
    conn: psycopg2.extensions.connection,
    job_id: str,
    user_id: int,
    *,
    phase: str,
    error_code: Optional[str] = None,
    error_detail: Optional[str] = None,
    retry_kind: Optional[str] = None,
    active: bool = False,
) -> Optional[GlueJob]:
    """Set a terminal phase and clear active in one statement."""
    now = datetime.now(timezone.utc)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "UPDATE glue_jobs SET "
            "phase = %s, active = %s, error_code = %s, error_detail = %s, "
            "retry_kind = %s, finished_at = %s, updated_at = %s "
            "WHERE id = %s AND user_id = %s "
            "RETURNING *",
            (
                phase,
                active,
                error_code,
                error_detail,
                retry_kind,
                now,
                now,
                job_id,
                user_id,
            ),
        )
        row = cur.fetchone()
    if not row:
        return None
    return _row_to_glue_job(row)


def insert_glue_take(
    conn: psycopg2.extensions.connection,
    *,
    take_id: str,
    user_id: int,
    descriptor_key: str,
    descriptor_json: str,
    ordinal: int,
    seed: int,
    status: str,
    r2_key: Optional[str] = None,
    seconds: Optional[float] = None,
    raw_response_r2_key: Optional[str] = None,
    raw_response_sha256: Optional[str] = None,
    raw_response_bytes: Optional[int] = None,
    metrics_json: str = "{}",
    reasons_json: str = "[]",
    error_code: Optional[str] = None,
) -> GlueTake:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "INSERT INTO glue_takes ("
            "id, user_id, descriptor_key, descriptor_json, ordinal, seed, status, "
            "r2_key, seconds, raw_response_r2_key, raw_response_sha256, raw_response_bytes, "
            "metrics_json, reasons_json, error_code"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING *",
            (
                take_id,
                user_id,
                descriptor_key,
                descriptor_json,
                ordinal,
                seed,
                status,
                r2_key,
                seconds,
                raw_response_r2_key,
                raw_response_sha256,
                raw_response_bytes,
                metrics_json,
                reasons_json,
                error_code,
            ),
        )
        row = cur.fetchone()
    return _row_to_glue_take(row)


def get_glue_take_for_descriptor(
    conn: psycopg2.extensions.connection,
    user_id: int,
    descriptor_key: str,
    *,
    take_id: Optional[str] = None,
    newest: bool = False,
) -> Optional[GlueTake]:
    """Fetch a ready take for one descriptor.

    ``take_id`` selects one exact take (validated against the same owner and
    descriptor). Otherwise the earliest ready take is returned by default
    (free earlier-take restore); ``newest=True`` returns the latest ready take
    for initial selection.
    """
    sql = (
        "SELECT * FROM glue_takes "
        "WHERE user_id = %s AND descriptor_key = %s AND status = %s"
    )
    params: list[Any] = [user_id, descriptor_key, "ready"]
    if take_id is not None:
        sql += " AND id = %s"
        params.append(take_id)
    order = "created_at DESC" if newest else "ordinal ASC"
    sql += f" ORDER BY {order} LIMIT 1"

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    if not row:
        return None
    return _row_to_glue_take(row)


def get_glue_excerpt(
    conn: psycopg2.extensions.connection,
    user_id: int,
    logical_key: str,
) -> Optional[GlueExcerpt]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM glue_excerpts WHERE user_id = %s AND logical_key = %s",
            (user_id, logical_key),
        )
        row = cur.fetchone()
    if not row:
        return None
    return _row_to_glue_excerpt(row)


def upsert_glue_excerpt(
    conn: psycopg2.extensions.connection,
    user_id: int,
    logical_key: str,
    *,
    side: str,
    seconds: float,
    content_hash: str,
    phase: str,
    version: int = 2,
    canonical_steps_json: Optional[str] = None,
    audio_sha256: Optional[str] = None,
    elevenlabs_song_id: Optional[str] = None,
    rejection_code: Optional[str] = None,
    safe_failure_detail: Optional[str] = None,
) -> GlueExcerpt:
    """Insert or advance one logical excerpt cache entry.

    Audit fields (canonical steps, audio checksum, provider song id) are never
    cleared by a later phase update; phase/rejection fields carry the newest
    state, including a copyright rejection as a successful negative entry.
    """
    now = datetime.now(timezone.utc)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "INSERT INTO glue_excerpts ("
            "user_id, logical_key, version, side, seconds, content_hash, "
            "canonical_steps_json, audio_sha256, elevenlabs_song_id, phase, "
            "rejection_code, safe_failure_detail, created_at, updated_at"
            ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (user_id, logical_key) DO UPDATE SET "
            "version = EXCLUDED.version, "
            "side = EXCLUDED.side, "
            "seconds = EXCLUDED.seconds, "
            "content_hash = EXCLUDED.content_hash, "
            "canonical_steps_json = COALESCE(EXCLUDED.canonical_steps_json, glue_excerpts.canonical_steps_json), "
            "audio_sha256 = COALESCE(EXCLUDED.audio_sha256, glue_excerpts.audio_sha256), "
            "elevenlabs_song_id = COALESCE(EXCLUDED.elevenlabs_song_id, glue_excerpts.elevenlabs_song_id), "
            "phase = EXCLUDED.phase, "
            "rejection_code = EXCLUDED.rejection_code, "
            "safe_failure_detail = EXCLUDED.safe_failure_detail, "
            "updated_at = %s "
            "RETURNING *",
            (
                user_id,
                logical_key,
                version,
                side,
                seconds,
                content_hash,
                canonical_steps_json,
                audio_sha256,
                elevenlabs_song_id,
                phase,
                rejection_code,
                safe_failure_detail,
                now,
                now,
                now,
            ),
        )
        row = cur.fetchone()
    return _row_to_glue_excerpt(row)


def read_songset_items_for_glue(
    conn: psycopg2.extensions.connection,
    songset_id: str,
) -> list[dict[str, Any]]:
    """Read the render-path item facts plus Glue columns, in position order.

    Mirrors pipeline.fetch_songset_items' select exactly (same joins and
    analysis fields) and adds the glue_enabled / glue_beats /
    glue_active_take_id / updated_at columns Glue resolution needs.
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT "
            "  si.id, "
            "  si.songset_id, "
            "  si.song_id, "
            "  si.recording_hash_prefix, "
            "  si.position, "
            "  si.gap_beats, "
            "  si.crossfade_enabled, "
            "  si.crossfade_duration_seconds, "
            "  si.key_shift_semitones, "
            "  si.tempo_ratio, "
            "  si.arrangement_steps, "
            "  si.glue_enabled, "
            "  si.glue_beats, "
            "  si.glue_active_take_id, "
            "  si.updated_at, "
            "  r.tempo_bpm, "
            "  r.duration_seconds, "
            "  r.sections, "
            "  r.beats, "
            "  r.content_hash AS recording_content_hash, "
            "  r.deleted_at, "
            "  s.title AS song_title "
            "FROM songset_items si "
            "LEFT JOIN recordings r ON si.recording_hash_prefix = r.hash_prefix "
            "LEFT JOIN songs s ON si.song_id = s.id "
            "WHERE si.songset_id = %s "
            "ORDER BY si.position",
            (songset_id,),
        )
        rows = cur.fetchall()
    return [dict(row) for row in rows]
