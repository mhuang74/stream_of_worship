from __future__ import annotations

import logging
import time
from pathlib import Path

import psycopg2
import psycopg2.extras

from sow_render_worker.asset_fetcher import AssetFetcher
from sow_render_worker.audio_engine import SongsetItem, generate_songset_audio
from sow_render_worker.chapters import generate_chapters_manifest
from sow_render_worker.db import (
    RenderProgress,
    complete_render_job,
    fail_render_job,
    get_render_job,
    start_render_job,
    update_render_progress,
)
from sow_render_worker.uploader import R2Uploader, RenderArtifacts
from sow_render_worker.video_engine import VideoEngine

logger = logging.getLogger(__name__)

PHASES = [
    "preparing",
    "mixing_audio",
    "rendering_frames",
    "encoding_video",
    "uploading",
]

DEFAULT_RENDER_RATIOS: dict[str, float] = {
    "720p_video": 0.8,
    "720p_audio": 0.4,
    "1080p_video": 0.65,
    "1080p_audio": 0.4,
}

MIN_HISTORICAL_JOBS = 3
MIN_REASONABLE_RATIO = 0.05
MAX_REASONABLE_RATIO = 5.0


def get_default_ratio(resolution: str, video_enabled: bool) -> float:
    key = f"{resolution}_{'video' if video_enabled else 'audio'}"
    if key in DEFAULT_RENDER_RATIOS:
        return DEFAULT_RENDER_RATIOS[key]
    return max(DEFAULT_RENDER_RATIOS.values())


def get_render_ratio(
    conn: psycopg2.extensions.connection,
    resolution: str,
    video_enabled: bool,
) -> float:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT "
            "  AVG(EXTRACT(EPOCH FROM (completed_at - started_at)) / NULLIF(total_duration_seconds, 0)) AS ratio, "
            "  COUNT(*) AS cnt "
            "FROM render_jobs "
            "WHERE status = %s "
            "  AND started_at IS NOT NULL "
            "  AND total_duration_seconds IS NOT NULL "
            "  AND total_duration_seconds > 0 "
            "  AND resolution = %s "
            "  AND video_enabled = %s",
            ("completed", resolution, video_enabled),
        )
        row = cur.fetchone()

    if not row or row["cnt"] < MIN_HISTORICAL_JOBS:
        return get_default_ratio(resolution, video_enabled)

    avg_ratio = row["ratio"]
    if avg_ratio is None or avg_ratio < MIN_REASONABLE_RATIO or avg_ratio > MAX_REASONABLE_RATIO:
        return get_default_ratio(resolution, video_enabled)

    return float(avg_ratio)


def fetch_songset_items(
    conn: psycopg2.extensions.connection,
    songset_id: str,
) -> list[SongsetItem]:
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
            "  r.tempo_bpm, "
            "  r.duration_seconds, "
            "  s.title AS song_title "
            "FROM songset_items si "
            "LEFT JOIN recordings r ON si.recording_hash_prefix = r.hash_prefix "
            "LEFT JOIN songs s ON si.song_id = s.id "
            "WHERE si.songset_id = %s "
            "ORDER BY si.position",
            (songset_id,),
        )
        rows = cur.fetchall()

    return [
        SongsetItem(
            id=row["id"],
            songset_id=row["songset_id"],
            song_id=row["song_id"],
            recording_hash_prefix=row["recording_hash_prefix"],
            position=row["position"],
            gap_beats=row["gap_beats"],
            crossfade_enabled=row["crossfade_enabled"],
            crossfade_duration_seconds=row["crossfade_duration_seconds"],
            key_shift_semitones=row["key_shift_semitones"],
            tempo_ratio=row["tempo_ratio"],
            tempo_bpm=row["tempo_bpm"],
            duration_seconds=row["duration_seconds"],
            song_title=row["song_title"],
        )
        for row in rows
    ]


class PipelineCancelledError(Exception):
    pass


def execute_render_pipeline(
    job_id: str,
    user_id: int,
    conn: psycopg2.extensions.connection,
    asset_fetcher: AssetFetcher | None = None,
    uploader: R2Uploader | None = None,
) -> None:
    job = get_render_job(conn, job_id, user_id)
    if not job:
        raise ValueError(f"Render job {job_id} not found")

    if asset_fetcher is None:
        asset_fetcher = AssetFetcher()
    if uploader is None:
        uploader = R2Uploader()

    asset_fetcher.initialize()
    temp_dir = asset_fetcher.get_job_temp_dir(job_id)
    pipeline_start = time.monotonic()

    def check_cancelled() -> None:
        current = get_render_job(conn, job_id, user_id)
        if not current or current.status == "cancelled":
            raise PipelineCancelledError(f"Render job {job_id} was cancelled")

    def elapsed_seconds() -> float:
        return time.monotonic() - pipeline_start

    try:
        start_render_job(conn, job_id, user_id)

        update_render_progress(
            conn,
            job_id,
            user_id,
            RenderProgress(
                phase=PHASES[0],
                phase_index=0,
                total_phases=len(PHASES),
                elapsed_seconds=0,
            ),
        )

        check_cancelled()

        items = fetch_songset_items(conn, job.songset_id)
        if not items:
            raise ValueError("Songset has no items")

        total_duration_seconds = sum(item.duration_seconds or 0 for item in items)
        render_ratio = get_render_ratio(conn, job.resolution, job.video_enabled)
        estimated_total_seconds = (
            total_duration_seconds * render_ratio if total_duration_seconds > 0 else 0
        )

        update_render_progress(
            conn,
            job_id,
            user_id,
            RenderProgress(
                phase=PHASES[1],
                phase_index=1,
                total_phases=len(PHASES),
                estimated_total_seconds=estimated_total_seconds,
                total_duration_seconds=total_duration_seconds,
                elapsed_seconds=elapsed_seconds(),
            ),
        )

        audio_output_path = str(Path(temp_dir) / "output.mp3")

        audio_result = generate_songset_audio(
            items,
            audio_output_path,
            asset_fetcher,
        )

        check_cancelled()

        accurate_total_duration = audio_result.total_duration_seconds
        accurate_render_ratio = get_render_ratio(conn, job.resolution, job.video_enabled)
        accurate_estimated_total = accurate_total_duration * accurate_render_ratio

        update_render_progress(
            conn,
            job_id,
            user_id,
            RenderProgress(
                phase=PHASES[2],
                phase_index=2,
                total_phases=len(PHASES),
                total_duration_seconds=accurate_total_duration,
                estimated_total_seconds=accurate_estimated_total,
                elapsed_seconds=elapsed_seconds(),
            ),
        )

        video_output_path: str | None = None
        if job.video_enabled:
            video_engine = VideoEngine(
                asset_fetcher,
                template=job.template,
                font_size_preset=job.font_size_preset,
                resolution=job.resolution,
                include_title_card=job.include_title_card,
                title_card_duration_seconds=job.title_card_duration_seconds or 5.0,
            )

            video_output_path = str(Path(temp_dir) / "output.mp4")

            update_render_progress(
                conn,
                job_id,
                user_id,
                RenderProgress(
                    phase=PHASES[3],
                    phase_index=3,
                    total_phases=len(PHASES),
                    elapsed_seconds=elapsed_seconds(),
                ),
            )

            video_engine.generate_video(
                audio_output_path,
                list(audio_result.segments),
                video_output_path,
            )

            check_cancelled()

        chapters_manifest = generate_chapters_manifest(
            list(audio_result.segments),
            asset_fetcher.download_lrc,
            audio_result.total_duration_seconds,
        )

        update_render_progress(
            conn,
            job_id,
            user_id,
            RenderProgress(
                phase=PHASES[4],
                phase_index=4,
                total_phases=len(PHASES),
                elapsed_seconds=elapsed_seconds(),
            ),
        )

        upload_result = uploader.upload_render_artifacts(
            job_id,
            RenderArtifacts(
                mp3_path=audio_output_path if job.audio_enabled else None,
                mp4_path=video_output_path,
                chapters=chapters_manifest,
            ),
        )

        complete_render_job(
            conn,
            job_id,
            user_id,
            mp3_r2_key=upload_result.mp3_r2_key,
            mp4_r2_key=upload_result.mp4_r2_key,
            chapters_r2_key=upload_result.chapters_r2_key,
        )

    except PipelineCancelledError:
        logger.info("Render job %s was cancelled, skipping failure marking", job_id)
        return

    except Exception as exc:
        current_job = get_render_job(conn, job_id, user_id)
        if current_job and current_job.status == "cancelled":
            return

        error_message = str(exc) if exc else "Unknown render error"
        logger.error("Render pipeline failed for job %s: %s", job_id, error_message)
        try:
            fail_render_job(conn, job_id, user_id, error_message)
        except Exception as fail_exc:
            logger.error("Failed to mark job %s as failed: %s", job_id, fail_exc)

        raise

    finally:
        try:
            asset_fetcher.cleanup_temp()
        except Exception as cleanup_err:
            logger.warning("Temp cleanup failed for job %s: %s", job_id, cleanup_err)
