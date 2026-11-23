from __future__ import annotations

import dataclasses
import json
import logging
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import psycopg2
import psycopg2.extras

from sow_render_worker.arrangements import validate_arrangement
from sow_render_worker.asset_fetcher import AssetFetcher, GlueDownloadError
from sow_render_worker.audio_engine import (
    AudioSegmentInfo,
    GlueClip,
    SongsetItem,
    calculate_total_duration,
    generate_songset_audio,
)
from sow_render_worker.chapters import chapter_titles, generate_chapters_manifest
from sow_render_worker.db import (
    GlueTake,
    RenderJob,
    RenderProgress,
    complete_render_job,
    fail_render_job,
    get_glue_take_by_id,
    get_render_glue_transitions,
    get_render_job,
    reclaim_likely_dead_job,
    reclaim_stale_job,
    start_render_job,
    update_render_progress,
)
from sow_render_worker.frame_renderer import has_lyric_content
from sow_render_worker.lrc_parser import parse_lrc, remap_lrc_to_clips
from sow_render_worker.uploader import R2Uploader, RenderArtifacts
from sow_render_worker.video_engine import (
    DEFAULT_TITLE_CARD_DURATION_SECONDS,
    ChapterInfo,
    VideoEngine,
    clamp_title_card_duration,
    title_card_lead_in_seconds,
)

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
            "  si.arrangement_steps, "
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
            recording_content_hash=row["recording_content_hash"],
            deleted_at=row["deleted_at"],
            updated_at=row.get("updated_at"),
        )
        for row in rows
    ]

    missing_items = [
        item for item in items
        if item.recording_hash_prefix and item.recording_content_hash is None
    ]

    if missing_items:
        hashes = ", ".join(dict.fromkeys(item.recording_hash_prefix for item in missing_items))
        raise ValueError(
            f"Songset contains {len(missing_items)} recording(s) not found in database: {hashes}. "
            f"Recording row may have been hard-deleted or never existed."
        )

    deleted_items = [
        item for item in items
        if item.recording_hash_prefix and item.deleted_at is not None
    ]

    if deleted_items:
        hashes = ", ".join(dict.fromkeys(item.recording_hash_prefix for item in deleted_items))
        raise ValueError(
            f"Songset contains {len(deleted_items)} soft-deleted recording(s): {hashes}. "
            f"Run 'sow-admin maintenance repair-songsets' to fix stale references."
        )

    # Re-validate every saved arrangement against the Recording's current
    # analysis. A stale or malformed arrangement must fail the render with the
    # repair-or-reset error instead of silently rendering the full Recording.
    # An item without a Recording is left alone: the audio engine already fails
    # such a render with "Item <id> has no recording".
    for i, (item, row) in enumerate(zip(items, rows)):
        if not item.recording_hash_prefix:
            continue
        steps = validate_arrangement(
            row["arrangement_steps"],
            row["sections"],
            row["beats"],
            row["duration_seconds"],
        )
        if steps is None:
            continue
        items[i] = dataclasses.replace(
            item,
            arrangement_steps=steps,
            duration_seconds=sum(step.duration_seconds for step in steps),
        )

    return songset_name, items


class PipelineCancelledError(Exception):
    pass


def _segment_to_chapter_info(seg, index: int, song_title: str) -> ChapterInfo:
    return ChapterInfo(
        position=index + 1,
        song_title=song_title,
        start_seconds=seg.start_time_seconds,
        end_seconds=seg.start_time_seconds + seg.duration_seconds,
    )


def _title_card_lead_in_resolver(
    job, asset_fetcher: AssetFetcher
) -> Callable[[tuple[AudioSegmentInfo, ...]], float]:
    """Lead-in seconds for this job's card, from the first sung lyric line."""
    card_seconds = clamp_title_card_duration(
        job.title_card_duration_seconds or DEFAULT_TITLE_CARD_DURATION_SECONDS
    )

    def resolve(segments: tuple[AudioSegmentInfo, ...]) -> float:
        first_lyric: float | None = None
        for segment in segments:
            hash_prefix = segment.item.recording_hash_prefix
            if not hash_prefix:
                continue
            try:
                lrc_content = asset_fetcher.download_lrc(hash_prefix)
            except Exception:
                lrc_content = None
            if not lrc_content:
                continue
            for line in remap_lrc_to_clips(
                parse_lrc(lrc_content),
                segment.clips,
                segment.item.id,
                tempo_bpm=segment.item.tempo_bpm,
            ):
                if has_lyric_content(line.text):
                    if first_lyric is None or line.global_time_seconds < first_lyric:
                        first_lyric = line.global_time_seconds
        if first_lyric is None:
            return 0.0
        return title_card_lead_in_seconds(first_lyric, card_seconds)

    return resolve


GLUE_NOT_READY = "GLUE_NOT_READY"
GLUE_STALE_SNAPSHOT = "GLUE_STALE_SNAPSHOT"

# Snapshot vs immutable take/item metadata. Durations are serialized with six
# decimals and timestamps with microsecond precision, so anything beyond a
# microsecond is a real mismatch.
GLUE_SNAPSHOT_SECONDS_EPSILON = 1e-6

_GLUE_SNAPSHOT_REQUIRED_FIELDS = (
    "from_item_id",
    "to_item_id",
    "take_id",
    "descriptor_key",
    "r2_key",
)


class GlueSnapshotError(RuntimeError):
    """The pinned Glue snapshot cannot be rendered as written.

    The stable code prefixes the message so the failed render's persisted
    ``error_message`` stays actionable. There is never an ordinary-gap fallback.
    """

    code = GLUE_NOT_READY

    def __init__(self, detail: str, *, code: str = GLUE_NOT_READY) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def _log_glue_render_event(event: str, *, level: str = "info", **fields: Any) -> None:
    """Emit one structured JSON line for a stable Glue render event."""
    payload: dict[str, Any] = {"event": event}
    for key, value in fields.items():
        if value is not None:
            payload[key] = value
    logger.log(
        getattr(logging, level.upper(), logging.INFO),
        json.dumps(payload, default=str, ensure_ascii=False, sort_keys=True),
    )


def _glue_failure(
    job_id: str,
    *,
    code: str,
    from_item_id: str | None,
    to_item_id: str | None,
    take_id: str | None,
    detail: str,
) -> GlueSnapshotError:
    _log_glue_render_event(
        "glue.render.glue.failed",
        level="error",
        jobId=job_id,
        takeId=take_id,
        toItemId=to_item_id,
        errorCode=code,
    )
    return GlueSnapshotError(
        f"AI transition {from_item_id or '?'} -> {to_item_id or '?'}: {detail}",
        code=code,
    )


def _glue_metrics(take: GlueTake) -> dict[str, Any]:
    try:
        metrics = json.loads(take.metrics_json or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return metrics if isinstance(metrics, dict) else {}


def _parse_snapshot_timestamp(value: Any) -> datetime | None:
    """Parse one ISO 8601 snapshot timestamp to an aware UTC datetime."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamps_match(expected: datetime, actual: datetime) -> bool:
    left = expected if expected.tzinfo else expected.replace(tzinfo=timezone.utc)
    right = actual if actual.tzinfo else actual.replace(tzinfo=timezone.utc)
    delta = abs(
        (left.astimezone(timezone.utc) - right.astimezone(timezone.utc)).total_seconds()
    )
    return delta <= GLUE_SNAPSHOT_SECONDS_EPSILON


def _validate_snapshot_item_identity(
    job_id: str,
    entry: dict[str, Any],
    *,
    from_item: SongsetItem,
    to_item: SongsetItem,
    from_item_id: str,
    to_item_id: str,
    take_id: str,
) -> None:
    """Reject a snapshot whose from/to items changed after creation.

    The webapp pinned the exact ``songset_items.updated_at`` of both Items.
    Arrangement edits, replacements, or reordering bump those timestamps; the
    render must fail with ``GLUE_STALE_SNAPSHOT`` instead of inserting a take
    that no longer matches the junction.
    """
    sides = (
        ("from", "from_updated_at", from_item),
        ("to", "to_updated_at", to_item),
    )
    for side, field, item in sides:
        expected = _parse_snapshot_timestamp(entry.get(field))
        actual = item.updated_at
        if expected is None or not isinstance(actual, datetime):
            raise _glue_failure(
                job_id,
                code=GLUE_STALE_SNAPSHOT,
                from_item_id=from_item_id,
                to_item_id=to_item_id,
                take_id=take_id,
                detail=f"{side} item identity is unverifiable ({field} missing or invalid)",
            )
        if not _timestamps_match(expected, actual):
            raise _glue_failure(
                job_id,
                code=GLUE_STALE_SNAPSHOT,
                from_item_id=from_item_id,
                to_item_id=to_item_id,
                take_id=take_id,
                detail=f"{side} item changed since the snapshot ({field} mismatch)",
            )


def load_render_glue_clips(
    conn: psycopg2.extensions.connection,
    job: RenderJob,
    items: list[SongsetItem],
    asset_fetcher: AssetFetcher,
) -> dict[str, GlueClip]:
    """Load the immutable render snapshot into Glue clips keyed by incoming item.

    Every snapshot entry is revalidated against its immutable ``glue_takes`` row
    and the Songset item order. Snapshot order is preserved. Any mismatch,
    missing object, checksum failure, or download failure raises
    :class:`GlueSnapshotError`; the worker never substitutes an earlier take or
    an ordinary gap.
    """
    try:
        transitions = get_render_glue_transitions(conn, job.id)
    except ValueError as exc:
        raise GlueSnapshotError(f"render job {job.id}: {exc}") from exc
    if not transitions:
        return {}

    index_by_item_id = {item.id: index for index, item in enumerate(items)}
    clips: dict[str, GlueClip] = {}
    seen_to_item_ids: set[str] = set()

    for position, entry in enumerate(transitions):
        from_item_id = entry.get("from_item_id")
        to_item_id = entry.get("to_item_id")
        take_id = entry.get("take_id")
        seconds = entry.get("seconds")

        malformed = any(
            not isinstance(entry.get(field), str) or not entry[field]
            for field in _GLUE_SNAPSHOT_REQUIRED_FIELDS
        ) or (
            not isinstance(seconds, (int, float))
            or isinstance(seconds, bool)
        )
        if malformed:
            raise _glue_failure(
                job.id,
                code=GLUE_NOT_READY,
                from_item_id=from_item_id if isinstance(from_item_id, str) else None,
                to_item_id=to_item_id if isinstance(to_item_id, str) else None,
                take_id=take_id if isinstance(take_id, str) else None,
                detail=f"snapshot entry {position} is malformed",
            )

        from_index = index_by_item_id.get(from_item_id)
        to_index = index_by_item_id.get(to_item_id)
        if from_index is None or to_index is None or to_index != from_index + 1:
            raise _glue_failure(
                job.id,
                code=GLUE_NOT_READY,
                from_item_id=from_item_id,
                to_item_id=to_item_id,
                take_id=take_id,
                detail="junction is not adjacent in this songset",
            )
        if to_item_id in seen_to_item_ids:
            raise _glue_failure(
                job.id,
                code=GLUE_NOT_READY,
                from_item_id=from_item_id,
                to_item_id=to_item_id,
                take_id=take_id,
                detail="junction is duplicated in the snapshot",
            )
        seen_to_item_ids.add(to_item_id)

        _validate_snapshot_item_identity(
            job.id,
            entry,
            from_item=items[from_index],
            to_item=items[to_index],
            from_item_id=from_item_id,
            to_item_id=to_item_id,
            take_id=take_id,
        )

        take = get_glue_take_by_id(conn, take_id, job.user_id)
        if take is None:
            raise _glue_failure(
                job.id,
                code=GLUE_NOT_READY,
                from_item_id=from_item_id,
                to_item_id=to_item_id,
                take_id=take_id,
                detail="take is missing for this render owner",
            )
        if take.user_id != job.user_id:
            raise _glue_failure(
                job.id,
                code=GLUE_NOT_READY,
                from_item_id=from_item_id,
                to_item_id=to_item_id,
                take_id=take_id,
                detail="take belongs to another user",
            )
        if take.status != "ready":
            raise _glue_failure(
                job.id,
                code=GLUE_NOT_READY,
                from_item_id=from_item_id,
                to_item_id=to_item_id,
                take_id=take_id,
                detail=f"take is not ready (status={take.status!r})",
            )
        if take.descriptor_key != entry["descriptor_key"]:
            raise _glue_failure(
                job.id,
                code=GLUE_NOT_READY,
                from_item_id=from_item_id,
                to_item_id=to_item_id,
                take_id=take_id,
                detail="descriptor key mismatch",
            )
        if take.r2_key != entry["r2_key"]:
            raise _glue_failure(
                job.id,
                code=GLUE_NOT_READY,
                from_item_id=from_item_id,
                to_item_id=to_item_id,
                take_id=take_id,
                detail="staged object key mismatch",
            )
        if take.seconds is None or (
            abs(float(seconds) - float(take.seconds)) > GLUE_SNAPSHOT_SECONDS_EPSILON
        ):
            raise _glue_failure(
                job.id,
                code=GLUE_NOT_READY,
                from_item_id=from_item_id,
                to_item_id=to_item_id,
                take_id=take_id,
                detail="staged duration mismatch",
            )

        metrics = _glue_metrics(take)
        snapshot_sha256 = entry.get("sha256")
        if snapshot_sha256 is None:
            snapshot_sha256 = entry.get("audio_sha256")
        if snapshot_sha256 is not None and not isinstance(snapshot_sha256, str):
            raise _glue_failure(
                job.id,
                code=GLUE_NOT_READY,
                from_item_id=from_item_id,
                to_item_id=to_item_id,
                take_id=take_id,
                detail="snapshot checksum is malformed",
            )
        row_sha256 = metrics.get("audioSha256")
        if (
            isinstance(snapshot_sha256, str)
            and isinstance(row_sha256, str)
            and snapshot_sha256.lower() != row_sha256.lower()
        ):
            raise _glue_failure(
                job.id,
                code=GLUE_NOT_READY,
                from_item_id=from_item_id,
                to_item_id=to_item_id,
                take_id=take_id,
                detail="staged checksum mismatch",
            )
        expected_sha256 = (
            snapshot_sha256
            if isinstance(snapshot_sha256, str)
            else (row_sha256 if isinstance(row_sha256, str) else None)
        )

        expected_bytes = entry.get("audio_bytes")
        if expected_bytes is None:
            expected_bytes = entry.get("bytes")
        if expected_bytes is not None and (
            not isinstance(expected_bytes, int)
            or isinstance(expected_bytes, bool)
            or expected_bytes <= 0
        ):
            raise _glue_failure(
                job.id,
                code=GLUE_NOT_READY,
                from_item_id=from_item_id,
                to_item_id=to_item_id,
                take_id=take_id,
                detail="snapshot byte length is malformed",
            )
        row_bytes = metrics.get("audioBytes")
        if (
            isinstance(expected_bytes, int)
            and isinstance(row_bytes, int)
            and expected_bytes != row_bytes
        ):
            raise _glue_failure(
                job.id,
                code=GLUE_NOT_READY,
                from_item_id=from_item_id,
                to_item_id=to_item_id,
                take_id=take_id,
                detail="staged byte length mismatch",
            )

        try:
            path = asset_fetcher.download_glue(
                take.r2_key,
                expected_bytes=expected_bytes,
                expected_sha256=expected_sha256,
                cache_key=f"glue-{take_id}",
                job_id=job.id,
                take_id=take_id,
            )
        except GlueDownloadError as exc:
            raise _glue_failure(
                job.id,
                code=exc.code,
                from_item_id=from_item_id,
                to_item_id=to_item_id,
                take_id=take_id,
                detail=f"audio download failed: {exc}",
            ) from exc

        clip = GlueClip(
            path=path,
            take_id=take_id,
            seconds=float(take.seconds),
            checksum=expected_sha256 or "",
        )
        clips[to_item_id] = clip
        _log_glue_render_event(
            "glue.render.glue.loaded",
            jobId=job.id,
            takeId=take_id,
            toItemId=to_item_id,
            seconds=clip.seconds,
        )

    return clips


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

        # The job temp dir belongs to the winning invocation. Claim before
        # creating it so a duplicate delivery that skips never removes the
        # live invocation's working files in the finally block.
        temp_dir = asset_fetcher.get_job_temp_dir(job_id)

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

        # The render snapshot is immutable: load and revalidate it before any
        # duration admission or mixing so a broken Glue fails visibly here
        # instead of silently rendering an ordinary gap.
        glue_clips = load_render_glue_clips(conn, job, items, asset_fetcher)
        effective_glue_clips = glue_clips or None

        # Admission uses the arranged timeline (crossfade overlap shortens it),
        # matching both the mix and the webapp's own admission check. A layout
        # of 0 means no item had a known duration — fall back to the raw sum as
        # a rough upper bound.
        total_duration = calculate_total_duration(
            items, asset_fetcher, glue_clips=effective_glue_clips
        )
        if total_duration <= 0:
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
            lead_in_resolver=(
                _title_card_lead_in_resolver(job, asset_fetcher)
                if job.video_enabled and job.include_title_card
                else None
            ),
            glue_clips=effective_glue_clips,
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
                title_card_duration_seconds=clamp_title_card_duration(
                    job.title_card_duration_seconds or DEFAULT_TITLE_CARD_DURATION_SECONDS
                ),
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
                glue_windows=audio_result.glue_windows,
            )

            video_engine.frame_renderer.clear_cache()

            titles_for_video = chapter_titles(audio_result.segments)
            chapters_for_video = [
                _segment_to_chapter_info(seg, i, titles_for_video[i])
                for i, seg in enumerate(audio_result.segments)
            ]
            if chapters_for_video:
                video_engine.inject_chapters(video_output_path, chapters_for_video, job_id=job_id)

            check_cancelled()

        chapters_manifest = generate_chapters_manifest(
            list(audio_result.segments),
            asset_fetcher.download_lrc,
            audio_result.total_duration_seconds,
            offset_seconds=audio_result.lead_in_seconds,
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
