from poc.songset_constructor.config import RunConfig
from poc.songset_constructor.graph.builder import build_graph
from poc.songset_constructor.graph.nodes import route_after_beam, route_validation, validate_score
from poc.songset_constructor.models import DraftItem, SongsetDraft, ValidationFeedback
from poc.songset_constructor.rules.transitions import recommend_transition


def test_no_llm_routes_to_finalize():
    assert route_after_beam({"config": RunConfig(no_llm=True), "beam_candidates": [object()]}) == "finalize_rank"


def test_validation_routes_refine_until_cap():
    state = {"feedback": ValidationFeedback(passed=False), "iterations": 2}
    assert route_validation(state) == "Refine"
    state["iterations"] = 3
    assert route_validation(state) == "Rejected"
    state["feedback"] = ValidationFeedback(passed=True)
    assert route_validation(state) == "Accepted"


def test_no_llm_graph_writes_artifacts(tmp_path, synthetic_pool, monkeypatch):
    monkeypatch.setattr(
        "poc.songset_constructor.graph.nodes.fetch_catalog_pool",
        lambda _config: synthetic_pool,
    )
    config = RunConfig(no_llm=True, output_dir=tmp_path, thread_id="test-thread")
    graph = build_graph(config)
    result = graph.invoke(
        {
            "config": config,
            "iterations": 0,
            "trace": [],
        },
        {"configurable": {"thread_id": config.thread_id}},
    )
    assert result["final_proposals"]
    assert (tmp_path / "proposals.json").exists()
    assert (tmp_path / "proposal_report.md").exists()
    assert (tmp_path / "candidate_pool.csv").exists()
    assert (tmp_path / "graph_trace.jsonl").exists()


def test_invalid_llm_draft_is_not_added_to_ranked_candidates(synthetic_pool):
    matrix = {
        (left.recording_hash_prefix, right.recording_hash_prefix): recommend_transition(left, right)
        for left in synthetic_pool
        for right in synthetic_pool
        if left.recording_hash_prefix != right.recording_hash_prefix
    }
    draft = SongsetDraft(
        items=[
            DraftItem(position=1, recording_hash_prefix="h001"),
            DraftItem(position=2, recording_hash_prefix="h001"),
            DraftItem(position=3, recording_hash_prefix="h003"),
            DraftItem(position=4, recording_hash_prefix="h004"),
            DraftItem(position=5, recording_hash_prefix="h005"),
        ]
    )
    update = validate_score(
        {
            "config": RunConfig(no_llm=False),
            "pool": synthetic_pool,
            "transition_matrix": matrix,
            "current_draft": draft,
            "iterations": 0,
        }
    )
    assert update["feedback"].passed is False
    assert "beam_candidates" not in update
