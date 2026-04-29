"""Stem separation worker for generating clean vocals and instrumental.

Ports the algorithm from poc/gen_clean_vocal_stem.py to run as an analysis service job.
Uses pre-loaded AudioSeparatorWrapper for BS-Roformer + UVR-De-Echo processing.
"""

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from ..config import settings
from ..models import Job, JobResult, JobStatus, StemSeparationJobRequest
from ..storage.cache import CacheManager
from ..storage.r2 import R2Client
from .separator_wrapper import AudioSeparatorWrapper

logger = logging.getLogger(__name__)


class StemSeparationWorkerError(Exception):
    """Base exception for stem separation worker errors."""

    pass


async def process_stem_separation(
    job: Job,
    separator_wrapper: AudioSeparatorWrapper,
    r2_client: R2Client,
    cache_manager: CacheManager,
) -> None:
    """Process a stem separation job.

    Downloads audio from R2, runs two-stage separation (BS-Roformer + UVR-De-Echo),
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
    logger.info(f"[{job.id}] Checking for existing clean stems in R2...")

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
            logger.info(f"[{job.id}] Clean stems already exist in R2, skipping")
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
        logger.info(f"[{job.id}] Using cached clean stems from {cache_dir}")
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
        logger.info(f"[{job.id}] Stem separation completed (cached)")
        return

    # Download audio from R2
    job.stage = "downloading"
    job.progress = 0.1
    job.updated_at = datetime.now(timezone.utc)

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        audio_path = temp_path / "audio.mp3"

        logger.info(f"[{job.id}] Downloading audio from R2...")
        await r2_client.download_audio(request.audio_url, audio_path)
        logger.info(f"[{job.id}] Audio download complete: {audio_path}")

        # Stage 1: BS-Roformer separation
        job.stage = "stage1_bs_roformer"
        job.progress = 0.3
        job.updated_at = datetime.now(timezone.utc)

        stage_output_dir = temp_path / "separation"
        logger.info(f"[{job.id}] Starting BS-Roformer separation...")

        try:
            (
                vocals_clean_path,
                vocals_reverb_path,
                instrumental_path,
            ) = await separator_wrapper.separate_stems(audio_path, stage_output_dir)
        except Exception as e:
            raise StemSeparationWorkerError(f"Stem separation failed: {e}") from e

        if not vocals_clean_path or not vocals_clean_path.exists():
            raise StemSeparationWorkerError("Stage 2 failed: No clean vocals file generated")

        job.progress = 0.7

        # Rename outputs to canonical names
        job.stage = "renaming_outputs"
        logger.info(f"[{job.id}] Renaming outputs to canonical names...")

        final_vocals = temp_path / "vocals_clean.flac"
        final_instrumental = temp_path / "instrumental_clean.flac"
        final_vocals_reverb = temp_path / "vocals_reverb.flac"

        shutil.copy2(vocals_clean_path, final_vocals)

        if instrumental_path and instrumental_path.exists():
            shutil.copy2(instrumental_path, final_instrumental)
        else:
            logger.warning(f"[{job.id}] No instrumental file generated")

        if vocals_reverb_path and vocals_reverb_path.exists():
            shutil.copy2(vocals_reverb_path, final_vocals_reverb)
        else:
            logger.warning(f"[{job.id}] No vocals_reverb (Stage 1 vocals) file generated")

        # Cache locally
        job.stage = "caching"
        job.progress = 0.8
        logger.info(f"[{job.id}] Caching results locally...")

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

        logger.info(f"[{job.id}] Uploading clean stems to R2...")
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

        logger.info(f"[{job.id}] Stem separation completed successfully")


async def get_clean_vocals_url(
    content_hash: str,
    r2_client: R2Client,
) -> Optional[str]:
    """Get the URL for clean vocals if it exists.

    Checks for vocals_clean.flac first, then vocals_clean.wav (legacy),
    then vocals.wav (Demucs).

    Args:
        content_hash: Full content hash
        r2_client: R2 client for checking

    Returns:
        S3 URL if found, None otherwise
    """
    hash_prefix = content_hash[:12]
    bucket = settings.SOW_R2_BUCKET

    # Check in priority order
    checks = [
        ("vocals_clean.flac", f"s3://{bucket}/{hash_prefix}/stems/vocals_clean.flac"),
        ("vocals_clean.wav", f"s3://{bucket}/{hash_prefix}/stems/vocals_clean.wav"),
        ("vocals.wav", f"s3://{bucket}/{hash_prefix}/stems/vocals.wav"),
    ]

    for name, url in checks:
        if await r2_client.check_exists(url):
            logger.debug(f"Found vocals stem: {name} at {url}")
            return url

    return None
