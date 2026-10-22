"""Upload and save flow for the admin LRC editor.

Handles local draft save, R2 backup, upload with stale-session
detection, and partial failure reporting.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import psycopg

from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.services.r2 import R2Client, R2ObjectIdentity

logger = logging.getLogger(__name__)


def generate_timestamped_filename() -> str:
    """Generate a timestamped filename component like '20260102-143055'."""
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def save_local_draft(cache_dir: Path, hash_prefix: str, lrc_content: str) -> Path:
    """Save a timestamped local draft LRC file.

    Drafts never overwrite previous work.

    Args:
        cache_dir: Admin cache directory
        hash_prefix: 12-character hash prefix
        lrc_content: Serialized LRC content

    Returns:
        Path to the saved draft file
    """
    lrc_dir = cache_dir / hash_prefix / "lrc"
    lrc_dir.mkdir(parents=True, exist_ok=True)

    ts = generate_timestamped_filename()
    draft_path = lrc_dir / f"lyrics.edited.{ts}.lrc"

    tmp_path = draft_path.with_suffix(".tmp")
    try:
        tmp_path.write_text(lrc_content, encoding="utf-8")
        tmp_path.replace(draft_path)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise

    return draft_path


def save_local_backup(cache_dir: Path, hash_prefix: str, transcribed_content: str) -> Optional[Path]:
    """Save a local timestamped backup of the current transcribed LRC content.

    Args:
        cache_dir: Admin cache directory
        hash_prefix: 12-character hash prefix
        transcribed_content: Current transcribed LRC content

    Returns:
        Path to the backup file, or None if no transcribed content
    """
    if not transcribed_content:
        return None

    lrc_dir = cache_dir / hash_prefix / "lrc"
    lrc_dir.mkdir(parents=True, exist_ok=True)

    ts = generate_timestamped_filename()
    backup_path = lrc_dir / f"lyrics.backup.{ts}.lrc"

    tmp_path = backup_path.with_suffix(".tmp")
    try:
        tmp_path.write_text(transcribed_content, encoding="utf-8")
        tmp_path.replace(backup_path)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise

    return backup_path


def upload_r2_backup(r2_client: R2Client, hash_prefix: str, transcribed_content: str) -> Optional[str]:
    """Upload a timestamped backup of the current transcribed LRC to R2.

    Args:
        r2_client: R2 client
        hash_prefix: 12-character hash prefix
        transcribed_content: Current transcribed LRC content

    Returns:
        S3 URL of the backup, or None if no transcribed content
    """
    if not transcribed_content:
        return None

    ts = generate_timestamped_filename()
    backup_key = f"{hash_prefix}/backups/lyrics.{ts}.lrc"

    return r2_client.upload_bytes(backup_key, transcribed_content.encode("utf-8"), content_type="text/plain")


def check_transcribed_changed(
    r2_client: R2Client,
    hash_prefix: str,
    original_identity: R2ObjectIdentity,
) -> Tuple[bool, str]:
    """Re-check transcribed R2 LRC identity against session token.

    Args:
        r2_client: R2 client
        hash_prefix: 12-character hash prefix
        original_identity: Session token captured at editor open

    Returns:
        Tuple of (changed: bool, reason: str)
    """
    current_identity = r2_client.get_lrc_identity(hash_prefix)

    if original_identity.exists and not current_identity.exists:
        return True, "Transcribed LRC disappeared from R2 since editor open"

    if not original_identity.exists and current_identity.exists:
        return True, "A new transcribed LRC appeared in R2 while editing a draft session"

    if original_identity.exists and current_identity.exists:
        if original_identity.etag and current_identity.etag and original_identity.etag != current_identity.etag:
            return True, "Transcribed LRC ETag changed since editor open (stale session)"

    return False, ""


def check_active_lrc_job(db_client: DatabaseClient, hash_prefix: str) -> Tuple[bool, str]:
    """Check if an active LRC generation job is running.

    Args:
        db_client: Database client
        hash_prefix: 12-character hash prefix

    Returns:
        Tuple of (active: bool, job_id: str)
    """
    try:
        recording = db_client.get_recording_by_hash(hash_prefix)
        if recording and recording.lrc_status == "processing" and recording.lrc_job_id:
            return True, recording.lrc_job_id
    except psycopg.OperationalError:
        logger.warning("DB unreachable while checking active LRC job; skipping check")
    return False, ""


@dataclass
class UploadResult:
    """Result of an upload attempt.

    Attributes:
        success: Whether the upload fully succeeded
        partial: Whether R2 upload succeeded but DB update failed
        r2_url: S3 URL of the uploaded LRC
        local_backup_path: Path to local transcribed backup
        r2_backup_url: S3 URL of R2 transcribed backup
        error: Error message if upload failed
    """

    success: bool
    partial: bool = False
    r2_url: Optional[str] = None
    local_backup_path: Optional[Path] = None
    r2_backup_url: Optional[str] = None
    error: Optional[str] = None


@dataclass
class R2UploadOutcome:
    """Result of the R2 half of an upload (no database writes)."""

    r2_url: Optional[str] = None
    local_backup_path: Optional[Path] = None
    r2_backup_url: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.r2_url is not None


def _log_stage(stage: str, started: float, **fields: object) -> None:
    """Log one timed save stage as a single INFO line."""
    elapsed_ms = (time.monotonic() - started) * 1000.0
    extra = " ".join(f"{k}={v}" for k, v in fields.items())
    logger.info(
        "save_upload stage=%s elapsed_ms=%.1f%s",
        stage,
        elapsed_ms,
        f" {extra}" if extra else "",
    )


def upload_revised_lrc(
    r2_client: R2Client,
    cache_dir: Path,
    revised_content: str,
    transcribed_identity: R2ObjectIdentity,
    original_transcribed_content: Optional[str],
    hash_prefix: str,
    force: bool = False,
) -> R2UploadOutcome:
    """Upload the revised LRC to R2 (no database access).

    Performs the stale-session check, creates backups, and uploads the revised
    LRC. Safe to call from a Textual thread worker: every stage is blocking
    boto3/filesystem I/O.

    Args:
        r2_client: R2 client
        cache_dir: Admin cache directory
        revised_content: Serialized revised LRC snapshot to upload
        transcribed_identity: R2 identity captured at editor open
        original_transcribed_content: Force-refreshed transcribed LRC content
        hash_prefix: 12-character hash prefix
        force: Skip stale-session (ETag) check when True

    Returns:
        R2UploadOutcome with outcome details
    """
    started = time.monotonic()

    stage_started = time.monotonic()
    if not force:
        changed, reason = check_transcribed_changed(
            r2_client, hash_prefix, transcribed_identity,
        )
        if changed:
            _log_stage("conflict_check", stage_started, blocked=True)
            logger.warning(
                "save_upload stage=conflict_check elapsed_ms=%.1f blocked=true reason=%s",
                (time.monotonic() - stage_started) * 1000.0,
                reason,
            )
            return R2UploadOutcome(error=f"Upload blocked: {reason}")
    else:
        logger.warning("ETag stale-session check skipped (force=True)")
    _log_stage("conflict_check", stage_started, blocked=False, forced=force)

    local_backup_path = None
    r2_backup_url = None

    if original_transcribed_content:
        stage_started = time.monotonic()
        try:
            local_backup_path = save_local_backup(cache_dir, hash_prefix, original_transcribed_content)
        except Exception as e:
            logger.warning(f"Failed to save local backup: {e}")
        _log_stage("local_backup", stage_started, ok=local_backup_path is not None)

        stage_started = time.monotonic()
        try:
            r2_backup_url = upload_r2_backup(r2_client, hash_prefix, original_transcribed_content)
        except Exception as e:
            logger.warning(f"Failed to upload R2 backup: {e}")
        _log_stage("r2_backup", stage_started, ok=r2_backup_url is not None)

    revised_path = cache_dir / hash_prefix / "lrc" / "lyrics.upload.tmp.lrc"
    revised_path.parent.mkdir(parents=True, exist_ok=True)

    stage_started = time.monotonic()
    try:
        revised_path.write_text(revised_content, encoding="utf-8")

        # Use upload_official_lrc for backup + upload
        # The editor's stale-session check (check_transcribed_changed) already handles
        # ETag-based protection, so we don't pass expected_etag here.
        r2_url = r2_client.upload_official_lrc(hash_prefix, revised_path)
    except Exception as e:
        _log_stage("r2_upload", stage_started, ok=False)
        _log_stage("total", started, ok=False)
        return R2UploadOutcome(
            local_backup_path=local_backup_path,
            r2_backup_url=r2_backup_url,
            error=f"R2 upload failed: {e}",
        )
    finally:
        try:
            if revised_path.exists():
                revised_path.unlink()
        except OSError:
            pass

    _log_stage("r2_upload", stage_started, ok=True, bytes=len(revised_content.encode("utf-8")))
    _log_stage("total", started, ok=True)
    return R2UploadOutcome(
        r2_url=r2_url,
        local_backup_path=local_backup_path,
        r2_backup_url=r2_backup_url,
    )


def finalize_upload(
    db_client: DatabaseClient,
    hash_prefix: str,
    outcome: R2UploadOutcome,
) -> UploadResult:
    """Write the uploaded LRC URL to the database (the only DB writer).

    Args:
        db_client: Database client
        hash_prefix: 12-character hash prefix
        outcome: Result of the R2 half of the upload

    Returns:
        UploadResult combining R2 and database outcomes
    """
    if not outcome.ok:
        return UploadResult(
            success=False,
            local_backup_path=outcome.local_backup_path,
            r2_backup_url=outcome.r2_backup_url,
            error=outcome.error or "R2 upload did not return a URL",
        )

    r2_url = outcome.r2_url
    stage_started = time.monotonic()
    try:
        db_client.update_recording_lrc(hash_prefix=hash_prefix, r2_lrc_url=r2_url)
    except Exception as e:
        _log_stage("db_update", stage_started, ok=False)
        return UploadResult(
            success=False,
            partial=True,
            r2_url=r2_url,
            local_backup_path=outcome.local_backup_path,
            r2_backup_url=outcome.r2_backup_url,
            error=f"R2 upload succeeded but DB update failed: {e}. Rerun status reconciliation.",
        )

    _log_stage("db_update", stage_started, ok=True)
    return UploadResult(
        success=True,
        r2_url=r2_url,
        local_backup_path=outcome.local_backup_path,
        r2_backup_url=outcome.r2_backup_url,
    )
