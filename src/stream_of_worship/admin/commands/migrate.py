"""Migration commands for sow-admin.

Provides one-time migration commands for schema and data changes.
"""

import hashlib
import re
import unicodedata
from pathlib import Path

import typer
from pypinyin import lazy_pinyin
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.admin.db.client import DatabaseClient

console = Console()
app = typer.Typer(help="Database migration operations")


def _compute_new_song_id(title: str, composer: str, lyricist: str) -> str:
    """Compute the new stable song ID format.

    Format: <pinyin_slug>_<8-hex-hash>
    Hash is computed from: sha256(NFKC(title) + "|" + NFKC(composer) + "|" + NFKC(lyricist))[:8]
    """
    norm = lambda s: unicodedata.normalize("NFKC", (s or "").strip())
    pinyin_parts = lazy_pinyin(norm(title))
    slug = re.sub(r"[^a-z0-9_]", "", "_".join(pinyin_parts).lower())
    payload = f"{norm(title)}|{norm(composer)}|{norm(lyricist)}"
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:8]
    song_id = f"{slug}_{digest}"
    if len(song_id) > 100:
        song_id = f"{slug[:91]}_{digest}"
    return song_id


@app.command("song-ids")
def migrate_song_ids(
    config_path: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Show what would be done without making changes",
    ),
) -> None:
    """Migrate song IDs from old row-based format to new content-hash format.

    This is a one-time migration that:
    1. Builds an old->new ID map for all songs
    2. Updates recordings.song_id references
    3. Updates songset_items.song_id references (in admin's songsets.db if exists)
    4. Updates songs.id in place

    The migration is idempotent - running it twice is a no-op if already migrated.
    """
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_path = config.db_path
    if not db_path.exists():
        console.print(f"[red]Database not found: {db_path}[/red]")
        raise typer.Exit(1)

    console.print(f"[bold]Migrating song IDs in {db_path}...[/bold]")

    client = DatabaseClient(db_path)

    try:
        # Step 1: Get all songs and build ID map
        with client.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, title, composer, lyricist FROM songs")
            songs = cursor.fetchall()

        if not songs:
            console.print("[yellow]No songs found in database.[/yellow]")
            return

        id_map = {}
        already_migrated = True

        for old_id, title, composer, lyricist in songs:
            new_id = _compute_new_song_id(title, composer, lyricist)
            if old_id != new_id:
                id_map[old_id] = new_id
                already_migrated = False

        if already_migrated:
            console.print(
                "[green]All song IDs are already in the new format. No migration needed.[/green]"
            )
            return

        console.print(f"Found {len(songs)} songs, {len(id_map)} need migration")

        if dry_run:
            console.print("\n[dim]Sample ID mappings (dry run):[/dim]")
            for old_id, new_id in list(id_map.items())[:5]:
                console.print(f"  {old_id} -> {new_id}")
            if len(id_map) > 5:
                console.print(f"  ... and {len(id_map) - 5} more")
            console.print("\n[yellow]Dry run - no changes made.[/yellow]")
            return

        # Step 2: Update recordings.song_id (must happen before songs.id due to FK)
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Updating recordings...", total=len(id_map))

            with client.transaction() as conn:
                cursor = conn.cursor()
                for old_id, new_id in id_map.items():
                    cursor.execute(
                        "UPDATE recordings SET song_id = ? WHERE song_id = ?",
                        (new_id, old_id),
                    )
                    progress.advance(task)

        # Step 3: Update songset_items in admin's songsets.db if it exists
        songsets_db_path = db_path.parent / "songsets.db"
        if songsets_db_path.exists():
            import sqlite3

            try:
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console,
                ) as progress:
                    task = progress.add_task("Updating songset_items...", total=len(id_map))

                    conn = sqlite3.connect(songsets_db_path)
                    cursor = conn.cursor()
                    for old_id, new_id in id_map.items():
                        cursor.execute(
                            "UPDATE songset_items SET song_id = ? WHERE song_id = ?",
                            (new_id, old_id),
                        )
                        progress.advance(task)
                    conn.commit()
                    conn.close()

                console.print(f"[green]Updated songset_items in {songsets_db_path}[/green]")
            except Exception as e:
                console.print(f"[yellow]Warning: Could not update songsets.db: {e}[/yellow]")

        # Step 4: Update songs.id (must happen last)
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Updating songs...", total=len(id_map))

            with client.transaction() as conn:
                cursor = conn.cursor()
                for old_id, new_id in id_map.items():
                    cursor.execute(
                        "UPDATE songs SET id = ? WHERE id = ?",
                        (new_id, old_id),
                    )
                    progress.advance(task)

        console.print(f"[green]Successfully migrated {len(id_map)} song IDs![/green]")
        console.print("\n[dim]Note: User songset_items tables will need manual migration[/dim]")
        console.print("[dim]or will be resolved when songs are re-added to songsets.[/dim]")

    except Exception as e:
        console.print(f"[red]Migration failed: {e}[/red]")
        raise typer.Exit(1)
    finally:
        client.close()
