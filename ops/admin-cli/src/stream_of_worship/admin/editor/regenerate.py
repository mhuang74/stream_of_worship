"""In-editor LRC force-regeneration pipeline.

Submits a forced LRC generation job to the analysis service, polls it to
completion, writes the resulting DB state, then downloads the freshly
published LRC from R2 so the open editor session can be refreshed without
leaving the TUI.

This is plain blocking I/O — the caller runs it on a daemon thread. It never
prints, never raises for expected failures, and never touches the UI: every
failure is returned as ``RegenerateResult(ok=False, error=...)``.
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from stream_of_worship.admin.audit import log_change
from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.services.analysis import (
    AnalysisClient,
    AnalysisResult,
    AnalysisServiceError,
    JobInfo,
)
from stream_of_worship.admin.services.lrc_jobs import lrc_generator_kwargs, resolve_lyrics_text
from stream_of_worship.admin.services.r2 import R2Client, R2ObjectIdentity
from stream_of_worship.admin.services.visibility import is_published, reprocess_blocked_reason

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 5.0
# Matches the single-song ``lyrics generate`` wait budget (20 minutes).
DEFAULT_TIMEOUT = 1200.0
R2_RETRY_INTERVAL = 2.0
R2_RETRY_ATTEMPTS = 30


@dataclass
class RegenerateResult:
    """Outcome of one in-editor regeneration attempt."""

    ok: bool
    error: Optional[str] = None
    lrc_content: Optional[str] = None
    identity: Optional[R2ObjectIdentity] = None
    job_id: Optional[str] = None
    # Provenance mirrored from the job result; None means the DB kept its
    # previous value (``update_recording_lrc`` COALESCEs on NULL).
    lrc_source: Optional[str] = None


def regenerate_lrc_for_editor(
    *,
    analysis_client: AnalysisClient,
    db_client: DatabaseClient,
    r2_client: R2Client,
    db_lock: threading.RLock,
    song_id: str,
    hash_prefix: str,
    generator: str = "auto",
    on_progress: Optional[Callable[[str], None]] = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    timeout: float = DEFAULT_TIMEOUT,
    r2_retry_interval: float = R2_RETRY_INTERVAL,
    r2_retry_attempts: int = R2_RETRY_ATTEMPTS,
) -> RegenerateResult:
    """Force-regenerate the LRC for ``hash_prefix`` and return its new content.

    Args:
        analysis_client: Client for the analysis service.
        db_client: Catalog DB client; every call runs under ``db_lock``.
        r2_client: Object-store client used for the LRC download.
        db_lock: The editor screen's ``RLock`` — psycopg connections are not
            safe for concurrent use.
        song_id: Song id from the open session (empty falls back to the
            recording's own ``song_id``).
        hash_prefix: Recording hash prefix.
        generator: Which generator to run: ``auto`` (YouTube → Qwen3 ASR →
            Whisper fallback), ``youtube``, ``qwen3`` or ``whisper`` — the same
            vocabulary as ``lyrics generate --generator``. A specific generator
            runs only that one and never falls back. The LRC result cache is
            bypassed (``force=True``) while the ASR transcription caches are
            reused, matching ``lyrics generate --force`` without ``--no-cache``.
        on_progress: Optional sink for human-readable progress lines.
        poll_interval: Seconds between job polls.
        timeout: Seconds to wait for the job before failing.
        r2_retry_interval: Seconds between LRC download attempts.
        r2_retry_attempts: Download attempts; the new object may take a
            moment to become readable after the job reports completion.

    Returns:
        RegenerateResult; ``ok=True`` only when the new LRC was downloaded.
    """
    with db_lock:
        recording = db_client.get_recording_by_hash(hash_prefix)
    if not recording:
        return RegenerateResult(ok=False, error=f"No recording found for {hash_prefix}.")

    target_song_id = song_id or recording.song_id
    if not target_song_id:
        return RegenerateResult(ok=False, error=f"Recording {hash_prefix} has no linked song.")
    if is_published(recording):
        return RegenerateResult(
            ok=False,
            error=reprocess_blocked_reason(target_song_id, hash_prefix, "regenerate LRC"),
        )

    with db_lock:
        song = db_client.get_song(target_song_id)
    lyrics_text = resolve_lyrics_text(song, recording) if song else None
    if not song or not lyrics_text:
        return RegenerateResult(ok=False, error=f"No lyrics found for song {target_song_id}.")

    if not recording.r2_audio_url:
        return RegenerateResult(ok=False, error=f"Recording {hash_prefix} has no audio URL.")

    try:
        job = analysis_client.submit_lrc(
            audio_url=recording.r2_audio_url,
            content_hash=recording.content_hash,
            lyrics_text=lyrics_text,
            song_title=song.title,
            force=True,
            **lrc_generator_kwargs(recording, generator, no_cache=False),
            tempo_bpm=recording.tempo_bpm,
            duration_seconds=recording.duration_seconds,
            song_id=target_song_id,
        )
    except AnalysisServiceError as e:
        return RegenerateResult(ok=False, error=f"Failed to submit LRC job: {e}")

    with db_lock:
        db_client.update_recording_status(
            hash_prefix=hash_prefix,
            lrc_status="processing",
            lrc_job_id=job.job_id,
        )
        log_change(
            "lrc_submit",
            song_id=target_song_id,
            hash_prefix=hash_prefix,
            via="editor_regenerate",
            job_id=job.job_id,
            force=True,
        )

    def _on_job_info(info: JobInfo) -> None:
        if on_progress is not None:
            on_progress(f"Regenerating LRC… {int(info.progress * 100)}% [{info.stage}]")

    try:
        final_job = analysis_client.wait_for_completion(
            job.job_id,
            poll_interval=poll_interval,
            timeout=timeout,
            callback=_on_job_info,
        )
    except AnalysisServiceError as e:
        _mark_failed(db_client, db_lock, hash_prefix)
        return RegenerateResult(ok=False, error=f"LRC generation failed: {e}", job_id=job.job_id)

    if final_job.status != "completed":
        _mark_failed(db_client, db_lock, hash_prefix)
        return RegenerateResult(
            ok=False,
            error=final_job.error_message or "LRC generation failed",
            job_id=job.job_id,
        )

    result = final_job.result if isinstance(final_job.result, AnalysisResult) else None
    lrc_url = result.lrc_url if result else None
    if not lrc_url:
        lrc_url = r2_client.lrc_exists(hash_prefix)
    if not lrc_url:
        _mark_failed(db_client, db_lock, hash_prefix)
        return RegenerateResult(
            ok=False,
            error="LRC generation completed but no LRC URL was reported",
            job_id=job.job_id,
        )

    with db_lock:
        db_client.update_recording_lrc(
            hash_prefix=hash_prefix,
            r2_lrc_url=lrc_url,
            visibility_status="review",
            lrc_source=result.lrc_source if result else None,
        )
        log_change(
            "lrc_save",
            song_id=target_song_id,
            hash_prefix=hash_prefix,
            via="editor_regenerate",
            r2_url=lrc_url,
        )

    content: Optional[str] = None
    for attempt in range(r2_retry_attempts):
        content = r2_client.download_lrc_content(hash_prefix)
        if content:
            break
        if attempt + 1 < r2_retry_attempts:
            time.sleep(r2_retry_interval)
    if not content:
        return RegenerateResult(
            ok=False,
            error="LRC job completed but the LRC is not readable from R2 yet",
            job_id=job.job_id,
        )

    identity = r2_client.get_lrc_identity(hash_prefix)
    return RegenerateResult(
        ok=True,
        lrc_content=content,
        identity=identity,
        job_id=job.job_id,
        lrc_source=result.lrc_source if result else None,
    )


def _mark_failed(db_client: DatabaseClient, db_lock: threading.RLock, hash_prefix: str) -> None:
    """Record a failed LRC status, swallowing DB errors (best effort)."""
    try:
        with db_lock:
            db_client.update_recording_status(hash_prefix=hash_prefix, lrc_status="failed")
    except Exception as e:  # never mask the generation error with a DB error
        logger.warning(f"Could not mark LRC status failed for {hash_prefix}: {e}")
