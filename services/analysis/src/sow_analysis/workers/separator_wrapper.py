"""Async wrapper for audio-separator with lifecycle management.

Mirrors the Qwen3AlignerWrapper pattern from services/qwen3.
Validates model availability at startup and creates per-call Separator
instances for thread safety.
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional, Tuple

from ..config import settings

logger = logging.getLogger(__name__)


class AudioSeparatorWrapper:
    """Async wrapper for audio-separator with startup validation.

    Validates that both BS-Roformer and UVR-De-Echo model files are
    loadable at startup via initialize(). Each call to separate_stems()
    creates its own Separator instances with per-call output directories,
    making the wrapper inherently thread-safe without shared mutable state.

    Attributes:
        model_dir: Directory containing model files
        bs_roformer_model: Name of BS-Roformer model file
        dereverb_model: Name of UVR-De-Echo model file
        output_format: Output audio format (FLAC or WAV)
    """

    def __init__(
        self,
        model_dir: Optional[Path] = None,
        bs_roformer_model: Optional[str] = None,
        dereverb_model: Optional[str] = None,
        output_format: str = "FLAC",
    ) -> None:
        self.model_dir = model_dir or settings.SOW_AUDIO_SEPARATOR_MODEL_DIR
        self.bs_roformer_model = bs_roformer_model or settings.SOW_BS_ROFORMER_MODEL
        self.dereverb_model = dereverb_model or settings.SOW_DEREVERB_MODEL
        self.output_format = output_format

        self._ready = False

    async def initialize(self) -> None:
        """Validate model availability by loading then discarding instances.

        Confirms both models can be loaded successfully. The actual Separator
        instances used during separation are created fresh per call.
        """
        logger.info("Validating audio-separator models...")
        loop = asyncio.get_running_loop()

        def _validate_models() -> None:
            from audio_separator.separator import Separator

            logger.info(f"Validating BS-Roformer model: {self.bs_roformer_model}")
            bs_sep = Separator(
                output_dir=str(settings.CACHE_DIR),
                model_file_dir=str(self.model_dir),
                output_format=self.output_format,
            )
            bs_sep.load_model(model_filename=self.bs_roformer_model)
            del bs_sep
            logger.info(f"BS-Roformer model validated: {self.bs_roformer_model}")

            logger.info(f"Validating UVR-De-Echo model: {self.dereverb_model}")
            dereverb_sep = Separator(
                output_dir=str(settings.CACHE_DIR),
                model_file_dir=str(self.model_dir),
                output_format=self.output_format,
            )
            dereverb_sep.load_model(model_filename=self.dereverb_model)
            del dereverb_sep
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

        Stage 1: Extract vocals and instrumental using BS-Roformer
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
            RuntimeError: If models are not ready
        """
        if not self._ready:
            raise RuntimeError("Models not ready. Call initialize() first.")

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
            sep.load_model(model_filename=self.bs_roformer_model)
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
        stage2_dir.mkdir(exist_ok=True)

        def _run_stage2():
            from audio_separator.separator import Separator

            sep = Separator(
                output_dir=str(stage2_dir),
                model_file_dir=str(self.model_dir),
                output_format=self.output_format,
            )
            sep.load_model(model_filename=self.dereverb_model)
            return sep.separate(str(vocals_file))

        stage2_outputs = await loop.run_in_executor(None, _run_stage2)

        dry_vocals_file: Optional[Path] = None
        for output_file in stage2_outputs:
            output_path = Path(output_file)
            if not output_path.is_absolute():
                output_path = stage2_dir / output_path

            name_lower = output_path.name.lower()
            if "no echo" in name_lower or "dry" in name_lower or "no_echo" in name_lower:
                dry_vocals_file = output_path
                break

        if not dry_vocals_file and stage2_outputs:
            dry_vocals_file = Path(stage2_outputs[0])
            if not dry_vocals_file.is_absolute():
                dry_vocals_file = stage2_dir / dry_vocals_file

        if not dry_vocals_file or not dry_vocals_file.exists():
            logger.error("Stage 2 failed: No dry vocals file found")
            return None, vocals_file, instrumental_file

        return dry_vocals_file, vocals_file, instrumental_file

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
