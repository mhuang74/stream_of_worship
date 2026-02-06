"""CLI entry point for sow-app TUI.

Provides the `sow-app` command for launching the Textual interface.
"""

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from stream_of_worship.app.app import SowApp
from stream_of_worship.app.config import AppConfig, ensure_app_config_exists, get_app_config_path

app = typer.Typer(
    name="sow-app",
    help="Stream of Worship - Songset Manager TUI",
    no_args_is_help=False,
)
console = Console()


@app.callback()
def main(
    config: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
        exists=False,
    ),
    version: bool = typer.Option(
        False,
        "--version",
        "-v",
        help="Show version and exit",
    ),
) -> None:
    """Stream of Worship User App - Manage worship songsets."""
    if version:
        console.print("sow-app version 0.2.0")
        raise typer.Exit()


def _check_first_run() -> bool:
    """Check if this is the first run (no config exists).

    Returns:
        True if first run
    """
    config_path = get_app_config_path()
    return not config_path.exists()


def _show_welcome() -> None:
    """Show welcome message for first run."""
    console.print(
        Panel.fit(
            "[bold green]Welcome to Stream of Worship![/bold green]\n\n"
            "This tool helps you create worship songsets with smooth transitions.\n"
            "Configuration will be created at: "
            f"[cyan]{get_app_config_path()}[/cyan]",
            title="sow-app",
            border_style="green",
        )
    )


def _check_database(config: AppConfig) -> bool:
    """Check if database exists and has data.

    Args:
        config: App configuration

    Returns:
        True if database is ready
    """
    if not config.db_path.exists():
        console.print(
            Panel.fit(
                "[bold red]Database not found![/bold red]\n\n"
                f"Expected at: [cyan]{config.db_path}[/cyan]\n\n"
                "Please run [bold]sow-admin catalog scrape[/bold] first to populate the database.",
                title="Error",
                border_style="red",
            )
        )
        return False
    return True


@app.command()
def run(
    config_path: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Launch the TUI application."""
    # First run check
    is_first_run = _check_first_run()
    if is_first_run:
        _show_welcome()

    # Load or create config
    try:
        if config_path:
            config = AppConfig.load(config_path)
        else:
            config = ensure_app_config_exists()
    except FileNotFoundError as e:
        console.print(f"[red]Config file not found: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error loading config: {e}[/red]")
        raise typer.Exit(1)

    # Check database
    if not _check_database(config):
        raise typer.Exit(1)

    # Launch TUI
    try:
        app_instance = SowApp(config)
        app_instance.run()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        raise typer.Exit(0)
    except Exception as e:
        console.print(f"[red]Error running app: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def config(
    show: bool = typer.Option(
        False,
        "--show",
        help="Show current configuration",
    ),
    edit: bool = typer.Option(
        False,
        "--edit",
        help="Open config in editor",
    ),
) -> None:
    """Manage application configuration."""
    config_path = get_app_config_path()

    if show or (not edit):
        if config_path.exists():
            config = AppConfig.load(config_path)
            console.print(f"[bold]Config file:[/bold] {config_path}")
            console.print(f"[bold]Database:[/bold] {config.db_path}")
            console.print(f"[bold]Cache dir:[/bold] {config.cache_dir}")
            console.print(f"[bold]Output dir:[/bold] {config.output_dir}")
            console.print(f"[bold]R2 Bucket:[/bold] {config.r2_bucket}")
        else:
            console.print(f"[yellow]No config file at {config_path}[/yellow]")
            console.print("Run [bold]sow-app run[/bold] to create default config.")

    if edit:
        import subprocess
        editor = __import__("os").environ.get("EDITOR", "nano")
        subprocess.call([editor, str(config_path)])


def cli_entry() -> None:
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    cli_entry()
