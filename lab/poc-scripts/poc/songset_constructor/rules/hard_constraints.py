"""Hard constraint validation H1-H8."""

from __future__ import annotations

from poc.songset_constructor.config import RunConfig
from poc.songset_constructor.models import SongsetProposal, TransitionCandidate, ValidationFeedback

RULE_DESCRIPTIONS: dict[str, str] = {
    "H1": "Phase coverage: the set must include exactly one phase-1 opener, at least one phase 3/4 "
    "worship/response song, and end on a phase 4/5 closer. Relaxable: when relaxed, the strict "
    "phase-1 count and phase 3/4 requirements are dropped, retaining only the phase 4/5 closer.",
    "H2": "Opening tempo: the first song must be phase 1 with tempo >= 110 BPM (a strong opener). "
    "Relaxable: the floor can be lowered via --relax-h2-bpm.",
    "H3": "Closing tempo: the last song must be phase 4/5 with tempo <= 90 BPM (80 BPM in intimate "
    "mode) — a calm closer. Relaxable: the ceiling can be raised via --relax-h3-bpm.",
    "H4": "Tempo jump: adjacent songs' BPM delta must stay <= 20 (15 without crossfade/gap; 25 if relaxed).",
    "H5": "Circle-of-fifths distance: adjacent keys must be within CFD 2 (3 if relaxed) unless the next "
    "song is transposed to match the suggested shift.",
    "H6": "Uniqueness: no duplicate song IDs allowed in the set.",
    "H7": "Phase arc: phase may drop by at most 1 between adjacent songs (no sharp backwards worship arc).",
    "H8": "Key confidence: songs with key confidence < 0.6 cannot be transposed (key_shift must stay 0).",
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
    phases = [item.phase for item in proposal.items]
    bpms = [item.bpm for item in proposal.items]

    # H0: Cardinality — the proposal must have exactly the requested song count.
    if len(proposal.items) != config.songs:
        failures.append((
            "H0",
            f"Proposal has {len(proposal.items)} songs but {config.songs} were requested.",
            "Add or remove songs to match the requested count.",
        ))
        # Return early to avoid IndexError on empty/short proposals.
        return ValidationFeedback(
            passed=False,
            violated=[code for code, _, _ in failures],
            errors=[message for _, message, _ in failures],
            repair_hints=[hint for _, _, hint in failures],
        )

    if relax_h1:
        h1_failed = phases[-1] not in {4, 5}
    else:
        h1_failed = (
            phases.count(1) != 1
            or not any(phase in {3, 4} for phase in phases)
            or phases[-1] not in {4, 5}
        )
    if h1_failed:
        failures.append(("H1", "Phase coverage must include one opener, worship/response, and phase 4/5 closer.", "Adjust ordering to follow phases 1-5."))
    opening_floor = config.opening_floor
    if bpms[0] is None or bpms[0] < opening_floor:
        failures.append(("H2", f"Opening tempo must be at least {opening_floor} BPM.", "Choose a stronger opener."))
    closing_limit = config.closing_limit
    if bpms[-1] is None or bpms[-1] > closing_limit:
        failures.append(("H3", f"Closing tempo must be <= {closing_limit} BPM.", "Choose a calmer closer."))

    h4_limit = 25 if relax_h4 else 20
    for left, right in zip(proposal.items, proposal.items[1:]):
        transition = matrix.get((left.recording_hash_prefix, right.recording_hash_prefix))
        bpm_delta = transition.bpm_delta if transition else abs((right.bpm or 0) - (left.bpm or 0))
        allowed = h4_limit if (right.crossfade_duration_seconds > 0 or right.gap_beats > 4) else min(15, h4_limit)
        if bpm_delta > allowed:
            failures.append(("H4", f"Tempo jump {bpm_delta:.1f} BPM from {left.title} to {right.title} exceeds {allowed}.", "Use a crossfade/gap or choose a closer tempo neighbor."))
        h5_limit = 3 if relax_h5 else 2
        distance = transition.cfd if transition else 6
        shifted_ok = transition is not None and transition.suggested_key_shift == right.key_shift_semitones and transition.suggested_key_shift != 0
        if distance > h5_limit and right.crossfade_duration_seconds <= 0 and not shifted_ok:
            failures.append(("H5", f"Circle-of-fifths distance {distance} from {left.title} to {right.title} exceeds {h5_limit}.", "Transpose the next song or choose a closer key."))

    if len({item.song_id for item in proposal.items}) != len(proposal.items):
        failures.append(("H6", "Songset cannot contain duplicate song IDs.", "Replace the duplicate song."))
    for left, right in zip(proposal.items, proposal.items[1:]):
        if right.phase < left.phase - 1:
            failures.append(("H7", f"Phase drops too far from {left.phase} to {right.phase}.", "Reorder to avoid a sharp backwards worship arc."))
    for item in proposal.items:
        if item.key_confidence is not None and item.key_confidence < 0.6 and item.key_shift_semitones != 0:
            failures.append(("H8", f"{item.title} has low key confidence and cannot be transposed.", "Set key_shift_semitones to 0 or choose a song with reliable key analysis."))

    return ValidationFeedback(
        passed=not failures,
        violated=[code for code, _, _ in failures],
        errors=[message for _, message, _ in failures],
        repair_hints=[hint for _, _, hint in failures],
    )
