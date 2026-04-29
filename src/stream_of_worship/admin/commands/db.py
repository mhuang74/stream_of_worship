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
from stream_of_worship.admin.db.client import DatabaseClient, SyncError
from stream_of_worship.admin.services.sync import (
    SyncConfigError,
    SyncNetworkError,
    get_sync_service_from_config,
)

# Optional libsql import for Turso bootstrap
try:
    import libsql

    LIBSQL_AVAILABLE = True
except ImportError:
    LIBSQL_AVAILABLE = False
    libsql = None  # type: ignore

console = Console()
app = typer.Typer(help="Database operations")


def get_db_client(config: AdminConfig) -> DatabaseClient:
    """Get a database client from config.

    Args:
        config: Admin configuration

    Returns:
        DatabaseClient instance
    """
    return DatabaseClient(
        db_path=config.db_path,
        turso_url=config.turso_database_url,
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
        client = DatabaseClient(db_path)
        client.reset_database()
        console.print("[green]Database reset and re-initialized successfully![/green]")
    elif db_path.exists():
        # Database exists, run migrations to apply any schema updates
        console.print(f"[yellow]Database already exists at {db_path}[/yellow]")
        console.print("Running migrations...")
        client = DatabaseClient(db_path)
        client.initialize_schema()
        console.print("[green]Migrations applied successfully![/green]")
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
        client = get_db_client(config)
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

        if config.turso_database_url:
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
    """
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    # Check if Turso is configured
    if not config.turso_database_url:
        console.print("[red]Turso database URL not configured.[/red]")
        console.print("Set turso.database_url in your config file.")
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
        console.print(f"\n[red]Network error: {e}[/red]")
        if e.status_code:
            console.print(f"Status code: {e.status_code}")
        raise typer.Exit(1)
    except SyncError as e:
        console.print(f"\n[red]Sync error: {e}[/red]")
        if e.cause:
            console.print(f"Cause: {e.cause}")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"\n[red]Unexpected error: {e}[/red]")
        raise typer.Exit(1)


@app.command("turso-bootstrap")
def turso_bootstrap(
    config_path: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
    seed: bool = typer.Option(
        False,
        "--seed",
        help="Copy local data to remote if remote is empty",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force bootstrap even if checks fail",
    ),
) -> None:
    """Bootstrap Turso cloud database with schema and optional seed data.

    Creates the schema on the Turso master database and optionally copies
    all local data to initialize the remote.

    Auto-migration: If a vanilla SQLite database exists without libsql metadata
    (detected by missing '-info' sidecar file), it is automatically backed up
    to a timestamped directory (sow.db.bak-<timestamp>/) before proceeding.
    Use --seed to migrate the data, or --force to discard it.

    Prerequisites:
    - Turso database created: turso db create sow-catalog
    - Turso token configured: SOW_TURSO_TOKEN environment variable
    - libsql installed: uv add --extra turso libsql

    Options:
        --seed: Copy local data to remote (also migrates vanilla SQLite if detected)
        --force: Overwrite remote even if it has data (also bypasses migration prompt)
    """
    # Check libsql availability
    if not LIBSQL_AVAILABLE:
        console.print("[red]libsql is not installed.[/red]")
        console.print("Install with: uv add --extra turso libsql")
        raise typer.Exit(1)

    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    # Check prerequisites
    if not config.turso_database_url:
        console.print("[red]Turso database URL not configured.[/red]")
        console.print("Set turso.database_url in your config file.")
        raise typer.Exit(1)

    turso_token = os.environ.get("SOW_TURSO_TOKEN")
    if not turso_token:
        console.print("[red]Turso token not configured.[/red]")
        console.print("Set SOW_TURSO_TOKEN environment variable.")
        raise typer.Exit(1)

    console.print("[bold]Bootstrapping Turso database...[/bold]")
    console.print(f"Local DB: {config.db_path}")
    console.print(f"Turso URL: {config.turso_database_url}")

    # Detect vanilla SQLite vs libsql replica
    info_path = config.db_path.parent / f"{config.db_path.name}-info"
    source_db_path = None

    if config.db_path.exists() and not info_path.exists():
        # Vanilla SQLite detected - requires migration
        if not seed and not force:
            console.print("\n[yellow]Detected existing SQLite database without libsql metadata.[/yellow]")
            console.print("Options:")
            console.print("  --seed   Migrate data and create embedded replica")
            console.print("  --force  Discard data and create fresh embedded replica")
            raise typer.Exit(1)

        # Create timestamped backup directory
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        backup_dir = config.db_path.parent / f"{config.db_path.name}.bak-{timestamp}"
        backup_dir.mkdir(parents=False, exist_ok=False)

        # Move db file and all sidecar files into backup
        shutil.move(config.db_path, backup_dir / config.db_path.name)
        for sibling in config.db_path.parent.glob(f"{config.db_path.name}-*"):
            shutil.move(sibling, backup_dir / sibling.name)

        if seed:
            source_db_path = backup_dir / config.db_path.name
            console.print(f"[green]Migrated database to {backup_dir}[/green]")
        else:
            console.print(f"[yellow]Discarded database (backed up to {backup_dir})[/yellow]")

    elif config.db_path.exists() and info_path.exists():
        # Already a libsql replica - proceed normally
        pass
    else:
        # Fresh replica (neither exists) - proceed normally
        pass

    # Connect to Turso with embedded replica
    try:
        conn = libsql.connect(
            str(config.db_path),
            sync_url=config.turso_database_url,
            auth_token=turso_token,
        )
    except Exception as e:
        console.print(f"[red]Failed to connect to Turso: {e}[/red]")
        raise typer.Exit(1)

    try:
        # Step 1: Create schema
        console.print("\n[yellow]Creating schema on Turso...[/yellow]")

        from stream_of_worship.admin.db.schema import ALL_SCHEMA_STATEMENTS

        cursor = conn.cursor()
        for statement in ALL_SCHEMA_STATEMENTS:
            cursor.execute(statement)

        # Run migrations (idempotent)
        for table in ("songs", "recordings"):
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN deleted_at TIMESTAMP")
            except Exception:
                pass

        conn.commit()

        console.print("[green]Schema created successfully![/green]")

        # Step 2: Seed data if requested
        if seed:
            console.print("\n[yellow]Checking remote data...[/yellow]")

            # Sync first to reflect remote state (crucial for vanilla SQLite migration)
            conn.sync()

            # Check if remote already has data
            cursor.execute("SELECT COUNT(*) FROM songs")
            remote_song_count = cursor.fetchone()[0]

            if remote_song_count > 0 and not force:
                console.print(f"[yellow]Remote already has {remote_song_count} songs.[/yellow]")
                console.print("Use --force to overwrite with local data.")
            else:
                # Copy local data to remote
                console.print("\n[yellow]Seeding remote database...[/yellow]")

                import sqlite3

                # Use backup as seed source if migrating, otherwise use current db_path
                seed_source = source_db_path if source_db_path else config.db_path
                local_conn = sqlite3.connect(seed_source)
                local_conn.row_factory = sqlite3.Row
                local_cursor = local_conn.cursor()

                try:
                    # Begin explicit transaction
                    cursor.execute("BEGIN")

                    # Copy songs (skip if table doesn't exist)
                    try:
                        local_cursor.execute("SELECT * FROM songs")
                        songs = local_cursor.fetchall()
                        if songs:
                            console.print(f"Copying {len(songs)} songs...")
                            columns = ", ".join(songs[0].keys())
                            placeholders = ", ".join(["?" for _ in songs[0].keys()])
                            sql = f"INSERT OR REPLACE INTO songs ({columns}) VALUES ({placeholders})"
                            cursor.executemany(sql, [tuple(song) for song in songs])
                    except sqlite3.OperationalError as e:
                        if "no such table" in str(e):
                            console.print("[yellow]Source has no 'songs' table, skipping...[/yellow]")
                        else:
                            raise

                    # Copy recordings (skip if table doesn't exist)
                    try:
                        local_cursor.execute("SELECT * FROM recordings")
                        recordings = local_cursor.fetchall()
                        if recordings:
                            console.print(f"Copying {len(recordings)} recordings...")
                            columns = ", ".join(recordings[0].keys())
                            placeholders = ", ".join(["?" for _ in recordings[0].keys()])
                            sql = f"INSERT OR REPLACE INTO recordings ({columns}) VALUES ({placeholders})"
                            cursor.executemany(sql, [tuple(recording) for recording in recordings])
                    except sqlite3.OperationalError as e:
                        if "no such table" in str(e):
                            console.print("[yellow]Source has no 'recordings' table, skipping...[/yellow]")
                        else:
                            raise

                    # Copy sync_metadata (skip if table doesn't exist)
                    try:
                        local_cursor.execute("SELECT * FROM sync_metadata")
                        metadata = local_cursor.fetchall()
                        if metadata:
                            console.print(f"Copying {len(metadata)} sync metadata entries...")
                            columns = ", ".join(metadata[0].keys())
                            placeholders = ", ".join(["?" for _ in metadata[0].keys()])
                            sql = (
                                f"INSERT OR REPLACE INTO sync_metadata ({columns}) VALUES ({placeholders})"
                            )
                            cursor.executemany(sql, [tuple(meta) for meta in metadata])
                    except sqlite3.OperationalError as e:
                        if "no such table" in str(e):
                            console.print("[yellow]Source has no 'sync_metadata' table, skipping...[/yellow]")
                        else:
                            raise

                    # Commit transaction and sync to remote
                    conn.commit()
                    conn.sync()
                    console.print("[green]Data seeded successfully![/green]")

                except Exception as e:
                    conn.rollback()
                    console.print(f"[red]Seed failed, transaction rolled back: {e}[/red]")
                    raise typer.Exit(1)
                finally:
                    local_conn.close()

        # Step 3: Sync (only if not seeded - seed path handles its own sync)
        if not seed:
            console.print("\n[yellow]Syncing with Turso...[/yellow]")
            conn.sync()
            console.print("[green]Sync completed![/green]")

        console.print("\n[bold green]Turso bootstrap completed successfully![/bold green]")

    except Exception as e:
        console.print(f"\n[red]Bootstrap failed: {e}[/red]")
        raise typer.Exit(1)
    finally:
        conn.close()


@app.command("tokens")
def show_tokens() -> None:
    """Show commands to create Turso tokens.

    Displays the turso CLI commands needed to create read-write
    and read-only tokens for the database.
    """
    console.print("[bold]Turso Token Commands[/bold]\n")

    console.print("# Create a read-write token (for admin):")
    console.print("[cyan]turso db tokens create sow-catalog --read-write[/cyan]\n")

    console.print("# Create a read-only token (for user app distribution):")
    console.print("[cyan]turso db tokens create sow-catalog --read-only[/cyan]\n")

    console.print("# Set the token in your environment:")
    console.print("[cyan]export SOW_TURSO_TOKEN=<token>[/cyan]")
