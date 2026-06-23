"""Analysis workers for processing jobs."""

# Optional imports - these require heavy dependencies (librosa, allin1, demucs)
# that are only available in the Docker container
try:
    from .analyzer import analyze_audio
    from .separator import separate_stems
except ImportError:
    analyze_audio = None
    separate_stems = None

# Optional LRC imports - require whisper and openai dependencies
try:
    from .lrc import generate_lrc, LRCWorkerError
except ImportError:
    generate_lrc = None
    LRCWorkerError = None

from .queue import JobQueue, Job

__all__ = [
    "analyze_audio",
    "separate_stems",
    "generate_lrc",
    "LRCWorkerError",
    "JobQueue",
    "Job",
]
