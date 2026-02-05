"""Main entry point for sow-admin CLI.

Provides a Typer-based CLI for managing Stream of Worship catalog,
audio recordings, and metadata.
"""

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

from stream_of_worship.admin import __version__
from stream_of_worship.admin.commands import catalog as catalog_commands
from stream_of_worship.admin.commands import db as db_commands

console = Console()

# Create the main Typer app
app = typer.Typer(
    name="sow-admin",
    help="Administrative tools for Stream of Worship",
    rich_markup_mode="rich",
)

# Add subcommand groups
app.add_typer(db_commands.app, name="db", help="Database operations")
app.add_typer(catalog_commands.app, name="catalog", help="Catalog operations")


def version_callback(value: bool) -> None:
    """Callback for --version flag."""
    if value:
        console.print(f"sow-admin version {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        None,
        "--version",
        "-v",
        help="Show version and exit",
        callback=version_callback,
        is_eager=True,
    ),
) -> None:
    """sow-admin: Administrative tools for Stream of Worship.

    Manage song catalogs, audio recordings, and metadata for the
    Stream of Worship system.

    ## Commands

    * [bold cyan]db[/bold cyan] - Database operations (init, status, reset)
    * [bold cyan]catalog[/bold cyan] - Catalog operations (scrape, list, search, show)

    ## Getting Started

    1. Initialize the database:
       [dim]$ sow-admin db init[/dim]

    2. Check database status:
       [dim]$ sow-admin db status[/dim]

    3. Scrape song catalog:
       [dim]$ sow-admin catalog scrape --limit 10[/dim]
    """
    pass


@app.command()
def config(
    action: str = typer.Argument(
        ...,
        help="Action to perform (show, set, path)",
    ),
    key: str = typer.Argument(
        None,
        help="Configuration key (for set action)",
    ),
    value: str = typer.Argument(
        None,
        help="Configuration value (for set action)",
    ),
) -> None:
    """Manage configuration.

    Show, set, or display the path to the configuration file.

    Examples:
        sow-admin config show          # Show all configuration
        sow-admin config set r2.bucket my-bucket
        sow-admin config path          # Show config file path
    """
    from stream_of_worship.admin.config import AdminConfig, get_config_path, ensure_config_exists

    if action == "show":
        try:
            cfg = ensure_config_exists()
        except Exception as e:
            console.print(f"[red]Error loading config: {e}[/red]")
            raise typer.Exit(1)

        table = Panel.fit(
            f"[cyan]Analysis URL:[/cyan] {cfg.analysis_url}\n"
            f"[cyan]R2 Bucket:[/cyan] {cfg.r2_bucket}\n"
            f"[cyan]R2 Endpoint:[/cyan] {cfg.r2_endpoint_url or '[not set]'}\n"
            f"[cyan]R2 Region:[/cyan] {cfg.r2_region}\n"
            f"[cyan]Turso URL:[/cyan] {cfg.turso_database_url or '[not set]'}\n"
            f"[cyan]Database Path:[/cyan] {cfg.db_path}",
            title="Configuration",
            border_style="green",
        )
        console.print(table)

    elif action == "set":
        if not key or value is None:
            console.print("[red]Usage: sow-admin config set <key> <value>[/red]")
            raise typer.Exit(1)

        try:
            cfg = ensure_config_exists()
            cfg.set(key, value)
            cfg.save()
            console.print(f"[green]Set {key} = {value}[/green]")
        except ValueError as e:
            console.print(f"[red]Error: {e}[/red]")
            raise typer.Exit(1)

    elif action == "path":
        console.print(get_config_path())

    else:
        console.print(f"[red]Unknown action: {action}[/red]")
        console.print("Valid actions: show, set, path")
        raise typer.Exit(1)


# Entry point for the CLI
def cli_entry() -> None:
    """Entry point for the CLI application."""
    app()


if __name__ == "__main__":
    cli_entry()
