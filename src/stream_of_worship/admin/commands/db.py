"""Database commands for sow-admin.

Provides CLI commands for database initialization, status checking,
and PostgreSQL connectivity.
"""

import os
import re
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from stream_of_worship.admin.config import get_config_path, AdminConfig
from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.services.sync import check_database_connection
from stream_of_worship.db.connection import ConnectionProvider

console = Console()
app = typer.Typer(help="Database operations")


def get_db_client(config: AdminConfig) -> DatabaseClient:
    """Get a database client from config.

    Args:
        config: Admin configuration

    Returns:
        DatabaseClient instance backed by a ConnectionProvider
    """
    provider = ConnectionProvider(config.get_connection_url())
    return DatabaseClient(provider)


@app.command("init")
def init_db(
    config_path: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Initialize the PostgreSQL database schema.

    Connects to the configured Neon/Postgres database and runs schema
    creation statements (tables, indexes, triggers).  Idempotent —
    safe to run on an existing database.
    """
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        # Create default config if it doesn't exist
        config = AdminConfig()
        config.save()
        console.print(f"[yellow]Created default config at {get_config_path()}[/yellow]")

    database_url = config.get_connection_url()
    if not database_url:
        console.print(
            Panel.fit(
                "[bold red]Database URL not configured![/bold red]\n\n"
                "Set database.url in your config file or SOW_DATABASE_URL env var.",
                title="Error",
                border_style="red",
            )
        )
        raise typer.Exit(1)

    try:
        console.print("Connecting to PostgreSQL...")
        client = get_db_client(config)
        client.initialize_schema()
        console.print("[green]Postgres schema initialized successfully![/green]")
    except Exception as e:
        console.print(f"\n[red]Error initializing database: {e}[/red]")
        raise typer.Exit(1)

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

    Displays connection health, table row counts, and Postgres
    recovery status.
    """
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    database_url = config.get_connection_url()

    # Connection info table
    info_table = Table(title="Database Connection")
    info_table.add_column("Property", style="cyan")
    info_table.add_column("Value", style="green")

    if database_url and "@" in database_url:
        # Mask password for display
        masked = re.sub(r"(://[^:]+:)([^@]+)(@)", r"\1****\3", database_url)
        info_table.add_row("Database URL", masked)
    else:
        info_table.add_row("Database URL", "[red]Not configured[/red]")

    if not database_url:
        console.print(info_table)
        console.print(
            "\n[yellow]Database URL not configured. Set database.url in config or SOW_DATABASE_URL env var.[/yellow]"
        )
        return

    # Health check
    healthy = check_database_connection(database_url)
    info_table.add_row(
        "Connection",
        "[green]OK[/green]" if healthy else "[red]FAILED[/red]",
    )

    console.print(info_table)

    if not healthy:
        console.print(
            "\n[yellow]Could not connect to database. Check URL, password, and network.[/yellow]"
        )
        return

    # Get database stats
    try:
        client = get_db_client(config)
        stats = client.get_stats()

        # Stats table
        stats_table = Table(title="Database Statistics")
        stats_table.add_column("Metric", style="cyan")
        stats_table.add_column("Value", style="green")

        stats_table.add_row("Songs", f"{stats.total_songs:,}")
        stats_table.add_row("Recordings", f"{stats.total_recordings:,}")
        stats_table.add_row(
            "Health Check",
            "[green]OK[/green]" if stats.is_healthy else "[red]FAILED[/red]",
        )

        console.print()
        console.print(stats_table)

    except Exception as e:
        console.print(f"\n[red]Error reading database: {e}[/red]")
        raise typer.Exit(1)


@app.command("url")
def show_url(
    config_path: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Show the database URL (password masked).

    Displays the configured database URL with the password redacted
    and indicates whether the SOW_DATABASE_PASSWORD env var is set.
    """
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        # Use default config
        config = AdminConfig()

    url = config.database_url or ""

    if url and "@" in url:
        # Mask password if present in URL
        masked = re.sub(r"(://[^:]+:)([^@]+)(@)", r"\1****\3", url)
    else:
        masked = "[not configured]"

    console.print(f"Database URL (masked): {masked}")

    if os.environ.get("SOW_DATABASE_PASSWORD"):
        console.print("Password: loaded from SOW_DATABASE_PASSWORD env var")
    else:
        console.print("Password: [yellow]NOT SET[/yellow] (set SOW_DATABASE_PASSWORD env var)")
