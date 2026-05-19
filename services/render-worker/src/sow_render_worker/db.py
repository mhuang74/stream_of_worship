import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import psycopg2
import psycopg2.extras


TOTAL_PHASES = 5

PHASE_ORDER = [
    "preparing",
    "mixing_audio",
    "rendering_frames",
    "encoding_video",
    "uploading",
]

ORPHANED_JOB_THRESHOLD_MINUTES = 30


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
    include_title_card: bool = False
    title_card_duration_seconds: Optional[float] = None
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


def get_phase_index(phase: str) -> int:
    if phase == "completed":
        return TOTAL_PHASES
    try:
        return PHASE_ORDER.index(phase)
    except ValueError:
        return -1


def _row_to_render_job(row: dict[str, Any]) -> RenderJob:
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
        include_title_card=row.get("include_title_card", False),
        title_card_duration_seconds=row.get("title_card_duration_seconds"),
        mp3_r2_key=row.get("mp3_r2_key"),
        mp4_r2_key=row.get("mp4_r2_key"),
        chapters_r2_key=row.get("chapters_r2_key"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
        completed_at=row.get("completed_at"),
    )


def get_connection(database_url: Optional[str] = None) -> psycopg2.extensions.connection:
    url = database_url or os.environ.get("DATABASE_URL")
    if not url:
        raise ValueError("DATABASE_URL is required")
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
            "UPDATE render_jobs SET status = %s, updated_at = %s "
            "WHERE id = %s AND user_id = %s "
            "RETURNING *",
            ("running", now, job_id, user_id),
        )
        row = cur.fetchone()
    if not row:
        return None
    return _row_to_render_job(row)


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

    if not updates:
        return get_render_job(conn, job_id, user_id)

    updates.append("updated_at = %s")
    params.append(now)

    params.extend([job_id, user_id])

    sql = f"UPDATE render_jobs SET {', '.join(updates)} WHERE id = %s AND user_id = %s RETURNING *"

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    if not row:
        return None
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
        delta = now - job.started_at
        final_elapsed_seconds = delta.total_seconds()

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "UPDATE render_jobs SET "
            "  status = %s, phase = %s, phase_index = %s, percent_complete = %s, "
            "  elapsed_seconds = %s, "
            "  mp3_r2_key = %s, mp4_r2_key = %s, chapters_r2_key = %s, "
            "  completed_at = %s, updated_at = %s "
            "WHERE id = %s AND user_id = %s "
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
            ),
        )
        row = cur.fetchone()
    if not row:
        return None
    return _row_to_render_job(row)


def fail_render_job(
    conn: psycopg2.extensions.connection,
    job_id: str,
    user_id: int,
    error_message: str,
) -> Optional[RenderJob]:
    now = datetime.now(timezone.utc)
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "UPDATE render_jobs SET status = %s, error_message = %s, updated_at = %s "
            "WHERE id = %s AND user_id = %s "
            "RETURNING *",
            ("failed", error_message, now, job_id, user_id),
        )
        row = cur.fetchone()
    if not row:
        return None
    return _row_to_render_job(row)


def recover_orphaned_jobs(
    conn: psycopg2.extensions.connection,
    threshold_minutes: int = ORPHANED_JOB_THRESHOLD_MINUTES,
) -> int:
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(minutes=threshold_minutes)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT id, songset_id FROM render_jobs "
            "WHERE status = %s AND updated_at < %s",
            ("running", threshold),
        )
        orphaned = cur.fetchall()

    if not orphaned:
        return 0

    error_msg = f"Job timed out after {threshold_minutes} minutes without progress"

    with conn.cursor() as cur:
        for job in orphaned:
            cur.execute(
                "UPDATE render_jobs SET status = %s, error_message = %s, updated_at = %s "
                "WHERE id = %s AND status = %s",
                ("failed", error_msg, now, job["id"], "running"),
            )

    return len(orphaned)
