"""LRC (Synchronized Lyrics) file parser.

Vendored byte-identical copy of ``ops/admin-cli/src/stream_of_worship/admin/services/lrc_parser.py``.

The analysis-service must not introduce a runtime dependency on admin-cli,
so this module duplicates the LRC parsing logic needed by the CPS helpers.
Both copies must remain byte-identical; the admin-cli source is the source of
truth. Any bugfix in one must be mirrored in the other. A shared package is a
future refactor.

Only the ``parse_lrc`` function and its supporting dataclasses (``LRCFile``,
``LRCLine``) are vendored — that is all the CPS pipeline requires.
"""

import re
from dataclasses import dataclass
from typing import List


@dataclass
class LRCLine:
    """A single timed lyric line.

    Attributes:
        time_seconds: Timestamp in seconds (e.g., 17.20 for [00:17.20])
        text: Lyric text without timestamp
        raw_timestamp: Original timestamp string (e.g., "[00:17.200]")
    """

    time_seconds: float
    text: str
    raw_timestamp: str


@dataclass
class LRCFile:
    """Parsed LRC file with metadata (backward-compatible).

    Attributes:
        lines: All parsed lyric lines
        line_count: Total number of lines
        duration_seconds: Duration from last timestamp
        raw_content: Original file content
    """

    lines: List[LRCLine]
    line_count: int
    duration_seconds: float
    raw_content: str


def _parse_milliseconds(ms_str: str) -> int:
    """Parse centisecond or millisecond string to integer milliseconds.

    Handles both 2-digit (centisecond) and 3-digit (millisecond) formats.
    """
    padded = ms_str.ljust(3, "0")[:3]
    return int(padded)


def parse_lrc(content: str) -> LRCFile:
    """Parse LRC file content (backward-compatible).

    Args:
        content: Raw LRC file content

    Returns:
        Parsed LRC file with all lines and metadata

    Raises:
        ValueError: If no valid LRC lines found
    """
    lines = []
    pattern = r"\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)"

    for line in content.split("\n"):
        match = re.match(pattern, line.strip())
        if match:
            minutes = int(match.group(1))
            seconds = int(match.group(2))
            milliseconds = _parse_milliseconds(match.group(3))
            text = match.group(4).strip()

            time_seconds = minutes * 60 + seconds + milliseconds / 1000.0
            raw_timestamp = f"[{match.group(1)}:{match.group(2)}.{match.group(3)}]"

            lines.append(LRCLine(time_seconds=time_seconds, text=text, raw_timestamp=raw_timestamp))

    if not lines:
        raise ValueError("No valid LRC lines found in file")

    duration_seconds = lines[-1].time_seconds if lines else 0.0

    return LRCFile(
        lines=lines, line_count=len(lines), duration_seconds=duration_seconds, raw_content=content
    )
