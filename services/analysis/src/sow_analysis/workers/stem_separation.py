"""Stem separation worker for generating dry vocals and instrumental.

Ports the algorithm from poc/gen_clean_vocal_stem.py to run as an analysis service job.
Uses AudioSeparatorWrapper for vocal separation + UVR-De-Echo processing.
"""

import asyncio
import logging
import shutil
import tempfile
import time
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Tuple

from ..config import settings
from ..models import Job, JobResult, JobStatus, StemSeparationJobRequest
from ..storage.cache import CacheManager
from ..storage.r2 import R2Client
from .separator_wrapper import AudioSeparatorWrapper

if TYPE_CHECKING:
    from ..services.mvsep_client import MvsepClient

# Import exception at module level to avoid local imports in retry loops
from ..services.mvsep_client import MvsepNonRetriableError

logger = logging.getLogger(__name__)

MVSEP_MAX_RETRIES = 3

# Legacy stem name mappings for cache backward compatibility
CACHE_STEM_LEGACY_NAMES = {
    "vocals_dry": "vocals_clean",
    "vocals": "vocals_reverb",
    "instrumental": "instrumental_clean",
}

CACHE_DIR_LEGACY = "stems_clean"


class StemSeparationWorkerError(Exception):
    """Base exception for stem separation worker errors."""

    pass


def find_cached_stem(cache_manager: "CacheManager", hash_32: str, stem_name: str) -> Optional[Path]:
    """Find a cached stem file, trying new name/dir then legacy fallback.

    Checks the new cache directory (stems/) first, then the old directory
    (stems_clean/) for backward compatibility. When a legacy file is found,
    it is lazily migrated (renamed) to the new path to avoid repeated
    fallback lookups.

    Args:
        cache_manager: Cache manager instance
        hash_32: 32-character content hash
        stem_name: Stem name (e.g., "vocals_dry", "vocals", "instrumental")

    Returns:
        Path if found (possibly after migration), None otherwise.
    """
    new_dir = cache_manager.cache_dir / "stems" / hash_32
    old_dir = cache_manager.cache_dir / CACHE_DIR_LEGACY / hash_32

    # Try new directory, new name
    primary = new_dir / f"{stem_name}.flac"
    if primary.exists():
        return primary

    # Try new directory, legacy name
    legacy_name = CACHE_STEM_LEGACY_NAMES.get(stem_name)
    if legacy_name:
        legacy_in_new = new_dir / f"{legacy_name}.flac"
        if legacy_in_new.exists():
            # Migrate: rename legacy file to new name in new dir
            primary.parent.mkdir(parents=True, exist_ok=True)
            legacy_in_new.rename(primary)
            return primary

    # Try old directory, new name
    if old_dir.exists():
        primary_in_old = old_dir / f"{stem_name}.flac"
        if primary_in_old.exists():
            # Migrate: move file from old dir to new dir
            primary.parent.mkdir(parents=True, exist_ok=True)
            primary_in_old.rename(primary)
            return primary

    # Try old directory, legacy name
    if legacy_name and old_dir.exists():
        legacy_in_old = old_dir / f"{legacy_name}.flac"
        if legacy_in_old.exists():
            # Migrate: move file from old dir to new dir with new name
            primary.parent.mkdir(parents=True, exist_ok=True)
            legacy_in_old.rename(primary)
            return primary

    return None


def _set_job_stage(job: Job, stage: str) -> None:
    """Update job stage in memory (does not persist to store)."""
    from datetime import datetime, timezone

    job.stage = stage
    job.updated_at = datetime.now(timezone.utc)


async def _separate_with_mvsep_fallback(
    input_path: Path,
    output_dir: Path,
    job: Job,
    mvsep_client: Optional["MvsepClient"],
    separator_wrapper: AudioSeparatorWrapper,
    local_model_semaphore: Optional[asyncio.Semaphore] = None,
) -> Tuple[Optional[Path], Optional[Path], Optional[Path]]:
    """Try MVSEP per-stage with cross-backend handoff; fall back to local on failure.

    Args:
        input_path: Path to input audio file
        output_dir: Directory for output files
        job: Job being processed (for stage updates)
        mvsep_client: Optional MVSEP client (None = use local only)
        separator_wrapper: Local separator wrapper for fallback
        local_model_semaphore: Optional semaphore to limit concurrent local model execution.
            Acquired around local separator calls (audio-separator uses BS-Roformer model).
            MVSEP (cloud API) does not acquire this semaphore.

    Returns:
        Tuple of (vocals_dry_path, vocals_path, instrumental_path).
        vocals_dry_path is None when Stage 2 is disabled or skipped.
    """
    total_start = time.monotonic()

    # Check if MVSEP is available
    if not mvsep_client or not mvsep_client.is_available:
        logger.info("MVSEP not available, using local audio-separator")
        sem = local_model_semaphore or nullcontext()
        async with sem:
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
        sem = local_model_semaphore or nullcontext()
        async with sem:
            return await separator_wrapper.separate_stems(input_path, output_dir)

    vocals, instrumental = stage1_result

    if not vocals:
        logger.error("MVSEP Stage 1 succeeded but no vocals file produced")
        _set_job_stage(job, "fallback_local")
        sem = local_model_semaphore or nullcontext()
        async with sem:
            return await separator_wrapper.separate_stems(input_path, output_dir)

    # --- Stage 2: De-reverb (optional) ---
    stage2_enabled = mvsep_client.stage2_sep_type is not None

    if not stage2_enabled:
        logger.info("MVSEP Stage 2 disabled (stage2_sep_type not set), skipping")
        return None, vocals, instrumental

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
        sem = local_model_semaphore or nullcontext()
        async with sem:
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
    local_model_semaphore: Optional[asyncio.Semaphore] = None,
) -> None:
    """Process a stem separation job.

    Downloads audio from R2, runs two-stage separation (vocal model + UVR-De-Echo),
    uploads results to R2, and caches locally.

    Args:
        job: Job to process
        separator_wrapper: Pre-initialized AudioSeparatorWrapper
        r2_client: R2 client for upload/download
        cache_manager: Cache manager for local caching
        mvsep_client: Optional MVSEP client for cloud processing
        local_model_semaphore: Optional semaphore to limit concurrent local model execution.
            Passed to _separate_with_mvsep_fallback() for local fallback paths.

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

    # Check if Stage 2 is enabled (determines which stems are required)
    stage2_enabled = mvsep_client is not None and mvsep_client.stage2_sep_type is not None

    job.stage = "checking_cache"
    logger.info("Checking for existing stems in R2...")

    # Check if already exists (short-circuit) with fallback chain
    vocals_dry_url = await r2_client.check_stem_exists(hash_prefix, "vocals_dry", "flac")
    vocals_url = await r2_client.check_stem_exists(hash_prefix, "vocals", "flac")
    instrumental_url = await r2_client.check_stem_exists(hash_prefix, "instrumental", "flac")

    if not request.options.force:
        if stage2_enabled:
            # All 3 stems required when Stage 2 is enabled
            if vocals_dry_url and vocals_url and instrumental_url:
                logger.info("Stems already exist in R2, skipping")
                job.result = JobResult(
                    vocals_dry_url=vocals_dry_url,
                    vocals_url=vocals_url,
                    instrumental_url=instrumental_url,
                )
                job.status = JobStatus.COMPLETED
                job.progress = 1.0
                job.stage = "complete"
                job.updated_at = datetime.now(timezone.utc)
                return
        else:
            # Only vocals + instrumental required when Stage 2 is skipped
            if vocals_url and instrumental_url:
                logger.info("Stems already exist in R2, skipping")
                job.result = JobResult(
                    vocals_dry_url=vocals_dry_url,  # May be None
                    vocals_url=vocals_url,
                    instrumental_url=instrumental_url,
                )
                job.status = JobStatus.COMPLETED
                job.progress = 1.0
                job.stage = "complete"
                job.updated_at = datetime.now(timezone.utc)
                return

    # Check local cache using find_cached_stem with fallback chain
    cache_vocals_dry = find_cached_stem(cache_manager, hash_32, "vocals_dry")
    cache_vocals = find_cached_stem(cache_manager, hash_32, "vocals")
    cache_instrumental = find_cached_stem(cache_manager, hash_32, "instrumental")

    cache_complete = False
    if not request.options.force:
        if stage2_enabled:
            cache_complete = cache_vocals_dry is not None and cache_vocals is not None and cache_instrumental is not None
        else:
            cache_complete = cache_vocals is not None and cache_instrumental is not None

    if cache_complete:
        logger.info(f"Using cached stems from {cache_manager.cache_dir / 'stems' / hash_32}")
        job.stage = "uploading"
        job.progress = 0.8

        # Upload cached files to R2
        # Return order: (vocals_dry_url, vocals_url, instrumental_url)
        vocals_dry_upload = cache_vocals_dry if cache_vocals_dry and cache_vocals_dry.exists() else None
        vocals_upload = cache_vocals if cache_vocals and cache_vocals.exists() else None
        instrumental_upload = cache_instrumental if cache_instrumental and cache_instrumental.exists() else None

        vocals_dry_r2_url, vocals_r2_url, instrumental_r2_url = await r2_client.upload_clean_stems(
            hash_prefix,
            vocals_dry_upload,
            instrumental_upload,
            vocals_upload,
        )

        job.result = JobResult(
            vocals_dry_url=vocals_dry_r2_url,
            vocals_url=vocals_r2_url,
            instrumental_url=instrumental_r2_url,
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
                vocals_dry_path,
                vocals_path,
                instrumental_path,
            ) = await _separate_with_mvsep_fallback(
                audio_path, stage_output_dir, job, mvsep_client, separator_wrapper,
                local_model_semaphore=local_model_semaphore,
            )
        except Exception as e:
            raise StemSeparationWorkerError(f"Stem separation failed: {e}") from e

        # When Stage 2 is enabled, vocals_dry is required. When skipped, vocals is the primary output.
        if stage2_enabled and (not vocals_dry_path or not vocals_dry_path.exists()):
            raise StemSeparationWorkerError("Stage 2 failed: No dry vocals file generated")

        job.progress = 0.7

        # Rename outputs to canonical names
        job.stage = "renaming_outputs"
        logger.info("Renaming outputs to canonical names...")

        final_vocals_dry = temp_path / "vocals_dry.flac"
        final_vocals = temp_path / "vocals.flac"
        final_instrumental = temp_path / "instrumental.flac"

        if vocals_dry_path and vocals_dry_path.exists():
            shutil.copy2(vocals_dry_path, final_vocals_dry)

        if vocals_path and vocals_path.exists():
            shutil.copy2(vocals_path, final_vocals)
        else:
            logger.warning("No vocals (Stage 1) file generated")

        if instrumental_path and instrumental_path.exists():
            shutil.copy2(instrumental_path, final_instrumental)
        else:
            logger.warning("No instrumental file generated")

        # Cache locally
        job.stage = "caching"
        job.progress = 0.8
        logger.info("Caching results locally...")

        cache_dir = cache_manager.cache_dir / "stems" / hash_32
        cache_dir.mkdir(parents=True, exist_ok=True)

        if final_vocals_dry.exists():
            shutil.copy2(final_vocals_dry, cache_dir / "vocals_dry.flac")
        if final_vocals.exists():
            shutil.copy2(final_vocals, cache_dir / "vocals.flac")
        if final_instrumental.exists():
            shutil.copy2(final_instrumental, cache_dir / "instrumental.flac")

        # Upload to R2
        job.stage = "uploading"
        job.progress = 0.9
        job.updated_at = datetime.now(timezone.utc)

        logger.info("Uploading stems to R2...")
        vocals_dry_upload = final_vocals_dry if final_vocals_dry.exists() else None
        vocals_upload = final_vocals if final_vocals.exists() else None
        instrumental_upload = final_instrumental if final_instrumental.exists() else None

        # Return order: (vocals_dry_url, vocals_url, instrumental_url)
        vocals_dry_url, vocals_r2_url, instrumental_url = await r2_client.upload_clean_stems(
            hash_prefix,
            vocals_dry_upload,
            instrumental_upload,
            vocals_upload,
        )

        # Set result
        job.result = JobResult(
            vocals_dry_url=vocals_dry_url,
            vocals_url=vocals_r2_url,
            instrumental_url=instrumental_url,
        )
        job.status = JobStatus.COMPLETED
        job.progress = 1.0
        job.stage = "complete"
        job.updated_at = datetime.now(timezone.utc)

        logger.info("Stem separation completed successfully")


async def get_vocals_dry_url(
    content_hash: str,
    r2_client: R2Client,
) -> Optional[str]:
    """Get the URL for dry vocals if it exists.

    Checks for vocals_dry.flac first (the canonical dry-vocal stem
    produced by the two-stage vocal separation + UVR-De-Echo pipeline),
    then falls back to vocals_clean.flac (legacy name) for backward
    compatibility. If not found, the caller should trigger stem
    separation to generate it.

    Args:
        content_hash: Full content hash
        r2_client: R2 client for checking

    Returns:
        S3 URL if found, None otherwise
    """
    hash_prefix = content_hash[:12]
    bucket = settings.SOW_R2_BUCKET

    # Try new name first
    url = f"s3://{bucket}/{hash_prefix}/stems/vocals_dry.flac"
    if await r2_client.check_exists(url):
        logger.debug(f"Found dry vocals: {url}")
        return url

    # Fallback to legacy name
    legacy_url = f"s3://{bucket}/{hash_prefix}/stems/vocals_clean.flac"
    if await r2_client.check_exists(legacy_url):
        logger.debug(f"Found dry vocals (legacy): {legacy_url}")
        return legacy_url

    return None
