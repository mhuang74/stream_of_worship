"""Songset commands for sow-admin.

Provides CLI commands for listing songsets with their items, songs, and recordings.
"""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.db.app.songset_client import SongsetClient
from stream_of_worship.db.connection import ConnectionProvider
from stream_of_worship.db.user_client import UserClient

console = Console()
app = typer.Typer(help="Songset operations")


def _get_connection_provider(config: AdminConfig) -> ConnectionProvider:
    """Get a ConnectionProvider from config.

    Args:
        config: Admin configuration.

    Returns:
        ConnectionProvider instance.
    """
    return ConnectionProvider(config.get_connection_url())


def _resolve_owner_emails(
    connection_provider: ConnectionProvider, songsets: list
) -> dict[int, str]:
    """Resolve user emails for a list of songsets in one round-trip.

    Args:
        connection_provider: DB connection provider.
        songsets: List of Songset objects.

    Returns:
        Dict mapping user_id to email string.
    """
    user_ids = list({s.user_id for s in songsets})
    if not user_ids:
        return {}

    result: dict[int, str] = {}
    with connection_provider.get_connection().cursor() as cursor:
        cursor.execute(
            'SELECT "id", "email" FROM "user" WHERE "id" = ANY(%s)',
            (user_ids,),
        )
        for row in cursor.fetchall():
            result[row[0]] = row[1]
    return result


@app.command("list")
def list_songsets(
    user: Optional[str] = typer.Option(
        None,
        "--user",
        "-u",
        help="Filter songsets to one user, resolved by email",
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        "-l",
        help="Cap the number of songsets returned",
    ),
    format: str = typer.Option(
        "table",
        "--format",
        "-f",
        help="Output format (table|ids)",
    ),
    config_path: Optional[Path] = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """List songsets with their items.

    Display songsets and their constituent songs with key/BPM/duration info.
    With no flags, lists every songset across all users. Use --user to filter
    by owner email. Orphaned items (no matching songs row) render dashes.
    """
    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    connection_provider = _get_connection_provider(config)
    songset_client = SongsetClient(connection_provider, user_id=0)

    try:
        # Resolve user filter if provided
        if user is not None:
            user_client = UserClient(connection_provider)
            resolved_user = user_client.get_user_by_email(user)
            if resolved_user is None:
                console.print(f"[red]User not found: {user}[/red]")
                raise typer.Exit(1)
            songsets = songset_client.list_songsets_for_user_id(resolved_user.id, limit=limit)
        else:
            songsets = songset_client.list_all_songsets(limit=limit)
    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]Error listing songsets: {e}[/red]")
        raise typer.Exit(1)

    if not songsets:
        console.print("[yellow]No songsets found.[/yellow]")
        return

    # Short-circuit ids format before the owner/items round-trips
    if format == "ids":
        for songset in songsets:
            console.print(songset.id)
        return

    # Resolve owner emails in one round-trip
    owner_emails = _resolve_owner_emails(connection_provider, songsets)

    # Batch-fetch items with song/recording data
    songset_ids = [s.id for s in songsets]
    items_by_songset = songset_client.list_songset_items_with_song_recording(songset_ids)

    # Table format: one row per songset item
    total_items = sum(len(items) for items in items_by_songset.values())
    table = Table(title=f"Songsets ({len(songsets)} total, {total_items} items)")
    table.add_column("Songset", style="green")
    table.add_column("Owner", style="cyan")
    table.add_column("#", style="dim", justify="right")
    table.add_column("Title", style="cyan")
    table.add_column("Duration", style="yellow")
    table.add_column("Key", style="magenta")
    table.add_column("BPM", style="white")
    table.add_column("Song ID", style="dim")

    current_songset: Optional[str] = None

    for songset in songsets:
        items = items_by_songset.get(songset.id, [])

        # Print section separator when songset changes
        if current_songset is not None:
            table.add_section()

        current_songset = songset.id
        owner_email = owner_emails.get(songset.user_id, "?")

        if not items:
            # Empty songset: one row with dashes
            table.add_row(
                songset.name,
                owner_email,
                "-",
                "(no songs)",
                "--:--",
                "-",
                "--",
                "-",
            )
        else:
            for item in items:
                position_str = str(item.position + 1)
                title = item.song_title or "(missing)"
                duration = item.formatted_duration if item.song_title else "--:--"
                key = item.display_key if item.song_title else "-"
                bpm = str(round(item.tempo_bpm)) if item.tempo_bpm is not None else "--"
                song_id = item.song_id

                table.add_row(
                    songset.name,
                    owner_email,
                    position_str,
                    title,
                    duration,
                    key,
                    bpm,
                    song_id,
                )

    console.print(table)
