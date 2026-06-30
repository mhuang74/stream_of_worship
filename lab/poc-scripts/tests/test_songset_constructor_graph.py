from poc.songset_constructor.config import RunConfig
from poc.songset_constructor.graph.builder import build_graph
from poc.songset_constructor.graph.nodes import route_after_beam, route_validation
from poc.songset_constructor.models import ValidationFeedback


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
