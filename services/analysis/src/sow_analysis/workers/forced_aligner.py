"""ForcedAligner wrapper with lazy initialization and double-check locking."""

from __future__ import annotations

import asyncio
import gc
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ForcedAlignerWrapper:
    """Async wrapper for Qwen3ForcedAligner with lifecycle management.

    Loads the model lazily on first use via _ensure_ready() with double-check
    locking. Concurrency is controlled externally by JobQueue via
    optional_semaphore() around the align() call only.

    Key differences from old Qwen3AlignerWrapper:
    - No internal semaphore (concurrency is external)
    - Double-check locking via asyncio.Lock
    - Raises RuntimeError on init failure
    - GPU memory cleanup in cleanup()
    """

    def __init__(
        self,
        model_path: str = "Qwen/Qwen3-ForcedAligner-0.6B",
        device: str = "auto",
    ) -> None:
        self.model_path = model_path
        self.device = device
        self.dtype = "float32"
        self._model: Optional[object] = None
        self._ready = False
        self._init_lock = asyncio.Lock()

    async def _ensure_ready(self) -> None:
        """Lazily initialize model on first use with double-check locking."""
        if self._ready:
            return
        async with self._init_lock:
            if self._ready:
                return
            await self.initialize()
            if not self._ready:
                raise RuntimeError(
                    "ForcedAligner model failed to load. Check model path and device."
                )

    async def initialize(self) -> None:
        """Load the Qwen3ForcedAligner model (runs in thread pool)."""
        logger.info("Loading Qwen3ForcedAligner model...")

        loop = asyncio.get_running_loop()

        def _load_model() -> object:
            import torch
            from qwen_asr import Qwen3ForcedAligner

            device = self.device
            if device == "auto":
                if torch.backends.mps.is_available():
                    device = "mps"
                elif torch.cuda.is_available():
                    device = "cuda"
                else:
                    device = "cpu"

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
            self._ready = False
            self._model = None

    async def align(
        self,
        audio_path: Path,
        lyrics_text: str,
        language: str = "Chinese",
    ) -> list[tuple[float, float, str]]:
        """Align lyrics to audio using the loaded model.

        No semaphore acquisition inside this method — concurrency is
        controlled externally by JobQueue.

        Returns:
            List of (start_time, end_time, text) tuples
        """
        await self._ensure_ready()

        loop = asyncio.get_running_loop()

        def _call_align() -> list[tuple[float, float, str]]:
            results = self._model.align(
                audio=str(audio_path),
                text=lyrics_text,
                language=language,
            )

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
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        logger.info("ForcedAligner unloaded")

    @property
    def is_ready(self) -> bool:
        return self._ready
