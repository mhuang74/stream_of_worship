"""Infrastructure management commands for sow-admin.

Provides commands for provisioning and managing infrastructure like Turso.
"""

import os
import sqlite3
from pathlib import Path

import typer
from rich.console import Console

from stream_of_worship.admin.config import AdminConfig, get_config_path
from stream_of_worship.admin.db.client import DatabaseClient, SyncError

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

    Options:
        --url: Turso database URL (saved to config)
        --seed: Copy local data to remote (requires local DB to exist)
        --force: Overwrite remote even if it has data (requires confirmation)
    """
    config_path_to_use = config_path if config_path else get_config_path()
    config_exists = config_path_to_use.exists()

    if config_exists:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    else:
        config = AdminConfig()
        console.print(f"[yellow]Created default config at {config_path_to_use}[/yellow]")

    effective_url = turso_url or os.environ.get("SOW_TURSO_URL") or config.effective_turso_url

    if not effective_url:
        console.print("[red]Turso database URL not configured.[/red]")
        console.print()
        console.print("Provide the URL via one of these methods:")
        console.print("  --url flag, SOW_TURSO_URL env var, or config file")
        console.print()
        raise typer.Exit(1)

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

    client = DatabaseClient(
        db_path=config.db_path,
        turso_url=effective_url,
        auth_token=turso_token,
    )

    try:
        console.print("\n[yellow]Creating schema on Turso via HTTP...[/yellow]")
        client.initialize_schema()
        console.print("[green]Schema created successfully![/green]")

        if seed:
            console.print("\n[yellow]Checking remote data...[/yellow]")

            client._sync_replica(fatal=True)
            cursor = client.connection.cursor()
            cursor.execute("SELECT COUNT(*) FROM songs")
            remote_song_count = cursor.fetchone()[0]

            if remote_song_count > 0 and not force:
                console.print(f"[yellow]Remote already has {remote_song_count} songs.[/yellow]")
                console.print("Use --force to overwrite with local data.")
                raise typer.Exit(1)

            if remote_song_count > 0 and force:
                console.print(f"[red]WARNING: Remote has {remote_song_count} songs.[/red]")
                console.print("[red]This will DELETE all remote data and replace with local.[/red]")
                console.print("\nType 'sow-catalog' to confirm: ", end="")
                if input().strip() != "sow-catalog":
                    console.print("[red]Aborted.[/red]")
                    raise typer.Exit(1)

            console.print("\n[yellow]Seeding remote database...[/yellow]")

            local_conn = sqlite3.connect(config.db_path)
            local_conn.row_factory = sqlite3.Row
            local_cursor = local_conn.cursor()

            def seed_table(table_name):
                try:
                    local_cursor.execute(f"SELECT * FROM {table_name}")
                    rows = local_cursor.fetchall()
                    if not rows:
                        return 0
                    console.print(f"Copying {table_name}...")
                    columns = ", ".join(rows[0].keys())
                    placeholders = ", ".join(["?" for _ in rows[0].keys()])
                    sql = f"INSERT OR REPLACE INTO {table_name} ({columns}) VALUES ({placeholders})"
                    statements = [(sql, tuple(row)) for row in rows]
                    client._execute_remote_transaction(statements)
                    return len(rows)
                except sqlite3.OperationalError as e:
                    if "no such table" in str(e):
                        console.print(f"[yellow]Source has no '{table_name}' table, skipping...[/yellow]")
                        return 0
                    raise

            try:
                songs_copied = seed_table("songs")
                recordings_copied = seed_table("recordings")
                sync_meta_copied = seed_table("sync_metadata")

                client._sync_replica(fatal=False)
                console.print(
                    f"[green]Data seeded successfully: "
                    f"{songs_copied} songs, {recordings_copied} recordings, "
                    f"{sync_meta_copied} metadata rows[/green]"
                )

            except Exception as e:
                console.print(f"[red]Seed failed: {e}[/red]")
                raise typer.Exit(1)
            finally:
                local_conn.close()

        console.print("\n[bold green]Turso initialization completed![/bold green]")

    except SyncError as e:
        console.print(f"\n[red]Initialization failed: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"\n[red]Initialization failed: {e}[/red]")
        raise typer.Exit(1)
    finally:
        client.close()
