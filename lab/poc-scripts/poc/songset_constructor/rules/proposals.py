"""Helpers for converting drafts and candidates into scored proposals."""

from __future__ import annotations

from collections.abc import Sequence

from poc.songset_constructor.config import RunConfig
from poc.songset_constructor.models import (
    DraftItem,
    ProposalItem,
    SongCandidate,
    SongsetDraft,
    SongsetProposal,
    TransitionCandidate,
)
from poc.songset_constructor.rules.fitness import middle_song_ids, score_with_diversity_penalty

from .phases import top_themes


def item_from_candidate(candidate: SongCandidate, position: int) -> ProposalItem:
    return ProposalItem(
        position=position,
        recording_hash_prefix=candidate.recording_hash_prefix,
        song_id=candidate.song_id,
        title=candidate.title,
        phase=candidate.phase,
        themes=top_themes(candidate.themes),
        bpm=candidate.tempo_bpm,
        key=candidate.musical_key,
        mode=candidate.musical_mode,
        key_confidence=candidate.key_confidence,
    )


def draft_from_candidates(candidates: Sequence[SongCandidate], rationale: str = "") -> SongsetDraft:
    return SongsetDraft(
        items=[
            DraftItem(position=index, recording_hash_prefix=candidate.recording_hash_prefix)
            for index, candidate in enumerate(candidates, start=1)
        ],
        rationale=rationale,
    )


def proposal_from_draft(
    draft: SongsetDraft,
    pool: Sequence[SongCandidate],
    score,
    *,
    llm_origin: bool,
    warnings: list[str] | None = None,
) -> SongsetProposal:
    by_hash = {candidate.recording_hash_prefix: candidate for candidate in pool}
    items: list[ProposalItem] = []
    for index, draft_item in enumerate(draft.items, start=1):
        candidate = by_hash[draft_item.recording_hash_prefix]
        items.append(
            ProposalItem(
                **draft_item.model_dump(exclude={"position"}),
                position=index,
                song_id=candidate.song_id,
                title=candidate.title,
                phase=candidate.phase,
                themes=top_themes(candidate.themes),
                bpm=candidate.tempo_bpm,
                key=candidate.musical_key,
                mode=candidate.musical_mode,
                key_confidence=candidate.key_confidence,
            )
        )
    return SongsetProposal(
        items=items,
        score=score,
        rationale=draft.rationale,
        hard_constraint_warnings=warnings or [],
        llm_origin=llm_origin,
    )


def proposal_hash_sequence(proposal: SongsetProposal) -> tuple[str, ...]:
    return tuple(item.recording_hash_prefix for item in proposal.items)


def composer_diversity(proposal: SongsetProposal, pool: Sequence[SongCandidate]) -> int:
    by_song = {candidate.song_id: candidate for candidate in pool}
    composers = {
        by_song.get(item.song_id).composer
        for item in proposal.items
        if by_song.get(item.song_id) and by_song[item.song_id].composer
    }
    return len(composers)


def rank_proposals(
    proposals: Sequence[SongsetProposal],
    pool: Sequence[SongCandidate],
    top_k: int,
    *,
    config: RunConfig | None = None,
    matrix: dict[tuple[str, str], TransitionCandidate] | None = None,
) -> list[SongsetProposal]:
    unique: dict[tuple[str, ...], SongsetProposal] = {}
    for proposal in proposals:
        key = proposal_hash_sequence(proposal)
        if key not in unique or proposal.score.total > unique[key].score.total:
            unique[key] = proposal
    ranked = sorted(
        unique.values(),
        key=lambda proposal: (
            -proposal.score.total,
            -composer_diversity(proposal, pool),
            proposal_hash_sequence(proposal),
        ),
    )
    if top_k <= 1 or len(ranked) <= top_k:
        selected = ranked[:top_k]
    elif config is not None and matrix is not None:
        # Greedy diverse selection with middle-song diversity penalty:
        # pick the highest-scoring proposal, add its middle songs to a used set,
        # then re-score remaining proposals with a penalty for overlapping
        # middle songs. This spreads middle-slot variety across the top-k.
        selected: list[SongsetProposal] = []
        used_middle: set[str] = set()
        remaining = list(ranked)
        while remaining and len(selected) < top_k:
            best: SongsetProposal | None = None
            best_score = -1.0
            best_idx = 0
            for idx, proposal in enumerate(remaining):
                penalized = score_with_diversity_penalty(
                    proposal, config, matrix, used_middle_songs=used_middle
                )
                if penalized.total > best_score or (
                    penalized.total == best_score and best is not None
                ):
                    best = proposal.model_copy(update={"score": penalized})
                    best_score = penalized.total
                    best_idx = idx
            assert best is not None
            selected.append(best)
            used_middle.update(middle_song_ids(best))
            remaining.pop(best_idx)
    else:
        # Fallback: greedy diverse selection by song overlap (no penalty scoring)
        selected: list[SongsetProposal] = []
        used_songs: set[str] = set()
        remaining = list(ranked)
        while remaining and len(selected) < top_k:
            best: SongsetProposal | None = None
            best_overlap = float("inf")
            best_idx = 0
            for idx, proposal in enumerate(remaining):
                proposal_songs = {item.song_id for item in proposal.items}
                overlap = len(proposal_songs & used_songs)
                if overlap < best_overlap or (overlap == best_overlap and best is not None):
                    best = proposal
                    best_overlap = overlap
                    best_idx = idx
            assert best is not None
            selected.append(best)
            used_songs.update(item.song_id for item in best.items)
            remaining.pop(best_idx)
    return [proposal.model_copy(update={"rank": index}) for index, proposal in enumerate(selected, start=1)]
