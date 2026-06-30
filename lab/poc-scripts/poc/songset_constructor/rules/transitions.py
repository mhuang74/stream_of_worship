"""Transition recommendations between adjacent songs."""

from __future__ import annotations

from poc.songset_constructor.models import SongCandidate, TransitionCandidate

from .harmony import cfd, key_compatibility_score, suggest_key_shift


def recommend_transition(from_cand: SongCandidate, to_cand: SongCandidate) -> TransitionCandidate:
    distance = cfd(
        from_cand.musical_key,
        from_cand.musical_mode,
        to_cand.musical_key,
        to_cand.musical_mode,
    )
    shift, shifted_distance = suggest_key_shift(
        from_cand.musical_key,
        from_cand.musical_mode,
        to_cand.musical_key,
        to_cand.musical_mode,
    )
    bpm_delta = abs((to_cand.tempo_bpm or 0.0) - (from_cand.tempo_bpm or 0.0))
    warnings: list[str] = []
    if (from_cand.key_confidence is not None and from_cand.key_confidence < 0.6) or (
        to_cand.key_confidence is not None and to_cand.key_confidence < 0.6
    ):
        warnings.append("low_key_confidence")

    if distance <= 1:
        technique = "pivot"
        crossfade_seconds = 0.0
        gap_beats = 2.0
    elif distance <= 2:
        technique = "relative" if from_cand.musical_mode != to_cand.musical_mode else "direct"
        crossfade_seconds = 0.0
        gap_beats = 2.0
    elif shifted_distance <= 2 and shift != 0:
        technique = "transposition"
        crossfade_seconds = 4.0
        gap_beats = 4.0
    elif distance == 3:
        technique = "vamp"
        crossfade_seconds = 6.0
        gap_beats = 4.0
    else:
        technique = "direct_modulation"
        crossfade_seconds = 8.0
        gap_beats = 6.0

    return TransitionCandidate(
        from_hash_prefix=from_cand.recording_hash_prefix,
        to_hash_prefix=to_cand.recording_hash_prefix,
        cfd=distance,
        bpm_delta=bpm_delta,
        key_compat=key_compatibility_score(distance),
        suggested_key_shift=shift,
        transition_technique=technique,
        crossfade_enabled=crossfade_seconds > 0,
        crossfade_duration_seconds=crossfade_seconds,
        gap_beats=gap_beats,
        warnings=warnings,
    )
