"""LRC (Synchronized Lyrics) file parser and utilities."""

import re
from dataclasses import dataclass
from typing import List


@dataclass
class LRCLine:
    """A single line of synchronized lyrics.

    Attributes:
        time_seconds: Timestamp in seconds (e.g., 17.2 for [00:17.200])
        text: Lyric text without timestamp
        raw_timestamp: Original timestamp string (e.g., "[00:17.200]")
    """

    time_seconds: float
    text: str
    raw_timestamp: str


@dataclass
class LRCFile:
    """Parsed LRC file with metadata.

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


def parse_lrc(content: str) -> LRCFile:
    """Parse LRC file content.

    Args:
        content: Raw LRC file content

    Returns:
        Parsed LRC file with all lines and metadata

    Raises:
        ValueError: If no valid LRC lines found
    """
    lines = []
    # Match [mm:ss.xx] or [mm:ss.xxx] format
    pattern = r"\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)"

    for line in content.split("\n"):
        match = re.match(pattern, line.strip())
        if match:
            minutes = int(match.group(1))
            seconds = int(match.group(2))
            milliseconds = int(match.group(3).ljust(3, "0")[:3])
            text = match.group(4).strip()

            time_seconds = minutes * 60 + seconds + milliseconds / 1000.0
            raw_timestamp = f"[{match.group(1)}:{match.group(2)}.{match.group(3)}]"

            # Include all lines, even empty ones
            lines.append(LRCLine(time_seconds=time_seconds, text=text, raw_timestamp=raw_timestamp))

    if not lines:
        raise ValueError("No valid LRC lines found in file")

    duration_seconds = lines[-1].time_seconds if lines else 0.0

    return LRCFile(
        lines=lines, line_count=len(lines), duration_seconds=duration_seconds, raw_content=content
    )


def format_duration(seconds: float) -> str:
    """Format duration in seconds to MM:SS format.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string like "4:19" or "12:05"
    """
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}:{secs:02d}"
