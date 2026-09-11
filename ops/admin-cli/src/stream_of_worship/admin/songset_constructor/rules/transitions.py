"""Transition recommendations between adjacent songs."""

from __future__ import annotations

from stream_of_worship.admin.songset_constructor.models import SongCandidate, TransitionCandidate
from stream_of_worship.admin.songset_constructor.rules.harmony import (
    cfd,
    key_compatibility_score,
    suggest_key_shift,
)


def _boundary_pair(
    left: SongCandidate, right: SongCandidate
) -> tuple[str | None, str | None, float | None, str | None, str | None, float | None, str]:
    """Key/mode/provenance selection — PAIR-LEVEL (all-or-nothing on keys).

    Returns (from_key, from_mode, from_conf, to_key, to_mode, to_conf, boundary_source).
    A pair is boundary-sourced only when both sides have boundary keys; mixed pairs
    compute entirely on song-level keys. Keys never mix provenance within one pair.
    """
    left_is_boundary = left.exit_key is not None
    right_is_boundary = right.entry_key is not None
    if left_is_boundary and right_is_boundary:
        boundary_source = "component"
        return (
            left.exit_key,
            left.exit_mode,
            left.exit_key_confidence,
            right.entry_key,
            right.entry_mode,
            right.entry_key_confidence,
            boundary_source,
        )
    return (
        left.musical_key,
        left.musical_mode,
        left.key_confidence,
        right.musical_key,
        right.musical_mode,
        right.key_confidence,
        "song_level",
    )


def _bpm_pair(left: SongCandidate, right: SongCandidate, boundary_source: str) -> tuple[float, list[str]]:
    """BPM is per-side best-available within component pairs.

    Each side independently uses its boundary BPM when present, else that song's
    song-level ``tempo_bpm`` (the pool's best per-side estimate). Gaps produce
    per-song warnings; ``boundary_source`` describes the keys, these warnings
    describe the BPM gaps.
    """
    warnings: list[str] = []
    if boundary_source == "component":
        from_bpm = left.exit_bpm
        to_bpm = right.entry_bpm
        if from_bpm is None and to_bpm is None:
            warnings.append("missing boundary bpm on both sides — delta unreliable")
        else:
            if from_bpm is None:
                from_bpm = left.tempo_bpm
                warnings.append(f"missing exit bpm on {left.title}; used song-level bpm")
            if to_bpm is None:
                to_bpm = right.tempo_bpm
                warnings.append(f"missing entry bpm on {right.title}; used song-level bpm")
        return abs((to_bpm or 0.0) - (from_bpm or 0.0)), warnings
    # song_level pairs: today's formula; missing BPM collapses to 0 delta — surface it.
    if left.tempo_bpm is None or right.tempo_bpm is None:
        missing_side = left.title if left.tempo_bpm is None else right.title
        warnings.append(f"missing bpm on {missing_side} — delta unreliable")
    return abs((right.tempo_bpm or 0.0) - (left.tempo_bpm or 0.0)), warnings


def recommend_transition(from_cand: SongCandidate, to_cand: SongCandidate) -> TransitionCandidate:
    from_key, from_mode, from_conf, to_key, to_mode, to_conf, boundary_source = _boundary_pair(
        from_cand, to_cand
    )
    distance = cfd(from_key, from_mode, to_key, to_mode)
    shift, shifted_distance = suggest_key_shift(from_key, from_mode, to_key, to_mode)
    bpm_delta, bpm_warnings = _bpm_pair(from_cand, to_cand, boundary_source)
    warnings: list[str] = [*bpm_warnings]
    if (from_conf is not None and from_conf < 0.6) or (to_conf is not None and to_conf < 0.6):
        warnings.append("low_key_confidence")

    if distance <= 1:
        technique = "pivot"
        crossfade_seconds = 0.0
        gap_beats = 2.0
    elif distance <= 2:
        technique = "relative" if from_mode != to_mode else "direct"
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
        boundary_source=boundary_source,
    )