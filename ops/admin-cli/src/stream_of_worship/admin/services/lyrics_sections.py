"""Derive timed arrangement sections from structured lyrics plus timed LRC.

The Arrange editor needs ``recordings.sections`` as a sorted list of
``{"label", "start", "end"}`` entries. The authoritative section labels and
their lyric lines live in ``recordings.structured_lyrics``; the only thing
missing is timing. Both artifacts already exist per recording, so timing can be
recovered locally and deterministically with no LLM or analysis-service call.

Why not reuse the component-analysis job output: with an LLM key configured the
aligner emits only chorus occurrences plus at most one ``loop_target`` verse,
which is a *subset* of the structured sections (measured pool-wide: a 6-section
recording yielded 3 components). Arrangement coverage depends on every section
being placeable, so this module matches every section occurrence instead.

Matching operates on the concatenated normalized text rather than line-for-line,
because LRC files routinely pack several lyric lines into one timestamped line
(e.g. ``[00:13.20] 我相信天父創造世界 我相信耶穌死裡復活``) while
``structured_lyrics`` lists each lyric line separately. A per-line window can
never match those, which is why a naive port of the analysis service's
``identify_from_structured_lyrics`` silently yields nothing for such songs.
"""

from __future__ import annotations

import bisect
import difflib
import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from stream_of_worship.admin.services.lrc_parser import parse_lrc

# Whole-block similarity accepted as "the same lyrics" (upstream uses 85
# per line; on concatenated text the same threshold behaves equivalently).
_BLOCK_MATCH_RATIO = 0.85
# Fallback line duration when the matched block ends the song.
_FALLBACK_LINE_SECONDS = 4.0

_STRIP_RE = re.compile(r"[^\w]")


@dataclass(frozen=True)
class DerivedSection:
    """One timed section occurrence, before beat snapping.

    Attributes:
        label: Section label taken from ``structured_lyrics`` (e.g. ``verse``).
        start: Start time in seconds.
        end: End time in seconds.
    """

    label: str
    start: float
    end: float


def normalize_line(text: str) -> str:
    """Reduce lyric text to its comparable form.

    Structured lyrics and LRC come from the same source, so the only differences
    are cosmetic: full-width spaces, punctuation, and case. ``\\w`` keeps CJK
    characters while dropping both, matching the analysis service's
    ``_normalize_line``.
    """
    return _STRIP_RE.sub("", text).casefold()


def _parse_sections(structured_lyrics: Any) -> list[tuple[str, list[str]]]:
    """Extract ``(label, lines)`` pairs from the structured-lyrics payload."""
    if isinstance(structured_lyrics, str):
        try:
            payload = json.loads(structured_lyrics)
        except (json.JSONDecodeError, TypeError):
            return []
    else:
        payload = structured_lyrics
    if not isinstance(payload, dict):
        return []

    parsed: list[tuple[str, list[str]]] = []
    for section in payload.get("sections") or []:
        if not isinstance(section, dict):
            continue
        label = str(section.get("label") or "").strip()
        lines = [line for line in (section.get("lines") or []) if isinstance(line, str)]
        if not label or not 1 <= len(label) <= 100 or not lines:
            # Sections without lyric lines (e.g. an instrumental intro) carry no
            # position information to match on.
            continue
        needle = "".join(normalize_line(line) for line in lines)
        if needle:
            parsed.append((label, lines))
    return parsed


def _find_spans(
    stream: str,
    needle: str,
    offsets: list[int],
) -> list[tuple[int, int, float]]:
    """All non-overlapping ``(char_start, char_end, confidence)`` occurrences.

    Exact matches are scanned first: both artifacts come from the same lyrics,
    so exact concatenated equality is the common case. When a section appears
    nowhere exactly (punctuation or a one-character divergence), each line
    boundary is tried as a fuzzy anchor and the best non-overlapping candidates
    are kept.
    """
    if not needle or not stream:
        return []

    spans: list[tuple[int, int, float]] = []
    position = 0
    while True:
        found = stream.find(needle, position)
        if found < 0:
            break
        spans.append((found, found + len(needle), 1.0))
        position = found + len(needle)
    if spans:
        return spans

    length = len(needle)
    candidates: list[tuple[int, int, float]] = []
    for offset in offsets:
        window = stream[offset : offset + length]
        if len(window) < length // 2:
            break
        ratio = difflib.SequenceMatcher(None, window, needle).ratio()
        if ratio >= _BLOCK_MATCH_RATIO:
            candidates.append((offset, offset + len(window), ratio))
    if not candidates:
        return []

    # Keep the strongest, earliest occurrences without overlap.
    candidates.sort(key=lambda entry: (-entry[2], entry[0]))
    for start, end, ratio in candidates:
        if all(start >= kept_end or end <= kept_start for kept_start, kept_end, _ in spans):
            spans.append((start, end, ratio))
    spans.sort()
    return spans


def derive_sections_from_lyrics(
    structured_lyrics: Any,
    lrc_content: Optional[str],
    duration_seconds: Optional[float],
) -> list[DerivedSection]:
    """Match every structured-lyrics section occurrence into the timed LRC.

    Args:
        structured_lyrics: ``recordings.structured_lyrics`` (JSON string or dict).
        lrc_content: Raw ``.lrc`` text, or None when unavailable.
        duration_seconds: Recording duration used for clamping the final section.

    Returns:
        Section occurrences sorted by start time. Empty when the inputs are
        missing or nothing matches. Overlaps are truncated at the next
        section's start, so the result is a monotonic, non-overlapping timeline
        -- the shape the editor's ``buildSnappedSteps`` expects.
    """
    if not lrc_content:
        return []
    sections = _parse_sections(structured_lyrics)
    if not sections:
        return []

    try:
        lrc_lines = parse_lrc(lrc_content).lines
    except Exception:
        return []
    timed = [line for line in lrc_lines if line.text and line.text.strip()]
    if len(timed) < 2:
        return []

    lrc_norm = [normalize_line(line.text) for line in timed]
    stream = "".join(lrc_norm)

    offsets: list[int] = []
    cursor = 0
    for text in lrc_norm:
        offsets.append(cursor)
        cursor += len(text)

    def _line_of(char_position: int) -> int:
        """Index of the LRC line whose text contains ``char_position``."""
        return max(0, bisect.bisect_right(offsets, char_position) - 1)

    occurrences: list[DerivedSection] = []
    seen: set[tuple[str, float, float]] = set()
    for label, lines in sections:
        needle = "".join(normalize_line(line) for line in lines)
        for char_start, char_end, _confidence in _find_spans(stream, needle, offsets):
            first_line = _line_of(char_start)
            last_line = _line_of(max(char_start, char_end - 1))
            start = timed[first_line].time_seconds
            if last_line + 1 < len(timed):
                end = timed[last_line + 1].time_seconds
            else:
                gaps = [
                    timed[index + 1].time_seconds - timed[index].time_seconds
                    for index in range(first_line, len(timed) - 1)
                ]
                average_gap = sum(gaps) / len(gaps) if gaps else _FALLBACK_LINE_SECONDS
                end = timed[last_line].time_seconds + average_gap
            if duration_seconds and duration_seconds > 0:
                start = min(start, duration_seconds)
                end = min(end, duration_seconds)
            if start >= end:
                continue
            key = (label, round(start, 3), round(end, 3))
            if key in seen:
                continue
            seen.add(key)
            occurrences.append(DerivedSection(label=label, start=start, end=end))

    if not occurrences:
        return []

    occurrences.sort(key=lambda section: (section.start, section.end))
    # Truncate overlaps so the timeline is monotonic and contiguous.
    resolved: list[DerivedSection] = []
    for index, section in enumerate(occurrences):
        end = section.end
        if index + 1 < len(occurrences):
            end = min(end, occurrences[index + 1].start)
        if end > section.start:
            resolved.append(DerivedSection(label=section.label, start=section.start, end=end))
    return resolved
