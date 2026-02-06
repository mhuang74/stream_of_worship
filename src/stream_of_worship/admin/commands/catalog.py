"""Catalog commands for sow-admin.

Provides CLI commands for scraping, listing, searching, and viewing
songs in the Stream of Worship catalog.
"""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.services.scraper import CatalogScraper

console = Console()
app = typer.Typer(help="Catalog operations")


def get_db_client(config: AdminConfig) -> DatabaseClient:
    """Get a database client from config.

    Args:
        config: Admin configuration

    Returns:
        DatabaseClient instance
    """
    return DatabaseClient(config.db_path)


@app.command("scrape")
def scrape_catalog(
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        "-l",
        help="Maximum number of songs to scrape",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Re-scrape all songs even if already in database",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Preview without saving to database",
    ),
    config_path: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Scrape song catalog from sop.org.

    Scrapes the song catalog from sop.org/songs and saves to the local database.
    Use --dry-run to preview without saving. Use --force to re-scrape existing songs.
    """
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_path = config.db_path

    if not db_path.exists():
        console.print(f"[red]Database not found at {db_path}[/red]")
        console.print("Run 'sow-admin db init' to create the database.")
        raise typer.Exit(1)

    # Initialize scraper
    if dry_run:
        console.print("[yellow]Dry run mode - songs will not be saved[/yellow]")
        scraper = CatalogScraper(db_client=None)
    else:
        db_client = get_db_client(config)
        scraper = CatalogScraper(db_client=db_client)

    # Scrape songs
    console.print(f"[cyan]Scraping songs from {scraper.url}...[/cyan]")
    if limit:
        console.print(f"[dim]Limit: {limit} songs[/dim]")

    try:
        songs = scraper.scrape_all_songs(limit=limit, force=force, incremental=not force)
    except Exception as e:
        console.print(f"[red]Error scraping songs: {e}[/red]")
        raise typer.Exit(1)

    if not songs:
        console.print("[yellow]No new songs to scrape.[/yellow]")
        return

    console.print(f"[green]Found {len(songs)} songs[/green]")

    # Preview table
    preview_table = Table(title="Scraped Songs Preview")
    preview_table.add_column("Row", style="dim")
    preview_table.add_column("Title", style="cyan")
    preview_table.add_column("Composer", style="green")
    preview_table.add_column("Album", style="yellow")
    preview_table.add_column("Key", style="magenta")

    for i, song in enumerate(songs[:20], 1):  # Show first 20
        preview_table.add_row(
            str(song.table_row_number or i),
            song.title,
            song.composer or "-",
            song.album_name or "-",
            song.musical_key or "-",
        )

    if len(songs) > 20:
        preview_table.add_row(
            "...",
            f"[dim]{len(songs) - 20} more songs...[/dim]",
            "",
            "",
            "",
        )

    console.print(preview_table)

    # Save to database unless dry run
    if not dry_run:
        console.print(f"[cyan]Saving {len(songs)} songs to database...[/cyan]")
        saved_count = scraper.save_songs(songs)
        console.print(f"[green]Successfully saved {saved_count}/{len(songs)} songs[/green]")
    else:
        console.print("[yellow]Dry run - no songs saved[/yellow]")


@app.command("list")
def list_songs(
    album: Optional[str] = typer.Option(
        None,
        "--album",
        "-a",
        help="Filter by album name",
    ),
    key: Optional[str] = typer.Option(
        None,
        "--key",
        "-k",
        help="Filter by musical key",
    ),
    composer: Optional[str] = typer.Option(
        None,
        "--composer",
        "-c",
        help="Filter by composer",
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        "-l",
        help="Maximum number of results",
    ),
    format: str = typer.Option(
        "table",
        "--format",
        "-f",
        help="Output format (table|ids)",
    ),
    config_path: Path = typer.Option(
        None,
        "--config",
        help="Path to config file",
    ),
) -> None:
    """List songs from catalog.

    Display songs from the local catalog database with optional filtering.
    Use --format ids to output one song ID per line for piping.
    """
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_path = config.db_path

    if not db_path.exists():
        console.print(f"[red]Database not found at {db_path}[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)

    try:
        songs = db_client.list_songs(album=album, key=key, limit=limit)
    except Exception as e:
        console.print(f"[red]Error listing songs: {e}[/red]")
        raise typer.Exit(1)

    if not songs:
        console.print("[yellow]No songs found matching the criteria.[/yellow]")
        return

    # Apply composer filter in memory (since list_songs doesn't support it directly)
    if composer:
        songs = [s for s in songs if s.composer and composer.lower() in s.composer.lower()]

    if not songs:
        console.print("[yellow]No songs found matching the criteria.[/yellow]")
        return

    if format == "ids":
        # Output one ID per line for piping
        for song in songs:
            console.print(song.id)
    else:
        # Table format
        table = Table(title=f"Songs ({len(songs)} total)")
        table.add_column("ID", style="dim", no_wrap=True)
        table.add_column("Title", style="cyan")
        table.add_column("Composer", style="green")
        table.add_column("Album", style="yellow")
        table.add_column("Key", style="magenta", justify="center")

        for song in songs:
            table.add_row(
                song.id,
                song.title,
                song.composer or "-",
                song.album_name or "-",
                song.musical_key or "-",
            )

        console.print(table)


@app.command("search")
def search_songs(
    query: str = typer.Argument(
        ...,
        help="Search query",
    ),
    field: str = typer.Option(
        "all",
        "--field",
        help="Field to search (title|lyrics|composer|all)",
    ),
    limit: int = typer.Option(
        20,
        "--limit",
        "-l",
        help="Maximum number of results",
    ),
    config_path: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Search songs in catalog.

    Search for songs by title, lyrics, composer, or all fields.
    Results are ordered by song ID.
    """
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_path = config.db_path

    if not db_path.exists():
        console.print(f"[red]Database not found at {db_path}[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)

    try:
        songs = db_client.search_songs(query, field=field, limit=limit)
    except Exception as e:
        console.print(f"[red]Error searching songs: {e}[/red]")
        raise typer.Exit(1)

    if not songs:
        console.print(f"[yellow]No songs found matching '{query}'.[/yellow]")
        return

    # Table format
    table = Table(title=f"Search Results for '{query}' ({len(songs)} found)")
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Title", style="cyan")
    table.add_column("Composer", style="green")
    table.add_column("Album", style="yellow")
    table.add_column("Key", style="magenta", justify="center")

    for song in songs:
        table.add_row(
            song.id,
            song.title,
            song.composer or "-",
            song.album_name or "-",
            song.musical_key or "-",
        )

    console.print(table)


@app.command("show")
def show_song(
    song_id: str = typer.Argument(
        ...,
        help="Song ID to display",
    ),
    config_path: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Show detailed info for a song.

    Display all fields for a specific song including full lyrics.
    """
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_path = config.db_path

    if not db_path.exists():
        console.print(f"[red]Database not found at {db_path}[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)

    try:
        song = db_client.get_song(song_id)
    except Exception as e:
        console.print(f"[red]Error retrieving song: {e}[/red]")
        raise typer.Exit(1)

    if not song:
        console.print(f"[red]Song not found: {song_id}[/red]")
        raise typer.Exit(1)

    # Build info panel
    info_lines = [
        f"[cyan]ID:[/cyan] {song.id}",
        f"[cyan]Title:[/cyan] {song.title}",
    ]

    if song.title_pinyin:
        info_lines.append(f"[cyan]Pinyin:[/cyan] {song.title_pinyin}")

    if song.composer:
        info_lines.append(f"[cyan]Composer:[/cyan] {song.composer}")

    if song.lyricist:
        info_lines.append(f"[cyan]Lyricist:[/cyan] {song.lyricist}")

    if song.album_name:
        info_lines.append(f"[cyan]Album:[/cyan] {song.album_name}")

    if song.album_series:
        info_lines.append(f"[cyan]Series:[/cyan] {song.album_series}")

    if song.musical_key:
        info_lines.append(f"[cyan]Key:[/cyan] {song.musical_key}")

    if song.table_row_number:
        info_lines.append(f"[cyan]Row #:[/cyan] {song.table_row_number}")

    info_lines.append(f"[cyan]Source:[/cyan] {song.source_url}")
    info_lines.append(f"[cyan]Scraped:[/cyan] {song.scraped_at}")

    console.print(Panel.fit(
        "\n".join(info_lines),
        title=f"Song: {song.title}",
        border_style="green",
    ))

    # Recording panel
    recording = db_client.get_recording_by_song_id(song_id)
    if recording:
        recording_lines = ["[cyan]Audio Available:[/cyan] [green]✓[/green]"]
        recording_lines.append(f"[cyan]Hash Prefix:[/cyan] {recording.hash_prefix}")

        if recording.file_size_bytes:
            size_mb = recording.file_size_bytes / (1024 * 1024)
            recording_lines.append(f"[cyan]File Size:[/cyan] {size_mb:.1f} MB")

        if recording.duration_seconds:
            minutes = int(recording.duration_seconds // 60)
            secs = int(recording.duration_seconds % 60)
            recording_lines.append(f"[cyan]Duration:[/cyan] {minutes}:{secs:02d}")

        # Analysis status
        if recording.analysis_status == "completed":
            analysis_text = "[green]completed[/green]"
        elif recording.analysis_status == "failed":
            analysis_text = "[red]failed[/red]"
        elif recording.analysis_status == "processing":
            analysis_text = "[yellow]processing[/yellow]"
        else:
            analysis_text = f"[dim]{recording.analysis_status}[/dim]"
        recording_lines.append(f"[cyan]Analysis:[/cyan] {analysis_text}")

        # LRC status
        if recording.lrc_status == "completed":
            lrc_text = "[green]completed[/green]"
        elif recording.lrc_status == "failed":
            lrc_text = "[red]failed[/red]"
        elif recording.lrc_status == "processing":
            lrc_text = "[yellow]processing[/yellow]"
        else:
            lrc_text = f"[dim]{recording.lrc_status}[/dim]"
        recording_lines.append(f"[cyan]LRC:[/cyan] {lrc_text}")

        console.print(Panel.fit(
            "\n".join(recording_lines),
            title="Recording",
            border_style="green",
        ))

    # Lyrics panel
    lyrics_list = song.lyrics_list
    if lyrics_list:
        lyrics_text = Text("\n".join(lyrics_list))
        console.print(Panel(
            lyrics_text,
            title=f"Lyrics ({len(lyrics_list)} lines)",
            border_style="cyan",
        ))
    else:
        console.print(Panel(
            "[dim]No lyrics available[/dim]",
            title="Lyrics",
            border_style="dim",
        ))
