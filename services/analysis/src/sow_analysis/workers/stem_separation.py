"""Stem separation worker for generating clean vocals and instrumental.

Ports the algorithm from poc/gen_clean_vocal_stem.py to run as an analysis service job.
Uses AudioSeparatorWrapper for vocal separation + UVR-De-Echo processing.
"""

import logging
import shutil
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Tuple

from ..config import settings
from ..models import Job, JobResult, JobStatus, StemSeparationJobRequest
from ..storage.cache import CacheManager
from ..storage.r2 import R2Client
from .separator_wrapper import AudioSeparatorWrapper

if TYPE_CHECKING:
    from ..services.mvsep_client import MvsepClient

logger = logging.getLogger(__name__)

MVSEP_MAX_RETRIES = 3


class StemSeparationWorkerError(Exception):
    """Base exception for stem separation worker errors."""

    pass


def _set_job_stage(job: Job, stage: str) -> None:
    """Update job stage and persist to store."""
    from datetime import datetime, timezone

    job.stage = stage
    job.updated_at = datetime.now(timezone.utc)


async def _separate_with_mvsep_fallback(
    input_path: Path,
    output_dir: Path,
    job: Job,
    mvsep_client: Optional["MvsepClient"],
    separator_wrapper: AudioSeparatorWrapper,
) -> Tuple[Optional[Path], Optional[Path], Optional[Path]]:
    """Try MVSEP per-stage with cross-backend handoff; fall back to local on failure.

    Args:
        input_path: Path to input audio file
        output_dir: Directory for output files
        job: Job being processed (for stage updates)
        mvsep_client: Optional MVSEP client (None = use local only)
        separator_wrapper: Local separator wrapper for fallback

    Returns:
        Tuple of (vocals_clean_path, vocals_reverb_path, instrumental_path)
    """
    total_start = time.monotonic()

    # Check if MVSEP is available
    if not mvsep_client or not mvsep_client.is_available:
        logger.info("MVSEP not available, using local audio-separator")
        return await separator_wrapper.separate_stems(input_path, output_dir)

    def _time_remaining() -> float:
        return settings.SOW_MVSEP_TOTAL_TIMEOUT - (time.monotonic() - total_start)

    # Helper to update job stage
    def stage_callback(stage: str) -> None:
        _set_job_stage(job, stage)
        logger.info(f"MVSEP stage: {stage}")

    # --- Stage 1: Vocal separation ---
    stage1_dir = output_dir / "mvsep_stage1"
    stage1_result = None

    for attempt in range(1, MVSEP_MAX_RETRIES + 1):
        if _time_remaining() <= 0:
            logger.warning("MVSEP total timeout exceeded, falling back to local")
            break

        try:
            vocals, instrumental = await mvsep_client.separate_vocals(
                input_path, stage1_dir, stage_callback
            )
            stage1_result = (vocals, instrumental)
            logger.info(f"MVSEP Stage 1 succeeded on attempt {attempt}")
            break
        except Exception as e:
            from ..services.mvsep_client import MvsepNonRetriableError

            if isinstance(e, MvsepNonRetriableError):
                logger.error(f"MVSEP Stage 1 non-retriable error: {e}")
                break
            logger.warning(f"MVSEP Stage 1 attempt {attempt} failed: {e}")
            if attempt >= MVSEP_MAX_RETRIES:
                logger.error(f"MVSEP Stage 1 exhausted all {MVSEP_MAX_RETRIES} retries")

    if stage1_result is None:
        # Stage 1 MVSEP failed — fall back to full local pipeline
        logger.info("MVSEP Stage 1 failed, falling back to full local pipeline")
        _set_job_stage(job, "fallback_local")
        return await separator_wrapper.separate_stems(input_path, output_dir)

    vocals, instrumental = stage1_result

    if not vocals:
        logger.error("MVSEP Stage 1 succeeded but no vocals file produced")
        _set_job_stage(job, "fallback_local")
        return await separator_wrapper.separate_stems(input_path, output_dir)

    # --- Stage 2: De-reverb ---
    stage2_dir = output_dir / "mvsep_stage2"
    stage2_result = None

    for attempt in range(1, MVSEP_MAX_RETRIES + 1):
        if _time_remaining() <= 0:
            logger.warning("MVSEP total timeout exceeded during Stage 2, using local fallback")
            break

        try:
            dry_vocals, reverb = await mvsep_client.remove_reverb(
                vocals, stage2_dir, stage_callback
            )
            stage2_result = (dry_vocals, reverb)
            logger.info(f"MVSEP Stage 2 succeeded on attempt {attempt}")
            break
        except Exception as e:
            from ..services.mvsep_client import MvsepNonRetriableError

            if isinstance(e, MvsepNonRetriableError):
                logger.error(f"MVSEP Stage 2 non-retriable error: {e}")
                break
            logger.warning(f"MVSEP Stage 2 attempt {attempt} failed: {e}")
            if attempt >= MVSEP_MAX_RETRIES:
                logger.error(f"MVSEP Stage 2 exhausted all {MVSEP_MAX_RETRIES} retries")

    if stage2_result is None:
        # Stage 2 MVSEP failed — local Stage 2 only (cross-backend handoff)
        logger.info("MVSEP Stage 2 failed, using local Stage 2 fallback")
        _set_job_stage(job, "fallback_local_stage2")
        dry_vocals, _ = await separator_wrapper.remove_reverb(vocals, stage2_dir)
        stage2_result = (dry_vocals, None)

    dry_vocals, _ = stage2_result
    return (dry_vocals, vocals, instrumental)


async def process_stem_separation(
    job: Job,
    separator_wrapper: AudioSeparatorWrapper,
    r2_client: R2Client,
    cache_manager: CacheManager,
    mvsep_client: Optional["MvsepClient"] = None,
) -> None:
    """Process a stem separation job.

    Downloads audio from R2, runs two-stage separation (vocal model + UVR-De-Echo),
    uploads results to R2, and caches locally.

    Args:
        job: Job to process
        separator_wrapper: Pre-initialized AudioSeparatorWrapper
        r2_client: R2 client for upload/download
        cache_manager: Cache manager for local caching

    Raises:
        StemSeparationWorkerError: If processing fails
    """
    from datetime import datetime, timezone

    request = job.request
    if not isinstance(request, StemSeparationJobRequest):
        raise StemSeparationWorkerError("Invalid request type for stem separation job")

    content_hash = request.content_hash
    hash_prefix = content_hash[:12]
    hash_32 = content_hash[:32]

    job.stage = "checking_cache"
    logger.info("Checking for existing clean stems in R2...")

    # Check if already exists (short-circuit)
    vocals_clean_url = f"s3://{settings.SOW_R2_BUCKET}/{hash_prefix}/stems/vocals_clean.flac"
    instrumental_clean_url = (
        f"s3://{settings.SOW_R2_BUCKET}/{hash_prefix}/stems/instrumental_clean.flac"
    )
    vocals_reverb_url = f"s3://{settings.SOW_R2_BUCKET}/{hash_prefix}/stems/vocals_reverb.flac"

    if not request.options.force:
        vocals_exists = await r2_client.check_exists(vocals_clean_url)
        instrumental_exists = await r2_client.check_exists(instrumental_clean_url)
        reverb_exists = await r2_client.check_exists(vocals_reverb_url)

        if vocals_exists and instrumental_exists and reverb_exists:
            logger.info("Clean stems already exist in R2, skipping")
            job.result = JobResult(
                vocals_clean_url=vocals_clean_url,
                instrumental_clean_url=instrumental_clean_url,
                vocals_reverb_url=vocals_reverb_url,
            )
            job.status = JobStatus.COMPLETED
            job.progress = 1.0
            job.stage = "complete"
            job.updated_at = datetime.now(timezone.utc)
            return

    # Check local cache
    cache_dir = cache_manager.cache_dir / "stems_clean" / hash_32
    cache_vocals = cache_dir / "vocals_clean.flac"
    cache_instrumental = cache_dir / "instrumental_clean.flac"
    cache_vocals_reverb = cache_dir / "vocals_reverb.flac"

    if (
        cache_vocals.exists()
        and cache_instrumental.exists()
        and cache_vocals_reverb.exists()
        and not request.options.force
    ):
        logger.info(f"Using cached clean stems from {cache_dir}")
        job.stage = "uploading"
        job.progress = 0.8

        # Upload cached files to R2
        vocals_url, instrumental_url, reverb_url = await r2_client.upload_clean_stems(
            hash_prefix,
            cache_vocals,
            cache_instrumental,
            cache_vocals_reverb,
        )

        job.result = JobResult(
            vocals_clean_url=vocals_url,
            instrumental_clean_url=instrumental_url,
            vocals_reverb_url=reverb_url,
        )
        job.status = JobStatus.COMPLETED
        job.progress = 1.0
        job.stage = "complete"
        job.updated_at = datetime.now(timezone.utc)
        logger.info("Stem separation completed (cached)")
        return

    # Download audio from R2
    job.stage = "downloading"
    job.progress = 0.1
    job.updated_at = datetime.now(timezone.utc)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        audio_path = temp_path / "audio.mp3"

        logger.info("Downloading audio from R2...")
        await r2_client.download_audio(request.audio_url, audio_path)
        logger.info(f"Audio download complete: {audio_path}")

        # Stem separation with MVSEP fallback
        stage_output_dir = temp_path / "separation"
        logger.info("Starting stem separation (MVSEP with local fallback)...")

        try:
            (
                vocals_clean_path,
                vocals_reverb_path,
                instrumental_path,
            ) = await _separate_with_mvsep_fallback(
                audio_path, stage_output_dir, job, mvsep_client, separator_wrapper
            )
        except Exception as e:
            raise StemSeparationWorkerError(f"Stem separation failed: {e}") from e

        if not vocals_clean_path or not vocals_clean_path.exists():
            raise StemSeparationWorkerError("Stage 2 failed: No clean vocals file generated")

        job.progress = 0.7

        # Rename outputs to canonical names
        job.stage = "renaming_outputs"
        logger.info("Renaming outputs to canonical names...")

        final_vocals = temp_path / "vocals_clean.flac"
        final_instrumental = temp_path / "instrumental_clean.flac"
        final_vocals_reverb = temp_path / "vocals_reverb.flac"

        shutil.copy2(vocals_clean_path, final_vocals)

        if instrumental_path and instrumental_path.exists():
            shutil.copy2(instrumental_path, final_instrumental)
        else:
            logger.warning("No instrumental file generated")

        if vocals_reverb_path and vocals_reverb_path.exists():
            shutil.copy2(vocals_reverb_path, final_vocals_reverb)
        else:
            logger.warning("No vocals_reverb (Stage 1 vocals) file generated")

        # Cache locally
        job.stage = "caching"
        job.progress = 0.8
        logger.info("Caching results locally...")

        cache_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(final_vocals, cache_vocals)
        if final_instrumental.exists():
            shutil.copy2(final_instrumental, cache_instrumental)
        if final_vocals_reverb.exists():
            shutil.copy2(final_vocals_reverb, cache_vocals_reverb)

        # Upload to R2
        job.stage = "uploading"
        job.progress = 0.9
        job.updated_at = datetime.now(timezone.utc)

        logger.info("Uploading clean stems to R2...")
        vocals_upload = cache_vocals if cache_vocals.exists() else final_vocals
        instrumental_upload = (
            cache_instrumental if cache_instrumental.exists() else final_instrumental
        )
        reverb_upload = (
            cache_vocals_reverb if cache_vocals_reverb.exists() else final_vocals_reverb
        )

        vocals_url, instrumental_url, vocals_reverb_url = await r2_client.upload_clean_stems(
            hash_prefix,
            vocals_upload,
            instrumental_upload if instrumental_upload.exists() else None,
            reverb_upload if reverb_upload.exists() else None,
        )

        # Set result
        job.result = JobResult(
            vocals_clean_url=vocals_url,
            instrumental_clean_url=instrumental_url,
            vocals_reverb_url=vocals_reverb_url,
        )
        job.status = JobStatus.COMPLETED
        job.progress = 1.0
        job.stage = "complete"
        job.updated_at = datetime.now(timezone.utc)

        logger.info("Stem separation completed successfully")


async def get_clean_vocals_url(
    content_hash: str,
    r2_client: R2Client,
) -> Optional[str]:
    """Get the URL for clean vocals if it exists.

    Only checks for vocals_clean.flac (the canonical clean-vocal stem
    produced by the two-stage vocal separation + UVR-De-Echo pipeline).
    If not found, the caller should trigger stem separation to generate it.

    Args:
        content_hash: Full content hash
        r2_client: R2 client for checking

    Returns:
        S3 URL if found, None otherwise
    """
    hash_prefix = content_hash[:12]
    bucket = settings.SOW_R2_BUCKET

    url = f"s3://{bucket}/{hash_prefix}/stems/vocals_clean.flac"
    if await r2_client.check_exists(url):
        logger.debug(f"Found clean vocals: {url}")
        return url

    return None
