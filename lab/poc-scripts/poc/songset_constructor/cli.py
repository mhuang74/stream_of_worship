"""Typer CLI for the songset constructor POC."""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Annotated, Any

import typer
from rich.console import Console

from poc.songset_constructor.config import RunConfig, default_output_dir
from poc.songset_constructor.graph.builder import build_graph
from poc.songset_constructor.graph.llm import build_chat_model
from poc.songset_constructor.rules.diagnostics import diagnostic_lines
from poc.songset_constructor.rules.hard_constraints import RULE_DESCRIPTIONS

app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")
console = Console()


SUMMARY_KEYS = (
    "pool_size",
    "dropped",
    "transitions",
    "candidates",
    "passed",
    "violated",
    "proposals",
)


def _format_trace_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (list, tuple, set)):
        return str(len(value))
    return str(value)


def _stop_details(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    trace = result.get("trace") or []
    if not trace:
        return ""
    data = trace[-1].get("data") if isinstance(trace[-1], dict) else None
    if not isinstance(data, dict):
        return ""
    parts = [
        f"{key}={_format_trace_value(data[key])}"
        for key in SUMMARY_KEYS
        if key in data and data[key] is not None
    ]
    return " " + " ".join(parts) if parts else ""


def _prompt_from_result(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    trace = result.get("trace") or []
    if not trace:
        return None
    data = trace[-1].get("data") if isinstance(trace[-1], dict) else None
    if not isinstance(data, dict):
        return None
    prompt = data.get("prompt")
    return prompt if isinstance(prompt, str) and prompt else None


def _print_prompt(node_name: str, prompt: str) -> None:
    console.print(f"prompt {node_name}")
    console.print(prompt)
    console.print(f"end prompt {node_name}")


def _trace_events(result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, dict):
        return []
    trace = result.get("trace") or []
    return [entry for entry in trace if isinstance(entry, dict)]


def _event_lines(result: Any) -> list[str]:
    lines = []
    for entry in _trace_events(result):
        node = entry.get("node", "unknown")
        event_name = entry.get("event", "event")
        iteration = entry.get("iteration", 0)
        data = entry.get("data") if isinstance(entry.get("data"), dict) else {}
        details = ", ".join(
            f"{key}={_format_trace_value(value)}"
            for key, value in data.items()
            if key != "prompt" and value not in (None, [], {})
        )
        suffix = f": {details}" if details else ""
        lines.append(f"- iteration {iteration} {node}.{event_name}{suffix}")
    return lines


def _latest_trace_data(result: Any, node: str) -> dict[str, Any]:
    for entry in reversed(_trace_events(result)):
        if entry.get("node") != node:
            continue
        data = entry.get("data")
        return data if isinstance(data, dict) else {}
    return {}


def _trace_data_by_node(result: Any) -> dict[str, dict[str, Any]]:
    return {
        node: _latest_trace_data(result, node)
        for node in (
            "load_catalog",
            "enrich_pool",
            "build_transition_matrix",
            "beam_seed_candidates",
            "finalize_rank",
            "validate_score",
        )
    }


def _rule_drop_diagnostics_text(config: RunConfig, result: Any) -> str:
    lines = diagnostic_lines(config, _trace_data_by_node(result))
    return "\n".join(f"- {line}" for line in lines)


_ROLE_TO_RULE: dict[str, str] = {
    "valid_openers_h2": "H2",
    "valid_closers_h3": "H3",
    "phase_1_candidates_h1": "H1",
    "phase_3_or_4_candidates_h1": "H1",
    "phase_4_or_5_candidates_h1": "H1",
    "compatible_transitions_h5": "H5",
}


def _relevant_rule_codes(config: RunConfig, result: Any) -> set[str]:
    trace_data = _trace_data_by_node(result)
    beam_data = trace_data["beam_seed_candidates"]
    codes: set[str] = set()
    rejections = beam_data.get("hard_rule_rejections") or {}
    if isinstance(rejections, dict):
        codes.update(str(code) for code in rejections)
    role = beam_data.get("role_eligibility") or {}
    if isinstance(role, dict):
        for key, value in role.items():
            if value == 0 and key in _ROLE_TO_RULE:
                codes.add(_ROLE_TO_RULE[key])
    return codes


def _rule_reference_text(config: RunConfig, result: Any) -> str:
    codes = sorted(_relevant_rule_codes(config, result))
    if not codes:
        codes = sorted(RULE_DESCRIPTIONS)
    lines = [f"- {code}: {RULE_DESCRIPTIONS[code]}" for code in codes]
    return "\n".join(lines)


def _fallback_no_results_summary(config: RunConfig, result: Any) -> str:
    trace_data = _trace_data_by_node(result)
    load_data = trace_data["load_catalog"]
    enrich_data = trace_data["enrich_pool"]
    transition_data = trace_data["build_transition_matrix"]
    beam_data = trace_data["beam_seed_candidates"]
    rank_data = trace_data["finalize_rank"]
    validation_data = trace_data["validate_score"]

    reasons = []
    pool_size = load_data.get("pool_size")
    enriched_size = enrich_data.get("pool_size")
    dropped = enrich_data.get("dropped")
    transitions = transition_data.get("transitions")
    candidates = beam_data.get("candidates")
    proposals = rank_data.get("proposals")

    if pool_size == 0:
        reasons.append("the catalog query returned an empty pool")
    if enriched_size == 0 and pool_size:
        reasons.append("all loaded songs were dropped during enrichment")
    elif dropped:
        reasons.append(f"{dropped} loaded songs lacked enough tempo/key metadata for enrichment")
    for line in diagnostic_lines(config, trace_data):
        reasons.append(line)
    if transitions == 0 and enriched_size:
        reasons.append("no compatible transitions survived the transition rules")
    if candidates == 0:
        reasons.append("the beam search could not assemble any sequence satisfying the hard rules")
    if proposals == 0:
        reasons.append("final ranking had no proposals to write")

    errors = validation_data.get("errors") or []
    hints = validation_data.get("repair_hints") or []
    if errors:
        reasons.append("LLM drafts failed validation: " + "; ".join(str(error) for error in errors))
    if hints:
        reasons.append("repair hints were: " + "; ".join(str(hint) for hint in hints))

    if not reasons:
        reasons.append("the graph ended before producing any artifact paths")
    return "No songset artifacts were written because " + "; ".join(reasons) + "."


def _llm_no_results_summary(config: RunConfig, result: Any) -> str:
    if config.no_llm:
        return _fallback_no_results_summary(config, result)

    diagnostics = _rule_drop_diagnostics_text(config, result) or "- none"
    rule_reference = _rule_reference_text(config, result)

    prompt = (
        "Write a succinct, clear, but detailed user-facing summary explaining why the "
        "songset constructor produced no results. Use only the facts below. Mention the "
        "construction stages that matter, avoid speculation, and keep it to 3-5 sentences. "
        "When the candidate count is below the requested song count, mention any majority "
        "or all-candidate drop rule shown in Rule-drop diagnostics. When you cite a Hard rule "
        "code (H1-H8), briefly explain what the rule requires and, where the role-eligibility "
        "counts are zero, note that no songs in the pool satisfy it.\n\n"
        f"Run configuration: {config.to_dict()}\n\n"
        "Trace events:\n" + "\n".join(_event_lines(result)) + "\n\n"
        "Rule-drop diagnostics:\n"
        f"{diagnostics}\n\n"
        "Hard rule reference:\n"
        f"{rule_reference}\n\n"
        f"Fallback diagnosis: {_fallback_no_results_summary(config, result)}"
    )
    chat = build_chat_model(config)
    response = chat.invoke(prompt)
    content = getattr(response, "content", response)
    if isinstance(content, list):
        content = " ".join(str(part) for part in content)
    summary = str(content).strip()
    return summary or _fallback_no_results_summary(config, result)


def _print_no_results_summary(config: RunConfig, result: Any) -> None:
    try:
        summary = _llm_no_results_summary(config, result)
    except Exception as exc:
        summary = f"{_fallback_no_results_summary(config, result)} LLM summary failed: {exc}"
    console.print("[yellow]No artifacts were written; no valid proposals were generated.[/yellow]")
    console.print(summary)


def _print_output_files(paths: dict[str, str]) -> None:
    if not paths:
        console.print("[yellow]Output files written: none[/yellow]")
        return

    console.print("[green]Output files written:[/green]")
    for path in paths.values():
        output_path = Path(path)
        console.print(f"  {output_path.name}: {output_path}")


def _run_graph_with_traces(graph: Any, input_value: Any, graph_config: dict) -> dict:
    started_at: dict[str, float] = {}
    latest_values: dict[str, Any] = {}
    interrupts = []

    for chunk in graph.stream(input_value, graph_config, stream_mode="debug"):
        if not isinstance(chunk, dict):
            continue
        payload = chunk.get("payload") or {}
        event_type = chunk.get("type")

        if event_type == "checkpoint":
            values = payload.get("values")
            if isinstance(values, dict):
                latest_values = values
            continue

        if event_type == "task":
            name = payload.get("name")
            task_id = payload.get("id")
            if name and task_id:
                started_at[task_id] = perf_counter()
                console.print(f"start {name}")
            continue

        if event_type != "task_result":
            continue

        name = payload.get("name")
        task_id = payload.get("id")
        if name and task_id:
            elapsed = perf_counter() - started_at.pop(task_id, perf_counter())
            node_result = payload.get("result")
            prompt = _prompt_from_result(node_result)
            if prompt:
                _print_prompt(name, prompt)
            details = _stop_details(node_result)
            console.print(f"stop {name} in {elapsed:.2f}s{details}")
        interrupts = payload.get("interrupts") or interrupts

    result = dict(latest_values)
    if interrupts:
        result["__interrupt__"] = interrupts
    return result


@app.command()
def construct(
    songs: Annotated[int, typer.Option("--songs", min=4, max=5)] = 5,
    top_k: Annotated[int, typer.Option("--top-k", min=1, max=10)] = 3,
    pool_limit: Annotated[int, typer.Option("--pool-limit", min=4)] = 200,
    output_dir: Annotated[Path | None, typer.Option("--output-dir")] = None,
    album_series: Annotated[list[str] | None, typer.Option("--album-series")] = None,
    include_cpw: Annotated[bool, typer.Option("--include-cpw/--no-include-cpw")] = False,
    intimate: Annotated[bool, typer.Option("--intimate/--no-intimate")] = False,
    hymnal_mode: Annotated[bool, typer.Option("--hymnal-mode/--no-hymnal-mode")] = False,
    season: Annotated[str | None, typer.Option("--season")] = None,
    interactive_review: Annotated[
        bool, typer.Option("--interactive-review/--no-interactive-review")
    ] = False,
    resume_thread_id: Annotated[str | None, typer.Option("--resume-thread-id")] = None,
    no_llm: Annotated[bool, typer.Option("--no-llm/--llm")] = False,
    llm_judge: Annotated[bool, typer.Option("--llm-judge/--no-llm-judge")] = False,
    llm_model: Annotated[str | None, typer.Option("--llm-model")] = None,
    env_file: Annotated[Path | None, typer.Option("--env-file")] = None,
    relax_h3_bpm: Annotated[int | None, typer.Option("--relax-h3-bpm", min=0)] = None,
    relax_h2_bpm: Annotated[int | None, typer.Option("--relax-h2-bpm", min=0)] = None,
    relax_h1: Annotated[bool, typer.Option("--relax-h1/--no-relax-h1")] = True,
    auto_relax: Annotated[bool, typer.Option("--auto-relax/--no-auto-relax")] = True,
    relax_h4: Annotated[bool, typer.Option("--relax-h4/--no-relax-h4")] = False,
    relax_h5: Annotated[bool, typer.Option("--relax-h5/--no-relax-h5")] = False,
    relax_h4_bpm: Annotated[int | None, typer.Option("--relax-h4-bpm", min=0)] = None,
    relax_h5_cfd: Annotated[int | None, typer.Option("--relax-h5-cfd", min=0)] = None,
) -> None:
    """Construct Chinese worship songset proposal artifacts."""
    try:
        config = RunConfig(
            songs=songs,
            top_k=top_k,
            pool_limit=pool_limit,
            output_dir=output_dir or default_output_dir(),
            album_series=album_series,
            include_cpw=include_cpw,
            intimate=intimate,
            hymnal_mode=hymnal_mode,
            season=season,
            interactive_review=interactive_review,
            resume_thread_id=resume_thread_id,
            no_llm=no_llm,
            llm_judge=llm_judge,
            llm_model=llm_model,
            env_file=env_file,
            relax_h3_bpm=relax_h3_bpm,
            relax_h2_bpm=relax_h2_bpm,
            relax_h1=relax_h1,
            auto_relax=auto_relax,
            relax_h4=relax_h4,
            relax_h5=relax_h5,
            relax_h4_bpm=relax_h4_bpm,
            relax_h5_cfd=relax_h5_cfd,
        )
        config.validate_environment()
    except Exception as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(2) from exc

    graph = build_graph(config)
    initial_state = {"config": config, "iterations": 0, "trace": []}
    graph_config = {"configurable": {"thread_id": config.thread_id}}

    try:
        result = _run_graph_with_traces(graph, initial_state, graph_config)
        while "__interrupt__" in result:
            interrupt_obj = result["__interrupt__"][0]
            # The debug stream serializes Interrupt dataclasses to dicts via
            # asdict(), so handle both dict and Interrupt object forms.
            payload = interrupt_obj["value"] if isinstance(interrupt_obj, dict) else interrupt_obj.value
            console.print(payload)
            action = typer.prompt("Review action (approve/reject)", default="approve")
            from langgraph.types import Command

            result = _run_graph_with_traces(graph, Command(resume={"action": action}), graph_config)
    except Exception as exc:
        console.print(f"[red]Run failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    paths = result.get("artifact_paths", {})
    _print_output_files(paths)
    if not paths:
        _print_no_results_summary(config, result)


def main() -> None:
    app()
