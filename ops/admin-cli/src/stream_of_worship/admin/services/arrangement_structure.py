"""Canonical Arrange-gate evaluation for recording analysis.

Third implementation of the shared snapping contract; the other two are
``delivery/webapp/src/lib/arrangements.ts`` (``buildSnappedSteps``) and
``delivery/render-worker/src/sow_render_worker/arrangements.py``. This module
exists so the admin CLI can answer "can the webapp open the Arrange editor for
this Recording?" without duplicating the rule, and so the write path
(``_finalize_arrangement_structure``) and the audit share one set of
primitives.

Keep it a literal mirror, not an improvement: the three implementations are
pinned together by
``delivery/webapp/src/test/fixtures/arrangements.cross_language.json``.

The gate is all-or-nothing per Recording: ``build_snapped_steps`` returns
``None`` if ANY section collapses to zero length after snapping, even when
every other section is fine. Presence of ``sections`` + ``beats`` alone
therefore does not imply the Arrange button opens --
:func:`evaluate_recording_structure` splits that case out as ``collapsed``.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, Sequence

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from stream_of_worship.admin.db.models import Recording

MAX_ARRANGEMENT_STEPS = 64

# Verdicts in precedence order (first match wins).
VERDICTS = (
    "no_duration",
    "missing_sections",
    "invalid_sections",
    "missing_beats",
    "no_beats_in_range",
    "collapsed",
    "no_usable_sections",
    "arrangeable",
)


@dataclass(frozen=True)
class RecordingSection:
    """One entry of ``recordings.sections`` (a JSON ``{label,start,end}`` dict)."""

    label: str
    start: float
    end: float


@dataclass(frozen=True)
class SnappedStep:
    """A section after its bounds were snapped to the detected beat grid.

    ``section_index`` is the position in the parsed sections array; sections
    skipped as unusable keep their neighbours' indices intact.
    """

    section_index: int
    label: str
    start: float
    end: float


@dataclass(frozen=True)
class StructureEvaluation:
    """Verdict for one Recording, with enough detail to act on.

    ``steps`` is populated only for ``arrangeable``; ``section`` only for
    ``collapsed`` (the offending section) and ``detail`` is ``""`` only for
    ``arrangeable``.
    """

    verdict: str
    steps: list[SnappedStep] = field(default_factory=list)
    detail: str = ""
    section: Optional[RecordingSection] = None


# ---------------------------------------------------------------------------
# Primitives shared with the write path (_finalize_arrangement_structure)
# ---------------------------------------------------------------------------


def coerce_beat_time(beat: Any) -> Optional[float]:
    """Extract a beat timestamp from either supported payload shape.

    The beat-grid artifact stores madmom's raw ``grid.tolist()``, i.e. a list of
    ``[time, beat_position]`` pairs, while ``recordings.beats`` stores flat
    timestamps. Accept both so the same grid can be snapped and persisted.

    This tolerance belongs to the WRITE path only. The evaluator must feed
    :func:`parse_beats` output into :func:`normalize_beats` so it sees exactly
    the same input the webapp does (the webapp drops pairs as non-numbers).
    """
    if isinstance(beat, bool):
        return None
    if isinstance(beat, (int, float)):
        return float(beat)
    if isinstance(beat, (list, tuple)) and beat:
        head = beat[0]
        if isinstance(head, (int, float)) and not isinstance(head, bool):
            return float(head)
    return None


def normalize_beats(beats: Sequence[Any], duration_seconds: float) -> list[float]:
    """Finite in-duration beats, sorted ascending with duplicates removed.

    Mirrors ``normalizeBeats`` in ``delivery/webapp/src/lib/arrangements.ts``
    and ``_normalize_beats`` in the render worker so the persisted grid snaps
    identically wherever it is consumed. Accepts both the madmom pair shape and
    flat timestamps (see :func:`coerce_beat_time`).
    """
    if not duration_seconds or duration_seconds <= 0:
        return []
    filtered: list[float] = []
    for beat in beats:
        time_value = coerce_beat_time(beat)
        if time_value is None:
            continue
        if 0 <= time_value <= duration_seconds:
            filtered.append(time_value)
    return sorted(set(filtered))


def round_seconds(value: float) -> float:
    """Canonical rounding shared with the webapp's ``roundSeconds``.

    ``Math.round`` is half-up, whereas Python's ``round`` is half-to-even, so
    implement half-up explicitly: the persisted bounds are compared against the
    webapp's own snapped values within ``VALIDATION_TOLERANCE_SECONDS`` (1e-6),
    and a half-even tie would drift.
    """
    return math.floor(value * 1e6 + 0.5) / 1e6


def snap_to_beat(value: float, beats: Sequence[float]) -> float:
    """Nearest beat; ties resolve to the earlier (lower) beat.

    Mirrors ``snapToBeat`` in the webapp and render worker: a candidate
    replaces the incumbent only on a strict ``<`` distance.
    """
    best = beats[0]
    best_distance = abs(value - best)
    for beat in beats[1:]:
        distance = abs(value - beat)
        if distance < best_distance:
            best = beat
            best_distance = distance
    return best


# ---------------------------------------------------------------------------
# Parsing (mirrors parseRecordingSections / parseRecordingBeats)
# ---------------------------------------------------------------------------


def _is_number(value: Any) -> bool:
    """JSON booleans/null/strings are not numbers; NaN/Infinity never pass."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    return False


def parse_sections(raw: Optional[str]) -> Optional[list[RecordingSection]]:
    """Parse ``recordings.sections``; ``None`` means absent or unreadable.

    ``null``, a blank string, unparseable JSON, a non-array, or any entry that
    is not a dict with a non-empty ``str`` label and finite non-bool
    ``start``/``end`` all yield ``None``. ``[]`` parses to ``[]`` -- distinct
    from ``None`` so the evaluator can tell "stored nothing" from "stored
    junk". Sections may touch/overlap and need not cover the Recording.
    """
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(parsed, list):
        return None
    sections: list[RecordingSection] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            return None
        label = entry.get("label")
        start = entry.get("start")
        end = entry.get("end")
        if not isinstance(label, str) or not label:
            return None
        if not _is_number(start) or not _is_number(end):
            return None
        sections.append(RecordingSection(label=label, start=float(start), end=float(end)))
    return sections


def parse_beats(raw: Optional[str]) -> list[float]:
    """Parse ``recordings.beats``; ``[]`` means absent or unreadable.

    Finite numbers only, sorted ascending with duplicates removed. madmom
    ``[time, position]`` pairs are DROPPED, not coerced: the webapp's
    ``parseRecordingBeats`` drops them too, so a row storing pairs is reported
    as ``missing_beats`` by both implementations.
    """
    if raw is None:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(parsed, list):
        return []
    beats: list[float] = []
    for beat in parsed:
        if _is_number(beat):
            beats.append(float(beat))
    return sorted(set(beats))


# ---------------------------------------------------------------------------
# Snapping
# ---------------------------------------------------------------------------


def _snap_pass(
    sections: Sequence[RecordingSection],
    normalized_beats: Sequence[float],
    duration_seconds: float,
) -> tuple[list[SnappedStep], Optional[tuple[int, float]]]:
    """Snap sections onto the grid; also report the first collapsing section.

    Returns ``(steps, None)`` when every section snapped cleanly, or
    ``(steps_before_collapse, (index, snapped_start))`` for the first section
    whose snapped bounds are not strictly ascending -- ``index`` and the
    collapse value come from this single pass so callers never re-derive them.
    Out-of-range sections are skipped, not fatal. Both
    :func:`build_snapped_steps` and :func:`evaluate_recording_structure` call
    this, so their verdicts cannot disagree.
    """
    steps: list[SnappedStep] = []
    for index, section in enumerate(sections):
        if section.start < 0 or section.end > duration_seconds:
            continue
        if section.start >= section.end:
            continue
        start = round_seconds(
            min(max(snap_to_beat(section.start, normalized_beats), 0.0), duration_seconds)
        )
        end = round_seconds(
            min(max(snap_to_beat(section.end, normalized_beats), 0.0), duration_seconds)
        )
        if not start < end:
            return steps, (index, start)
        steps.append(
            SnappedStep(section_index=index, label=section.label, start=start, end=end)
        )
    return steps, None


def build_snapped_steps(
    sections: Optional[Sequence[RecordingSection]],
    beats: Sequence[float],
    duration_seconds: float,
) -> Optional[list[SnappedStep]]:
    """Snap every section start/end independently to the nearest beat.

    Mirrors ``buildSnappedSteps``. Returns ``None`` when sections are missing
    or empty, when no beat falls inside the Recording, when no section is
    usable, or when ANY section collapses to zero length -- that last case
    disables Arrange for the whole Recording.
    """
    if not sections:
        return None
    normalized_beats = normalize_beats(beats, duration_seconds)
    if not normalized_beats:
        return None
    steps, collapse = _snap_pass(sections, normalized_beats, duration_seconds)
    if collapse is not None:
        return None
    return steps or None


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate_recording_structure(recording: Recording) -> StructureEvaluation:
    """Classify one Recording against the webapp's Arrange availability rule.

    Verdicts, first match wins (see :data:`VERDICTS`): ``no_duration``,
    ``missing_sections``, ``invalid_sections``, ``missing_beats``,
    ``no_beats_in_range``, ``collapsed``, ``no_usable_sections``, and finally
    ``arrangeable``.

    Reads ``recording.sections``/``recording.beats`` raw, never
    ``Recording.beats_list`` (that swallows malformed JSON as ``[]`` and would
    erase the ``missing_beats`` vs ``invalid_*`` split).
    """
    duration = recording.duration_seconds
    if duration is None or duration <= 0:
        return StructureEvaluation(verdict="no_duration", detail="duration_seconds missing or <= 0")

    raw_sections = recording.sections
    sections = parse_sections(raw_sections)
    if sections is None:
        if raw_sections is None or not raw_sections.strip():
            return StructureEvaluation(
                verdict="missing_sections", detail="recordings.sections absent or empty"
            )
        return StructureEvaluation(
            verdict="invalid_sections",
            detail="recordings.sections is not a JSON array of {label,start,end}",
        )
    if not sections:
        return StructureEvaluation(
            verdict="missing_sections", detail="recordings.sections absent or empty"
        )

    raw_beats = recording.beats
    beats = parse_beats(raw_beats)
    if not beats:
        return StructureEvaluation(
            verdict="missing_beats",
            detail="recordings.beats absent or has no finite beat times",
        )

    normalized_beats = normalize_beats(beats, duration)
    if not normalized_beats:
        return StructureEvaluation(
            verdict="no_beats_in_range", detail=f"no beat in 0..{duration:.2f}s"
        )

    steps, collapse = _snap_pass(sections, normalized_beats, duration)
    if collapse is not None:
        collapsed_index, snapped = collapse
        section = sections[collapsed_index]
        return StructureEvaluation(
            verdict="collapsed",
            steps=steps,
            detail=(
                f"section {collapsed_index} {section.label!r} "
                f"{section.start:g}..{section.end:g}s collapses to {snapped:g}s"
            ),
            section=section,
        )
    if not steps:
        return StructureEvaluation(
            verdict="no_usable_sections",
            detail=f"no section within 0..{duration:.2f}s with start < end",
        )
    return StructureEvaluation(verdict="arrangeable", steps=steps, detail="")
