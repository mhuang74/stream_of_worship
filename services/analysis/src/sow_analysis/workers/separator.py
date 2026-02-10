"""Stem separation worker using Demucs."""

import asyncio
import logging
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from ..storage.cache import CacheManager

logger = logging.getLogger(__name__)


async def separate_stems(
    audio_path: Path,
    output_dir: Path,
    model: str = "htdemucs",
    device: str = "cpu",
    cache_manager: Optional[CacheManager] = None,
    content_hash: Optional[str] = None,
    force: bool = False,
) -> Path:
    """Separate audio into stems using Demucs.

    Steps:
    1. Check cache for existing stems (if not force)
    2. Run demucs.separate subprocess
    3. Move results to output_dir
    4. Cache results
    5. Return stems directory path

    Args:
        audio_path: Path to input audio file
        output_dir: Directory to save stems
        model: Demucs model name (htdemucs, demucs, etc.)
        device: Device to use (cuda or cpu)
        cache_manager: Optional cache manager for caching
        content_hash: Content hash for cache lookup
        force: Re-process even if cached

    Returns:
        Path to directory containing stem files
    """
    # Check cache first
    if not force and cache_manager and content_hash:
        cached_dir = cache_manager.get_stems_dir(content_hash)
        if cached_dir:
            # Copy cached stems to output_dir
            output_dir.mkdir(parents=True, exist_ok=True)
            for stem in ("bass", "drums", "other", "vocals"):
                src = cached_dir / f"{stem}.wav"
                dst = output_dir / f"{stem}.wav"
                if src.exists():
                    shutil.copy2(str(src), str(dst))
            return output_dir

    # Run demucs in temp directory
    logger.info(f"Starting stem separation with demucs (model={model}, device={device})")
    demucs_start = time.time()

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)

        # Build demucs command
        cmd = [
            sys.executable,
            "-m",
            "demucs.separate",
            "--out",
            temp_path.as_posix(),
            "--name",
            model,
            "--device",
            device,
            audio_path.as_posix(),
        ]

        logger.info(f"Running demucs command: {' '.join(cmd)}")
        cmd_start = time.time()

        # Run demucs subprocess
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
            ),
        )

        cmd_elapsed = time.time() - cmd_start
        logger.info(f"demucs separation completed in {cmd_elapsed:.2f}s")

        # Demucs creates: temp_dir / model / audio_filename / {stem}.wav
        demucs_output_dir = temp_path / model / audio_path.stem

        # Move stems to output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        stem_count = 0
        for stem in ("bass", "drums", "other", "vocals"):
            src = demucs_output_dir / f"{stem}.wav"
            dst = output_dir / f"{stem}.wav"
            if src.exists():
                shutil.move(str(src), str(dst))
                stem_count += 1

    total_elapsed = time.time() - demucs_start
    logger.info(f"Stem separation finished in {total_elapsed:.2f}s - {stem_count} stems saved to {output_dir}")

    # Cache results
    if cache_manager and content_hash:
        cache_manager.save_stems(content_hash, output_dir)

    return output_dir
