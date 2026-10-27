"""In-editor LRC forced-alignment pipeline.

Submits a forced-alignment (Qwen3ForcedAligner) job to the analysis service
for a recording whose LRC only needs retiming, polls it to completion, writes
the resulting DB state, then downloads the freshly published LRC from R2 so
the open editor session can be refreshed without leaving the TUI.

This is plain blocking I/O — the caller runs it on a daemon thread. It never
prints, never raises for expected failures, and never touches the UI: every
failure is returned as ``AlignResult(ok=False, error=...)``.
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from stream_of_worship.admin.audit import log_change
from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.editor.regenerate import _mark_failed
from stream_of_worship.admin.services.analysis import (
    AnalysisClient,
    AnalysisResult,
    AnalysisServiceError,
    JobInfo,
)
from stream_of_worship.admin.services.lrc_jobs import (
    alignment_wait_timeout,
    resolve_alignment_lyrics_text,
)
from stream_of_worship.admin.services.r2 import R2Client, R2ObjectIdentity
from stream_of_worship.admin.services.visibility import is_published, reprocess_blocked_reason

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 5.0
R2_RETRY_INTERVAL = 2.0
R2_RETRY_ATTEMPTS = 30
# recordings.lrc_source written after a successful forced alignment; the editor
# header renders this constant so a hotkey `A` refresh needs no extra plumbing.
ALIGNED_LRC_SOURCE = "forced_alignment"


@dataclass
class AlignResult:
    """Outcome of one in-editor forced-alignment attempt."""

    ok: bool
    error: Optional[str] = None
    lrc_content: Optional[str] = None
    identity: Optional[R2ObjectIdentity] = None
    job_id: Optional[str] = None


def align_lrc_for_editor(
    *,
    analysis_client: AnalysisClient,
    db_client: DatabaseClient,
    r2_client: R2Client,
    db_lock: threading.RLock,
    hash_prefix: str,
    language: str = "auto",
    use_vocals_stem: bool = True,
    force: bool = True,
    on_progress: Optional[Callable[[str], None]] = None,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    timeout: Optional[float] = None,
    r2_retry_interval: float = R2_RETRY_INTERVAL,
    r2_retry_attempts: int = R2_RETRY_ATTEMPTS,
) -> AlignResult:
    """Force-align the LRC for ``hash_prefix`` and return its new content.

    Args:
        analysis_client: Client for the analysis service.
        db_client: Catalog DB client; every call runs under ``db_lock``.
        r2_client: Object-store client used for the LRC download.
        db_lock: The editor screen's ``RLock`` — psycopg connections are not
            safe for concurrent use.
        hash_prefix: Recording hash prefix.
        language: Alignment language mode: ``auto``, ``zh`` or ``en`` — the
            same vocabulary as ``lyrics align --language``.
        use_vocals_stem: Align against the Demucs vocals stem (service default;
            more accurate on a full mix).
        force: Bypass the service's alignment result cache. The hotkey is
            itself the confirmation, so this defaults to ``True``.
        on_progress: Optional sink for human-readable progress lines.
        poll_interval: Seconds between job polls.
        timeout: Seconds to wait for the job before failing; ``None`` derives the
            budget from the recording duration (chunk-scaled).
        r2_retry_interval: Seconds between LRC download attempts.
        r2_retry_attempts: Download attempts; the new object may take a
            moment to become readable after the job reports completion.

    Returns:
        AlignResult; ``ok=True`` only when the new LRC was downloaded.
    """
    with db_lock:
        recording = db_client.get_recording_by_hash(hash_prefix)
    if not recording:
        return AlignResult(ok=False, error=f"No recording found for {hash_prefix}.")

    wait_timeout = (
        alignment_wait_timeout(recording.duration_seconds) if timeout is None else timeout
    )

    song_id = recording.song_id or ""
    if is_published(recording):
        return AlignResult(
            ok=False,
            error=reprocess_blocked_reason(song_id, hash_prefix, "re-align LRC"),
        )

    with db_lock:
        song = db_client.get_song(song_id) if song_id else None
    lyrics_text, _source = resolve_alignment_lyrics_text(song, recording, r2_client)
    if not lyrics_text:
        return AlignResult(ok=False, error=f"No lyrics found for song {song_id or hash_prefix}.")

    if not recording.r2_audio_url:
        return AlignResult(ok=False, error=f"Recording {hash_prefix} has no audio URL.")

    try:
        job = analysis_client.submit_forced_alignment(
            audio_url=recording.r2_audio_url,
            content_hash=recording.content_hash,
            lyrics_text=lyrics_text,
            song_title=song.title if song else "",
            language=language,
            force=force,
            use_vocals_stem=use_vocals_stem,
        )
    except AnalysisServiceError as e:
        return AlignResult(ok=False, error=f"Failed to submit forced alignment job: {e}")

    with db_lock:
        db_client.update_recording_status(
            hash_prefix=hash_prefix,
            lrc_status="processing",
            lrc_job_id=job.job_id,
        )
        log_change(
            "lrc_submit",
            song_id=song_id,
            hash_prefix=hash_prefix,
            via="editor_align",
            job_id=job.job_id,
            force=force,
        )

    def _on_job_info(info: JobInfo) -> None:
        if on_progress is not None:
            on_progress(f"Aligning LRC… {int(info.progress * 100)}% [{info.stage}]")

    try:
        final_job = analysis_client.wait_for_completion(
            job.job_id,
            poll_interval=poll_interval,
            timeout=wait_timeout,
            callback=_on_job_info,
        )
    except AnalysisServiceError as e:
        _mark_failed(db_client, db_lock, hash_prefix)
        return AlignResult(ok=False, error=f"Forced alignment failed: {e}", job_id=job.job_id)

    if final_job.status != "completed":
        _mark_failed(db_client, db_lock, hash_prefix)
        return AlignResult(
            ok=False,
            error=final_job.error_message or "Forced alignment failed",
            job_id=job.job_id,
        )

    result = final_job.result if isinstance(final_job.result, AnalysisResult) else None
    lrc_url = result.lrc_url if result else None
    if not lrc_url:
        lrc_url = r2_client.lrc_exists(hash_prefix)
    if not lrc_url:
        _mark_failed(db_client, db_lock, hash_prefix)
        return AlignResult(
            ok=False,
            error="Forced alignment completed but no LRC URL was reported",
            job_id=job.job_id,
        )

    with db_lock:
        db_client.update_recording_lrc(
            hash_prefix=hash_prefix,
            r2_lrc_url=lrc_url,
            visibility_status="review",
            lrc_source=ALIGNED_LRC_SOURCE,
        )
        log_change(
            "lrc_save",
            song_id=song_id,
            hash_prefix=hash_prefix,
            via="editor_align",
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
        return AlignResult(
            ok=False,
            error="LRC job completed but the LRC is not readable from R2 yet",
            job_id=job.job_id,
        )

    identity = r2_client.get_lrc_identity(hash_prefix)
    return AlignResult(ok=True, lrc_content=content, identity=identity, job_id=job.job_id)
