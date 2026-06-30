"""LangGraph orchestration for the agentic songset constructor POC."""

from __future__ import annotations

import os
from collections.abc import Iterator
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

try:
    from langgraph.checkpoint.memory import InMemorySaver
except ImportError:  # pragma: no cover - older LangGraph compatibility
    from langgraph.checkpoint.memory import MemorySaver as InMemorySaver

try:
    from langgraph.checkpoint.sqlite import SqliteSaver
except ImportError:  # pragma: no cover - optional checkpoint package
    SqliteSaver = None

try:
    from langgraph.types import interrupt
except ImportError:  # pragma: no cover
    interrupt = None

from .artifacts import write_artifacts as write_artifact_files
from .catalog import fetch_catalog, load_connection_provider
from .models import ConstructorConfig, ConstructorState, LlmDraft, SongCandidate, SongsetProposal
from .music_rules import (
    beam_seed_candidates,
    build_transition,
    detect_dead_ends,
    enrich_candidate,
    make_proposal,
    validate_and_score,
)


GraphNode = Callable[[ConstructorState], ConstructorState]
Planner = Callable[[ConstructorState], list[LlmDraft]]


def build_graph(
    config: ConstructorConfig,
    *,
    catalog_loader: Callable[[ConstructorConfig], list[SongCandidate]] | None = None,
    planner: Planner | None = None,
    output_dir: Path | None = None,
    checkpointer: Any | None = None,
) -> Any:
    loader = catalog_loader or _default_catalog_loader
    llm_planner = planner or build_llm_planner(config)
    run_output_dir = output_dir or Path(config.output_dir)

    graph = StateGraph(ConstructorState)
    graph.add_node("load_catalog", _trace_node("load_catalog", lambda s: load_catalog(s, loader)))
    graph.add_node("enrich_pool", _trace_node("enrich_pool", enrich_pool))
    graph.add_node(
        "build_transition_matrix",
        _trace_node("build_transition_matrix", build_transition_matrix),
    )
    graph.add_node("beam_seed_candidates", _trace_node("beam_seed_candidates", seed_candidates))
    graph.add_node("llm_plan", _trace_node("llm_plan", lambda s: llm_plan(s, llm_planner)))
    graph.add_node("validate_score", _trace_node("validate_score", validate_score))
    graph.add_node(
        "llm_refine",
        _trace_node("llm_refine", lambda s: llm_plan(s, llm_planner, refine=True)),
    )
    graph.add_node("optional_review", _trace_node("optional_review", optional_review))
    graph.add_node(
        "write_artifacts",
        _trace_node("write_artifacts", lambda s: write_artifacts(s, run_output_dir)),
    )
    graph.set_entry_point("load_catalog")
    graph.add_edge("load_catalog", "enrich_pool")
    graph.add_edge("enrich_pool", "build_transition_matrix")
    graph.add_edge("build_transition_matrix", "beam_seed_candidates")
    graph.add_edge("beam_seed_candidates", "llm_plan")
    graph.add_edge("llm_plan", "validate_score")
    graph.add_conditional_edges(
        "validate_score",
        should_refine,
        {"refine": "llm_refine", "review": "optional_review"},
    )
    graph.add_edge("llm_refine", "validate_score")
    graph.add_edge("optional_review", "write_artifacts")
    graph.add_edge("write_artifacts", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver())


def run_constructor(
    config: ConstructorConfig,
    *,
    catalog_loader: Callable[[ConstructorConfig], list[SongCandidate]] | None = None,
    planner: Planner | None = None,
) -> ConstructorState:
    with _checkpointer_for_config(config) as checkpointer:
        app = build_graph(
            config,
            catalog_loader=catalog_loader,
            planner=planner,
            checkpointer=checkpointer,
        )
        initial = ConstructorState(config=config)
        thread_id = config.resume_thread_id or "songset-constructor-poc"
        result = app.invoke(initial, config={"configurable": {"thread_id": thread_id}})
    if isinstance(result, ConstructorState):
        return result
    return ConstructorState.model_validate(result)


@contextmanager
def _checkpointer_for_config(config: ConstructorConfig) -> Iterator[Any]:
    if (config.interactive_review or config.resume_thread_id) and SqliteSaver is not None:
        output_dir = Path(config.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_path = output_dir / "langgraph_checkpoint.sqlite"
        with SqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
            yield saver
        return
    yield InMemorySaver()


def build_llm_planner(config: ConstructorConfig) -> Planner:
    api_key = os.environ.get("SOW_LLM_API_KEY")
    base_url = os.environ.get("SOW_LLM_BASE_URL")
    model = config.llm_model or os.environ.get("SOW_LLM_MODEL")
    missing = [
        name
        for name, value in {
            "SOW_LLM_API_KEY": api_key,
            "SOW_LLM_BASE_URL": base_url,
            "SOW_LLM_MODEL": model,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"Missing LLM configuration: {', '.join(missing)}")

    llm = ChatOpenAI(api_key=api_key, base_url=base_url, model=model, temperature=0.2)
    structured = llm.with_structured_output(LlmDraft)
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "You assemble Chinese worship songsets. Return only known recording_hashes. "
                "Deterministic validation is authoritative.",
            ),
            (
                "human",
                "Need {songs} songs. Pool JSON: {pool}. Prior drafts/feedback: {feedback}.",
            ),
        ]
    )

    def planner(state: ConstructorState) -> list[LlmDraft]:
        pool_summary = [
            {
                "hash": song.recording_hash_prefix,
                "title": song.title,
                "bpm": song.bpm,
                "key": song.musical_key,
                "mode": song.musical_mode,
                "themes": song.inferred_themes,
                "phase": song.phase,
            }
            for song in state.pool[:80]
        ]
        draft = (prompt | structured).invoke(
            {
                "songs": state.config.songs,
                "pool": pool_summary,
                "feedback": state.validation_feedback[-10:],
            }
        )
        return [draft]

    return planner


def load_catalog(
    state: ConstructorState,
    loader: Callable[[ConstructorConfig], list[SongCandidate]],
) -> ConstructorState:
    pool = loader(state.config)
    state.pool = pool
    return state


def enrich_pool(state: ConstructorState) -> ConstructorState:
    state.pool = [
        enrich_candidate(song)
        for song in state.pool
        if song.bpm > 0 and song.musical_key
    ]
    return state


def build_transition_matrix(state: ConstructorState) -> ConstructorState:
    matrix = {song.recording_hash_prefix: {} for song in state.pool}
    for source in state.pool:
        for target in state.pool:
            if source.recording_hash_prefix == target.recording_hash_prefix:
                continue
            matrix[source.recording_hash_prefix][target.recording_hash_prefix] = build_transition(
                source,
                target,
            )
    state.transition_matrix = matrix
    dead_ends = detect_dead_ends(state.pool, state.transition_matrix)
    if dead_ends:
        state.validation_feedback.extend(dead_ends[:20])
    return state


def seed_candidates(state: ConstructorState) -> ConstructorState:
    state.candidate_beams = beam_seed_candidates(state.pool, state.transition_matrix, state.config)
    return state


def llm_plan(state: ConstructorState, planner: Planner, refine: bool = False) -> ConstructorState:
    drafts = planner(state)
    state.llm_drafts.extend(drafts)
    state.iteration += 1 if refine else 0
    return state


def validate_score(state: ConstructorState) -> ConstructorState:
    pool_by_hash = {song.recording_hash_prefix: song for song in state.pool}
    proposals: list[SongsetProposal] = []
    valid_draft_seen = False
    for draft in state.llm_drafts[-3:]:
        if len(set(draft.recording_hashes)) != len(draft.recording_hashes):
            state.validation_feedback.append("H2 duplicate recordings")
            continue
        songs = [pool_by_hash[h] for h in draft.recording_hashes if h in pool_by_hash]
        if len(songs) == len(draft.recording_hashes):
            proposals.append(
                make_proposal(songs, state.transition_matrix, state.config, draft.rationale)
            )
            valid_draft_seen = True
        else:
            missing = sorted(set(draft.recording_hashes) - set(pool_by_hash))
            state.validation_feedback.append(f"Unknown recording hashes: {', '.join(missing)}")

    proposals.extend(state.candidate_beams)
    for proposal in proposals:
        errors, warnings, score = validate_and_score(
            proposal,
            pool_by_hash,
            state.transition_matrix,
            state.config,
        )
        proposal.validation_errors = errors
        proposal.validation_warnings = warnings
        proposal.score_breakdown = score
        if errors:
            state.validation_feedback.extend(errors)
    valid = [proposal for proposal in proposals if not proposal.validation_errors]
    state.final_proposals = sorted(
        valid,
        key=lambda p: p.score_breakdown.total,
        reverse=True,
    )[: state.config.top_k]
    if state.llm_drafts and not valid_draft_seen and state.iteration < 3:
        state.final_proposals = []
    return state


def should_refine(state: ConstructorState) -> str:
    if state.final_proposals:
        return "review"
    if state.iteration < 3:
        return "refine"
    state.final_proposals = state.candidate_beams[: state.config.top_k]
    return "review"


def optional_review(state: ConstructorState) -> ConstructorState:
    if state.config.interactive_review and interrupt is not None:
        summaries = [
            {
                "score": proposal.score_breakdown.total,
                "items": [item.model_dump() for item in proposal.items],
                "warnings": proposal.validation_warnings,
            }
            for proposal in state.final_proposals
        ]
        interrupt({"proposals": summaries})
    return state


def write_artifacts(state: ConstructorState, output_dir: Path) -> ConstructorState:
    paths = write_artifact_files(state, output_dir)
    state.trace.append({"node": "write_artifacts", "paths": {k: str(v) for k, v in paths.items()}})
    return state


def _default_catalog_loader(config: ConstructorConfig) -> list[SongCandidate]:
    with load_connection_provider() as provider:
        return fetch_catalog(provider, config)


def _trace_node(name: str, fn: GraphNode) -> GraphNode:
    def wrapped(state: ConstructorState) -> ConstructorState:
        state = ConstructorState.model_validate(state)
        before = len(state.final_proposals)
        state = fn(state)
        state.trace.append(
            {
                "node": name,
                "pool_size": len(state.pool),
                "candidate_beams": len(state.candidate_beams),
                "final_proposals_before": before,
                "final_proposals_after": len(state.final_proposals),
                "iteration": state.iteration,
            }
        )
        return state

    return wrapped
