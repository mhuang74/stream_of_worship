"""LRC generation worker — stub.

Real implementation added in Phase 6 (Whisper transcription + LLM alignment).
"""

from pathlib import Path
from typing import Any


class LRCWorkerNotImplementedError(Exception):
    """Raised when LRC generation is attempted before Phase 6."""

    pass


async def generate_lrc(
    audio_path: Path,
    lyrics_text: str,
    options: Any,
) -> Path:
    """Generate timestamped LRC file.

    TODO (Phase 6): Implement Whisper transcription + LLM line alignment.

    Args:
        audio_path: Path to audio file
        lyrics_text: Original lyrics text
        options: LRC generation options

    Raises:
        LRCWorkerNotImplementedError: Always raised in Phase 4

    Returns:
        Path to generated LRC file (never actually returned in Phase 4)
    """
    raise LRCWorkerNotImplementedError("LRC worker not yet implemented (Phase 6)")
