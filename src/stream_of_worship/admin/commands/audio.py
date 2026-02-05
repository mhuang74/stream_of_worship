"""Audio commands for sow-admin.

Provides CLI commands for downloading audio from YouTube, listing
recordings, and viewing recording details.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Recording
from stream_of_worship.admin.services.hasher import compute_file_hash, get_hash_prefix
from stream_of_worship.admin.services.r2 import R2Client
from stream_of_worship.admin.services.youtube import YouTubeDownloader

console = Console()
app = typer.Typer(help="Audio recording operations")


@app.command("download")
def download_audio(
    song_id: str = typer.Argument(..., help="Song ID to download audio for"),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Preview without downloading"
    ),
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config file"
    ),
) -> None:
    """Download audio from YouTube for a song.

    Searches YouTube using the song's title, composer and album, downloads
    the top result as MP3, hashes it, uploads to R2, and persists a
    recording entry in the local database.
    """
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    if not config.db_path.exists():
        console.print(f"[red]Database not found at {config.db_path}[/red]")
        raise typer.Exit(1)

    db_client = DatabaseClient(config.db_path)

    # Look up the song
    song = db_client.get_song(song_id)
    if not song:
        console.print(f"[red]Song not found: {song_id}[/red]")
        raise typer.Exit(1)

    console.print(f"[cyan]Song:[/cyan] {song.title}")
    if song.composer:
        console.print(f"[cyan]Composer:[/cyan] {song.composer}")
    if song.album_name:
        console.print(f"[cyan]Album:[/cyan] {song.album_name}")

    # Abort if a recording already exists for this song
    existing = db_client.get_recording_by_song_id(song_id)
    if existing:
        console.print(
            f"[yellow]Recording already exists for this song "
            f"(hash: {existing.hash_prefix})[/yellow]"
        )
        raise typer.Exit(0)

    # Build and display the YouTube search query
    downloader = YouTubeDownloader()
    query = downloader.build_search_query(
        title=song.title,
        composer=song.composer,
        album=song.album_name,
    )
    console.print(f"[dim]Search query: {query}[/dim]")

    if dry_run:
        console.print("[yellow]Dry run - no download will occur[/yellow]")
        return

    # Download audio from YouTube
    console.print("[cyan]Downloading audio from YouTube...[/cyan]")
    try:
        audio_path = downloader.download(query)
    except RuntimeError as e:
        console.print(f"[red]Download failed: {e}[/red]")
        raise typer.Exit(1)

    file_size = audio_path.stat().st_size
    console.print(f"[green]Downloaded: {audio_path.name}[/green]")
    console.print(f"[dim]File size: {file_size:,} bytes[/dim]")

    # Compute content hash
    content_hash = compute_file_hash(audio_path)
    prefix = get_hash_prefix(content_hash)
    console.print(f"[dim]Hash prefix: {prefix}[/dim]")

    # Upload to R2
    console.print("[cyan]Uploading to R2...[/cyan]")
    try:
        r2_client = R2Client(
            bucket=config.r2_bucket,
            endpoint_url=config.r2_endpoint_url,
            region=config.r2_region,
        )
        r2_url = r2_client.upload_audio(audio_path, prefix)
        console.print(f"[green]Uploaded: {r2_url}[/green]")
    except ValueError as e:
        console.print(f"[red]R2 configuration error: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Upload failed: {e}[/red]")
        raise typer.Exit(1)

    # Persist recording
    recording = Recording(
        content_hash=content_hash,
        hash_prefix=prefix,
        song_id=song_id,
        original_filename=audio_path.name,
        file_size_bytes=file_size,
        imported_at=datetime.now().isoformat(),
        r2_audio_url=r2_url,
    )
    db_client.insert_recording(recording)
    console.print(f"[green]Recording saved (hash_prefix: {prefix})[/green]")

    # Clean up temp file
    audio_path.unlink(missing_ok=True)


@app.command("list")
def list_recordings(
    status: Optional[str] = typer.Option(
        None,
        "--status",
        "-s",
        help="Filter by analysis status (pending|processing|completed|failed)",
    ),
    format: str = typer.Option(
        "table", "--format", "-f", help="Output format (table|ids)"
    ),
    limit: Optional[int] = typer.Option(
        None, "--limit", "-l", help="Maximum number of results"
    ),
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config file"
    ),
) -> None:
    """List audio recordings.

    Display recordings from the database with optional status filtering.
    Use ``--format ids`` for one hash prefix per line (pipeable).
    """
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    if not config.db_path.exists():
        console.print(f"[red]Database not found at {config.db_path}[/red]")
        raise typer.Exit(1)

    db_client = DatabaseClient(config.db_path)
    recordings = db_client.list_recordings(status=status, limit=limit)

    if not recordings:
        console.print("[yellow]No recordings found.[/yellow]")
        return

    if format == "ids":
        for rec in recordings:
            console.print(rec.hash_prefix)
    else:
        table = Table(title=f"Recordings ({len(recordings)} total)")
        table.add_column("Hash Prefix", style="dim", no_wrap=True)
        table.add_column("Song", style="cyan")
        table.add_column("Filename", style="green")
        table.add_column("Size", style="yellow", justify="right")
        table.add_column("Status", style="magenta", justify="center")

        for rec in recordings:
            song_title = "-"
            if rec.song_id:
                song = db_client.get_song(rec.song_id)
                if song:
                    song_title = song.title

            size_str = f"{rec.file_size_bytes:,}" if rec.file_size_bytes else "-"

            if rec.analysis_status == "completed":
                status_text = f"[green]{rec.analysis_status}[/green]"
            elif rec.analysis_status == "failed":
                status_text = f"[red]{rec.analysis_status}[/red]"
            elif rec.analysis_status == "processing":
                status_text = f"[yellow]{rec.analysis_status}[/yellow]"
            else:
                status_text = rec.analysis_status

            table.add_row(
                rec.hash_prefix,
                song_title,
                rec.original_filename,
                size_str,
                status_text,
            )

        console.print(table)


@app.command("show")
def show_recording(
    hash_prefix: str = typer.Argument(
        ..., help="Hash prefix of the recording to show"
    ),
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config file"
    ),
) -> None:
    """Show detailed info for a recording.

    Displays all metadata for a recording, including analysis results
    when available.
    """
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    if not config.db_path.exists():
        console.print(f"[red]Database not found at {config.db_path}[/red]")
        raise typer.Exit(1)

    db_client = DatabaseClient(config.db_path)

    recording = db_client.get_recording_by_hash(hash_prefix)
    if not recording:
        console.print(f"[red]Recording not found: {hash_prefix}[/red]")
        raise typer.Exit(1)

    info_lines = [
        f"[cyan]Hash Prefix:[/cyan] {recording.hash_prefix}",
        f"[cyan]Full Hash:[/cyan] {recording.content_hash}",
    ]

    if recording.song_id:
        song = db_client.get_song(recording.song_id)
        if song:
            info_lines.append(f"[cyan]Song:[/cyan] {song.title} ({song.id})")
        else:
            info_lines.append(f"[cyan]Song ID:[/cyan] {recording.song_id}")

    info_lines.extend([
        f"[cyan]Filename:[/cyan] {recording.original_filename}",
        f"[cyan]Size:[/cyan] {recording.file_size_bytes:,} bytes",
        f"[cyan]Imported:[/cyan] {recording.imported_at}",
    ])

    if recording.r2_audio_url:
        info_lines.append(f"[cyan]Audio URL:[/cyan] {recording.r2_audio_url}")

    # Status
    info_lines.append("")
    info_lines.append(f"[cyan]Analysis Status:[/cyan] {recording.analysis_status}")
    if recording.analysis_job_id:
        info_lines.append(f"[cyan]Analysis Job:[/cyan] {recording.analysis_job_id}")
    info_lines.append(f"[cyan]LRC Status:[/cyan] {recording.lrc_status}")
    if recording.lrc_job_id:
        info_lines.append(f"[cyan]LRC Job:[/cyan] {recording.lrc_job_id}")

    # Analysis results (only shown when analysis is complete)
    if recording.has_analysis:
        info_lines.append("")
        info_lines.append("[bold]Analysis Results:[/bold]")
        if recording.duration_seconds is not None:
            info_lines.append(f"[cyan]Duration:[/cyan] {recording.formatted_duration}")
        if recording.tempo_bpm is not None:
            info_lines.append(f"[cyan]Tempo:[/cyan] {recording.tempo_bpm} BPM")
        if recording.musical_key:
            info_lines.append(f"[cyan]Key:[/cyan] {recording.musical_key}")
        if recording.musical_mode:
            info_lines.append(f"[cyan]Mode:[/cyan] {recording.musical_mode}")
        if recording.key_confidence is not None:
            info_lines.append(f"[cyan]Key Confidence:[/cyan] {recording.key_confidence:.2f}")
        if recording.loudness_db is not None:
            info_lines.append(f"[cyan]Loudness:[/cyan] {recording.loudness_db:.1f} dB")

    console.print(Panel.fit(
        "\n".join(info_lines),
        title=f"Recording: {recording.hash_prefix}",
        border_style="green",
    ))
