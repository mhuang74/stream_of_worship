"""CLI entry point for sow-app TUI.

Provides the `sow-app` command for launching the Textual interface,
database health checks, and managing songset exports/imports.
"""

import logging
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel

from stream_of_worship.app.app import SowApp
from stream_of_worship.app.config import AppConfig, ensure_app_config_exists, get_app_config_path
from stream_of_worship.app.db.read_client import ReadOnlyClient
from stream_of_worship.app.db.songset_client import SongsetClient
from stream_of_worship.app.logging_config import setup_logging
from stream_of_worship.app.services.catalog import CatalogService
from stream_of_worship.app.services.songset_io import ImportResult, SongsetIOService
from stream_of_worship.db.connection import ConnectionProvider

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
    """Check if database connection is alive.

    Args:
        config: App configuration

    Returns:
        True if database is ready
    """
    database_url = config.get_connection_url()
    if not database_url:
        console.print(
            Panel.fit(
                "[bold red]Database not configured![/bold red]\n\n"
                "Set database.url in your config file or SOW_DATABASE_URL env var.",
                title="Error",
                border_style="red",
            )
        )
        return False
    return True


def _check_catalog_health(config: AppConfig) -> None:
    """Check catalog health and warn user if incomplete.

    Args:
        config: App configuration
    """
    from stream_of_worship.db.connection import ConnectionProvider

    provider = ConnectionProvider(config.get_connection_url())
    read_client = ReadOnlyClient(provider)
    try:
        catalog = CatalogService(read_client)
        health = catalog.get_catalog_health()

        # Log detailed stats for transparency
        lrc_ready = health.get("lrc_ready", 0)
        logging.getLogger("sow_app").info(
            f"Catalog health check: status={health['status']}, "
            f"total_songs={health['total_songs']}, "
            f"total_recordings={health['total_recordings']}, "
            f"lrc_ready={lrc_ready}"
        )

        if health["status"] == "ready":
            console.print(
                f"[green]✓[/green] Catalog ready: {lrc_ready} song(s) with lyrics available"
            )
            return

        # Show warning for incomplete states
        console.print(
            Panel.fit(
                f"[bold yellow]Catalog Incomplete[/bold yellow]\n\n"
                f"Songs: {health['total_songs']}\n"
                f"Recordings: {health['total_recordings']}\n"
                f"LRC Ready: {lrc_ready}\n\n"
                f"[cyan]{health['guidance']}[/cyan]\n\n"
                "You can still launch the app, but the Browse screen will be empty.",
                title="Warning",
                border_style="yellow",
            )
        )
    finally:
        read_client.close()


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

    # Set up logging first
    log_dir = config.log_dir
    logger = setup_logging(log_dir)
    logger.info(f"App configuration loaded from: {config_path if config_path else 'default'}")

    # Check catalog health
    _check_catalog_health(config)
    database_url = config.get_connection_url()
    logger.info(f"Database URL: {database_url.split('@')[-1] if '@' in database_url else '(masked)'}")
    logger.info(f"Cache dir: {config.cache_dir}")
    console.print(f"[dim]Session log: {log_dir}/sow_app.log[/dim]")

    # Launch TUI
    try:
        app_instance = SowApp(config)
        logger.info("Launching TUI application")
        app_instance.run()
        logger.info("Application exited normally")
    except KeyboardInterrupt:
        logger.info("Application interrupted by user (Ctrl+C)")
        console.print("\n[yellow]Interrupted by user[/yellow]")
        raise typer.Exit(0)
    except Exception as e:
        logger.exception(f"Application error: {e}")
        console.print(f"[red]Error running app: {e}[/red]")
        raise typer.Exit(1)


# Database subcommand group
db_app = typer.Typer(help="Database connectivity and health")
app.add_typer(db_app, name="db")


@db_app.command("check")
def check_db(
    config_path: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Verify PostgreSQL database connectivity."""
    try:
        if config_path:
            config = AppConfig.load(config_path)
        else:
            config = AppConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-app run' first.[/red]")
        raise typer.Exit(1)

    database_url = config.get_connection_url()
    if not database_url:
        console.print("[red]Database URL not configured.[/red]")
        console.print("Set database.url in your config file or SOW_DATABASE_URL env var.")
        raise typer.Exit(1)

    console.print("Checking database connectivity...")
    from stream_of_worship.app.services.sync import check_database_connection

    if check_database_connection(database_url):
        console.print("[green]Database connection OK[/green]")
    else:
        console.print("[red]Database connection FAILED[/red]")
        raise typer.Exit(1)


# Songsets subcommand group
songsets_app = typer.Typer(help="Songset export/import operations")
app.add_typer(songsets_app, name="songsets")


@songsets_app.command("export")
def export_songset(
    songset_id: str = typer.Argument(..., help="Songset ID to export"),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output file path (default: <name>_<id>.json in export dir)",
    ),
    config_path: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Export a songset to JSON file."""
    try:
        if config_path:
            config = AppConfig.load(config_path)
        else:
            config = AppConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-app run' first.[/red]")
        raise typer.Exit(1)

    provider = ConnectionProvider(config.get_connection_url())
    songset_client = SongsetClient(provider)

    try:
        io_service = SongsetIOService(songset_client)

        # Get songset to determine default filename
        songset = songset_client.get_songset(songset_id)
        if not songset:
            console.print(f"[red]Songset not found: {songset_id}[/red]")
            raise typer.Exit(1)

        if output is None:
            safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in songset.name)
            output = config.songsets_export_dir / f"{safe_name}_{songset_id}.json"

        io_service.export_songset(songset_id, output)
        console.print(f"[green]Exported songset to:[/green] {output}")

    except Exception as e:
        console.print(f"[red]Export failed: {e}[/red]")
        raise typer.Exit(1)
    finally:
        songset_client.close()


@songsets_app.command("export-all")
def export_all_songsets(
    output_dir: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Output directory (default: songsets_export_dir from config)",
    ),
    config_path: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Export all songsets to JSON files."""
    try:
        if config_path:
            config = AppConfig.load(config_path)
        else:
            config = AppConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-app run' first.[/red]")
        raise typer.Exit(1)

    if output_dir is None:
        output_dir = config.songsets_export_dir

    provider = ConnectionProvider(config.get_connection_url())
    songset_client = SongsetClient(provider)

    try:
        io_service = SongsetIOService(songset_client)
        exported = io_service.export_all(output_dir)

        console.print(f"[green]Exported {len(exported)} songset(s) to:[/green] {output_dir}")
        for path in exported:
            console.print(f"  - {path.name}")

    except Exception as e:
        console.print(f"[red]Export failed: {e}[/red]")
        raise typer.Exit(1)
    finally:
        songset_client.close()


@songsets_app.command("import")
def import_songset(
    input_file: Path = typer.Argument(..., help="JSON file to import", exists=True),
    on_conflict: str = typer.Option(
        "rename",
        "--on-conflict",
        help="How to handle conflicts: rename, replace, or skip",
    ),
    config_path: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Import a songset from JSON file."""
    try:
        if config_path:
            config = AppConfig.load(config_path)
        else:
            config = AppConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-app run' first.[/red]")
        raise typer.Exit(1)

    # Initialize clients (shared single connection via ConnectionProvider)
    provider = ConnectionProvider(config.get_connection_url())
    read_client = ReadOnlyClient(provider)
    songset_client = SongsetClient(provider)

    try:
        # Provide get_recording for validation
        def get_recording(hash_prefix: str):
            return read_client.get_recording_by_hash(hash_prefix)

        io_service = SongsetIOService(songset_client, get_recording=get_recording)
        result: ImportResult = io_service.import_songset(input_file, on_conflict=on_conflict)

        if result.success:
            console.print(f"[green]Imported songset:[/green] {result.songset_id}")
            console.print(f"  Items: {result.imported_items}")
            if result.orphaned_items > 0:
                console.print(f"  [yellow]Orphaned: {result.orphaned_items}[/yellow]")
            if result.warnings:
                for warning in result.warnings[:5]:
                    console.print(f"  [yellow]Warning: {warning}[/yellow]")
                if len(result.warnings) > 5:
                    console.print(f"  ... and {len(result.warnings) - 5} more warnings")
        else:
            console.print(f"[red]Import failed:[/red] {result.error}")
            raise typer.Exit(1)

    except Exception as e:
        console.print(f"[red]Import failed: {e}[/red]")
        raise typer.Exit(1)
    finally:
        read_client.close()
        songset_client.close()


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
            cfg = AppConfig.load(config_path)
            console.print(f"[bold]Config file:[/bold] {config_path}")
            console.print(f"[bold]Database URL:[/bold] {cfg.database_url or '(not configured)'}")
            console.print(f"[bold]Cache dir:[/bold] {cfg.cache_dir}")
            console.print(f"[bold]Export dir:[/bold] {cfg.songsets_export_dir}")
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
