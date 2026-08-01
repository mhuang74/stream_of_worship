"""Deterministic beam search for songset candidates."""

from __future__ import annotations

from collections.abc import Iterable
from itertools import combinations

from stream_of_worship.admin.songset_constructor.config import RunConfig
from stream_of_worship.admin.songset_constructor.models import (
    ScoreBreakdown,
    SongCandidate,
    SongsetProposal,
    TransitionCandidate,
)
from stream_of_worship.admin.songset_constructor.rules.fitness import score
from stream_of_worship.admin.songset_constructor.rules.hard_constraints import validate
from stream_of_worship.admin.songset_constructor.rules.proposals import (
    draft_from_candidates,
    proposal_from_draft,
    rank_proposals,
)

_TEMPLATES: dict[int, tuple[int, ...]] = {
    2: (1, 4),
    3: (1, 3, 5),
    4: (1, 3, 4, 5),
    5: (1, 2, 3, 4, 5),
}


def _template(songs: int) -> tuple[int, ...]:
    return _TEMPLATES[songs]


def compute_fan_out(
    pool: list[SongCandidate],
    matrix: dict[tuple[str, str], TransitionCandidate],
    config: RunConfig,
) -> list[SongCandidate]:
    updated = []
    for candidate in pool:
        fan_out = 0
        for other in pool:
            if candidate.recording_hash_prefix == other.recording_hash_prefix:
                continue
            transition = matrix.get((candidate.recording_hash_prefix, other.recording_hash_prefix))
            if (
                transition
                and transition.bpm_delta <= config.h4_limit
                and (transition.cfd <= config.h5_limit or transition.suggested_key_shift != 0)
            ):
                fan_out += 1
        updated.append(
            candidate.model_copy(update={"fan_out": fan_out, "is_dead_end": fan_out == 0})
        )
    return updated


def _candidate_sort_key(candidate: SongCandidate) -> tuple:
    return (
        candidate.is_dead_end,
        candidate.phase,
        -(candidate.tempo_bpm or 0),
        candidate.recording_hash_prefix,
    )


def _phase_matches(candidate: SongCandidate, acceptable: set[int]) -> bool:
    return candidate.phase in acceptable or any(p in acceptable for p in candidate.secondary_phases)


def _phase_score(candidate: SongCandidate, target_phase: int) -> int:
    if candidate.phase == target_phase or target_phase in candidate.secondary_phases:
        return 0
    return abs((candidate.phase or 3) - target_phase)


def _sort_key_phase_tempo(seq: list[SongCandidate], target: tuple[int, ...]) -> tuple:
    return (
        sum(_phase_score(item, target[index]) for index, item in enumerate(seq)),
        sum(
            abs((seq[index + 1].tempo_bpm or 0) - (seq[index].tempo_bpm or 0))
            for index in range(len(seq) - 1)
        ),
        tuple(item.recording_hash_prefix for item in seq),
    )


def _sort_key_theme_diverse(seq: list[SongCandidate], target: tuple[int, ...]) -> tuple:
    theme_count = len({t for item in seq for t in (item.themes or {})})
    return (
        sum(_phase_score(item, target[index]) for index, item in enumerate(seq)),
        sum(abs((seq[index + 1].tempo_bpm or 0) - (seq[index].tempo_bpm or 0)) for index in range(len(seq) - 1)),
        -theme_count,
        tuple(item.recording_hash_prefix for item in seq),
    )


def _sort_key_tempo_dynamic(seq: list[SongCandidate], target: tuple[int, ...]) -> tuple:
    bpms = [item.tempo_bpm or 0 for item in seq]
    bpm_range = max(bpms) - min(bpms) if bpms else 0
    return (
        sum(_phase_score(item, target[index]) for index, item in enumerate(seq)),
        sum(abs((seq[index + 1].tempo_bpm or 0) - (seq[index].tempo_bpm or 0)) for index in range(len(seq) - 1)),
        -bpm_range,
        tuple(item.recording_hash_prefix for item in seq),
    )


def _sort_key_hash_reversed(seq: list[SongCandidate], target: tuple[int, ...]) -> tuple:
    return (
        sum(_phase_score(item, target[index]) for index, item in enumerate(seq)),
        sum(abs((seq[index + 1].tempo_bpm or 0) - (seq[index].tempo_bpm or 0)) for index in range(len(seq) - 1)),
        tuple(reversed([item.recording_hash_prefix for item in seq])),
    )


_SORT_STRATEGIES = (
    _sort_key_phase_tempo,
    _sort_key_theme_diverse,
    _sort_key_tempo_dynamic,
    _sort_key_hash_reversed,
)


def _sequences(
    pool: list[SongCandidate],
    config: RunConfig,
    matrix: dict[tuple[str, str], TransitionCandidate],
    width: int = 8,
) -> Iterable[list[SongCandidate]]:
    target = _template(config.count)
    by_hash = {candidate.recording_hash_prefix: candidate for candidate in pool}
    beams: list[list[SongCandidate]] = [[]]
    for position, target_phase in enumerate(target, start=1):
        expanded: list[list[SongCandidate]] = []
        for beam in beams:
            used = {candidate.song_id for candidate in beam}
            for candidate in pool:
                if candidate.song_id in used:
                    continue
                if position == 1:
                    if config.relax_h1:
                        if not _phase_matches(candidate, {1, 2}):
                            continue
                    elif not _phase_matches(candidate, {1}):
                        continue
                    if candidate.tempo_bpm is None or candidate.tempo_bpm < config.opening_floor:
                        continue
                if position == len(target):
                    if not _phase_matches(candidate, {4, 5}):
                        continue
                    if candidate.tempo_bpm is None or candidate.tempo_bpm > config.closing_limit:
                        continue
                if beam and candidate.phase < beam[-1].phase - 1 and not any(
                    p >= beam[-1].phase - 1 for p in candidate.secondary_phases
                ):
                    continue
                if candidate.is_dead_end and position != len(target):
                    continue
                if beam:
                    left = beam[-1]
                    transition = matrix.get((left.recording_hash_prefix, candidate.recording_hash_prefix))
                    bpm_delta = (
                        transition.bpm_delta
                        if transition
                        else abs((candidate.tempo_bpm or 0) - (left.tempo_bpm or 0))
                    )
                    allowed = (
                        config.h4_limit
                        if transition and (transition.crossfade_duration_seconds > 0 or transition.gap_beats > 0)
                        else config.h4_no_crossfade_limit
                    )
                    if bpm_delta > allowed:
                        continue
                    distance = transition.cfd if transition else 6
                    shifted_ok = transition is not None and transition.suggested_key_shift != 0
                    if distance > config.h5_limit and not shifted_ok:
                        continue
                expanded.append([*beam, by_hash[candidate.recording_hash_prefix]])
        if position == 1:
            expanded.sort(key=lambda seq: _sort_key_phase_tempo(seq, target))
            beams = expanded[: max(width, 1)]
        else:
            opener_groups: dict[str, list[list[SongCandidate]]] = {}
            for seq in expanded:
                opener_key = seq[0].recording_hash_prefix
                opener_groups.setdefault(opener_key, []).append(seq)
            opener_ranked: list[list[list[SongCandidate]]] = []
            for group_seqs in opener_groups.values():
                middle_groups: dict[tuple[str, ...], list[list[SongCandidate]]] = {}
                for seq in group_seqs:
                    mid_key = tuple(c.recording_hash_prefix for c in seq[1:])
                    middle_groups.setdefault(mid_key, []).append(seq)
                ranked_middle: list[list[SongCandidate]] = []
                for mg in middle_groups.values():
                    mg.sort(key=lambda seq: _sort_key_phase_tempo(seq, target))
                    ranked_middle.append(mg)
                ranked_middle.sort(key=lambda mg: _sort_key_phase_tempo(mg[0], target))
                opener_ranked.append(ranked_middle)
            opener_ranked.sort(key=lambda mg_list: _sort_key_phase_tempo(mg_list[0][0], target))
            selected: list[list[SongCandidate]] = []
            seen_keys: set[tuple[str, ...]] = set()
            indices = [0] * len(opener_ranked)
            while len(selected) < width:
                made_progress = False
                for oi, mg_list in enumerate(opener_ranked):
                    if indices[oi] >= len(mg_list):
                        continue
                    mg = mg_list[indices[oi]]
                    best_seq = mg[0]
                    seq_key = tuple(c.recording_hash_prefix for c in best_seq)
                    if seq_key not in seen_keys:
                        seen_keys.add(seq_key)
                        selected.append(best_seq)
                        made_progress = True
                        if len(selected) >= width:
                            break
                    indices[oi] += 1
                if not made_progress:
                    break
            if len(selected) < width:
                remaining = [
                    s for s in sorted(expanded, key=lambda seq: _sort_key_phase_tempo(seq, target))
                    if tuple(c.recording_hash_prefix for c in s) not in seen_keys
                ]
                selected.extend(remaining[: width - len(selected)])
            beams = selected[: max(width, 1)]
        if not beams:
            return
    yield from beams


def _proposal_for_sequence(
    sequence: list[SongCandidate],
    config: RunConfig,
    matrix: dict[tuple[str, str], TransitionCandidate],
    *,
    warnings: list[str] | None = None,
) -> SongsetProposal:
    draft = draft_from_candidates(sequence, rationale="Deterministic beam seed.")
    if len(draft.items) > 1:
        updated_items = [draft.items[0]]
        for left, right in zip(draft.items, draft.items[1:]):
            transition = matrix.get((left.recording_hash_prefix, right.recording_hash_prefix))
            if transition:
                updated_items.append(
                    right.model_copy(
                        update={
                            "key_shift_semitones": transition.suggested_key_shift,
                            "crossfade_enabled": transition.crossfade_enabled,
                            "crossfade_duration_seconds": transition.crossfade_duration_seconds,
                            "gap_beats": transition.gap_beats,
                        }
                    )
                )
            else:
                updated_items.append(right)
        draft = draft.model_copy(update={"items": updated_items})
    placeholder = ScoreBreakdown(f_theme=0, f_tempo=0, f_harmony=0, f_diversity=0, total=0)
    proposal = proposal_from_draft(draft, sequence, placeholder, llm_origin=False, warnings=warnings)
    return proposal.model_copy(update={"score": score(proposal, config, matrix)})


def search(
    pool: list[SongCandidate],
    config: RunConfig,
    matrix: dict[tuple[str, str], TransitionCandidate],
    *,
    width: int = 8,
) -> list[SongsetProposal]:
    sorted_pool = sorted(pool, key=_candidate_sort_key)
    proposals: list[SongsetProposal] = []
    for sequence in _sequences(sorted_pool, config, matrix, width=width):
        proposal = _proposal_for_sequence(sequence, config, matrix)
        if validate(proposal, config, matrix, relax_h1=config.relax_h1, relax_h4=config.relax_h4, relax_h5=config.relax_h5).passed:
            proposals.append(proposal)
    if not proposals and config.count == 5:
        compact_config = RunConfig(**{**config.to_dict(), "count": 4})
        for sequence in _sequences(sorted_pool, compact_config, matrix, width=width):
            proposal = _proposal_for_sequence(sequence, compact_config, matrix, warnings=["fell_back_to_4_song_template"])
            if validate(proposal, compact_config, matrix, relax_h1=compact_config.relax_h1, relax_h4=compact_config.relax_h4, relax_h5=compact_config.relax_h5).passed:
                proposals.append(proposal)
    if not proposals:
        relaxed_config = RunConfig(**{**config.to_dict(), "relax_h4": True, "relax_h5": True})
        relaxed_pool = sorted(compute_fan_out(pool, matrix, relaxed_config), key=_candidate_sort_key)
        for sequence in _sequences(relaxed_pool, relaxed_config, matrix, width=max(width * 2, 16)):
            proposal = _proposal_for_sequence(sequence, relaxed_config, matrix, warnings=["relaxed_H4_H5"])
            if validate(proposal, relaxed_config, matrix, relax_h4=True, relax_h5=True).passed:
                proposals.append(proposal)
    if config.auto_relax and not proposals:
        relaxed_config = RunConfig(**{
            **config.to_dict(),
            "relax_h3_bpm": config.relax_h3_bpm if config.relax_h3_bpm is not None else (90 if config.intimate else 100),
            "relax_h2_bpm": config.relax_h2_bpm if config.relax_h2_bpm is not None else 80,
            "relax_h4": True,
            "relax_h5": True,
        })
        relaxed_pool = sorted(compute_fan_out(pool, matrix, relaxed_config), key=_candidate_sort_key)
        for sequence in _sequences(relaxed_pool, relaxed_config, matrix, width=max(width * 2, 16)):
            proposal = _proposal_for_sequence(sequence, relaxed_config, matrix, warnings=["relaxed_H2_H3", "relaxed_H4_H5"])
            if validate(proposal, relaxed_config, matrix, relax_h4=True, relax_h5=True).passed:
                proposals.append(proposal)
    if config.auto_relax and config.relax_h1 and not proposals:
        relaxed_config = RunConfig(**{
            **config.to_dict(),
            "relax_h3_bpm": config.relax_h3_bpm if config.relax_h3_bpm is not None else (90 if config.intimate else 100),
            "relax_h2_bpm": config.relax_h2_bpm if config.relax_h2_bpm is not None else 80,
            "relax_h4": True,
            "relax_h5": True,
        })
        relaxed_pool = sorted(compute_fan_out(pool, matrix, relaxed_config), key=_candidate_sort_key)
        for sequence in _sequences(relaxed_pool, relaxed_config, matrix, width=max(width * 2, 16)):
            proposal = _proposal_for_sequence(sequence, relaxed_config, matrix, warnings=["relaxed_H1", "relaxed_H2_H3", "relaxed_H4_H5"])
            if validate(proposal, relaxed_config, matrix, relax_h4=True, relax_h5=True, relax_h1=True).passed:
                proposals.append(proposal)
    return rank_proposals(proposals, pool, config.proposals, config=config, matrix=matrix)


def exhaustive_fallback(
    pool: list[SongCandidate],
    config: RunConfig,
    matrix: dict[tuple[str, str], TransitionCandidate],
) -> list[SongsetProposal]:
    proposals = []
    for sequence in combinations(pool, config.count):
        proposal = _proposal_for_sequence(list(sequence), config, matrix)
        if validate(proposal, config, matrix).passed:
            proposals.append(proposal)
    return rank_proposals(proposals, pool, config.proposals, config=config, matrix=matrix)
