"""Hard constraint validation H1-H9."""

from __future__ import annotations

from stream_of_worship.admin.constants import SONGSET_MAX_DURATION_SECONDS
from stream_of_worship.admin.songset_constructor.config import RunConfig
from stream_of_worship.admin.songset_constructor.models import (
    SongsetProposal,
    TransitionCandidate,
    ValidationFeedback,
)

RULE_DESCRIPTIONS: dict[str, str] = {
    "H1": "Phase coverage: the set must include exactly one phase-1 *primary* opener, at least one "
    "phase 3/4 worship/response song (primary or secondary), and end on a phase 4/5 closer "
    "(primary or secondary). Relaxable: when relaxed, the strict phase-1 count and phase 3/4 "
    "requirements are dropped, retaining only the phase 4/5 closer.",
    "H2": "Opening tempo: the first song must be phase 1 with tempo >= 90 BPM (a strong opener). "
    "Relaxable: the floor can be lowered via --relax-h2-bpm.",
    "H3": "Closing tempo: the last song must be phase 4/5 with tempo <= 90 BPM (80 BPM in intimate "
    "mode) — a calm closer. Relaxable: the ceiling can be raised via --relax-h3-bpm.",
    "H4": "Tempo jump: adjacent songs' BPM delta must stay <= 45 (40 without crossfade/gap; 55 if relaxed). "
    "gap_beats > 0 (any gap) triggers the crossfade-tier cap.",
    "H5": "Circle-of-fifths distance: adjacent keys must be within CFD 3 (4 if relaxed) unless the next "
    "song is transposed to match the suggested shift.",
    "H6": "Uniqueness: no duplicate song IDs allowed in the set.",
    "H7": "Phase arc: phase may drop by at most 1 between adjacent songs (no sharp backwards worship arc).",
    "H8": "Key confidence: songs with key confidence < 0.6 cannot be transposed (key_shift must stay 0).",
    "H9": f"Total duration: the sum of all songs' durations must not exceed {SONGSET_MAX_DURATION_SECONDS}s "
    f"(25 min). Songs with unknown duration (None) contribute 0 and do not trigger H9.",
}


def validate(
    proposal: SongsetProposal,
    config: RunConfig,
    matrix: dict[tuple[str, str], TransitionCandidate],
    *,
    relax_h4: bool = False,
    relax_h5: bool = False,
    relax_h1: bool = False,
) -> ValidationFeedback:
    failures: list[tuple[str, str, str]] = []
    primary_phases = [item.phase for item in proposal.items]
    item_phases: list[set[int]] = []
    for item in proposal.items:
        phases = {item.phase}
        phases.update(item.secondary_phases)
        item_phases.append(phases)
    bpms = [item.bpm for item in proposal.items]

    if len(proposal.items) != config.count:
        failures.append((
            "H0",
            f"Proposal has {len(proposal.items)} songs but {config.count} were requested.",
            "Add or remove songs to match the requested count.",
        ))
        return ValidationFeedback(
            passed=False,
            violated=[code for code, _, _ in failures],
            errors=[message for _, message, _ in failures],
            repair_hints=[hint for _, _, hint in failures],
        )

    if relax_h1:
        h1_failed = not (item_phases[-1] & {4, 5})
    else:
        h1_failed = (
            sum(1 for p in primary_phases if p == 1) != 1
            or not any(phases & {3, 4} for phases in item_phases[1:-1])
            or not (item_phases[-1] & {4, 5})
        )
    if h1_failed:
        failures.append(("H1", "Phase coverage must include one opener, worship/response, and phase 4/5 closer.", "Adjust ordering to follow phases 1-5."))
    opening_floor = config.opening_floor
    if bpms[0] is None or bpms[0] < opening_floor:
        failures.append(("H2", f"Opening tempo must be at least {opening_floor} BPM.", "Choose a stronger opener."))
    closing_limit = config.closing_limit
    if bpms[-1] is None or bpms[-1] > closing_limit:
        failures.append(("H3", f"Closing tempo must be <= {closing_limit} BPM.", "Choose a calmer closer."))

    h4_limit = config.h4_limit
    for left, right in zip(proposal.items, proposal.items[1:]):
        transition = matrix.get((left.recording_hash_prefix, right.recording_hash_prefix))
        bpm_delta = transition.bpm_delta if transition else abs((right.bpm or 0) - (left.bpm or 0))
        allowed = (
            config.h4_limit
            if (right.crossfade_duration_seconds > 0 or right.gap_beats > 0)
            else config.h4_no_crossfade_limit
        )
        if bpm_delta > allowed:
            failures.append(("H4", f"Tempo jump {bpm_delta:.1f} BPM from {left.title} to {right.title} exceeds {allowed}.", "Use a crossfade/gap or choose a closer tempo neighbor."))
        h5_limit = config.h5_limit
        distance = transition.cfd if transition else 6
        shifted_ok = transition is not None and transition.suggested_key_shift == right.key_shift_semitones and transition.suggested_key_shift != 0
        if distance > h5_limit and right.crossfade_duration_seconds <= 0 and not shifted_ok:
            failures.append(("H5", f"Circle-of-fifths distance {distance} from {left.title} to {right.title} exceeds {h5_limit}.", "Transpose the next song or choose a closer key."))

    if len({item.song_id for item in proposal.items}) != len(proposal.items):
        failures.append(("H6", "Songset cannot contain duplicate song IDs.", "Replace the duplicate song."))
    for i in range(len(proposal.items) - 1):
        left_phases = item_phases[i]
        right_phases = item_phases[i + 1]
        if not any(r >= l - 1 for l in left_phases for r in right_phases):
            failures.append(("H7", f"Phase drops too far from {proposal.items[i].phase} to {proposal.items[i + 1].phase}.", "Reorder to avoid a sharp backwards worship arc."))
    for item in proposal.items:
        if item.key_confidence is not None and item.key_confidence < 0.6 and item.key_shift_semitones != 0:
            failures.append(("H8", f"{item.title} has low key confidence and cannot be transposed.", "Set key_shift_semitones to 0 or choose a song with reliable key analysis."))

    total_duration = sum(item.duration_seconds or 0.0 for item in proposal.items)
    if total_duration > SONGSET_MAX_DURATION_SECONDS:
        failures.append((
            "H9",
            f"Total duration {total_duration:.0f}s exceeds {SONGSET_MAX_DURATION_SECONDS}s (25 min) limit.",
            "Replace one song with a shorter alternative, or reduce song count (≤4).",
        ))

    return ValidationFeedback(
        passed=not failures,
        violated=[code for code, _, _ in failures],
        errors=[message for _, message, _ in failures],
        repair_hints=[hint for _, _, hint in failures],
    )
