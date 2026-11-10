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
supplies *labels*. So consecutive timed lines are grouped into section
occurrences, each keeping a lyric-line cursor that only moves forward. A timed
line that continues the cursor, or that completes a lyric line the occurrence
only started, belongs to the open occurrence. A timed line that restarts the
sequence opens a new occurrence, which is what keeps a repeated chorus two
separate sections. A timed line shared by two sections is attributed to the
section that also explains the following lines rather than to whichever section
is listed first. Lines no section explains close the current occurrence without
extending it and never open one, so an unplaceable song still yields ``[]``.
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
# A timed line counts as a fragment of a lyric line only when it covers this
# much of it -- long lines are routinely split across two timestamps, and short
# generic syllables must not claim one.
_MIN_FRAGMENT_RATIO = 0.25
# Following timed lines consulted when a new occurrence's section is ambiguous.
_LOOKAHEAD_LINES = 4
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


@dataclass(frozen=True)
class _Run:
    """A candidate occurrence: consecutive timed lines attributed to one section."""

    section: int
    start: int
    end: int


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


def _line_similarity(text: str, expected: str) -> float:
    """How much of ``expected`` the joined ``text`` explains, 0.0-1.0.

    Containment is a full match; otherwise the ``difflib`` ratio, which is what
    tolerates a one-character ASR/typing divergence in a whole line.
    """
    if not text or not expected:
        return 0.0
    if expected in text:
        return 1.0
    return difflib.SequenceMatcher(None, text, expected).ratio()


def _line_score(text: str, expected: str) -> float:
    """Score one timed line against one structured lyric line.

    ``1.0`` when the timed line contains the whole lyric line, which is how a
    timestamp packing two lyric lines matches both. A timed line that is itself a
    fragment of the lyric line scores the share it covers, so a line split across
    two timestamps is still recognisable. Anything else must clear
    ``_LINE_MATCH_RATIO`` on the whole line.
    """
    if not text or not expected:
        return 0.0
    if expected in text:
        return 1.0
    if text in expected:
        share = len(text) / len(expected)
        return share if len(text) >= 2 and share >= _MIN_FRAGMENT_RATIO else 0.0
    ratio = difflib.SequenceMatcher(None, text, expected).ratio()
    return ratio if ratio >= _LINE_MATCH_RATIO else 0.0


def _line_matches(
    text: str,
    sections: Sequence[tuple[str, list[str]]],
) -> dict[int, dict[int, float]]:
    """``{section index: {lyric line index: score}}`` for one timed line."""
    matches: dict[int, dict[int, float]] = {}
    for index, (_label, lines) in enumerate(sections):
        scores = {position: _line_score(text, line) for position, line in enumerate(lines)}
        scores = {position: score for position, score in scores.items() if score > 0.0}
        if scores:
            matches[index] = scores
    return matches


def _next_line(
    text: str,
    scores: dict[int, float],
    lines: Sequence[str],
    line_index: int,
) -> int:
    """First lyric line of the section this timed line does not explain.

    Only whole-line matches advance past a line: a fuzzy or fragment score is
    coincidental overlap, not evidence the line was sung here. Advancing also
    stops once the explained lines no longer fit inside the timed line, because a
    section that lists the same line twice -- the pool's chorus blocks do -- must
    not have its second copy swallowed by the first timestamp.
    """
    position = line_index + 1
    covered = len(lines[line_index])
    while position < len(lines) and scores.get(position, 0.0) >= 1.0:
        covered += len(lines[position])
        if covered > len(text):
            break
        position += 1
    return position


def _lookahead(
    texts: Sequence[str],
    sections: Sequence[tuple[str, list[str]]],
    matches: Sequence[dict[int, dict[int, float]]],
    line_index: int,
    section: int,
    next_line: int,
) -> int:
    """How many following timed lines this section also explains, in order.

    Decides between sections when a timed line is shared: the section that keeps
    explaining what comes next is the one being sung.
    """
    explained = 0
    position = line_index + 1
    while position < len(matches) and explained < _LOOKAHEAD_LINES:
        scores = matches[position].get(section) or {}
        candidates = [index for index in scores if index >= next_line]
        if not candidates:
            break
        next_line = _next_line(
            texts[position], scores, sections[section][1], min(candidates)
        )
        explained += 1
        position += 1
    return explained


def _assign_runs(
    texts: Sequence[str],
    sections: Sequence[tuple[str, list[str]]],
) -> list[_Run]:
    """Group timed lines into section occurrences, in sung order.

    A timed line continues the open occurrence when it explains a lyric line the
    occurrence has not reached yet, or when it completes a lyric line whose
    earlier part opened the occurrence. Otherwise it starts a new occurrence --
    picking the section that also explains the following lines, so a line shared
    by a verse and a chorus is attributed by its neighbours rather than by
    structured-lyrics order -- or, when no section explains it, it closes the
    current occurrence without extending it.
    """
    matches = [_line_matches(text, sections) for text in texts]
    runs: list[_Run] = []
    section: Optional[int] = None
    next_line = 0
    start = 0
    pending_line: Optional[int] = None

    def _close(end: int) -> None:
        nonlocal section
        if section is not None:
            runs.append(_Run(section, start, end))
            section = None

    for position, scores in enumerate(matches):
        if section is not None:
            section_scores = scores.get(section) or {}
            candidates = [index for index in section_scores if index >= next_line]
            if not candidates and pending_line is not None:
                # The timed line may finish a lyric line the previous one only
                # started (a long line split across two timestamps).
                previous = matches[position - 1].get(section) or {}
                if pending_line == next_line - 1 and previous.get(pending_line, 0.0) < 1.0:
                    joined = texts[position - 1] + texts[position]
                    expected = sections[section][1][pending_line]
                    if _line_similarity(joined, expected) >= _LINE_MATCH_RATIO:
                        candidates = [pending_line]
            if candidates:
                chosen = min(candidates)
                rival = max(
                    (max(other.values()) for index, other in scores.items() if index != section),
                    default=0.0,
                )
                if section_scores[chosen] >= rival - 1e-9:
                    pending_line = chosen
                    next_line = _next_line(
                        texts[position], section_scores, sections[section][1], chosen
                    )
                    continue
            _close(position)

        if not scores:
            continue
        best: Optional[tuple[tuple[int, float, int], int, int]] = None
        for index, section_scores in scores.items():
            chosen = min(section_scores)
            key = (
                _lookahead(
                    texts,
                    sections,
                    matches,
                    position,
                    index,
                    _next_line(texts[position], section_scores, sections[index][1], chosen),
                ),
                max(section_scores.values()),
                -index,
            )
            if best is None or key > best[0]:
                best = (key, index, chosen)
        _key, section, chosen = best
        start = position
        pending_line = chosen
        next_line = _next_line(texts[position], scores[section], sections[section][1], chosen)

    _close(len(texts))
    return runs


def _run_is_evidenced(
    run: _Run,
    texts: Sequence[str],
    sections: Sequence[tuple[str, list[str]]],
) -> bool:
    """True when the occurrence's joined text is one of the section's lines.

    A run built from fragments alone is dropped: a two-character overlap with a
    chorus line is not evidence the chorus was sung here.
    """
    joined = "".join(texts[position] for position in range(run.start, run.end))
    return any(
        _line_similarity(joined, line) >= _LINE_MATCH_RATIO
        for line in sections[run.section][1]
    )


def derive_sections_from_lyrics(
    structured_lyrics: Any,
    lrc_content: Optional[str],
    duration_seconds: Optional[float],
) -> list[DerivedSection]:
    """Group timed lines into section occurrences, in sung order.

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

    texts = [normalize_line(line.text) for line in timed]
    occurrences: list[DerivedSection] = []
    for run in _assign_runs(texts, sections):
        if not _run_is_evidenced(run, texts, sections):
            continue
        start = timed[run.start].time_seconds
        if run.end < len(timed):
            end = timed[run.end].time_seconds
        else:
            gaps = [
                timed[position + 1].time_seconds - timed[position].time_seconds
                for position in range(run.start, len(timed) - 1)
            ]
            average_gap = sum(gaps) / len(gaps) if gaps else _FALLBACK_LINE_SECONDS
            end = timed[run.end - 1].time_seconds + average_gap
        if duration_seconds and duration_seconds > 0:
            start = min(start, duration_seconds)
            end = min(end, duration_seconds)
        if start < end:
            occurrences.append(
                DerivedSection(label=sections[run.section][0], start=start, end=end)
            )

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
