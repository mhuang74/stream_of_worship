"""Async wrapper for audio-separator with lifecycle management.

Mirrors the Qwen3AlignerWrapper pattern from services/qwen3.
Loads models at startup and keeps them resident for reuse.
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional, Tuple

from ..config import settings

logger = logging.getLogger(__name__)


class AudioSeparatorWrapper:
    """Async wrapper for audio-separator with pre-loaded models.

    Loads both BS-Roformer and UVR-De-Echo models at startup via initialize()
    and unloads via cleanup(). Model loading runs in a thread pool to avoid
    blocking the event loop.

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
        """Initialize wrapper with model configuration.

        Args:
            model_dir: Directory containing model files (default: from settings)
            bs_roformer_model: BS-Roformer model filename (default: from settings)
            dereverb_model: UVR-De-Echo model filename (default: from settings)
            output_format: Output audio format (default: FLAC)
        """
        self.model_dir = model_dir or settings.SOW_AUDIO_SEPARATOR_MODEL_DIR
        self.bs_roformer_model = bs_roformer_model or settings.SOW_BS_ROFORMER_MODEL
        self.dereverb_model = dereverb_model or settings.SOW_DEREVERB_MODEL
        self.output_format = output_format

        self._bs_roformer_separator: Optional[object] = None
        self._dereverb_separator: Optional[object] = None
        self._ready = False

    async def initialize(self) -> None:
        """Load both models in thread pool. Allows graceful failure."""
        logger.info("Loading audio-separator models...")
        loop = asyncio.get_running_loop()

        def _load_models() -> Tuple[object, object]:
            """Synchronous model loader for thread pool."""
            from audio_separator.separator import Separator

            # Load BS-Roformer model
            logger.info(f"Loading BS-Roformer model: {self.bs_roformer_model}")
            bs_separator = Separator(
                output_dir=str(settings.CACHE_DIR),
                model_file_dir=str(self.model_dir),
                output_format=self.output_format,
            )
            bs_separator.load_model(model_filename=self.bs_roformer_model)
            logger.info(f"BS-Roformer model loaded successfully: {self.bs_roformer_model}")

            # Load UVR-De-Echo model
            logger.info(f"Loading UVR-De-Echo model: {self.dereverb_model}")
            dereverb_separator = Separator(
                output_dir=str(settings.CACHE_DIR),
                model_file_dir=str(self.model_dir),
                output_format=self.output_format,
            )
            dereverb_separator.load_model(model_filename=self.dereverb_model)
            logger.info(f"UVR-De-Echo model loaded successfully: {self.dereverb_model}")

            return bs_separator, dereverb_separator

        try:
            self._bs_roformer_separator, self._dereverb_separator = await loop.run_in_executor(
                None, _load_models
            )
            self._ready = True
            logger.info("Audio-separator models loaded and ready")
        except Exception as e:
            logger.error(f"Failed to load audio-separator models: {e}")
            # Don't raise - allow service to start with models NOT ready
            # Health check will indicate status until models load
            self._ready = False
            self._bs_roformer_separator = None
            self._dereverb_separator = None

    async def separate_stems(
        self,
        input_path: Path,
        output_dir: Path,
    ) -> Tuple[Optional[Path], Optional[Path], Optional[Path]]:
        """Run two-stage stem separation.

        Stage 1: Extract vocals and instrumental using BS-Roformer
        Stage 2: Remove echo/reverb from vocals using UVR-De-Echo

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
        if not self._ready or not self._bs_roformer_separator or not self._dereverb_separator:
            raise RuntimeError("Models not ready. Call initialize() first.")

        loop = asyncio.get_running_loop()
        output_dir.mkdir(parents=True, exist_ok=True)

        # Stage 1: BS-Roformer separation
        stage1_dir = output_dir / "stage1"
        stage1_dir.mkdir(exist_ok=True)

        def _run_stage1():
            """Synchronous stage 1 separation."""
            sep = self._bs_roformer_separator
            original = sep.model_instance.output_dir
            sep.output_dir = str(stage1_dir)
            sep.model_instance.output_dir = str(stage1_dir)
            try:
                return sep.separate(str(input_path))
            finally:
                sep.output_dir = original
                sep.model_instance.output_dir = original

        stage1_outputs = await loop.run_in_executor(None, _run_stage1)

        # Find vocals and instrumental from stage 1
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

        # Stage 2: UVR-De-Echo on vocals
        stage2_dir = output_dir / "stage2"
        stage2_dir.mkdir(exist_ok=True)

        def _run_stage2():
            """Synchronous stage 2 dereverb."""
            sep = self._dereverb_separator
            original = sep.model_instance.output_dir
            sep.output_dir = str(stage2_dir)
            sep.model_instance.output_dir = str(stage2_dir)
            try:
                return sep.separate(str(vocals_file))
            finally:
                sep.output_dir = original
                sep.model_instance.output_dir = original

        stage2_outputs = await loop.run_in_executor(None, _run_stage2)

        # Find dry vocals from stage 2
        dry_vocals_file: Optional[Path] = None
        for output_file in stage2_outputs:
            output_path = Path(output_file)
            if not output_path.is_absolute():
                output_path = stage2_dir / output_path

            name_lower = output_path.name.lower()
            # De-Echo models typically output "No Echo" and "Echo" stems
            if "no echo" in name_lower or "dry" in name_lower or "no_echo" in name_lower:
                dry_vocals_file = output_path
                break

        # Fallback: take first output
        if not dry_vocals_file and stage2_outputs:
            dry_vocals_file = Path(stage2_outputs[0])
            if not dry_vocals_file.is_absolute():
                dry_vocals_file = stage2_dir / dry_vocals_file

        if not dry_vocals_file or not dry_vocals_file.exists():
            logger.error("Stage 2 failed: No dry vocals file found")
            return None, vocals_file, instrumental_file

        return dry_vocals_file, vocals_file, instrumental_file

    async def cleanup(self) -> None:
        """Unload models and release resources."""
        self._ready = False
        self._bs_roformer_separator = None
        self._dereverb_separator = None
        import gc

        gc.collect()
        logger.info("Audio-separator models unloaded")

    @property
    def is_ready(self) -> bool:
        """Check if models are loaded and ready."""
        return self._ready
