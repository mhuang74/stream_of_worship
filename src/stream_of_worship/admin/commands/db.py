"""Database commands for sow-admin.

Provides CLI commands for database initialization, status checking,
and reset operations.
"""

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from stream_of_worship.admin.config import get_config_path, AdminConfig
from stream_of_worship.admin.db.client import DatabaseClient

console = Console()
app = typer.Typer(help="Database operations")


def get_db_client(config: AdminConfig) -> DatabaseClient:
    """Get a database client from config.

    Args:
        config: Admin configuration

    Returns:
        DatabaseClient instance
    """
    return DatabaseClient(config.db_path)


@app.command("init")
def init_db(
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force re-initialization (destructive)",
    ),
    config_path: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Initialize the local database.

    Creates the database file and initializes the schema with tables,
    indexes, and triggers. Use --force to reset an existing database.
    """
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        # Create default config if it doesn't exist
        config = AdminConfig()
        config.save()
        console.print(f"[yellow]Created default config at {get_config_path()}[/yellow]")

    db_path = config.db_path

    if db_path.exists() and not force:
        console.print(f"[yellow]Database already exists at {db_path}[/yellow]")
        console.print("Use --force to re-initialize (this will delete all data)")
        raise typer.Exit(1)

    if force and db_path.exists():
        console.print(f"[red]Resetting database at {db_path}...[/red]")
        client = DatabaseClient(db_path)
        client.reset_database()
        console.print("[green]Database reset and re-initialized successfully![/green]")
    else:
        console.print(f"Creating database at {db_path}...")
        client = DatabaseClient(db_path)
        client.initialize_schema()
        console.print("[green]Database initialized successfully![/green]")

    # Show status after init
    show_status(config_path)


@app.command("status")
def show_status(
    config_path: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Show database status and statistics.

    Displays information about the database including:
    - Database file path and existence
    - Table row counts
    - Integrity check results
    - Last sync timestamp
    """
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_path = config.db_path

    # Database file info
    exists = db_path.exists()

    info_table = Table(title="Database Information")
    info_table.add_column("Property", style="cyan")
    info_table.add_column("Value", style="green")

    info_table.add_row("Database Path", str(db_path))
    info_table.add_row("Exists", "Yes" if exists else "No")

    if exists:
        size = db_path.stat().st_size
        info_table.add_row("File Size", f"{size:,} bytes ({size / 1024 / 1024:.2f} MB)")

    console.print(info_table)

    if not exists:
        console.print("\n[yellow]Database does not exist. Run 'sow-admin db init' to create it.[/yellow]")
        return

    # Get database stats
    try:
        client = DatabaseClient(db_path)
        stats = client.get_stats()

        # Stats table
        stats_table = Table(title="Database Statistics")
        stats_table.add_column("Metric", style="cyan")
        stats_table.add_column("Value", style="green")

        stats_table.add_row("Songs", f"{stats.total_songs:,}")
        stats_table.add_row("Recordings", f"{stats.total_recordings:,}")
        stats_table.add_row("Integrity Check", "[green]OK[/green]" if stats.integrity_ok else "[red]FAILED[/red]")
        stats_table.add_row(
            "Foreign Keys",
            "[green]Enabled[/green]" if stats.foreign_keys_enabled else "[red]Disabled[/red]",
        )

        if stats.last_sync_at:
            stats_table.add_row("Last Sync", stats.last_sync_at)
        else:
            stats_table.add_row("Last Sync", "[dim]Never[/dim]")

        console.print()
        console.print(stats_table)

    except Exception as e:
        console.print(f"\n[red]Error reading database: {e}[/red]")
        raise typer.Exit(1)


@app.command("reset")
def reset_db(
    confirm: bool = typer.Option(
        False,
        "--confirm",
        help="Confirm destructive reset (required)",
    ),
    config_path: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Reset the database (DESTRUCTIVE).

    Deletes all data and re-initializes the database schema.
    This operation cannot be undone.
    """
    if not confirm:
        console.print(Panel.fit(
            "[red]WARNING: This will DELETE ALL DATA in the database![/red]\n\n"
            "Run with --confirm to proceed.",
            title="Database Reset",
            border_style="red",
        ))
        raise typer.Exit(1)

    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found.[/red]")
        raise typer.Exit(1)

    db_path = config.db_path

    if not db_path.exists():
        console.print("[yellow]Database does not exist. Creating new database...[/yellow]")
        init_db(force=False, config_path=config_path)
        return

    console.print(Panel.fit(
        f"[red]Resetting database at {db_path}...[/red]",
        title="Database Reset",
        border_style="red",
    ))

    client = DatabaseClient(db_path)
    client.reset_database()

    console.print("[green]Database reset successfully![/green]")


@app.command("path")
def show_path(
    config_path: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Show the database file path.

    Outputs the path to the database file. Useful for scripting
    or when you need to manually inspect the database.
    """
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        # Use default path
        config = AdminConfig()

    console.print(config.db_path)
