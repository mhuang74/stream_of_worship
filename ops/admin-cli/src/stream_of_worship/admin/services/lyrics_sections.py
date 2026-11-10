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
being placeable, so this module matches every sung line instead.

Matching is per timed LRC line, not per section block. Two pool-wide shapes
break block matching:

* LRC files routinely pack several lyric lines into one timestamped line
  (``[00:13.20] 我相信天父創造世界 我相信耶穌死裡復活``) while
  ``structured_lyrics`` lists each lyric line separately; and
* the sung order repeats and alternates the listed lines (a ``[verse]`` block of
  eight lines whose lines 5-8 are sung after a chorus), so the section's lines
  are never contiguous in the LRC even though every line is present.

The LRC is the ground truth for *timing and order*; ``structured_lyrics`` only
supplies *labels*. So each timed line is labelled by whichever section explains
the most of its text, and consecutive lines sharing a label form one occurrence.
Unlabelled lines close the current occurrence (they do not extend it) and never
open one, so an unplaceable song still yields ``[]``.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from collections.abc import Sequence
from typing import Any, Optional

from stream_of_worship.admin.services.lrc_parser import parse_lrc

# Whole-line similarity accepted as the same lyric line (the analysis service
# uses the same threshold per line).
_LINE_MATCH_RATIO = 0.85
# Fallback line duration when the matched occurrence ends the song.
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
    """Extract ``(label, normalized lines)`` pairs from the structured lyrics."""
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
        normalized = [normalize_line(line) for line in lines]
        normalized = [line for line in normalized if line]
        if normalized:
            parsed.append((label, normalized))
    return parsed


def _coverage(lrc_norm: str, section_lines: Sequence[str]) -> float:
    """Characters of ``lrc_norm`` explained by the section's lyric lines.

    Exact substring occurrences win; a line that is merely *close* to one listed
    line (a one-character ASR/login divergence) still counts when the whole-line
    similarity clears ``_LINE_MATCH_RATIO``. Longer matches score higher, so a
    short generic line cannot outvote the section actually being sung.
    """
    if not lrc_norm:
        return 0.0

    covered = [False] * len(lrc_norm)
    for needle in section_lines:
        start = lrc_norm.find(needle)
        while start >= 0:
            for index in range(start, start + len(needle)):
                covered[index] = True
            start = lrc_norm.find(needle, start + len(needle))
    score = float(sum(covered))
    if score > 0:
        return score

    best = 0.0
    for needle in section_lines:
        ratio = difflib.SequenceMatcher(None, lrc_norm, needle).ratio()
        if ratio > best:
            best = ratio
    return float(len(lrc_norm)) * best if best >= _LINE_MATCH_RATIO else 0.0


def _label_lines(
    lrc_norm: Sequence[str],
    sections: Sequence[tuple[str, list[str]]],
) -> list[Optional[str]]:
    """Label each timed line with the section that explains most of its text."""
    labels: list[Optional[str]] = []
    for text in lrc_norm:
        best_label: Optional[str] = None
        best_score = 0.0
        for label, lines in sections:
            score = _coverage(text, lines)
            if score > best_score:
                best_label, best_score = label, score
        labels.append(best_label)
    return labels


def derive_sections_from_lyrics(
    structured_lyrics: Any,
    lrc_content: Optional[str],
    duration_seconds: Optional[float],
) -> list[DerivedSection]:
    """Label every timed lyric line and group consecutive same-label lines.

    Args:
        structured_lyrics: ``recordings.structured_lyrics`` (JSON string or dict).
        lrc_content: Raw ``.lrc`` text, or None when unavailable.
        duration_seconds: Recording duration used for clamping the final section.

    Returns:
        Section occurrences sorted by start time. Empty when the inputs are
        missing or no timed line is attributable to a structured section.
        Overlaps are truncated at the next section's start, so the result is a
        monotonic, non-overlapping timeline -- the shape the editor's
        ``buildSnappedSteps`` expects.
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

    labels = _label_lines([normalize_line(line.text) for line in timed], sections)

    occurrences: list[DerivedSection] = []
    index = 0
    while index < len(timed):
        label = labels[index]
        if label is None:
            index += 1
            continue
        last = index
        while last + 1 < len(timed) and labels[last + 1] == label:
            last += 1

        start = timed[index].time_seconds
        if last + 1 < len(timed):
            end = timed[last + 1].time_seconds
        else:
            gaps = [
                timed[position + 1].time_seconds - timed[position].time_seconds
                for position in range(index, len(timed) - 1)
            ]
            average_gap = sum(gaps) / len(gaps) if gaps else _FALLBACK_LINE_SECONDS
            end = timed[last].time_seconds + average_gap
        if duration_seconds and duration_seconds > 0:
            start = min(start, duration_seconds)
            end = min(end, duration_seconds)
        if start < end:
            occurrences.append(DerivedSection(label=label, start=start, end=end))
        index = last + 1

    if not occurrences:
        return []

    # Grouping yields disjoint runs already; truncation keeps the contract if a
    # future rule ever emits overlapping bounds.
    resolved: list[DerivedSection] = []
    for position, section in enumerate(occurrences):
        end = section.end
        if position + 1 < len(occurrences):
            end = min(end, occurrences[position + 1].start)
        if end > section.start:
            resolved.append(DerivedSection(label=section.label, start=section.start, end=end))
    return resolved
