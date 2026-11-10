"""Canonical arrangement/timing vocabulary for the render worker.

This is the strict Python mirror of
``delivery/webapp/src/lib/arrangements.ts`` (issue #282, spec:
``specs/song_arrangement_editor.md`` §1). The shared numeric fixture
``delivery/webapp/src/test/fixtures/arrangements.cross_language.json`` is
asserted identical to 1e-9 by both test suites.

Keep this module importable without importing ``audio_engine``: the engine
will import :class:`ArrangementStep` (and :func:`validate_arrangement`) from
here, so this module must never import back into the engine and must stay
free of side effects.

Rounding rule, pinned in both languages: integer half-up to six decimals via
``math.floor(x * 1e6 + 0.5) / 1e6`` — never the built-in ``round()``
(half-even), whose divergence at the 6th decimal is exactly the validation
tolerance and would cause spurious staleness.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

MAX_ARRANGEMENT_STEPS = 64

VALIDATION_TOLERANCE_SECONDS = 0.000001

# Exact repair-or-reset message raised for any present-invalid arrangement:
# malformed JSON, missing/stale analysis, structural problems, or index/label/
# bounds mismatch (mirrored verbatim in arrangements.ts).
ARRANGEMENT_UNAVAILABLE_OR_STALE_MESSAGE = (
    "Song arrangement is unavailable or stale; "
    "repair the Recording analysis or reset the arrangement"
)

PARSE_FAILURE_MESSAGE = "Song arrangement JSON is malformed or structurally invalid"


@dataclass(frozen=True)
class RecordingSection:
    label: str
    start: float
    end: float


@dataclass(frozen=True)
class ArrangementStep:
    # None only for a Songset-local LRC-inferred Intro; detected sections use
    # their zero-based Recording section index.
    section_index: int | None
    label: str
    start_seconds: float
    end_seconds: float
    # True for an inferred Intro and user-adjusted bounds: skips only the
    # beat-snap equality check, never range validation.
    manual_bounds: bool = False

    @property
    def duration_seconds(self) -> float:
        """Selected section length; audio_engine sums these for arranged duration."""
        return self.end_seconds - self.start_seconds


def _is_number(value: Any) -> bool:
    """JSON booleans/null/strings are not numbers; NaN/Infinity never pass."""
    try:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
    except (OverflowError, ValueError):
        return False


def _is_integral_number(value: Any) -> bool:
    return _is_number(value) and float(value).is_integer()


def _round_seconds(value: float) -> float:
    return math.floor(value * 1e6 + 0.5) / 1e6


def _load_json(value: str | None) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _coerce_section(value: Any) -> RecordingSection | None:
    if isinstance(value, RecordingSection):
        return value
    if not isinstance(value, Mapping):
        return None
    label = value.get("label")
    start = value.get("start")
    end = value.get("end")
    if not isinstance(label, str) or not label:
        return None
    if not _is_number(start) or not _is_number(end):
        return None
    return RecordingSection(label=label, start=float(start), end=float(end))


def parse_recording_sections(value: str | None) -> list[RecordingSection] | None:
    """SQL NULL or unreadable analysis -> None.

    Sections may touch/overlap and need not cover the Recording; each still
    needs a nonempty label and finite start/end. Extra analysis keys (e.g.
    ``duration``) are ignored.
    """
    parsed = _load_json(value)
    if parsed is None or not isinstance(parsed, list):
        return None
    sections: list[RecordingSection] = []
    for entry in parsed:
        section = _coerce_section(entry)
        if section is None:
            return None
        sections.append(section)
    return sections


def _sorted_deduped(beats: list[float]) -> list[float]:
    """Ascending order with duplicates removed (shared by parse and snap)."""
    beats.sort()
    deduped: list[float] = []
    for beat in beats:
        if not deduped or beat != deduped[-1]:
            deduped.append(beat)
    return deduped


def _normalize_beats(beats: Sequence[Any], duration_seconds: float) -> list[float]:
    if not _is_number(duration_seconds) or duration_seconds <= 0:
        return []
    filtered = [
        float(beat)
        for beat in beats
        if _is_number(beat) and 0 <= beat <= duration_seconds
    ]
    return _sorted_deduped(filtered)


def parse_recording_beats(value: str | None) -> list[float]:
    """Finite beats, sorted ascending, deduped.

    Snapping drops out-of-duration values; an empty list means analysis is
    unavailable.
    """
    parsed = _load_json(value)
    if not isinstance(parsed, list):
        return []
    return _sorted_deduped(
        [float(beat) for beat in parsed if _is_number(beat)]
    )


def _snap_to_beat(value: float, beats: Sequence[float]) -> float:
    best = beats[0]
    best_distance = abs(value - best)
    for beat in beats[1:]:
        distance = abs(value - beat)
        # Ties choose the earlier beat: strict '<' keeps the first candidate.
        if distance < best_distance:
            best = beat
            best_distance = distance
    return best


def build_snapped_steps(
    sections: Sequence[RecordingSection | Mapping[str, Any]] | None,
    beats: Sequence[Any],
    duration_seconds: float,
) -> list[ArrangementStep] | None:
    """Snap every section start/end to the nearest detected beat.

    Bounds are clamped to ``[0, duration_seconds]`` and rounded with the
    canonical half-up rule. ``section_index`` is the position in the parsed
    sections array; invalid sections are skipped but keep their indices.
    Returns None when beats are missing, no section is valid, or any snap
    collapses a section to zero length (analysis unavailable).
    """
    if not sections:
        return None
    if not _is_number(duration_seconds) or duration_seconds <= 0:
        return None
    normalized_beats = _normalize_beats(beats, duration_seconds)
    if not normalized_beats:
        return None

    steps: list[ArrangementStep] = []
    for index, entry in enumerate(sections):
        section = _coerce_section(entry)
        if section is None:
            continue
        if section.start < 0 or section.end > duration_seconds:
            continue
        if section.start >= section.end:
            continue

        start = _round_seconds(
            min(max(_snap_to_beat(section.start, normalized_beats), 0.0), duration_seconds)
        )
        end = _round_seconds(
            min(max(_snap_to_beat(section.end, normalized_beats), 0.0), duration_seconds)
        )
        if not start < end:
            return None
        steps.append(
            ArrangementStep(
                section_index=index,
                label=section.label,
                start_seconds=start,
                end_seconds=end,
            )
        )
    return steps or None


_BASE_STEP_KEYS = frozenset({"sectionIndex", "label", "startSeconds", "endSeconds"})
_MANUAL_STEP_KEYS = _BASE_STEP_KEYS | {"manualBounds"}


def _is_valid_step_payload(value: Any) -> bool:
    """Exactly the base keys plus optionally ``manualBounds: true``.

    Mirrors ``isValidStep`` in arrangements.ts: ``manualBounds`` must be the
    JSON literal ``true`` (``1 is not True`` in Python, so ints are rejected),
    and a null section index is legal only for the inferred Intro.
    """
    if not isinstance(value, Mapping):
        return False
    keys = set(value.keys())
    if keys != _BASE_STEP_KEYS and keys != _MANUAL_STEP_KEYS:
        return False
    has_manual = "manualBounds" in value
    if has_manual and value["manualBounds"] is not True:
        return False
    index = value["sectionIndex"]
    label = value["label"]
    start = value["startSeconds"]
    end = value["endSeconds"]
    if not isinstance(label, str) or not 1 <= len(label) <= 100:
        return False
    if not _is_number(start) or not _is_number(end) or start < 0 or start >= end:
        return False
    if index is None:
        return label == "intro" and start == 0 and has_manual
    return _is_integral_number(index) and int(index) >= 0


def parse_song_arrangement(raw: str | None) -> tuple[ArrangementStep, ...] | None:
    """Strictly parse persisted JSON; NULL stays distinct from malformed JSON.

    ``None`` means SQL NULL (no custom arrangement). A present value that is
    malformed or structurally invalid raises :class:`ValueError` — callers use
    :func:`validate_arrangement` to translate that into the repair-or-reset
    message.
    """
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        raise ValueError(PARSE_FAILURE_MESSAGE) from None
    if not isinstance(parsed, Mapping) or set(parsed.keys()) != {"version", "steps"}:
        raise ValueError(PARSE_FAILURE_MESSAGE)
    version = parsed["version"]
    steps_payload = parsed["steps"]
    if not _is_integral_number(version) or int(version) != 1:
        raise ValueError(PARSE_FAILURE_MESSAGE)
    if not isinstance(steps_payload, list):
        raise ValueError(PARSE_FAILURE_MESSAGE)
    if not 1 <= len(steps_payload) <= MAX_ARRANGEMENT_STEPS:
        raise ValueError(PARSE_FAILURE_MESSAGE)
    steps: list[ArrangementStep] = []
    for payload in steps_payload:
        if not _is_valid_step_payload(payload):
            raise ValueError(PARSE_FAILURE_MESSAGE)
        raw_index = payload["sectionIndex"]
        steps.append(
            ArrangementStep(
                section_index=None if raw_index is None else int(raw_index),
                label=str(payload["label"]),
                start_seconds=float(payload["startSeconds"]),
                end_seconds=float(payload["endSeconds"]),
                manual_bounds=payload.get("manualBounds") is True,
            )
        )
    return tuple(steps)


def validate_arrangement(
    raw: str | None,
    sections_json: str | None,
    beats_json: str | None,
    recording_duration: float | None,
) -> tuple[ArrangementStep, ...] | None:
    """Validate a persisted arrangement against current Recording analysis.

    Returns ``None`` for SQL NULL (original full Recording) and the saved
    Steps when they match freshly snapped bounds. Raises the repair-or-reset
    :class:`ValueError` for any present-invalid value: malformed JSON, missing
    or collapsed analysis, structural problems, or stale label/index/bounds.

    A null-index Intro and any ``manual_bounds`` Step are Songset-Item-local
    cuts: analysis is consulted only when a detected Step exists, and only the
    snapped-bounds equality is skipped — range checks stay unconditional.
    """
    if raw is None:
        return None
    try:
        steps = parse_song_arrangement(raw)
    except ValueError:
        raise ValueError(ARRANGEMENT_UNAVAILABLE_OR_STALE_MESSAGE) from None
    if steps is None:
        return None
    if not _is_number(recording_duration) or recording_duration <= 0:
        raise ValueError(ARRANGEMENT_UNAVAILABLE_OR_STALE_MESSAGE)

    detected = [step for step in steps if step.section_index is not None]
    expected_by_index: dict[int, ArrangementStep] = {}
    if detected:
        sections = parse_recording_sections(sections_json)
        beats = parse_recording_beats(beats_json)
        snapped = build_snapped_steps(sections, beats, recording_duration)
        if snapped is None:
            raise ValueError(ARRANGEMENT_UNAVAILABLE_OR_STALE_MESSAGE)
        expected_by_index = {step.section_index: step for step in snapped}

    for step in steps:
        if step.section_index is not None:
            expected = expected_by_index.get(step.section_index)
            if expected is None:
                raise ValueError(ARRANGEMENT_UNAVAILABLE_OR_STALE_MESSAGE)
            if expected.label != step.label:
                raise ValueError(ARRANGEMENT_UNAVAILABLE_OR_STALE_MESSAGE)
            if not step.manual_bounds and (
                abs(step.start_seconds - expected.start_seconds)
                > VALIDATION_TOLERANCE_SECONDS
                or abs(step.end_seconds - expected.end_seconds)
                > VALIDATION_TOLERANCE_SECONDS
            ):
                raise ValueError(ARRANGEMENT_UNAVAILABLE_OR_STALE_MESSAGE)
        if (
            step.start_seconds < 0
            or step.end_seconds > recording_duration
            or step.start_seconds >= step.end_seconds
        ):
            raise ValueError(ARRANGEMENT_UNAVAILABLE_OR_STALE_MESSAGE)
    return steps
