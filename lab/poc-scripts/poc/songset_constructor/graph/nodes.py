"""LangGraph node implementations."""

from __future__ import annotations

from difflib import get_close_matches

from langgraph.types import interrupt

from poc.songset_constructor.artifacts.trace import event
from poc.songset_constructor.artifacts.writer import write_artifacts as write_output_artifacts
from poc.songset_constructor.db import fetch_catalog_pool
from poc.songset_constructor.models import (
    DraftItem,
    JudgeRanking,
    ScoreBreakdown,
    SongsetDraft,
    SongsetProposal,
    ValidationFeedback,
)
from poc.songset_constructor.rules.beam import compute_fan_out, search
from poc.songset_constructor.rules.diagnostics import beam_diagnostics, enrichment_drop_diagnostics
from poc.songset_constructor.rules.fitness import score
from poc.songset_constructor.rules.hard_constraints import validate
from poc.songset_constructor.rules.phases import apply_seasonal_bias, fuse_themes, infer_phase
from poc.songset_constructor.rules.proposals import proposal_from_draft, rank_proposals
from poc.songset_constructor.rules.themes import (
    classify_embedding_themes,
    classify_lyrics_themes,
    classify_title_themes,
)
from poc.songset_constructor.rules.transitions import recommend_transition
from poc.songset_constructor.rules.embeddings import load_theme_anchors
from poc.songset_constructor.graph.llm import build_chat_model, structured
from poc.songset_constructor.graph.state import ConstructorState


def _trace(state: ConstructorState, node: str, name: str, data: dict | None = None) -> list[dict]:
    return [event(node, name, data, int(state.get("iterations", 0) or 0))]


def load_catalog(state: ConstructorState) -> dict:
    config = state["config"]
    pool = fetch_catalog_pool(config)
    return {"pool": pool, "trace": _trace(state, "load_catalog", "exit", {"pool_size": len(pool)})}


def enrich_pool(state: ConstructorState) -> dict:
    config = state["config"]
    anchors = load_theme_anchors()
    enriched = []
    dropped = 0
    drop_diagnostics = enrichment_drop_diagnostics(state.get("pool", []))
    for candidate in state.get("pool", []):
        if candidate.tempo_bpm is None and candidate.musical_key is None:
            dropped += 1
            continue
        title = classify_title_themes(candidate.title, candidate.title_pinyin)
        lyrics = classify_lyrics_themes(candidate.lyrics_raw)
        song_emb, line_emb = classify_embedding_themes(
            candidate.song_embedding,
            candidate.line_embeddings,
            anchors,
        )
        fused = apply_seasonal_bias(fuse_themes(title, lyrics, song_emb, line_emb), config.season)
        enriched.append(
            candidate.model_copy(
                update={
                    "themes": fused,
                    "phase": infer_phase(fused, candidate.tempo_bpm),
                    "is_hymn": candidate.album_series == "HYMN",
                }
            )
        )
    return {
        "pool": enriched,
        "trace": _trace(
            state,
            "enrich_pool",
            "exit",
            {
                "pool_size": len(enriched),
                "dropped": dropped,
                **drop_diagnostics,
            },
        ),
    }


def build_transition_matrix(state: ConstructorState) -> dict:
    pool = state.get("pool", [])
    matrix = {}
    for left in pool:
        for right in pool:
            if left.recording_hash_prefix == right.recording_hash_prefix:
                continue
            transition = recommend_transition(left, right)
            if transition.cfd <= 6:
                matrix[(left.recording_hash_prefix, right.recording_hash_prefix)] = transition
    pool = compute_fan_out(pool, matrix, state["config"])
    return {
        "pool": pool,
        "transition_matrix": matrix,
        "trace": _trace(state, "build_transition_matrix", "exit", {"transitions": len(matrix)}),
    }


def beam_seed_candidates(state: ConstructorState) -> dict:
    config = state["config"]
    beam_width = max(config.top_k * 5, 40)
    proposals = search(
        state.get("pool", []),
        state["config"],
        state.get("transition_matrix", {}),
        width=beam_width,
    )
    diagnostics = (
        beam_diagnostics(state.get("pool", []), state["config"], state.get("transition_matrix", {}))
        if not proposals
        else {}
    )
    return {
        "beam_candidates": proposals,
        "trace": _trace(
            state,
            "beam_seed_candidates",
            "exit",
            {"candidates": len(proposals), **diagnostics},
        ),
    }


def _pool_prompt(state: ConstructorState) -> str:
    rows = []
    for candidate in state.get("pool", []):
        themes = ", ".join(sorted(candidate.themes, key=candidate.themes.get, reverse=True)[:2])
        rows.append(
            f"{candidate.recording_hash_prefix}: {candidate.title}, phase {candidate.phase}, "
            f"{candidate.tempo_bpm} BPM, key {candidate.musical_key} {candidate.musical_mode}, themes {themes}"
        )
    return "\n".join(rows[: state["config"].pool_limit])


def _coerce_known_hashes(draft: SongsetDraft, known: set[str]) -> tuple[SongsetDraft, list[str]]:
    repairs = []
    items = []
    for item in draft.items:
        if item.recording_hash_prefix in known:
            items.append(item)
            continue
        replacement = get_close_matches(item.recording_hash_prefix, known, n=1)
        if replacement:
            repairs.append(
                f"Replaced hallucinated hash {item.recording_hash_prefix} with {replacement[0]}."
            )
            items.append(item.model_copy(update={"recording_hash_prefix": replacement[0]}))
    return draft.model_copy(update={"items": items}), repairs


def llm_plan(state: ConstructorState) -> dict:
    config = state["config"]
    injected = state.get("llm")
    planner = (
        injected if injected is not None else structured(build_chat_model(config), SongsetDraft)
    )
    prompt = (
        f"Select a {config.songs}-song Chinese worship set using only these hash prefixes.\n"
        f"Return exactly {config.songs} items.\n\n{_pool_prompt(state)}"
    )
    draft = planner.invoke(prompt)
    known = {candidate.recording_hash_prefix for candidate in state.get("pool", [])}
    draft, repairs = _coerce_known_hashes(draft, known)
    return {
        "current_draft": draft,
        "llm_drafts": [draft],
        "trace": _trace(state, "llm_plan", "llm_call", {"prompt": prompt, "repairs": repairs}),
    }


def _draft_to_proposal(state: ConstructorState, draft: SongsetDraft) -> SongsetProposal:
    placeholder = ScoreBreakdown(f_theme=0, f_tempo=0, f_harmony=0, f_diversity=0, total=0)
    proposal = proposal_from_draft(draft, state.get("pool", []), placeholder, llm_origin=True)
    return proposal.model_copy(
        update={"score": score(proposal, state["config"], state.get("transition_matrix", {}))}
    )


def validate_score(state: ConstructorState) -> dict:
    draft = state.get("current_draft")
    if draft is None:
        feedback = ValidationFeedback(passed=False, errors=["No current draft."])
        return {
            "feedback": feedback,
            "trace": _trace(
                state,
                "validate_score",
                "validation",
                {
                    "passed": False,
                    "violated": feedback.violated,
                    "errors": feedback.errors,
                    "repair_hints": feedback.repair_hints,
                },
            ),
        }
    proposal = _draft_to_proposal(state, draft)
    feedback = validate(
        proposal,
        state["config"],
        state.get("transition_matrix", {}),
        relax_h1=state["config"].relax_h1,
        relax_h4=state["config"].relax_h4,
        relax_h5=state["config"].relax_h5,
    )
    update = {
        "feedback": feedback,
        "trace": _trace(
            state,
            "validate_score",
            "validation",
            {
                "passed": feedback.passed,
                "violated": feedback.violated,
                "errors": feedback.errors,
                "repair_hints": feedback.repair_hints,
            },
        ),
    }
    if feedback.passed:
        update["beam_candidates"] = [proposal]
    return update


def llm_refine(state: ConstructorState) -> dict:
    config = state["config"]
    injected = state.get("llm")
    refiner = (
        injected if injected is not None else structured(build_chat_model(config), SongsetDraft)
    )
    feedback = state.get("feedback")
    prompt = (
        f"Repair this {config.songs}-song draft using only known hash prefixes.\n"
        f"Errors: {feedback.errors if feedback else []}\nHints: {feedback.repair_hints if feedback else []}\n"
        f"Prior draft: {state.get('current_draft')}\nPool:\n{_pool_prompt(state)}"
    )
    draft = refiner.invoke(prompt)
    known = {candidate.recording_hash_prefix for candidate in state.get("pool", [])}
    draft, repairs = _coerce_known_hashes(draft, known)
    iteration = int(state.get("iterations", 0) or 0) + 1
    return {
        "current_draft": draft,
        "llm_drafts": [draft],
        "iterations": iteration,
        "trace": [
            event("llm_refine", "llm_call", {"prompt": prompt, "repairs": repairs}, iteration)
        ],
    }


def finalize_rank_node(state: ConstructorState) -> dict:
    config = state["config"]
    proposals = rank_proposals(
        state.get("beam_candidates", []),
        state.get("pool", []),
        config.top_k,
        config=config,
        matrix=state.get("transition_matrix", {}),
    )
    return {
        "final_proposals": proposals,
        "trace": _trace(state, "finalize_rank", "exit", {"proposals": len(proposals)}),
    }


def llm_judge(state: ConstructorState) -> dict:
    config = state["config"]
    injected = state.get("judge_llm") or state.get("llm")
    judge = injected if injected is not None else structured(build_chat_model(config), JudgeRanking)
    prompt = "Rank these finalist songsets without changing deterministic order:\n" + "\n".join(
        f"{proposal.rank}: {[item.recording_hash_prefix for item in proposal.items]}"
        for proposal in state.get("final_proposals", [])
    )
    ranking = judge.invoke(prompt)
    reasons = {
        tuple(item.recording_hash_prefixes): (item.reason, item.score)
        for item in getattr(ranking, "rankings", [])
    }
    proposals = []
    for proposal in state.get("final_proposals", []):
        key = tuple(item.recording_hash_prefix for item in proposal.items)
        reason, judge_score = reasons.get(key, (None, None))
        proposals.append(
            proposal.model_copy(update={"judge_reason": reason, "judge_score": judge_score})
        )
    return {
        "final_proposals": proposals,
        "trace": _trace(
            state,
            "llm_judge",
            "llm_call",
            {"prompt": prompt, "rankings": len(reasons)},
        ),
    }


def optional_review(state: ConstructorState) -> dict:
    proposals = state.get("final_proposals", [])
    top = proposals[0].model_dump(mode="json") if proposals else None
    decision = interrupt({"question": "Approve top proposal?", "top": top})
    action = decision.get("action", "approve")
    if action == "edit":
        current = state.get("current_draft")
        edits = decision.get("edits", {})
        if not current and proposals:
            # Seed current_draft from the top proposal so --no-llm
            # interactive-review edits have a base to apply to.
            top_proposal = proposals[0]
            current = SongsetDraft(
                items=[
                    DraftItem(
                        position=i,
                        recording_hash_prefix=item.recording_hash_prefix,
                        key_shift_semitones=item.key_shift_semitones,
                        crossfade_enabled=item.crossfade_enabled,
                        crossfade_duration_seconds=item.crossfade_duration_seconds,
                        gap_beats=item.gap_beats,
                        tempo_ratio=item.tempo_ratio,
                    )
                    for i, item in enumerate(top_proposal.items, start=1)
                ],
                rationale=top_proposal.rationale,
            )
        if current and "items" in edits:
            current = SongsetDraft.model_validate(
                {"items": edits["items"], "rationale": edits.get("rationale", current.rationale)}
            )
        return {
            "edits": decision,
            "current_draft": current,
            "trace": _trace(state, "optional_review", "resume", {"action": "edit"}),
        }
    return {
        "approved": action == "approve",
        "edits": decision,
        "trace": _trace(state, "optional_review", "resume", {"action": action}),
    }


def write_artifacts(state: ConstructorState) -> dict:
    trace = [*state.get("trace", []), *_trace(state, "write_artifacts", "artifact_written")]
    paths = write_output_artifacts(
        config=state["config"],
        proposals=state.get("final_proposals", []),
        pool=state.get("pool", []),
        trace=trace,
    )
    return {"artifact_paths": paths, "trace": _trace(state, "write_artifacts", "exit", paths)}


def route_after_beam(state: ConstructorState) -> str:
    if state["config"].no_llm or not state.get("beam_candidates"):
        return "finalize_rank"
    return "llm_plan"


def route_validation(state: ConstructorState) -> str:
    feedback = state.get("feedback")
    if feedback and feedback.passed:
        return "Accepted"
    if int(state.get("iterations", 0) or 0) < 3:
        return "Refine"
    return "Rejected"


def route_finalize(state: ConstructorState) -> str:
    if not state.get("final_proposals"):
        return "end_no_proposals"
    if state["config"].llm_judge:
        return "judge"
    if state["config"].interactive_review:
        return "review"
    return "write"


def route_after_judge(state: ConstructorState) -> str:
    return "review" if state["config"].interactive_review else "write"


def route_review(state: ConstructorState) -> str:
    edits = state.get("edits") or {}
    action = edits.get("action", "approve")
    if action == "edit":
        return "edit"
    if action == "reject" or state.get("approved") is False:
        return "reject"
    return "approve"
