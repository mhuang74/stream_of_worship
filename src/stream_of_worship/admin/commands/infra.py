"""Infrastructure management commands for sow-admin.

Provides commands for provisioning and managing infrastructure like Turso.
"""

import os
import sqlite3
from pathlib import Path

import typer
from rich.console import Console

from stream_of_worship.admin.config import AdminConfig, get_config_path
from stream_of_worship.admin.db.schema import (
    ALL_SCHEMA_STATEMENTS,
    apply_column_migrations,
)

try:
    import libsql

    LIBSQL_AVAILABLE = True
except ImportError:
    LIBSQL_AVAILABLE = False
    libsql = None  # type: ignore

console = Console()
app = typer.Typer(help="Infrastructure management (one-time setup)")


@app.command("turso-init")
def turso_init(
    config_path: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
    turso_url: str = typer.Option(
        None,
        "--url",
        "-u",
        help="Turso database URL (saved to config if provided)",
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
        help="Force bootstrap even if remote has existing data",
    ),
) -> None:
    """Initialize Turso cloud database with schema and optional seed data.

    Creates the schema on the Turso master database and optionally copies
    all local data to initialize the remote.

    Prerequisites:
    - Turso database created: turso db create sow-catalog
    - Turso token configured: SOW_TURSO_TOKEN environment variable
    - libsql installed: uv add --extra turso libsql

    Options:
        --url: Turso database URL (saved to config)
        --seed: Copy local data to remote (requires local DB to exist)
        --force: Overwrite remote even if it has data (requires confirmation)
    """
    # Check libsql availability
    if not LIBSQL_AVAILABLE:
        console.print("[red]libsql is not installed.[/red]")
        console.print("Install with: uv add --extra turso libsql")
        raise typer.Exit(1)

    # Handle config loading/creation
    config_path_to_use = config_path if config_path else get_config_path()
    config_exists = config_path_to_use.exists()

    if config_exists:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    else:
        # Create default config for new users
        config = AdminConfig()
        console.print(f"[yellow]Created default config at {config_path_to_use}[/yellow]")

    # Handle Turso URL (CLI flag takes precedence, then env var, then config)
    effective_url = turso_url or os.environ.get("SOW_TURSO_URL") or config.effective_turso_url

    if not effective_url:
        console.print("[red]Turso database URL not configured.[/red]")
        console.print()
        console.print("Provide the URL via one of these methods:")
        console.print("  --url flag, SOW_TURSO_URL env var, or config file")
        console.print()
        raise typer.Exit(1)

    # Save Turso URL to config if provided via flag
    if turso_url:
        config.turso_database_url = turso_url
        config.save(config_path)
        console.print(f"[green]Saved Turso URL to config at {config_path_to_use}[/green]")

    turso_token = os.environ.get("SOW_TURSO_TOKEN")
    if not turso_token:
        console.print("[red]Turso token not configured.[/red]")
        console.print("Set SOW_TURSO_TOKEN environment variable.")
        raise typer.Exit(1)

    console.print("[bold]Initializing Turso database...[/bold]")
    console.print(f"Local DB: {config.db_path}")
    console.print(f"Turso URL: {effective_url}")

    if seed and not config.db_path.exists():
        console.print("[red]Local database not found for seeding.[/red]")
        console.print("Run 'sow-admin db init' first.")
        raise typer.Exit(1)

    # Connect to Turso with embedded replica
    try:
        conn = libsql.connect(
            str(config.db_path),
            sync_url=effective_url,
            auth_token=turso_token,
        )
    except Exception as e:
        console.print(f"[red]Failed to connect to Turso: {e}[/red]")
        raise typer.Exit(1)

    try:
        # Step 1: Create schema
        console.print("\n[yellow]Creating schema on Turso...[/yellow]")

        cursor = conn.cursor()
        for statement in ALL_SCHEMA_STATEMENTS:
            cursor.execute(statement)

        # Run column migrations (idempotent)
        apply_column_migrations(cursor)

        conn.commit()

        console.print("[green]Schema created successfully![/green]")

        # Step 2: Seed data if requested
        if seed:
            console.print("\n[yellow]Checking remote data...[/yellow]")

            # Sync first to reflect remote state
            conn.sync()

            # Check if remote already has data
            cursor.execute("SELECT COUNT(*) FROM songs")
            remote_song_count = cursor.fetchone()[0]

            if remote_song_count > 0 and not force:
                console.print(f"[yellow]Remote already has {remote_song_count} songs.[/yellow]")
                console.print("Use --force to overwrite with local data.")
                raise typer.Exit(1)

            if remote_song_count > 0 and force:
                # Interactive confirmation
                console.print(f"[red]WARNING: Remote has {remote_song_count} songs.[/red]")
                console.print("[red]This will DELETE all remote data and replace with local.[/red]")
                console.print("\nType 'sow-catalog' to confirm: ", end="")
                if input().strip() != "sow-catalog":
                    console.print("[red]Aborted.[/red]")
                    raise typer.Exit(1)

            # Copy local data to remote
            console.print("\n[yellow]Seeding remote database...[/yellow]")

            local_conn = sqlite3.connect(config.db_path)
            local_conn.row_factory = sqlite3.Row
            local_cursor = local_conn.cursor()

            def seed_table(table_name):
                try:
                    local_cursor.execute(f"SELECT * FROM {table_name}")
                    first_batch = True
                    while True:
                        rows = local_cursor.fetchmany(100)
                        if not rows:
                            break
                        if first_batch:
                            console.print(f"Copying {table_name}...")
                            columns = ", ".join(rows[0].keys())
                            placeholders = ", ".join(["?" for _ in rows[0].keys()])
                            sql = f"INSERT OR REPLACE INTO {table_name} ({columns}) VALUES ({placeholders})"
                            first_batch = False
                        cursor.executemany(sql, [tuple(row) for row in rows])
                except sqlite3.OperationalError as e:
                    if "no such table" in str(e):
                        console.print(f"[yellow]Source has no '{table_name}' table, skipping...[/yellow]")
                    else:
                        raise

            try:
                seed_table("songs")
                seed_table("recordings")
                seed_table("sync_metadata")

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

        console.print("\n[bold green]Turso initialization completed successfully![/bold green]")

    except Exception as e:
        console.print(f"\n[red]Initialization failed: {e}[/red]")
        raise typer.Exit(1)
    finally:
        conn.close()
