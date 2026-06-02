"""Upload and save flow for the admin LRC editor.

Handles local draft save, R2 backup, upload with stale-session
detection, and partial failure reporting.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.editor.state import EditorState
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
    recording = db_client.get_recording_by_hash(hash_prefix)
    if recording and recording.lrc_status == "processing" and recording.lrc_job_id:
        return True, recording.lrc_job_id
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


def upload_revised_lrc(
    r2_client: R2Client,
    db_client: DatabaseClient,
    cache_dir: Path,
    state: EditorState,
    original_transcribed_content: Optional[str],
    hash_prefix: str,
) -> UploadResult:
    """Upload the revised LRC to R2 and update the database.

    Performs stale-session check, active job check, creates backups,
    uploads revised LRC, and updates DB.

    Args:
        r2_client: R2 client
        db_client: Database client
        cache_dir: Admin cache directory
        state: Current editor state
        original_transcribed_content: Force-refreshed transcribed LRC content
        hash_prefix: 12-character hash prefix

    Returns:
        UploadResult with outcome details
    """
    changed, reason = check_transcribed_changed(
        r2_client, hash_prefix, state.transcribed_identity,
    )
    if changed:
        return UploadResult(success=False, error=f"Upload blocked: {reason}")

    active, job_id = check_active_lrc_job(db_client, hash_prefix)
    if active:
        return UploadResult(success=False, error=f"Upload blocked: LRC generation job {job_id} is active")

    local_backup_path = None
    r2_backup_url = None

    if original_transcribed_content:
        try:
            local_backup_path = save_local_backup(cache_dir, hash_prefix, original_transcribed_content)
        except Exception as e:
            logger.warning(f"Failed to save local backup: {e}")

        try:
            r2_backup_url = upload_r2_backup(r2_client, hash_prefix, original_transcribed_content)
        except Exception as e:
            logger.warning(f"Failed to upload R2 backup: {e}")

    revised_content = state.serialize()
    revised_path = cache_dir / hash_prefix / "lrc" / "lyrics.upload.tmp.lrc"
    revised_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        revised_path.write_text(revised_content, encoding="utf-8")

        r2_url = r2_client.upload_lrc(revised_path, hash_prefix)
    except Exception as e:
        return UploadResult(
            success=False,
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

    try:
        db_client.update_recording_lrc(hash_prefix=hash_prefix, r2_lrc_url=r2_url)
    except Exception as e:
        return UploadResult(
            success=False,
            partial=True,
            r2_url=r2_url,
            local_backup_path=local_backup_path,
            r2_backup_url=r2_backup_url,
            error=f"R2 upload succeeded but DB update failed: {e}. Rerun status reconciliation.",
        )

    return UploadResult(
        success=True,
        r2_url=r2_url,
        local_backup_path=local_backup_path,
        r2_backup_url=r2_backup_url,
    )
