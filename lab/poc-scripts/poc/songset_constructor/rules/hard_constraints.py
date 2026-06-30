"""Hard constraint validation H1-H8."""

from __future__ import annotations

from poc.songset_constructor.config import RunConfig
from poc.songset_constructor.models import SongsetProposal, TransitionCandidate, ValidationFeedback


def validate(
    proposal: SongsetProposal,
    config: RunConfig,
    matrix: dict[tuple[str, str], TransitionCandidate],
    *,
    relax_h4: bool = False,
    relax_h5: bool = False,
) -> ValidationFeedback:
    failures: list[tuple[str, str, str]] = []
    phases = [item.phase for item in proposal.items]
    bpms = [item.bpm for item in proposal.items]

    if phases.count(1) != 1 or not any(phase in {3, 4} for phase in phases) or phases[-1] not in {4, 5}:
        failures.append(("H1", "Phase coverage must include one opener, worship/response, and phase 4/5 closer.", "Adjust ordering to follow phases 1-5."))
    if bpms[0] is None or bpms[0] < 110:
        failures.append(("H2", "Opening tempo must be at least 110 BPM.", "Choose a stronger opener."))
    closing_limit = 80 if config.intimate else 90
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
