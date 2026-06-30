"""Helpers for converting drafts and candidates into scored proposals."""

from __future__ import annotations

from collections.abc import Sequence

from poc.songset_constructor.models import DraftItem, ProposalItem, SongCandidate, SongsetDraft, SongsetProposal

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
    )[:top_k]
    return [proposal.model_copy(update={"rank": index}) for index, proposal in enumerate(ranked, start=1)]
