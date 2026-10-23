"""Analysis workers for processing jobs.

Full analysis runs on Modal (sow-analyzer); librosa is a hard dependency for the
local librosa helpers, so the analyzer imports are unconditional. LRC needs the
openai dependency, hence its optional import.
"""

from .analyzer import analyze_audio
from .queue import Job, JobQueue

# Optional LRC imports - require openai dependencies
try:
    from .lrc import LRCWorkerError, generate_lrc
except ImportError:
    generate_lrc = None
    LRCWorkerError = None

__all__ = [
    "analyze_audio",
    "generate_lrc",
    "LRCWorkerError",
    "JobQueue",
    "Job",
]