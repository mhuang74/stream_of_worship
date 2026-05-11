"""Database commands for sow-admin.

Provides CLI commands for database initialization, status checking,
and connection URL display for PostgreSQL.
"""

import os
import re
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from stream_of_worship.admin.config import AdminConfig, get_config_path
from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.services.sync import check_database_connection
from stream_of_worship.db.connection import ConnectionProvider

console = Console()
app = typer.Typer(help="Database operations")


def _display_stats(client: DatabaseClient) -> None:
    """Display database statistics using an already-open client."""
    stats = client.get_stats()

    stats_table = Table(title="Database Statistics")
    stats_table.add_column("Metric", style="cyan")
    stats_table.add_column("Value", style="green")

    stats_table.add_row("Songs", f"{stats.total_songs:,}")
    stats_table.add_row("Recordings", f"{stats.total_recordings:,}")
    stats_table.add_row(
        "Health Check",
        "[green]OK[/green]" if stats.is_healthy else "[red]FAILED[/red]",
    )
    stats_table.add_row("Schema Version", stats.sync_version)

    console.print()
    console.print(stats_table)


def _get_db_client(config: AdminConfig) -> DatabaseClient:
    """Get a database client from config.

    Args:
        config: Admin configuration

    Returns:
        DatabaseClient instance
    """
    provider = ConnectionProvider(config.get_connection_url())
    return DatabaseClient(provider)


@app.command("init")
def init_db(
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force re-initialization (runs schema statements again)",
    ),
    config_path: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Initialize the PostgreSQL database schema.

    Creates tables, indexes, and triggers if they don't exist.
    Use --force to re-run schema creation on an existing database.
    """
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        # Create default config if it doesn't exist
        config = AdminConfig()
        config.save()
        console.print(f"[yellow]Created default config at {get_config_path()}[/yellow]")

    try:
        config.get_connection_url()
    except ValueError as e:
        console.print(f"[red]Database URL not configured: {e}[/red]")
        console.print("Set database.url in your config or SOW_DATABASE_URL env var.")
        raise typer.Exit(1)

    if force:
        console.print("[yellow]Force flag set — re-initializing schema...[/yellow]")

    console.print("Connecting to PostgreSQL...")

    try:
        client = _get_db_client(config)
        with client:
            client.initialize_schema()
            console.print("[green]Postgres schema initialized successfully![/green]")
            # Display connection info without re-connecting
            _show_connection_info(config)
            # Reuse same connection so Neon's pooler doesn't route us to a stale backend
            _display_stats(client)
    except Exception as e:
        console.print(f"[red]Failed to initialize schema: {e}[/red]")
        raise typer.Exit(1)


def _show_connection_info(config: AdminConfig) -> bool:
    """Print the connection info table; return True if the database is reachable."""
    masked = _mask_url(config.database_url)

    info_table = Table(title="Database Connection")
    info_table.add_column("Property", style="cyan")
    info_table.add_column("Value", style="green")

    info_table.add_row("Database URL", masked)
    has_password = bool(os.environ.get("SOW_DATABASE_PASSWORD"))
    info_table.add_row(
        "Password",
        "[green]Set via env var[/green]" if has_password else "[red]NOT SET[/red]",
    )

    try:
        conn_url = config.get_connection_url()
        is_healthy = check_database_connection(conn_url)
        info_table.add_row(
            "Health",
            "[green]Connected[/green]" if is_healthy else "[red]Unreachable[/red]",
        )
    except ValueError as e:
        info_table.add_row("Health", f"[red]{e}[/red]")
        console.print(info_table)
        return False

    console.print(info_table)
    return is_healthy


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
    - Connection URL (masked)
    - Connection health
    - Table row counts
    """
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    is_healthy = _show_connection_info(config)

    if not is_healthy:
        console.print(
            "\n[yellow]Database is unreachable. Check your URL and network.[/yellow]"
        )
        return

    try:
        client = _get_db_client(config)
        with client:
            _display_stats(client)
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
    """Show the database connection URL (password masked).

    Outputs the configured database URL with the password redacted
    for security. Also indicates whether SOW_DATABASE_PASSWORD is set.
    """
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        # Use default config
        config = AdminConfig()

    masked = _mask_url(config.database_url)
    console.print(f"Database URL (masked): {masked}")

    if os.environ.get("SOW_DATABASE_PASSWORD"):
        console.print("Password: [green]loaded from SOW_DATABASE_PASSWORD env var[/green]")
    else:
        console.print("Password: [red]NOT SET (SOW_DATABASE_PASSWORD env var missing)[/red]")


def _mask_url(url: str) -> str:
    """Mask password in a database URL.

    Args:
        url: Database URL that may contain a password.

    Returns:
        URL with password replaced by '****'.
    """
    if not url:
        return "(not configured)"
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:****@", url)
