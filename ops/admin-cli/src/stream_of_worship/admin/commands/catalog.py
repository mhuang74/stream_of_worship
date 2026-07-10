"""Catalog commands for sow-admin.

Provides CLI commands for scraping, listing, searching, and viewing
songs in the Stream of Worship catalog.
"""

import re
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Song
from stream_of_worship.admin.services.catalog_edit import (
    build_song_diff,
    build_song_from_review,
    compute_song_id,
    normalize_reviewed_data,
    parse_review_document,
    render_review_document,
    review_document_in_editor,
)
from stream_of_worship.admin.services.scraper import CatalogScraper
from stream_of_worship.admin.services.youtube import (
    _fetch_transcript_draft,
    derive_song_defaults,
    extract_video_metadata,
)
from stream_of_worship.db.connection import ConnectionProvider

console = Console()
app = typer.Typer(help="Catalog operations")


def _extract_series_sort_key(series: Optional[str]) -> tuple:
    """Extract sort key from album_series for sorting.

    Returns tuple of (prefix, number) where prefix is the text before the number
    and number is the numeric portion. This groups series by type first, then by number.

    Examples:
        '敬拜讚美15' -> ('敬拜讚美', 15)
        '兒童敬拜讚美 (14EP)' -> ('兒童敬拜讚美 ', 14)
        '台語敬拜讚美 (1)' -> ('台語敬拜讚美 ', 1)
        None or no digits -> ('', 0)
    """
    if not series:
        return ("", 0)
    match = re.search(r"\d+", series)
    if not match:
        return (series, 0)
    prefix = series[: match.start()]
    number = int(match.group())
    return (prefix, number)


def get_db_client(config: AdminConfig) -> DatabaseClient:
    """Get a database client from config.

    Args:
        config: Admin configuration

    Returns:
        DatabaseClient instance
    """
    provider = ConnectionProvider(config.get_connection_url())
    return DatabaseClient(provider)


def _prompt_confirmation(message: str) -> bool:
    try:
        return input(f"{message} [y/n]: ").strip().lower() in {"y", "yes"}
    except (EOFError, KeyboardInterrupt):
        return False


def _render_song_summary(song: Song, *, title: str) -> Panel:
    lines = [
        f"[cyan]ID:[/cyan] {song.id}",
        f"[cyan]Title:[/cyan] {song.title}",
        f"[cyan]Source URL:[/cyan] {song.source_url}",
    ]
    if song.composer:
        lines.append(f"[cyan]Composer:[/cyan] {song.composer}")
    if song.lyricist:
        lines.append(f"[cyan]Lyricist:[/cyan] {song.lyricist}")
    if song.album_name:
        lines.append(f"[cyan]Album:[/cyan] {song.album_name}")
    if song.album_series:
        lines.append(f"[cyan]Album Series:[/cyan] {song.album_series}")
    if song.musical_key:
        lines.append(f"[cyan]Key:[/cyan] {song.musical_key}")
    if song.deleted_at:
        lines.append(f"[cyan]Deleted At:[/cyan] {song.deleted_at}")
    lyrics_count = len(song.lyrics_list)
    lines.append(f"[cyan]Lyrics Lines:[/cyan] {lyrics_count}")
    return Panel.fit("\n".join(lines), title=title, border_style="green")


def _review_song_fields(
    initial_fields: dict[str, str | None],
    *,
    comments: list[str] | None = None,
):
    document = render_review_document(initial_fields, comments=comments)
    reviewed_text = review_document_in_editor(document)
    reviewed_data = parse_review_document(reviewed_text)
    return normalize_reviewed_data(reviewed_data)


def _show_proposed_song(song: Song, *, heading: str) -> None:
    console.print(_render_song_summary(song, title=heading))
    lyrics = song.lyrics_list
    if lyrics:
        preview = "\n".join(lyrics[:12])
        if len(lyrics) > 12:
            preview += "\n..."
        console.print(
            Panel(preview, title=f"Lyrics Preview ({len(lyrics)} lines)", border_style="cyan")
        )
    else:
        console.print(Panel("[dim]No lyrics[/dim]", title="Lyrics Preview", border_style="dim"))


def _print_duplicate_guidance(song: Song) -> None:
    status = "deleted" if song.deleted_at else "active"
    console.print(
        f"[yellow]Found matching {status} song: {song.id} — {song.title} ({song.source_url})[/yellow]"
    )
    if song.deleted_at:
        console.print(f"[cyan]Suggested next step:[/cyan] sow-admin catalog restore {song.id}")
    else:
        console.print(f"[cyan]Suggested next step:[/cyan] sow-admin catalog edit {song.id}")
    console.print(
        f"[cyan]Audio replacement:[/cyan] sow-admin audio download {song.id} --url <youtube-url> --force"
    )


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
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
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

    if scraper.last_run_duplicate_count:
        console.print(
            f"[yellow]Skipped {scraper.last_run_duplicate_count} duplicate row(s) "
            f"in source table (first occurrence kept)[/yellow]"
        )

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
        saved_count, elapsed = scraper.save_songs(songs)
        console.print(
            f"[green]Successfully saved {saved_count}/{len(songs)} songs in {elapsed:.2f}s[/green]"
        )
    else:
        console.print("[yellow]Dry run - no songs saved[/yellow]")


@app.command("insert")
def insert_song(
    youtube: Optional[str] = typer.Option(
        None,
        "--youtube",
        help="Direct YouTube URL to prefill metadata, captions, and audio import",
    ),
    song_id: Optional[str] = typer.Option(
        None,
        "--id",
        help="Override generated song ID",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-n",
        help="Show reviewed data and planned actions without writing",
    ),
    config_path: Path = typer.Option(
        None,
        "--config",
        "-c",
        help="Path to config file",
    ),
) -> None:
    """Insert a manually curated catalog song."""
    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)

    initial_fields = {
        "title": "",
        "composer": "",
        "lyricist": "",
        "album_name": "",
        "album_series": "",
        "musical_key": "",
        "source_url": youtube or "",
        "lyrics_raw": "",
    }
    comments = ["Review and curate every field before saving."]
    transcript_source = None

    if youtube:
        try:
            metadata = extract_video_metadata(youtube)
        except RuntimeError as e:
            console.print(f"[red]{e}[/red]")
            raise typer.Exit(1)

        initial_fields.update(derive_song_defaults(metadata))
        initial_fields["source_url"] = metadata.webpage_url
        comments.append(
            f"youtube_title = {metadata.title!r}, duration = {metadata.duration or 'unknown'} seconds"
        )

        try:
            transcript_draft = _fetch_transcript_draft(metadata.webpage_url)
            transcript_source = transcript_draft.source
            if transcript_draft.lines:
                initial_fields["lyrics_raw"] = "\n".join(transcript_draft.lines)
            comments.append(
                f'lyrics_source = "{transcript_draft.source}, {len(transcript_draft.lines)} draft lines"'
            )
        except RuntimeError as e:
            comments.append("lyrics_source = unavailable")
            console.print(f"[yellow]Transcript draft unavailable: {e}[/yellow]")

    reviewed = _review_song_fields(initial_fields, comments=comments)
    final_song_id = song_id or compute_song_id(reviewed.title, reviewed.composer, reviewed.lyricist)
    proposed_song = build_song_from_review(reviewed, song_id=final_song_id)

    duplicate_song = db_client.get_song(final_song_id, include_deleted=True)
    if duplicate_song:
        console.print(f"[red]Song ID already exists: {final_song_id}[/red]")
        _print_duplicate_guidance(duplicate_song)
        raise typer.Exit(1)

    duplicate_source = db_client.find_song_by_source_url(reviewed.source_url, include_deleted=True)
    if duplicate_source:
        console.print(f"[red]Source URL already exists: {reviewed.source_url}[/red]")
        _print_duplicate_guidance(duplicate_source)
        raise typer.Exit(1)

    _show_proposed_song(proposed_song, heading="Proposed Song")

    if dry_run:
        console.print(f"[cyan]Generated song ID:[/cyan] {final_song_id}")
        if youtube:
            console.print(f"[cyan]Planned audio download URL:[/cyan] {reviewed.source_url}")
        if transcript_source:
            console.print(
                f"[cyan]Caption draft:[/cyan] {transcript_source}, {len(proposed_song.lyrics_list)} lines"
            )
        return

    if not _prompt_confirmation("Insert this catalog song?"):
        console.print("[yellow]Insert cancelled.[/yellow]")
        raise typer.Exit(0)

    try:
        db_client.insert_song(proposed_song)
    except Exception as e:
        console.print(f"[red]Failed to insert song: {e}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Inserted song {proposed_song.id}[/green]")

    if youtube:
        try:
            from stream_of_worship.admin.commands.audio import import_youtube_audio_for_song

            import_youtube_audio_for_song(
                song_id=proposed_song.id,
                youtube_url=reviewed.source_url,
                config=config,
                db_client=db_client,
                console=console,
                force=False,
                skip_video_confirm=True,
                analyze=False,
                lrc=False,
            )
        except typer.Exit:
            console.print(
                f"[yellow]Song inserted without audio. Retry with:[/yellow] "
                f"sow-admin audio download {proposed_song.id} --url {reviewed.source_url}"
            )
            raise
        except Exception as e:
            console.print(
                f"[yellow]Song inserted, but audio import failed: {e}[/yellow]\n"
                f"[cyan]Retry:[/cyan] sow-admin audio download {proposed_song.id} --url {reviewed.source_url}"
            )


@app.command("edit")
def edit_song(
    song_id: str = typer.Argument(..., help="Song ID to edit"),
    config_path: Path = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Review and update nominal catalog metadata for an active song."""
    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)
    existing = db_client.get_song(song_id)
    if not existing:
        console.print(f"[red]Song not found: {song_id}[/red]")
        raise typer.Exit(1)

    reviewed = _review_song_fields(
        {
            "title": existing.title,
            "composer": existing.composer or "",
            "lyricist": existing.lyricist or "",
            "album_name": existing.album_name or "",
            "album_series": existing.album_series or "",
            "musical_key": existing.musical_key or "",
            "source_url": existing.source_url,
            "lyrics_raw": existing.lyrics_raw or "",
        },
        comments=[f"Editing existing song_id = {existing.id!r}"],
    )
    updated_song = build_song_from_review(
        reviewed,
        existing_song_id=existing.id,
        created_at=existing.created_at,
        scraped_at=existing.scraped_at,
    )

    diff_text = build_song_diff(existing, updated_song)
    console.print(_render_song_summary(updated_song, title="Reviewed Song"))
    if diff_text:
        console.print(Panel(diff_text, title="Proposed Diff", border_style="yellow"))

    if not _prompt_confirmation("Save these catalog changes?"):
        console.print("[yellow]Edit cancelled.[/yellow]")
        raise typer.Exit(0)

    if not db_client.update_song(updated_song):
        console.print(f"[red]Failed to update song: {song_id}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Updated song {song_id}[/green]")
    if (existing.lyrics_raw or "") != (updated_song.lyrics_raw or ""):
        console.print(f"[cyan]Follow-up:[/cyan] sow-admin audio lrc {song_id} --force")
        console.print(f"[cyan]Follow-up:[/cyan] sow-admin audio embed {song_id}")


def _read_song_ids_from_stdin() -> list[str]:
    """Read song IDs from stdin, one per line.

    Returns:
        List of non-empty, stripped song IDs
    """
    song_ids = []
    for line in sys.stdin:
        line = line.strip()
        if line:
            song_ids.append(line)
    return song_ids


@app.command("delete")
def delete_song(
    song_id: Optional[str] = typer.Argument(None, help="Song ID to delete"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    stdin: bool = typer.Option(False, "--stdin", help="Read song IDs from stdin (one per line)"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Soft-delete a catalog song while preserving associated R2 files.

    Marks the song as deleted in the database and places its active recordings
    on hold so they disappear from the user app. R2 assets are preserved
    so they can be reviewed, restored, or purged by maintenance commands.

    For batch deletion, pipe song IDs via stdin:

        sow-admin catalog list --album album1 --format ids | sow-admin catalog delete --stdin
    """
    if not song_id and not stdin:
        console.print("[red]Error: Either provide a song_id argument or use --stdin flag[/red]")
        raise typer.Exit(1)

    if song_id and stdin:
        console.print("[red]Error: Cannot use both song_id argument and --stdin flag[/red]")
        raise typer.Exit(1)

    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)

    if stdin:
        _delete_songs_batch(db_client, yes, console)
    else:
        _delete_song_single(song_id, db_client, yes, console)


def _delete_song_single(
    song_id: str,
    db_client: DatabaseClient,
    yes: bool,
    console: Console,
) -> None:
    """Delete a single song by song_id."""
    song = db_client.get_song(song_id, include_deleted=True)
    if not song:
        console.print(f"[red]Song not found: {song_id}[/red]")
        raise typer.Exit(1)

    if song.deleted_at:
        console.print(f"[red]Song is already soft-deleted: {song_id}[/red]")
        raise typer.Exit(1)

    recordings = db_client.list_recordings_by_song_id(song_id)
    references = db_client.count_songset_references(song_id)

    info_lines = [
        f"[cyan]Song ID:[/cyan] {song_id}",
        f"[cyan]Title:[/cyan] {song.title}",
    ]
    if song.album_name:
        info_lines.append(f"[cyan]Album:[/cyan] {song.album_name}")
    if song.album_series:
        info_lines.append(f"[cyan]Album Series:[/cyan] {song.album_series}")
    info_lines.append(f"[cyan]Active recordings:[/cyan] {len(recordings)}")
    info_lines.append(f"[cyan]Songset references:[/cyan] {references}")

    console.print(
        Panel.fit(
            "\n".join(info_lines),
            title="Song to Delete",
            border_style="yellow",
        )
    )

    if not yes:
        console.print(
            "[yellow]This soft-deletes the song row and holds its recordings; "
            "R2 assets remain for maintenance review.[/yellow]"
        )
        confirmed = _prompt_confirmation("Soft-delete this song?")
        if not confirmed:
            console.print("[yellow]Deletion cancelled.[/yellow]")
            raise typer.Exit(0)

    console.print("[cyan]Soft-deleting song...[/cyan]")
    db_client.soft_delete_song(song_id)
    held_count = db_client.hold_recordings_for_song(song_id)
    console.print(f"[green]Song {song_id} soft-deleted successfully.[/green]")
    console.print(f"[cyan]Recordings placed on hold:[/cyan] {held_count}")
    console.print("[dim]R2 audio, stems, and LRC files were preserved.[/dim]")


def _delete_songs_batch(
    db_client: DatabaseClient,
    yes: bool,
    console: Console,
) -> None:
    """Delete multiple songs from stdin."""
    song_ids = _read_song_ids_from_stdin()

    if not song_ids:
        console.print("[yellow]No song IDs provided via stdin[/yellow]")
        raise typer.Exit(0)

    console.print(f"[cyan]Looking up {len(song_ids)} song(s)...[/cyan]")

    songs_to_delete: list[tuple[str, Song]] = []
    not_found: list[str] = []
    already_deleted: list[str] = []

    for sid in song_ids:
        song = db_client.get_song(sid, include_deleted=True)
        if not song:
            not_found.append(sid)
        elif song.deleted_at:
            already_deleted.append(sid)
        else:
            songs_to_delete.append((sid, song))

    if not_found:
        console.print(
            f"[yellow]Song not found for {len(not_found)} ID(s): "
            f"{', '.join(not_found[:5])}{'...' if len(not_found) > 5 else ''}[/yellow]"
        )

    if already_deleted:
        console.print(
            f"[yellow]Already soft-deleted: {len(already_deleted)} ID(s): "
            f"{', '.join(already_deleted[:5])}{'...' if len(already_deleted) > 5 else ''}[/yellow]"
        )

    if not songs_to_delete:
        console.print("[yellow]No valid songs to delete.[/yellow]")
        raise typer.Exit(0)

    info_lines = [
        f"[cyan]Count:[/cyan] {len(songs_to_delete)} song(s)",
        "",
        "[bold]Songs to delete:[/bold]",
    ]
    for sid, song in songs_to_delete[:10]:
        info_lines.append(f"  • {sid}: {song.title}")
    if len(songs_to_delete) > 10:
        info_lines.append(f"  ... and {len(songs_to_delete) - 10} more")

    console.print(
        Panel.fit(
            "\n".join(info_lines),
            title="Batch Delete Songs",
            border_style="yellow",
        )
    )

    if not yes:
        console.print(
            "[yellow]This soft-deletes song rows and holds their recordings; "
            "R2 assets remain for maintenance review.[/yellow]"
        )
        confirmed = _prompt_confirmation(f"Soft-delete {len(songs_to_delete)} song(s)?")
        if not confirmed:
            console.print("[yellow]Deletion cancelled.[/yellow]")
            raise typer.Exit(0)

    deleted_count = 0
    failed_count = 0

    for sid, song in songs_to_delete:
        try:
            db_client.soft_delete_song(sid)
            db_client.hold_recordings_for_song(sid)
            deleted_count += 1
            console.print(f"[green]✓ Deleted: {sid} ({song.title})[/green]")
        except Exception as e:
            failed_count += 1
            console.print(f"[red]✗ Failed to delete {sid}: {e}[/red]")

    console.print()
    console.print(f"[bold]Summary:[/bold] {deleted_count} deleted, {failed_count} failed")


@app.command("restore")
def restore_song(
    song_id: str = typer.Argument(..., help="Song ID to restore"),
    config_path: Path = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Restore a soft-deleted song row."""
    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)
    song = db_client.get_song(song_id, include_deleted=True)
    if not song:
        console.print(f"[red]Song not found: {song_id}[/red]")
        raise typer.Exit(1)

    if not db_client.restore_song(song_id):
        console.print(f"[red]Failed to restore song: {song_id}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Restored song {song_id}[/green]")
    held_recordings = [
        recording
        for recording in db_client.list_recordings_by_song_id(song_id, include_deleted=True)
        if recording.visibility_status == "hold" and recording.deleted_at is None
    ]
    if held_recordings:
        table = Table(title="Held Recordings")
        table.add_column("Hash Prefix", style="cyan")
        table.add_column("Visibility", style="yellow")
        for recording in held_recordings:
            table.add_row(recording.hash_prefix, recording.visibility_status or "-")
        console.print(table)
    console.print(
        f"[cyan]Suggested next step:[/cyan] sow-admin audio set-visibility {song_id} --status review"
    )
    console.print(
        f"[cyan]Suggested next step:[/cyan] sow-admin audio set-visibility {song_id} --status published"
    )


@app.command("list")
def list_songs(
    album: Optional[str] = typer.Option(
        None,
        "--album",
        "-a",
        help="Filter by album name (substring, case-insensitive; matches album_name or album_series)",
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
    sort: str = typer.Option(
        "series",
        "--sort",
        "-s",
        help="Sort order (album|series|title|id)",
    ),
    albums: bool = typer.Option(
        False,
        "--albums",
        help="Show album names with song counts instead of individual songs",
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
    deleted: bool = typer.Option(
        False,
        "--deleted",
        help="Show soft-deleted songs instead of active songs",
    ),
    config_path: Path = typer.Option(
        None,
        "--config",
        help="Path to config file",
    ),
) -> None:
    """List songs from catalog.

    Display songs from the local catalog database with optional filtering.
    Use --sort to change sort order (default: album). Use --sort series to
    sort by album series number (e.g. 敬拜讚美15 → 15). Use --albums to
    show only album names with song counts. Use --format ids for piping.
    """
    if sort not in ("album", "series", "title", "id"):
        console.print(
            f"[red]Invalid sort option: {sort}. Choose from: album, series, title, id[/red]"
        )
        raise typer.Exit(1)

    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)

    if albums:
        try:
            album_list = db_client.list_albums(include_deleted=deleted)
        except Exception as e:
            console.print(f"[red]Error listing albums: {e}[/red]")
            raise typer.Exit(1)

        if not album_list:
            console.print("[yellow]No albums found.[/yellow]")
            return

        if sort == "series":
            album_list.sort(key=lambda a: _extract_series_sort_key(a[1]))

        table = Table(title=f"Albums ({len(album_list)} total)")
        table.add_column("Album", style="yellow")
        if sort == "series":
            table.add_column("Series", style="magenta")
        table.add_column("Songs", style="cyan", justify="right")

        for album_name, album_series, count in album_list:
            if sort == "series":
                table.add_row(album_name or "-", album_series or "-", str(count))
            else:
                table.add_row(album_name or "-", str(count))

        console.print(table)
        return

    try:
        if deleted:
            songs = db_client.list_deleted_songs()
            if album:
                al = album.lower()
                songs = [
                    s
                    for s in songs
                    if (s.album_name and al in s.album_name.lower())
                    or (s.album_series and al in s.album_series.lower())
                ]
            if key:
                songs = [song for song in songs if song.musical_key == key]
            order_map = {
                "album": lambda s: (s.album_name or "", s.title, s.id),
                "series": lambda s: (
                    _extract_series_sort_key(s.album_series),
                    s.album_name or "",
                    s.title,
                    s.id,
                ),
                "title": lambda s: (s.title, s.id),
                "id": lambda s: (s.id,),
            }
            songs.sort(key=order_map.get(sort, order_map["album"]))
            if limit is not None:
                songs = songs[:limit]
        else:
            songs = db_client.list_songs(album=album, key=key, limit=limit, sort_by=sort)
    except Exception as e:
        console.print(f"[red]Error listing songs: {e}[/red]")
        raise typer.Exit(1)

    if not songs:
        console.print("[yellow]No songs found matching the criteria.[/yellow]")
        return

    # Apply composer filter in memory (since list_songs doesn't support it directly)
    if composer:
        songs = [s for s in songs if s.composer and composer.lower() in s.composer.lower()]

    # Re-sort by series number in memory (SQL sort is lexicographic on album_series text)
    if sort == "series":
        songs.sort(
            key=lambda s: (
                _extract_series_sort_key(s.album_series),
                s.album_name or "",
                s.title,
                s.id,
            )
        )

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
        table.add_column("Title", style="cyan")
        table.add_column("Key", style="magenta", justify="center")
        table.add_column("Album", style="yellow")
        table.add_column("Album Series", style="white")
        table.add_column("Composer", style="green")
        table.add_column("ID", style="dim", no_wrap=True)

        for song in songs:
            table.add_row(
                song.title,
                song.musical_key or "-",
                song.album_name or "-",
                song.album_series or "-",
                song.composer or "-",
                song.id,
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
        help="Field to search (title|lyrics|composer|album|all)",
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
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
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
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
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

    console.print(
        Panel.fit(
            "\n".join(info_lines),
            title=f"Song: {song.title}",
            border_style="green",
        )
    )

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

        console.print(
            Panel.fit(
                "\n".join(recording_lines),
                title="Recording",
                border_style="green",
            )
        )

    # Lyrics panel
    lyrics_list = song.lyrics_list
    if lyrics_list:
        lyrics_text = Text("\n".join(lyrics_list))
        console.print(
            Panel(
                lyrics_text,
                title=f"Lyrics ({len(lyrics_list)} lines)",
                border_style="cyan",
            )
        )
    else:
        console.print(
            Panel(
                "[dim]No lyrics available[/dim]",
                title="Lyrics",
                border_style="dim",
            )
        )
