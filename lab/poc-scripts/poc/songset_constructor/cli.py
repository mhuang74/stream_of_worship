"""Typer CLI for the songset constructor POC."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from poc.songset_constructor.config import RunConfig, default_output_dir
from poc.songset_constructor.graph.builder import build_graph

app = typer.Typer(no_args_is_help=True, rich_markup_mode="rich")
console = Console()


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
    interactive_review: Annotated[bool, typer.Option("--interactive-review/--no-interactive-review")] = False,
    resume_thread_id: Annotated[str | None, typer.Option("--resume-thread-id")] = None,
    no_llm: Annotated[bool, typer.Option("--no-llm/--llm")] = False,
    llm_judge: Annotated[bool, typer.Option("--llm-judge/--no-llm-judge")] = False,
    llm_model: Annotated[str | None, typer.Option("--llm-model")] = None,
    env_file: Annotated[Path | None, typer.Option("--env-file")] = None,
) -> None:
    """Construct Chinese worship songset proposal artifacts."""
    try:
        config = RunConfig(
            songs=songs,
            top_k=top_k,
            pool_limit=pool_limit,
            output_dir=output_dir or default_output_dir(),
            album_series=album_series or ["PW", "DEV"],
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
        )
        config.validate_environment()
    except Exception as exc:
        console.print(f"[red]Configuration error:[/red] {exc}")
        raise typer.Exit(2) from exc

    graph = build_graph(config)
    initial_state = {"config": config, "iterations": 0, "trace": []}
    graph_config = {"configurable": {"thread_id": config.thread_id}}

    try:
        result = graph.invoke(initial_state, graph_config)
        while "__interrupt__" in result:
            payload = result["__interrupt__"][0].value
            console.print(payload)
            action = typer.prompt("Review action (approve/reject)", default="approve")
            from langgraph.types import Command

            result = graph.invoke(Command(resume={"action": action}), graph_config)
    except Exception as exc:
        console.print(f"[red]Run failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    paths = result.get("artifact_paths", {})
    if paths:
        console.print("[green]Artifacts written:[/green]")
        for name, path in paths.items():
            console.print(f"  {name}: {path}")
    else:
        console.print("[yellow]No artifacts were written; no valid proposals were generated.[/yellow]")


def main() -> None:
    app()
