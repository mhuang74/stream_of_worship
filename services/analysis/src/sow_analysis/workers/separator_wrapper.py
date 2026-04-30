"""Async wrapper for audio-separator with lazy initialization.

Mirrors the Qwen3AlignerWrapper pattern from services/qwen3.
Validates model availability on first use and creates per-call Separator
instances for thread safety.
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional, Tuple

from ..config import settings

logger = logging.getLogger(__name__)


class AudioSeparatorWrapper:
    """Async wrapper for audio-separator with lazy initialization.

    Models are validated on first use via _ensure_ready(), not at startup.
    Each call to separate_stems() creates its own Separator instances with
    per-call output directories, making the wrapper inherently thread-safe
    without shared mutable state.

    Attributes:
        model_dir: Directory containing model files
        vocal_model: Name of vocal separation model file
        dereverb_model: Name of UVR-De-Echo model file
        output_format: Output audio format (FLAC or WAV)
    """

    def __init__(
        self,
        model_dir: Optional[Path] = None,
        vocal_model: Optional[str] = None,
        dereverb_model: Optional[str] = None,
        output_format: str = "FLAC",
    ) -> None:
        self.model_dir = model_dir or settings.SOW_AUDIO_SEPARATOR_MODEL_DIR
        self.vocal_model = vocal_model or settings.SOW_VOCAL_SEPARATION_MODEL
        self.dereverb_model = dereverb_model or settings.SOW_DEREVERB_MODEL
        self.output_format = output_format

        self._ready = False
        self._init_lock = asyncio.Lock()

    async def _ensure_ready(self) -> None:
        """Lazily initialize models on first use. Thread-safe via lock."""
        if self._ready:
            return
        async with self._init_lock:
            if self._ready:
                return
            await self.initialize()
            if not self._ready:
                raise RuntimeError("Model validation failed. Check that model files exist.")

    async def initialize(self) -> None:
        """Validate model availability by loading then discarding instances.

        Confirms both models can be loaded successfully. The actual Separator
        instances used during separation are created fresh per call.
        """
        logger.info("Validating audio-separator models...")
        loop = asyncio.get_running_loop()

        def _validate_models() -> None:
            from audio_separator.separator import Separator

            logger.info(f"Validating vocal separation model: {self.vocal_model}")
            sep1 = Separator(
                output_dir=str(settings.CACHE_DIR),
                model_file_dir=str(self.model_dir),
                output_format=self.output_format,
            )
            sep1.load_model(model_filename=self.vocal_model)
            del sep1
            logger.info(f"Vocal separation model validated: {self.vocal_model}")

            logger.info(f"Validating UVR-De-Echo model: {self.dereverb_model}")
            sep2 = Separator(
                output_dir=str(settings.CACHE_DIR),
                model_file_dir=str(self.model_dir),
                output_format=self.output_format,
            )
            sep2.load_model(model_filename=self.dereverb_model)
            del sep2
            logger.info(f"UVR-De-Echo model validated: {self.dereverb_model}")

        try:
            await loop.run_in_executor(None, _validate_models)
            self._ready = True
            logger.info("Audio-separator models validated and ready")
        except Exception as e:
            logger.error(f"Failed to validate audio-separator models: {e}")
            self._ready = False

    async def separate_stems(
        self,
        input_path: Path,
        output_dir: Path,
    ) -> Tuple[Optional[Path], Optional[Path], Optional[Path]]:
        """Run two-stage stem separation.

        Stage 1: Extract vocals and instrumental
        Stage 2: Remove echo/reverb from vocals using UVR-De-Echo

        Each stage creates its own Separator instance with the correct
        output directory, avoiding shared mutable state and making this
        method safe for concurrent use.

        Args:
            input_path: Path to input audio file
            output_dir: Directory for output files

        Returns:
            Tuple of (vocals_clean_path, vocals_reverb_path, instrumental_path).
            vocals_reverb_path is the Stage 1 vocals before de-echo (still contains
            reverb), useful for transition processing. Any element may be None if
            the corresponding stage failed to produce output.

        Raises:
            RuntimeError: If model validation fails on first use
        """
        await self._ensure_ready()

        loop = asyncio.get_running_loop()
        output_dir.mkdir(parents=True, exist_ok=True)

        stage1_dir = output_dir / "stage1"
        stage1_dir.mkdir(exist_ok=True)

        def _run_stage1():
            from audio_separator.separator import Separator

            sep = Separator(
                output_dir=str(stage1_dir),
                model_file_dir=str(self.model_dir),
                output_format=self.output_format,
            )
            sep.load_model(model_filename=self.vocal_model)
            return sep.separate(str(input_path))

        stage1_outputs = await loop.run_in_executor(None, _run_stage1)

        vocals_file: Optional[Path] = None
        instrumental_file: Optional[Path] = None

        for output_file in stage1_outputs:
            output_path = Path(output_file)
            if not output_path.is_absolute():
                output_path = stage1_dir / output_path

            name_lower = output_path.name.lower()
            if "vocals" in name_lower:
                vocals_file = output_path
            elif "instrumental" in name_lower:
                instrumental_file = output_path

        if not vocals_file or not vocals_file.exists():
            logger.error("Stage 1 failed: No vocals file found")
            return None, None, None

        if not instrumental_file or not instrumental_file.exists():
            logger.warning("Stage 1: No instrumental file found")

        stage2_dir = output_dir / "stage2"
        dry_vocals_file, _ = await self.remove_reverb(vocals_file, stage2_dir)

        if not dry_vocals_file or not dry_vocals_file.exists():
            logger.error("Stage 2 failed: No dry vocals file found")
            return None, vocals_file, instrumental_file

        return dry_vocals_file, vocals_file, instrumental_file

    async def remove_reverb(
        self,
        vocals_path: Path,
        output_dir: Path,
    ) -> tuple[Optional[Path], Optional[Path]]:
        """Run Stage 2 only: remove echo/reverb from vocals using UVR-De-Echo.

        Used as a local fallback when MVSEP Stage 1 succeeds but Stage 2 fails,
        avoiding re-running Stage 1 locally.

        Args:
            vocals_path: Path to vocals file (Stage 1 output)
            output_dir: Directory for output files

        Returns:
            Tuple of (dry_vocals_path, reverb_path).
            Either element may be None if the stage failed to produce output.

        Raises:
            RuntimeError: If model validation fails on first use
        """
        await self._ensure_ready()

        loop = asyncio.get_running_loop()
        output_dir.mkdir(parents=True, exist_ok=True)

        def _run_stage2():
            from audio_separator.separator import Separator

            sep = Separator(
                output_dir=str(output_dir),
                model_file_dir=str(self.model_dir),
                output_format=self.output_format,
            )
            sep.load_model(model_filename=self.dereverb_model)
            return sep.separate(str(vocals_path))

        stage2_outputs = await loop.run_in_executor(None, _run_stage2)

        dry_vocals_file: Optional[Path] = None
        reverb_file: Optional[Path] = None
        for output_file in stage2_outputs:
            output_path = Path(output_file)
            if not output_path.is_absolute():
                output_path = output_dir / output_path

            name_lower = output_path.name.lower()
            if "no echo" in name_lower or "dry" in name_lower or "no_echo" in name_lower:
                dry_vocals_file = output_path
            elif "reverb" in name_lower or "echo" in name_lower:
                reverb_file = output_path

        if not dry_vocals_file and stage2_outputs:
            dry_vocals_file = Path(stage2_outputs[0])
            if not dry_vocals_file.is_absolute():
                dry_vocals_file = output_dir / dry_vocals_file

        return dry_vocals_file, reverb_file

    async def cleanup(self) -> None:
        """Release resources (no persistent models to unload)."""
        self._ready = False
        import gc

        gc.collect()
        logger.info("Audio-separator wrapper cleaned up")

    @property
    def is_ready(self) -> bool:
        """Check if models are validated and ready."""
        return self._ready
