from __future__ import annotations

import logging
import signal
import time
from pathlib import Path
from typing import Any

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
    reclaim_likely_dead_job,
    reclaim_stale_job,
    start_render_job,
    update_render_progress,
)
from sow_render_worker.uploader import R2Uploader, RenderArtifacts
from sow_render_worker.video_engine import ChapterInfo, VideoEngine

logger = logging.getLogger(__name__)

LAMBDA_TIMEOUT_SAFETY_MARGIN_SECONDS = 60

MAX_SONGSET_ITEMS = 5
MAX_SONGSET_DURATION_SECONDS = 1500

_shutdown_requested = False


def _sigterm_handler(signum: int, frame) -> None:
    global _shutdown_requested
    _shutdown_requested = True


signal.signal(signal.SIGTERM, _sigterm_handler)

PHASES = [
    "preparing",
    "mixing_audio",
    "rendering_frames",
    "encoding_video",
    "uploading",
]

DEFAULT_RENDER_RATIOS: dict[str, float] = {
    "720p_video": 0.5,
    "720p_audio": 0.4,
    "1080p_video": 0.5,
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
) -> tuple[str, list[SongsetItem]]:
    """
    Returns: (songset_name, list of SongsetItem)
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT name FROM songsets WHERE id = %s",
            (songset_id,),
        )
        songset_row = cur.fetchone()
        songset_name = songset_row["name"] if songset_row else "Worship Set"

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

    items = [
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

    return songset_name, items


class PipelineCancelledError(Exception):
    pass


def _segment_to_chapter_info(seg, index: int) -> ChapterInfo:
    item = seg.item
    return ChapterInfo(
        position=index + 1,
        song_title=(
            item.song_title
            or (str(item.song_id) if item.song_id else f"Song {index + 1}")
        ),
        start_seconds=seg.start_time_seconds,
        end_seconds=seg.start_time_seconds + seg.duration_seconds,
    )


def execute_render_pipeline(
    job_id: str,
    user_id: int,
    conn: psycopg2.extensions.connection,
    asset_fetcher: AssetFetcher | None = None,
    uploader: R2Uploader | None = None,
    lambda_context: Any | None = None,
) -> None:
    job = get_render_job(conn, job_id, user_id)
    if not job:
        raise ValueError(f"Render job {job_id} not found")

    if not job.audio_enabled and not job.video_enabled:
        raise ValueError("At least one of audio_enabled or video_enabled must be True")

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

    def check_lambda_timeout() -> None:
        global _shutdown_requested
        if _shutdown_requested:
            _shutdown_requested = False
            raise TimeoutError("Lambda received SIGTERM, shutting down gracefully")
        if lambda_context is None:
            return
        remaining_ms = lambda_context.get_remaining_time_in_millis()
        remaining_seconds = remaining_ms / 1000
        if remaining_seconds < LAMBDA_TIMEOUT_SAFETY_MARGIN_SECONDS:
            raise TimeoutError(
                f"Lambda timeout imminent ({remaining_seconds:.0f}s remaining, "
                f"need {LAMBDA_TIMEOUT_SAFETY_MARGIN_SECONDS}s safety margin)"
            )

    def elapsed_seconds() -> float:
        return time.monotonic() - pipeline_start

    try:
        started = start_render_job(conn, job_id, user_id)
        if not started:
            reclaimed = reclaim_likely_dead_job(conn, job_id, user_id)
            if reclaimed:
                logger.info(
                    "Reclaimed likely-dead job %s (no progress for 60+s), retrying",
                    job_id,
                )
                started = start_render_job(conn, job_id, user_id)

            if not started:
                logger.info(
                    "Render job %s was already claimed by another invocation, skipping",
                    job_id,
                )
                return

        check_lambda_timeout()

        logger.info(
            "[%s] Pipeline started: resolution=%s, video=%s, audio=%s, items=%d",
            job_id, job.resolution, job.video_enabled, job.audio_enabled, 0,
        )

        update_render_progress(
            conn,
            job_id,
            user_id,
            RenderProgress(
                phase=PHASES[0],
                phase_index=0,
                total_phases=len(PHASES),
                elapsed_seconds=0,
                percent_complete=0.0,
                estimated_seconds_left=None,
            ),
        )
        logger.info(
            "[%s] Phase 1/%d: %s (elapsed=%.1fs)",
            job_id, len(PHASES), PHASES[0], elapsed_seconds(),
        )

        check_cancelled()

        songset_name, items = fetch_songset_items(conn, job.songset_id)
        if not items:
            raise ValueError("Songset has no items")

        total_duration = sum(item.duration_seconds or 0 for item in items)
        if len(items) > MAX_SONGSET_ITEMS or total_duration > MAX_SONGSET_DURATION_SECONDS:
            raise ValueError(
                f"Songset exceeds limit: {len(items)} songs / {total_duration:.0f}s "
                f"(max {MAX_SONGSET_ITEMS} songs / {MAX_SONGSET_DURATION_SECONDS}s)"
            )

        total_duration_seconds = sum(item.duration_seconds or 0 for item in items)
        if total_duration_seconds <= 0:
            total_duration_seconds = 180.0 * len(items)
            logger.warning(
                "[%s] Songset items have no valid duration_seconds — "
                "using rough estimate of %.0fs (%d items × 180s/item)",
                job_id,
                total_duration_seconds,
                len(items),
            )
        render_ratio = get_render_ratio(conn, job.resolution, job.video_enabled)
        estimated_total_seconds = total_duration_seconds * render_ratio

        check_lambda_timeout()

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
                percent_complete=(1 / len(PHASES)) * 100,
                estimated_seconds_left=max(0, estimated_total_seconds - elapsed_seconds()) if estimated_total_seconds else None,
            ),
        )
        logger.info(
            "[%s] Phase 2/%d: %s (elapsed=%.1fs)",
            job_id, len(PHASES), PHASES[1], elapsed_seconds(),
        )

        audio_output_path = str(Path(temp_dir) / "output.mp3")

        def audio_progress_callback(step: int, total_steps: int) -> None:
            logger.info(
                "[%s] Audio mixing: step %d/%d (%d%%)",
                job_id, step, total_steps, int(step / total_steps * 100) if total_steps > 0 else 0,
            )

        audio_result = generate_songset_audio(
            items,
            audio_output_path,
            asset_fetcher,
            progress_callback=audio_progress_callback,
            job_id=job_id,
        )

        if not Path(audio_output_path).exists():
            raise FileNotFoundError(
                f"Audio output file not found after generation: {audio_output_path}"
            )

        check_cancelled()

        accurate_total_duration = audio_result.total_duration_seconds
        accurate_render_ratio = get_render_ratio(conn, job.resolution, job.video_enabled)
        accurate_estimated_total = accurate_total_duration * accurate_render_ratio

        check_lambda_timeout()

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
                percent_complete=(2 / len(PHASES)) * 100,
                estimated_seconds_left=max(0, accurate_estimated_total - elapsed_seconds()) if accurate_estimated_total else None,
            ),
        )
        logger.info(
            "[%s] Phase 3/%d: %s (elapsed=%.1fs)",
            job_id, len(PHASES), PHASES[2], elapsed_seconds(),
        )

        video_output_path: str | None = None
        if job.video_enabled:
            title_card_lines = job.title_card_lines if job.title_card_lines else None
            video_engine = VideoEngine(
                asset_fetcher,
                template=job.template,
                font_size_preset=job.font_size_preset,
                resolution=job.resolution,
                include_title_card=job.include_title_card,
                title_card_duration_seconds=job.title_card_duration_seconds or 5.0,
                title_card_lines=title_card_lines,
                songset_name=songset_name,
                font_family=job.font_family,
            )

            video_output_path = str(Path(temp_dir) / "output.mp4")

            check_lambda_timeout()

            update_render_progress(
                conn,
                job_id,
                user_id,
                RenderProgress(
                    phase=PHASES[3],
                    phase_index=3,
                    total_phases=len(PHASES),
                    elapsed_seconds=elapsed_seconds(),
                    percent_complete=(3 / len(PHASES)) * 100,
                    estimated_seconds_left=max(0, accurate_estimated_total - elapsed_seconds()) if accurate_estimated_total else None,
                ),
            )
            logger.info(
                "[%s] Phase 4/%d: %s (elapsed=%.1fs)",
                job_id, len(PHASES), PHASES[3], elapsed_seconds(),
            )

            _last_video_progress_log_seconds = 0.0
            _last_video_db_update_time = pipeline_start
            _job_no_longer_running = False

            encoding_video_phase_index = PHASES.index("encoding_video")

            def video_progress_callback(frame_count: int, total_frames: int) -> None:
                nonlocal _last_video_progress_log_seconds, _last_video_db_update_time, _job_no_longer_running

                if _job_no_longer_running:
                    return

                now = time.monotonic()
                video_seconds = frame_count / video_engine.fps
                total_video_seconds = total_frames / video_engine.fps

                if video_seconds - _last_video_progress_log_seconds >= 30:
                    logger.info(
                        "[%s] Video encoding: %.0fs/%.0fs (%d/%d frames, %.1f%%)",
                        job_id, video_seconds, total_video_seconds,
                        frame_count, total_frames,
                        frame_count / total_frames * 100 if total_frames > 0 else 0,
                    )
                    _last_video_progress_log_seconds = video_seconds

                if now - _last_video_db_update_time >= 5:
                    phase_base = encoding_video_phase_index / len(PHASES) * 100
                    phase_weight = 1 / len(PHASES) * 100
                    frame_progress = frame_count / total_frames if total_frames > 0 else 0
                    current_percent = phase_base + frame_progress * phase_weight

                    result = update_render_progress(
                        conn,
                        job_id,
                        user_id,
                        RenderProgress(
                            phase=PHASES[3],
                            phase_index=3,
                            total_phases=len(PHASES),
                            elapsed_seconds=elapsed_seconds(),
                            percent_complete=current_percent,
                            estimated_seconds_left=max(0, accurate_estimated_total - elapsed_seconds()) if accurate_estimated_total else None,
                        ),
                    )
                    if result is None:
                        _job_no_longer_running = True
                        return
                    _last_video_db_update_time = now

            video_engine.generate_video(
                audio_output_path,
                list(audio_result.segments),
                video_output_path,
                progress_callback=video_progress_callback,
                timeout_check_callback=check_lambda_timeout,
                job_id=job_id,
            )

            chapters_for_video = [
                _segment_to_chapter_info(seg, i)
                for i, seg in enumerate(audio_result.segments)
            ]
            if chapters_for_video:
                video_engine.inject_chapters(video_output_path, chapters_for_video, job_id=job_id)

            check_cancelled()

        chapters_manifest = generate_chapters_manifest(
            list(audio_result.segments),
            asset_fetcher.download_lrc,
            audio_result.total_duration_seconds,
        )

        check_lambda_timeout()

        update_render_progress(
            conn,
            job_id,
            user_id,
            RenderProgress(
                phase=PHASES[4],
                phase_index=4,
                total_phases=len(PHASES),
                elapsed_seconds=elapsed_seconds(),
                percent_complete=(4 / len(PHASES)) * 100,
                estimated_seconds_left=max(0, accurate_estimated_total - elapsed_seconds()) if accurate_estimated_total else None,
            ),
        )
        logger.info(
            "[%s] Phase 5/%d: %s (elapsed=%.1fs)",
            job_id, len(PHASES), PHASES[4], elapsed_seconds(),
        )

        upload_result = uploader.upload_render_artifacts(
            job_id,
            RenderArtifacts(
                mp3_path=audio_output_path if job.audio_enabled else None,
                mp4_path=video_output_path,
                chapters=chapters_manifest,
            ),
        )

        logger.info("[%s] Pipeline completed in %.1fs", job_id, elapsed_seconds())

        complete_render_job(
            conn,
            job_id,
            user_id,
            mp3_r2_key=upload_result.mp3_r2_key,
            mp4_r2_key=upload_result.mp4_r2_key,
            chapters_r2_key=upload_result.chapters_r2_key,
        )

    except MemoryError as mem_exc:
        logger.critical(
            "[%s] Render pipeline hit memory limit: %s",
            job_id, str(mem_exc),
        )
        try:
            fail_render_job(conn, job_id, user_id, str(mem_exc))
        except Exception as fail_exc:
            logger.error("[%s] Failed to mark job as failed: %s", job_id, fail_exc)
        raise

    except PipelineCancelledError:
        logger.info("[%s] Render job was cancelled, skipping failure marking", job_id)
        return

    except Exception as exc:
        current_job = get_render_job(conn, job_id, user_id)
        if current_job and current_job.status == "cancelled":
            return

        error_message = str(exc) if exc else "Unknown render error"
        logger.error("[%s] Render pipeline failed: %s", job_id, error_message)
        try:
            fail_render_job(conn, job_id, user_id, error_message)
        except Exception as fail_exc:
            logger.error("[%s] Failed to mark job as failed: %s", job_id, fail_exc)

        raise

    finally:
        try:
            asset_fetcher.cleanup_temp()
        except Exception as cleanup_err:
            logger.warning("[%s] Temp cleanup failed: %s", job_id, cleanup_err)
