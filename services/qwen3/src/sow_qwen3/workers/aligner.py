"""Qwen3ForcedAligner wrapper with async initialization and concurrency control."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable


class Qwen3AlignerWrapper:
    """Async wrapper for Qwen3ForcedAligner with lifecycle management.

    Loads the model lazily at startup via initialize() and unloads it via cleanup().
    Model loading runs in a thread pool to avoid blocking the event loop.
    Alignment requests are limited by a semaphore for concurrency control.
    """

    def __init__(
        self,
        model_path: Path,
        device: str = "auto",
        max_concurrent: int = 1,
    ) -> None:
        """Initialize the aligner wrapper.

        Args:
            model_path: Path to the model (HuggingFace model ID or local path)
            device: Device to run on ("auto", "mps", "cuda", "cpu")
            max_concurrent: Maximum concurrent alignment requests
        """
        self.model_path = model_path
        self.device = device
        self.dtype = "float32"
        self._max_concurrent = max_concurrent
        self._model: object | None = None
        self._ready = False
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def initialize(self) -> None:
        """Load the Qwen3ForcedAligner model.

        Model loading runs in a thread pool to avoid blocking the event loop.
        """
        logger.info("Loading Qwen3ForcedAligner model...")

        loop = asyncio.get_running_loop()

        def _load_model() -> object:
            """Synchronous model loader for thread pool execution.

            Returns:
                Loaded Qwen3ForcedAligner instance
            """
            import torch
            from qwen_asr import Qwen3ForcedAligner

            # Auto-detect device if needed
            device = self.device
            if device == "auto":
                if torch.backends.mps.is_available():
                    device = "mps"
                elif torch.cuda.is_available():
                    device = "cuda"
                else:
                    device = "cpu"

            # Map dtype string to torch dtype
            dtype_map = {
                "bfloat16": torch.bfloat16,
                "float16": torch.float16,
                "float32": torch.float32,
            }
            torch_dtype = dtype_map.get(self.dtype, torch.float32)

            logger.info(
                f"Loading Qwen3ForcedAligner from {self.model_path} "
                f"on device={device}, dtype={self.dtype}"
            )

            # Load model
            model = Qwen3ForcedAligner.from_pretrained(
                str(self.model_path),
                dtype=torch_dtype,
                device_map=device,
            )

            return model

        try:
            self._model = await loop.run_in_executor(None, _load_model)
            self._ready = True
            logger.info("Qwen3ForcedAligner loaded and ready")
        except Exception as e:
            logger.error(f"Failed to load Qwen3ForcedAligner: {e}")
            # Don't raise - allow service to start with model NOT ready
            # Health check will return 503 until model is successfully loaded
            self._ready = False
            self._model = None

    async def align(
        self,
        audio_path: Path,
        lyrics_text: str,
        language: str = "Chinese",
    ) -> list[tuple[float, float, str]]:
        """Align lyrics to audio using the loaded model.

        Args:
            audio_path: Path to audio file
            lyrics_text: The lyrics/text to align to the audio
            language: Language hint (e.g., "Chinese", "English")

        Returns:
            List of (start_time, end_time, text) tuples

        Raises:
            RuntimeError: If model is not ready
        """
        if not self._ready or self._model is None:
            raise RuntimeError("Model not ready. Call initialize() first.")

        loop = asyncio.get_running_loop()

        async with self._semaphore:
            def _call_align() -> list[tuple[float, float, str]]:
                """Synchronous alignment call for thread pool execution.

                Returns:
                    List of (start_time, end_time, text) tuples
                """
                results = self._model.align(
                    audio=str(audio_path),
                    text=lyrics_text,
                    language=language,
                )

                # Extract (start, end, text) tuples from results
                raw_segments = []
                for segment_list in results:
                    for segment in segment_list:
                        text = segment.text.strip()
                        if text:
                            raw_segments.append(
                                (segment.start_time, segment.end_time, text)
                            )

                return raw_segments

            return await loop.run_in_executor(None, _call_align)

    async def cleanup(self) -> None:
        """Unload the model and release resources."""
        self._ready = False
        self._model = None
        logger.info("Qwen3ForcedAligner unloaded")

    @property
    def is_ready(self) -> bool:
        """Check if the model is loaded and ready.

        Returns:
            True if model is ready, False otherwise
        """
        return self._ready
