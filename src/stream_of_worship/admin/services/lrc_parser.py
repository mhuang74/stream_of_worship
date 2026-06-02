"""LRC (Synchronized Lyrics) file parser, serializer, and utilities.

Supports parsing LRC content into editable timed lyric rows plus preserved
non-editable content (metadata tags, unknown lines). Serialization writes
canonical ``[mm:ss.xx]`` centisecond timestamps while preserving metadata
and unknown lines in their original relative positions.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import List, Optional


RECOGNIZED_METADATA_TAGS = frozenset({"ti", "ar", "al", "by", "offset", "re", "ve"})

_TIMED_LINE_RE = re.compile(r"^\[(\d{2}):(\d{2})\.(\d{2,3})\]\s*(.*)")
_METADATA_TAG_RE = re.compile(r"^\[([a-zA-Z]+):(.*)\]\s*$")


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
class LRCPreservedLine:
    """A non-timestamp line preserved from the original LRC.

    Covers recognized metadata tags (``[ti:...]``, ``[ar:...]``, etc.)
    and any unrecognized lines that are not timed lyric rows.

    Attributes:
        raw: The original line text including any brackets
        tag: Metadata tag key (e.g., "ti") for recognized tags, None otherwise
        value: Metadata tag value for recognized tags, None otherwise
    """

    raw: str
    tag: Optional[str] = None
    value: Optional[str] = None


@dataclass
class LRCParsedContent:
    """Full parsed LRC content with editable rows and preserved lines.

    Attributes:
        timed_lines: Editable timed lyric rows in order
        preserved_lines: Non-editable lines kept in their original positions
        raw_content: Original file content
    """

    timed_lines: List[LRCLine]
    preserved_lines: List[LRCPreservedLine]
    raw_content: str

    @property
    def line_count(self) -> int:
        return len(self.timed_lines)

    @property
    def duration_seconds(self) -> float:
        return self.timed_lines[-1].time_seconds if self.timed_lines else 0.0


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


def parse_lrc_full(content: str) -> LRCParsedContent:
    """Parse LRC content into editable timed rows plus preserved non-editable content.

    Recognized metadata tags (ti, ar, al, by, offset, re, ve) and unknown
    non-timestamp lines are preserved in their original relative positions.

    Args:
        content: Raw LRC file content

    Returns:
        LRCParsedContent with timed_lines and preserved_lines
    """
    timed_lines: List[LRCLine] = []
    preserved_lines: List[LRCPreservedLine] = []

    raw_lines = content.split("\n")
    if raw_lines and raw_lines[-1] == "":
        raw_lines = raw_lines[:-1]

    for raw_line in raw_lines:
        stripped = raw_line.strip()
        if not stripped:
            preserved_lines.append(LRCPreservedLine(raw=raw_line))
            continue

        meta_match = _METADATA_TAG_RE.match(stripped)
        if meta_match:
            tag = meta_match.group(1).lower()
            value = meta_match.group(2).strip()
            preserved_lines.append(LRCPreservedLine(raw=raw_line, tag=tag, value=value))
            continue

        timed_match = _TIMED_LINE_RE.match(stripped)
        if timed_match:
            minutes = int(timed_match.group(1))
            seconds = int(timed_match.group(2))
            milliseconds = _parse_milliseconds(timed_match.group(3))
            text = timed_match.group(4).strip()

            time_seconds = minutes * 60 + seconds + milliseconds / 1000.0
            raw_timestamp = (
                f"[{timed_match.group(1)}:{timed_match.group(2)}.{timed_match.group(3)}]"
            )

            timed_lines.append(
                LRCLine(time_seconds=time_seconds, text=text, raw_timestamp=raw_timestamp)
            )
            continue

        preserved_lines.append(LRCPreservedLine(raw=raw_line))

    return LRCParsedContent(
        timed_lines=timed_lines,
        preserved_lines=preserved_lines,
        raw_content=content,
    )


def format_centiseconds(time_seconds: float) -> str:
    """Format seconds to ``[mm:ss.xx]`` centisecond string.

    Uses centisecond rounding (2 decimal places) with correct carry.
    For example, 59.995 rounds to [01:00.00].

    Args:
        time_seconds: Timestamp in seconds (>= 0)

    Returns:
        Formatted string like "[01:23.45]"
    """
    clamped = max(0.0, time_seconds)
    centiseconds = round(clamped * 100)
    total_cs = int(centiseconds)
    minutes = total_cs // 6000
    remaining_cs = total_cs % 6000
    seconds = remaining_cs // 100
    cs = remaining_cs % 100
    return f"[{minutes:02d}:{seconds:02d}.{cs:02d}]"


def serialize_lrc(timed_lines: List[LRCLine], preserved_lines: Optional[List[LRCPreservedLine]] = None) -> str:
    """Serialize timed lyric lines and preserved content to LRC format.

    Writes canonical ``[mm:ss.xx]`` centisecond timestamps. Preserved
    lines are included at the top of the file (metadata tags first, then
    other preserved lines), followed by timed lyric rows.

    Args:
        timed_lines: Timed lyric rows to serialize
        preserved_lines: Optional preserved non-editable content

    Returns:
        LRC-formatted string
    """
    parts: List[str] = []

    if preserved_lines:
        metadata_lines = [p for p in preserved_lines if p.tag is not None]
        other_preserved = [p for p in preserved_lines if p.tag is None and p.raw.strip()]
        for p in metadata_lines:
            parts.append(p.raw)
        for p in other_preserved:
            parts.append(p.raw)
        if metadata_lines or other_preserved:
            parts.append("")

    for line in timed_lines:
        ts = format_centiseconds(line.time_seconds)
        text = line.text
        parts.append(f"{ts}{text}")

    return "\n".join(parts) + "\n"


def compute_lrc_hash(content: str) -> str:
    """Compute SHA-256 hash of LRC content for session identity.

    Args:
        content: Raw LRC file content

    Returns:
        Hex-encoded SHA-256 digest
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def build_draft_from_catalog(lyrics_lines: Optional[str] = None, lyrics_raw: Optional[str] = None) -> List[LRCLine]:
    """Build draft LRC lines from catalog lyrics when no R2 LRC exists.

    Prefers parsed ``lyrics_lines`` (JSON array) if present, falls back
    to non-empty lines from ``lyrics_raw``. Each draft line gets timestamp
    ``00:00.00``.

    Args:
        lyrics_lines: JSON-encoded list of lyric lines
        lyrics_raw: Raw lyrics text with newline separators

    Returns:
        List of LRCLine with default zero timestamps
    """
    import json

    lines: List[str] = []

    if lyrics_lines:
        try:
            parsed = json.loads(lyrics_lines)
            if isinstance(parsed, list) and parsed:
                lines = [str(s).strip() for s in parsed if str(s).strip()]
        except (json.JSONDecodeError, TypeError):
            pass

    if not lines and lyrics_raw:
        lines = [line.strip() for line in lyrics_raw.split("\n") if line.strip()]

    return [LRCLine(time_seconds=0.0, text=text, raw_timestamp="[00:00.00]") for text in lines]


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
