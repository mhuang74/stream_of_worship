"""CLI for the agentic songset constructor POC."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from .graph import run_constructor
from .models import ConstructorConfig


app = typer.Typer(help="Generate read-only songset proposal artifacts with a LangGraph agent.")
console = Console()


@app.command()
def main(
    songs: int = typer.Option(
        5,
        "--songs",
        min=4,
        max=5,
        help="Song count; webapp-compatible values are 4 or 5.",
    ),
    top_k: int = typer.Option(3, "--top-k", min=1),
    pool_limit: int = typer.Option(200, "--pool-limit", min=10),
    output_dir: Path = typer.Option(
        Path("lab/poc-scripts/output/songset_constructor"),
        "--output-dir",
    ),
    album_series: str | None = typer.Option(None, "--album-series"),
    include_dev: bool = typer.Option(True, "--include-dev/--no-include-dev"),
    include_cpw: bool = typer.Option(False, "--include-cpw/--no-include-cpw"),
    intimate: bool = typer.Option(False, "--intimate"),
    hymnal_mode: bool = typer.Option(False, "--hymnal-mode"),
    season: str | None = typer.Option(None, "--season"),
    interactive_review: bool = typer.Option(False, "--interactive-review"),
    resume_thread_id: str | None = typer.Option(None, "--resume-thread-id"),
    llm_model: str | None = typer.Option(None, "--llm-model"),
) -> None:
    config = ConstructorConfig(
        songs=songs,
        top_k=top_k,
        pool_limit=pool_limit,
        output_dir=str(output_dir),
        album_series=album_series,
        include_dev=include_dev,
        include_cpw=include_cpw,
        intimate=intimate,
        hymnal_mode=hymnal_mode,
        season=season,
        interactive_review=interactive_review,
        resume_thread_id=resume_thread_id,
        llm_model=llm_model,
    )
    state = run_constructor(config)
    console.print(f"Wrote {len(state.final_proposals)} proposal(s) to {output_dir}")


if __name__ == "__main__":
    app()
