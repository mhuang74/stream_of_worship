from io import StringIO

from rich.console import Console
from typer.testing import CliRunner

from poc.songset_constructor import cli
from poc.songset_constructor.cli import app
from poc.songset_constructor.models import SongCandidate


def test_cli_traces_no_proposals_without_writing_artifacts(tmp_path, synthetic_pool, monkeypatch):
    monkeypatch.setattr(
        "poc.songset_constructor.graph.nodes.fetch_catalog_pool",
        lambda _config: synthetic_pool,
    )
    monkeypatch.setattr(
        "poc.songset_constructor.graph.nodes.search",
        lambda _pool, _config, _transition_matrix: [],
    )

    result = CliRunner().invoke(
        app,
        [
            "--no-llm",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "start load_catalog" in result.output
    assert "stop load_catalog in " in result.output
    assert "pool_size=6" in result.output
    assert "start beam_seed_candidates" in result.output
    assert "stop beam_seed_candidates in " in result.output
    assert "candidates=0" in result.output
    assert "stop finalize_rank in " in result.output
    assert "proposals=0" in result.output
    assert "No artifacts were written; no valid proposals were generated." in result.output
    assert "No songset artifacts were written because" in result.output
    assert "the beam search could not assemble any" in result.output
    assert "hard rules" in result.output
    assert not (tmp_path / "graph_trace.jsonl").exists()


def test_cli_uses_llm_to_summarize_no_results(tmp_path, synthetic_pool, monkeypatch):
    monkeypatch.setenv("SOW_LLM_API_KEY", "test-key")
    monkeypatch.setenv("SOW_LLM_MODEL", "test-model")
    monkeypatch.setattr(
        "poc.songset_constructor.graph.nodes.fetch_catalog_pool",
        lambda _config: synthetic_pool,
    )
    monkeypatch.setattr(
        "poc.songset_constructor.graph.nodes.search",
        lambda _pool, _config, _transition_matrix: [],
    )
    prompts = []

    class FakeChat:
        def invoke(self, prompt):
            prompts.append(prompt)
            return "LLM summary: beam search produced zero candidates after transition analysis."

    monkeypatch.setattr(cli, "build_chat_model", lambda _config: FakeChat())

    result = CliRunner().invoke(
        app,
        [
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "No artifacts were written; no valid proposals were generated." in result.output
    assert "LLM summary: beam search produced zero candidates" in result.output
    assert len(prompts) == 1
    assert "beam_seed_candidates.exit: candidates=0" in prompts[0]
    assert "finalize_rank.exit: proposals=0" in prompts[0]


def test_cli_no_llm_explains_enrichment_shortfall(tmp_path, monkeypatch):
    pool = [
        SongCandidate(
            song_id=f"missing-{index}",
            title=f"Missing Metadata {index}",
            recording_hash_prefix=f"m{index:03d}",
        )
        for index in range(5)
    ]
    pool.append(
        SongCandidate(
            song_id="valid-1",
            title="Ready Song",
            recording_hash_prefix="ready001",
            tempo_bpm=118,
            musical_key="G",
            musical_mode="maj",
            phase=1,
        )
    )
    monkeypatch.setattr(
        "poc.songset_constructor.graph.nodes.fetch_catalog_pool",
        lambda _config: pool,
    )

    result = CliRunner().invoke(
        app,
        [
            "--no-llm",
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert "only 1 enriched candidates remain for a 5-song set" in result.output
    assert "most loaded songs were dropped by missing_tempo_and_key_metadata (5/6)" in result.output
    assert "No artifacts were written; no valid proposals were generated." in result.output


def test_llm_no_results_prompt_includes_rule_drop_diagnostics(tmp_path, monkeypatch):
    monkeypatch.setenv("SOW_LLM_API_KEY", "test-key")
    monkeypatch.setenv("SOW_LLM_MODEL", "test-model")
    pool = [
        SongCandidate(
            song_id=f"missing-{index}",
            title=f"Missing Metadata {index}",
            recording_hash_prefix=f"m{index:03d}",
        )
        for index in range(4)
    ]
    pool.append(
        SongCandidate(
            song_id="valid-1",
            title="Ready Song",
            recording_hash_prefix="ready001",
            tempo_bpm=118,
            musical_key="G",
            musical_mode="maj",
            phase=1,
        )
    )
    monkeypatch.setattr(
        "poc.songset_constructor.graph.nodes.fetch_catalog_pool",
        lambda _config: pool,
    )
    prompts = []

    class FakeChat:
        def invoke(self, prompt):
            prompts.append(prompt)
            return "LLM summary with diagnostics."

    monkeypatch.setattr(cli, "build_chat_model", lambda _config: FakeChat())

    result = CliRunner().invoke(
        app,
        [
            "--output-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert len(prompts) == 1
    assert "Rule-drop diagnostics:" in prompts[0]
    assert "only 1 enriched candidates remain for a 5-song set" in prompts[0]
    assert "most loaded songs were dropped by missing_tempo_and_key_metadata (4/5)" in prompts[0]


def test_debug_trace_prints_full_llm_prompt(monkeypatch):
    output = StringIO()
    monkeypatch.setattr(
        cli,
        "console",
        Console(file=output, force_terminal=False, color_system=None),
    )

    class FakeGraph:
        def stream(self, _input_value, _graph_config, stream_mode):
            assert stream_mode == "debug"
            yield {"type": "task", "payload": {"id": "task-1", "name": "llm_plan"}}
            yield {
                "type": "task_result",
                "payload": {
                    "id": "task-1",
                    "name": "llm_plan",
                    "result": {
                        "trace": [
                            {
                                "data": {
                                    "prompt": "FULL PROMPT LINE 1\nFULL PROMPT LINE 2",
                                    "repairs": [],
                                }
                            }
                        ]
                    },
                    "interrupts": [],
                },
            }
            yield {"type": "checkpoint", "payload": {"values": {"artifact_paths": {}}}}

    cli._run_graph_with_traces(FakeGraph(), {}, {})

    rendered = output.getvalue()
    assert "prompt llm_plan" in rendered
    assert "FULL PROMPT LINE 1\nFULL PROMPT LINE 2" in rendered
    assert "end prompt llm_plan" in rendered
    assert "stop llm_plan in " in rendered
