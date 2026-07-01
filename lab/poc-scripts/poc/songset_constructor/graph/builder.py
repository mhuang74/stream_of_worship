"""Build the songset constructor graph."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from poc.songset_constructor.config import RunConfig
from poc.songset_constructor.graph.checkpointer import choose_checkpointer
from poc.songset_constructor.graph.nodes import (
    beam_seed_candidates,
    build_transition_matrix,
    enrich_pool,
    finalize_rank_node,
    llm_judge,
    llm_plan,
    llm_refine,
    load_catalog,
    optional_review,
    route_after_beam,
    route_after_judge,
    route_finalize,
    route_review,
    route_validation,
    validate_score,
    write_artifacts,
)
from poc.songset_constructor.graph.state import ConstructorState


def build_graph(config: RunConfig):
    builder = StateGraph(ConstructorState)
    builder.add_node("load_catalog", load_catalog)
    builder.add_node("enrich_pool", enrich_pool)
    builder.add_node("build_transition_matrix", build_transition_matrix)
    builder.add_node("beam_seed_candidates", beam_seed_candidates)
    builder.add_node("llm_plan", llm_plan)
    builder.add_node("validate_score", validate_score)
    builder.add_node("llm_refine", llm_refine)
    builder.add_node("finalize_rank", finalize_rank_node)
    builder.add_node("llm_judge", llm_judge)
    builder.add_node("optional_review", optional_review)
    builder.add_node("write_artifacts", write_artifacts)

    builder.add_edge(START, "load_catalog")
    builder.add_edge("load_catalog", "enrich_pool")
    builder.add_edge("enrich_pool", "build_transition_matrix")
    builder.add_edge("build_transition_matrix", "beam_seed_candidates")
    builder.add_conditional_edges(
        "beam_seed_candidates",
        route_after_beam,
        {"llm_plan": "llm_plan", "finalize_rank": "finalize_rank"},
    )
    builder.add_edge("llm_plan", "validate_score")
    builder.add_conditional_edges(
        "validate_score",
        route_validation,
        {"Accepted": "finalize_rank", "Refine": "llm_refine", "Rejected": "finalize_rank"},
    )
    builder.add_edge("llm_refine", "validate_score")
    builder.add_conditional_edges(
        "finalize_rank",
        route_finalize,
        {"judge": "llm_judge", "review": "optional_review", "write": "write_artifacts", "end_no_proposals": END},
    )
    builder.add_conditional_edges(
        "llm_judge",
        route_after_judge,
        {"review": "optional_review", "write": "write_artifacts"},
    )
    builder.add_conditional_edges(
        "optional_review",
        route_review,
        {"approve": "write_artifacts", "reject": END, "edit": "validate_score"},
    )
    builder.add_edge("write_artifacts", END)
    return builder.compile(checkpointer=choose_checkpointer(config))
