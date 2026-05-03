"""Database commands for sow-admin.

Provides CLI commands for database initialization, status checking,
reset operations, and Turso sync.
"""

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from stream_of_worship.admin.config import get_config_path, AdminConfig
from stream_of_worship.admin.db.client import DatabaseClient, LIBSQL_AVAILABLE, SyncError
from stream_of_worship.admin.services.sync import (
    SyncConfigError,
    SyncNetworkError,
    get_sync_service_from_config,
)

console = Console()
app = typer.Typer(help="Database operations")


def get_db_client(config: AdminConfig) -> DatabaseClient:
    """Get a database client from config.

    Uses effective_turso_url which checks SOW_TURSO_URL env var.

    Args:
        config: Admin configuration

    Returns:
        DatabaseClient instance
    """
    return DatabaseClient(
        db_path=config.db_path,
        turso_url=config.effective_turso_url,
        turso_token=os.environ.get("SOW_TURSO_TOKEN"),
    )


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

    if force and db_path.exists():
        console.print(f"[red]Resetting database at {db_path}...[/red]")
        # Delete existing db + sidecar files for clean slate
        db_path.unlink()
        for f in db_path.parent.glob(f"{db_path.name}-*"):
            if f.is_dir():
                import shutil
                shutil.rmtree(f)
            else:
                f.unlink(missing_ok=True)

        turso_url = config.effective_turso_url
        if turso_url and LIBSQL_AVAILABLE:
            client = DatabaseClient(
                db_path,
                turso_url=turso_url,
                turso_token=os.environ.get("SOW_TURSO_TOKEN"),
            )
            try:
                client.sync()
                console.print("[green]Database reset and synced from Turso![/green]")
            except SyncError as e:
                client.close()
                if db_path.exists():
                    db_path.unlink()
                for f in db_path.parent.glob(f"{db_path.name}-*"):
                    if f.is_dir():
                        import shutil
                        shutil.rmtree(f)
                    else:
                        f.unlink(missing_ok=True)
                console.print(f"[yellow]Turso sync failed: {e}[/yellow]")
                console.print("[yellow]Falling back to local-only reset...[/yellow]")
                client = DatabaseClient(db_path)
                client.initialize_schema()
                console.print("[green]Database reset locally (run 'db sync' later).[/green]")
        else:
            client = DatabaseClient(db_path)
            client.reset_database()
            console.print("[green]Database reset and re-initialized successfully![/green]")
    elif db_path.exists():
        console.print(f"[yellow]Database already exists at {db_path}[/yellow]")
        console.print("Running migrations...")
        turso_url = config.effective_turso_url
        if turso_url and LIBSQL_AVAILABLE:
            # Use libsql to apply migrations and push to Turso
            client = DatabaseClient(
                db_path,
                turso_url=turso_url,
                turso_token=os.environ.get("SOW_TURSO_TOKEN"),
            )
            try:
                client.sync()
                console.print("[green]Migrations applied and synced with Turso![/green]")
            except SyncError as e:
                console.print(f"[yellow]Sync failed ({e}), applying migrations locally only.[/yellow]")
                client.close()
                client = DatabaseClient(db_path)
                client.initialize_schema()
                console.print("[green]Migrations applied locally.[/green]")
        else:
            client = DatabaseClient(db_path)
            client.initialize_schema()
            console.print("[green]Migrations applied successfully![/green]")
    else:
        console.print(f"Creating database at {db_path}...")
        turso_url = config.effective_turso_url
        if turso_url and LIBSQL_AVAILABLE:
            # Turso configured: create libsql replica and sync from remote
            # Do NOT call initialize_schema() — let sync pull schema from Turso
            client = DatabaseClient(
                db_path,
                turso_url=turso_url,
                turso_token=os.environ.get("SOW_TURSO_TOKEN"),
            )
            try:
                client.sync()
                console.print("[green]Database initialized and synced from Turso![/green]")
            except SyncError as e:
                # If sync fails, fall back to local-only init
                client.close()
                if db_path.exists():
                    db_path.unlink()
                # Delete any sidecar files
                for f in db_path.parent.glob(f"{db_path.name}-*"):
                    if f.is_dir():
                        import shutil
                        shutil.rmtree(f)
                    else:
                        f.unlink(missing_ok=True)
                console.print(f"[yellow]Turso sync failed: {e}[/yellow]")
                console.print("[yellow]Falling back to local-only initialization...[/yellow]")
                client = DatabaseClient(db_path)
                client.initialize_schema()
                console.print("[green]Database initialized locally (run 'db sync' later to pull data).[/green]")
        else:
            # No Turso: standard local-only init
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
    - Turso sync configuration
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
        console.print(
            "\n[yellow]Database does not exist. Run 'sow-admin db init' to create it.[/yellow]"
        )
        return

    # Get database stats
    try:
        # Use sqlite3-only client for local stats (avoids libsql metadata error
        # when local DB is a vanilla SQLite file created by db init)
        client = DatabaseClient(db_path)
        stats = client.get_stats()

        # Stats table
        stats_table = Table(title="Database Statistics")
        stats_table.add_column("Metric", style="cyan")
        stats_table.add_column("Value", style="green")

        stats_table.add_row("Songs", f"{stats.total_songs:,}")
        stats_table.add_row("Recordings", f"{stats.total_recordings:,}")
        stats_table.add_row(
            "Integrity Check", "[green]OK[/green]" if stats.integrity_ok else "[red]FAILED[/red]"
        )
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

        # Sync status table
        sync_service = get_sync_service_from_config(config)
        sync_status = sync_service.get_sync_status()

        sync_table = Table(title="Sync Configuration")
        sync_table.add_column("Property", style="cyan")
        sync_table.add_column("Value", style="green")

        sync_table.add_row(
            "Status",
            "[green]Enabled[/green]" if sync_status.enabled else "[dim]Disabled[/dim]",
        )
        sync_table.add_row(
            "libsql",
            "[green]Available[/green]"
            if sync_status.libsql_available
            else "[red]Not Installed[/red]",
        )

        if config.effective_turso_url:
            sync_table.add_row("Device ID", sync_status.local_device_id or "[dim]Not set[/dim]")
            sync_table.add_row("Sync Version", sync_status.sync_version)
            sync_table.add_row("Turso URL", sync_status.turso_url)

        console.print()
        console.print(sync_table)

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
        console.print(
            Panel.fit(
                "[red]WARNING: This will DELETE ALL DATA in the database![/red]\n\n"
                "Run with --confirm to proceed.",
                title="Database Reset",
                border_style="red",
            )
        )
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

    console.print(
        Panel.fit(
            f"[red]Resetting database at {db_path}...[/red]",
            title="Database Reset",
            border_style="red",
        )
    )

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


@app.command("sync")
def sync_db(
    config_path: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
    turso_url: str = typer.Option(
        None,
        "--turso-url",
        "-u",
        help="Turso database URL (overrides config and SOW_TURSO_URL env var)",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force sync even if configuration appears invalid",
    ),
) -> None:
    """Sync database with Turso cloud.

    Synchronizes the local SQLite database with Turso cloud using
    embedded replicas. Requires Turso URL to be configured.

    Turso URL priority: --turso-url flag > SOW_TURSO_URL env var > config file
    """
    # Check if we need to create a new config (onboarding scenario)
    config_path_to_use = config_path if config_path else get_config_path()
    config_exists = config_path_to_use.exists()

    if not config_exists and not turso_url and not os.environ.get("SOW_TURSO_URL"):
        console.print("[yellow]Config file not found and Turso URL not specified.[/yellow]")
        console.print()
        console.print("To sync from Turso, provide the URL via one of these methods:")
        console.print()
        console.print("  1. Use --turso-url flag:")
        console.print("     [dim]$ sow-admin db sync --turso-url libsql://your-db.turso.io[/dim]")
        console.print()
        console.print("  2. Set SOW_TURSO_URL environment variable:")
        console.print("     [dim]$ export SOW_TURSO_URL=libsql://your-db.turso.io[/dim]")
        console.print("     [dim]$ sow-admin db sync[/dim]")
        console.print()
        console.print("  3. Run 'db init' first to create config, then edit it:")
        console.print("     [dim]$ sow-admin db init[/dim]")
        console.print("     [dim]$ sow-admin config set turso.database_url libsql://your-db.turso.io[/dim]")
        console.print("     [dim]$ sow-admin db sync[/dim]")
        console.print()
        raise typer.Exit(1)

    # Load or create config
    if config_exists:
        config = AdminConfig.load(config_path)
    else:
        # Create config with provided Turso URL
        config = AdminConfig()

    # If --turso-url is provided, save it to config for future use
    if turso_url:
        config.turso_database_url = turso_url
        config.save(config_path)
        console.print(f"[green]Saved Turso URL to config at {config_path_to_use}[/green]")

    # Check if Turso is configured (use effective URL which checks env var)
    effective_url = turso_url or config.effective_turso_url
    if not effective_url:
        console.print("[red]Turso database URL not configured.[/red]")
        console.print()
        console.print("Provide the URL via one of these methods:")
        console.print("  --turso-url flag, SOW_TURSO_URL env var, or config file")
        console.print()
        raise typer.Exit(1)

    sync_service = get_sync_service_from_config(config)
    sync_status = sync_service.get_sync_status()

    # Check prerequisites
    if not sync_status.libsql_available:
        console.print("[red]libsql is not installed.[/red]")
        console.print("Install with: uv add --extra turso libsql")
        raise typer.Exit(1)

    # Validate configuration
    is_valid, errors = sync_service.validate_config()
    if not is_valid and not force:
        console.print("[red]Sync configuration errors:[/red]")
        for error in errors:
            console.print(f"  - {error}")
        console.print("\nUse --force to attempt sync anyway.")
        raise typer.Exit(1)

    # Show sync status before starting
    console.print(f"Database: {config.db_path}")
    console.print(f"Turso URL: {sync_status.turso_url}")
    if sync_status.last_sync_at:
        console.print(f"Last sync: {sync_status.last_sync_at}")
    else:
        console.print("Last sync: [dim]Never[/dim]")

    # Check for missing sync history (fresh installation or metadata corruption)
    if config.db_path.exists() and not sync_status.last_sync_at:
        console.print("\n[yellow]Note: Local database exists but has no sync history.[/yellow]")
        console.print("[yellow]Will perform initial sync from Turso...[/yellow]")

    console.print("\n[yellow]Syncing with Turso...[/yellow]")

    # Execute sync
    try:
        result = sync_service.execute_sync()
        console.print(f"\n[green]{result.message}[/green]")

        # Show updated status
        updated_status = sync_service.get_sync_status()
        if updated_status.last_sync_at:
            console.print(f"Last sync: {updated_status.last_sync_at}")

    except SyncConfigError as e:
        console.print(f"\n[red]Configuration error: {e}[/red]")
        raise typer.Exit(1)
    except SyncNetworkError as e:
        error_msg = str(e).lower()
        console.print(f"\n[red]Network error: {e}[/red]")
        if e.status_code:
            console.print(f"Status code: {e.status_code}")
        
        # Provide helpful messages for common errors
        if "write" in error_msg and ("forbidden" in error_msg or "blocked" in error_msg or "permission" in error_msg):
            console.print("\n[yellow]Tip: Your Turso token has read-only permissions.[/yellow]")
            console.print("[yellow]Sync operations require a token with write access.[/yellow]")
            console.print("[dim]Generate a full-access token with:[/dim]")
            console.print("  [dim]turso db tokens create sow-catalog --full-access[/dim]")
        elif "metadata file" in error_msg:
            console.print("\n[yellow]Tip: Database metadata is corrupted.[/yellow]")
            console.print("[yellow]Run 'sow-admin db reset' to reset local database, then sync again.[/yellow]")
        
        raise typer.Exit(1)
    except SyncError as e:
        console.print(f"\n[red]Sync error: {e}[/red]")
        if e.cause:
            console.print(f"Cause: {e.cause}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"\n[red]Unexpected error: {e}[/red]")
        raise typer.Exit(1)

