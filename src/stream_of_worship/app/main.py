"""CLI entry point for sow-app TUI.

Provides the `sow-app` command for launching the Textual interface,
syncing with Turso, and managing songset exports/imports.
"""

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
from stream_of_worship.app.services.sync import (
    AppSyncService,
    SyncNetworkError,
    TursoNotConfiguredError,
)

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
                "Please run [bold]sow-app sync[/bold] to download the catalog, or\n"
                "copy a database file from another machine.",
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
    read_client = ReadOnlyClient(
        config.db_path,
        turso_url=config.turso_database_url,
        turso_token=config.turso_readonly_token,
    )
    try:
        catalog = CatalogService(read_client)
        health = catalog.get_catalog_health()

        if health["status"] == "ready":
            console.print(
                f"[green]✓[/green] Catalog ready: {health['analyzed_recordings']} analyzed recording(s)"
            )
            return

        # Show warning for incomplete states
        console.print(
            Panel.fit(
                f"[bold yellow]Catalog Incomplete[/bold yellow]\n\n"
                f"Songs: {health['total_songs']}\n"
                f"Recordings: {health['total_recordings']}\n"
                f"Analyzed: {health['analyzed_recordings']}\n\n"
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

    # Check catalog health
    _check_catalog_health(config)

    # Set up logging
    log_dir = config.log_dir
    logger = setup_logging(log_dir)
    logger.info(f"App configuration loaded from: {config_path if config_path else 'default'}")
    logger.info(f"Database: {config.db_path}")
    logger.info(f"Songsets DB: {config.songsets_db_path}")
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


@app.command()
def sync(
    config_path: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Sync catalog database with Turso cloud."""
    try:
        if config_path:
            config = AppConfig.load(config_path)
        else:
            config = AppConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-app run' first.[/red]")
        raise typer.Exit(1)

    if not config.is_turso_configured:
        console.print("[red]Turso not configured.[/red]")
        console.print("Set turso.database_url and turso.readonly_token in your config.")
        raise typer.Exit(1)

    console.print("[bold]Syncing catalog...[/bold]")
    console.print(f"Local DB: {config.db_path}")
    console.print(f"Turso URL: {config.turso_database_url}")

    # Initialize clients
    read_client = ReadOnlyClient(
        config.db_path,
        turso_url=config.turso_database_url,
        turso_token=config.turso_readonly_token,
    )
    songset_client = SongsetClient(config.songsets_db_path)

    sync_service = AppSyncService(
        read_client=read_client,
        songset_client=songset_client,
        config_dir=config.db_path.parent.parent,
        turso_url=config.turso_database_url,
        turso_token=config.turso_readonly_token,
        backup_retention=config.songsets_backup_retention,
    )

    try:
        result = sync_service.execute_sync()
        console.print(f"[green]{result.message}[/green]")
        if result.backup_path:
            console.print(f"[dim]Pre-sync backup: {result.backup_path}[/dim]")

        # Show updated status
        status = sync_service.get_sync_status()
        if status.last_sync_at:
            console.print(f"[dim]Last sync: {status.last_sync_at}[/dim]")

    except TursoNotConfiguredError as e:
        console.print(f"[red]Configuration error: {e}[/red]")
        raise typer.Exit(1)
    except SyncNetworkError as e:
        console.print(f"[red]Network error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Sync failed: {e}[/red]")
        raise typer.Exit(1)
    finally:
        read_client.close()
        songset_client.close()


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

    songset_client = SongsetClient(config.songsets_db_path)
    songset_client.initialize_schema()

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

    songset_client = SongsetClient(config.songsets_db_path)
    songset_client.initialize_schema()

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

    # Initialize clients
    read_client = ReadOnlyClient(
        config.db_path,
        turso_url=config.turso_database_url,
        turso_token=config.turso_readonly_token,
    )
    songset_client = SongsetClient(config.songsets_db_path)
    songset_client.initialize_schema()

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
            console.print(f"[bold]Catalog DB:[/bold] {cfg.db_path}")
            console.print(f"[bold]Songsets DB:[/bold] {cfg.songsets_db_path}")
            console.print(f"[bold]Cache dir:[/bold] {cfg.cache_dir}")
            console.print(f"[bold]Export dir:[/bold] {cfg.songsets_export_dir}")
            console.print(f"[bold]Turso URL:[/bold] {cfg.turso_database_url or '(not configured)'}")
            console.print(f"[bold]Sync on startup:[/bold] {cfg.sync_on_startup}")
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
