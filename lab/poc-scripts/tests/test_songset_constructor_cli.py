from io import StringIO

from rich.console import Console
from typer.testing import CliRunner

from poc.songset_constructor import cli
from poc.songset_constructor.cli import app


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
    assert not (tmp_path / "graph_trace.jsonl").exists()


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
