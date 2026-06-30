"""LangGraph state schema."""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from poc.songset_constructor.config import RunConfig
from poc.songset_constructor.models import (
    SongCandidate,
    SongsetDraft,
    SongsetProposal,
    TransitionCandidate,
    ValidationFeedback,
)


class ConstructorState(TypedDict, total=False):
    config: RunConfig
    pool: list[SongCandidate]
    transition_matrix: dict[tuple[str, str], TransitionCandidate]
    beam_candidates: Annotated[list[SongsetProposal], operator.add]
    llm_drafts: Annotated[list[SongsetDraft], operator.add]
    current_draft: SongsetDraft | None
    feedback: ValidationFeedback | None
    iterations: int
    final_proposals: list[SongsetProposal]
    trace: Annotated[list[dict[str, Any]], operator.add]
    approved: bool | None
    edits: dict[str, Any] | None
    artifact_paths: dict[str, str]
    llm: Any
    judge_llm: Any
