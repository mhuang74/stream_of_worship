"""Audio commands for sow-admin.

Provides CLI commands for downloading audio from YouTube, listing
recordings, and viewing recording details.
"""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import typer
from botocore.exceptions import ClientError
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.syntax import Syntax
from rich.table import Table

from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Recording
from stream_of_worship.admin.services.analysis import (
    AnalysisClient,
    AnalysisServiceError,
    JobInfo,
)
from stream_of_worship.admin.services.hasher import compute_file_hash, get_hash_prefix
from stream_of_worship.admin.services.lrc_parser import format_duration, parse_lrc
from stream_of_worship.admin.services.r2 import R2Client
from stream_of_worship.admin.services.youtube import (
    DURATION_WARNING_THRESHOLD,
    OFFICIAL_LYRICS_SUFFIX,
    YouTubeDownloader,
)

console = Console()
app = typer.Typer(help="Audio recording operations")


# Helper functions for download flow


def _format_duration_mmss(seconds: Optional[float]) -> str:
    """Format seconds as MM:SS.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string like "7:42"
    """
    if seconds is None:
        return "Unknown"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}:{secs:02d}"


def _display_video_preview(
    video_info: dict, console: Console, threshold: int = DURATION_WARNING_THRESHOLD
) -> None:
    """Display video preview in Rich Panel with duration warning.

    Args:
        video_info: Dict with video metadata (title, duration, webpage_url)
        console: Rich console instance
        threshold: Duration threshold in seconds for warning
    """
    title = video_info.get("title", "Unknown")
    duration = video_info.get("duration")
    url = video_info.get("webpage_url", "Unknown")

    duration_str = _format_duration_mmss(duration)
    is_long = duration is not None and duration > threshold

    # Build panel content
    lines = [
        f"[cyan]Title:[/cyan] {title}",
        f"[cyan]Duration:[/cyan] {duration_str}",
        f"[cyan]URL:[/cyan] {url}",
    ]

    if is_long:
        lines.append("")
        lines.append(f"[yellow bold]⚠ Warning: Video exceeds {threshold // 60} minutes[/yellow bold]")

    border_style = "yellow" if is_long else "green"

    console.print(
        Panel.fit(
            "\n".join(lines),
            title="Video Preview",
            border_style=border_style,
        )
    )


def _prompt_confirmation(message: str) -> bool:
    """Prompt for y/n confirmation, return True if accepted.

    Args:
        message: Prompt message to display

    Returns:
        True if user confirms (y), False otherwise
    """
    try:
        response = input(f"{message} [y/n]: ").strip().lower()
        return response in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def _prompt_manual_url(max_attempts: int = 3) -> Optional[str]:
    """Prompt for manual URL, validate format, return URL or None.

    Args:
        max_attempts: Maximum number of validation attempts

    Returns:
        Valid YouTube URL or None if cancelled
    """
    for attempt in range(max_attempts):
        try:
            url = input("Enter YouTube URL (or press Enter to cancel): ").strip()
        except (EOFError, KeyboardInterrupt):
            return None

        if not url:
            return None

        # Validate URL format (contains youtube.com or youtu.be)
        if "youtube.com" in url or "youtu.be" in url:
            return url

        console.print("[yellow]Invalid YouTube URL. Please enter a valid YouTube URL.[/yellow]")

    console.print("[red]Too many invalid attempts. Cancelling.[/red]")
    return None


def _delete_r2_object_safe(
    r2_client: R2Client,
    url: Optional[str],
    description: str,
    console: Console,
) -> None:
    """Safely delete R2 object, showing status and handling errors.

    Args:
        r2_client: R2 client instance
        url: S3 URL of object to delete
        description: Human-readable description for console output
        console: Rich console instance
    """
    if not url:
        return
    try:
        _, key = R2Client.parse_s3_url(url)
        r2_client.delete_file(key)
        console.print(f"[green]✓ Deleted {description}[/green]")
    except Exception as e:
        console.print(f"[yellow]⚠ Could not delete {description}: {e}[/yellow]")


def _delete_recording_and_files(
    db_client: DatabaseClient,
    r2_client: R2Client,
    recording: Recording,
    console: Console,
) -> None:
    """Delete recording from DB and R2. Shared by delete command and --force flag.

    Args:
        db_client: Database client instance
        r2_client: R2 client instance
        recording: Recording to delete
        console: Rich console instance
    """
    # Delete R2 files
    _delete_r2_object_safe(r2_client, recording.r2_audio_url, "audio file", console)
    _delete_r2_object_safe(r2_client, recording.r2_stems_url, "stems file", console)
    _delete_r2_object_safe(r2_client, recording.r2_lrc_url, "LRC file", console)

    # Delete DB record
    db_client.delete_recording(recording.hash_prefix)


def _format_size_mb(bytes: Optional[int]) -> str:
    """Format bytes as MB with 2 decimal places.

    Args:
        bytes: Size in bytes

    Returns:
        Formatted string like "6.97 MB"
    """
    if bytes is None:
        return "-- MB"
    mb = bytes / (1024 * 1024)
    return f"{mb:.2f} MB"


def _format_duration(seconds: Optional[float]) -> str:
    """Format duration as MM:SS.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string like "3:45"
    """
    if seconds is None:
        return "--:--"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}:{secs:02d}"


def _colorize_status(status: str) -> str:
    """Get Rich markup for status.

    Args:
        status: Status string

    Returns:
        Rich markup string
    """
    if status == "completed":
        return f"[green]{status}[/green]"
    elif status == "processing":
        return f"[yellow]{status}[/yellow]"
    elif status == "failed":
        return f"[red]{status}[/red]"
    else:
        return f"[dim]{status}[/dim]"


def _submit_analysis_job(
    recording: Recording,
    analysis_url: str,
    db_client: DatabaseClient,
    console: Console,
    force: bool = False,
    no_stems: bool = False,
) -> Optional[str]:
    """Submit analysis job for a recording.

    Args:
        recording: Recording to analyze
        analysis_url: Analysis service URL
        db_client: Database client for storing results
        console: Rich console for output
        force: Force re-analysis if already completed
        no_stems: Skip stem separation

    Returns:
        Job ID if submission succeeded, None otherwise
    """
    try:
        client = AnalysisClient(analysis_url)
        job = client.submit_analysis(
            audio_url=recording.r2_audio_url,
            content_hash=recording.content_hash,
            generate_stems=not no_stems,
            force=force,
        )

        # Update DB
        db_client.update_recording_status(
            hash_prefix=recording.hash_prefix,
            analysis_status="processing",
            analysis_job_id=job.job_id,
        )

        console.print(f"[green]Analysis submitted (job: {job.job_id})[/green]")
        return job.job_id
    except AnalysisServiceError as e:
        if e.status_code == 401:
            console.print(f"[yellow]⚠ Authentication failed for analysis: {e}[/yellow]")
        else:
            console.print(f"[yellow]⚠ Failed to submit analysis: {e}[/yellow]")
        return None
    except ValueError as e:
        console.print(f"[yellow]⚠ Analysis service not configured: {e}[/yellow]")
        return None


def _submit_lrc_job(
    song_id: str,
    recording: Recording,
    analysis_url: str,
    db_client: DatabaseClient,
    console: Console,
    force: bool = False,
    whisper_model: str = "large-v3",
    language: str = "zh",
    no_vocals: bool = False,
) -> Optional[str]:
    """Submit LRC generation job for a recording.

    Args:
        song_id: Song ID for looking up lyrics
        recording: Recording to generate LRC for
        analysis_url: Analysis service URL
        db_client: Database client for storing results
        console: Rich console for output
        force: Force re-generation if already completed
        whisper_model: Whisper model to use
        language: Language hint for Whisper
        no_vocals: Don't use vocals stem

    Returns:
        Job ID if submission succeeded, None otherwise
    """
    # Look up song for lyrics
    song = db_client.get_song(song_id)
    if not song or not song.lyrics_raw:
        console.print(f"[yellow]⚠ No lyrics found for song {song_id}, skipping LRC generation[/yellow]")
        return None

    try:
        client = AnalysisClient(analysis_url)
        job = client.submit_lrc(
            audio_url=recording.r2_audio_url,
            content_hash=recording.content_hash,
            lyrics_text=song.lyrics_raw,
            whisper_model=whisper_model,
            language=language,
            use_vocals_stem=not no_vocals,
            force=force,
            youtube_url=recording.youtube_url or "",
        )

        # Update DB
        db_client.update_recording_status(
            hash_prefix=recording.hash_prefix,
            lrc_status="processing",
            lrc_job_id=job.job_id,
        )

        console.print(f"[green]LRC job submitted (job: {job.job_id})[/green]")
        return job.job_id
    except AnalysisServiceError as e:
        console.print(f"[yellow]⚠ Failed to submit LRC job: {e}[/yellow]")
        return None
    except ValueError as e:
        console.print(f"[yellow]⚠ Analysis service not configured for LRC: {e}[/yellow]")
        return None


@app.command("download")
def download_audio(
    song_id: str = typer.Argument(..., help="Song ID to download audio for"),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Preview without downloading"
    ),
    url: Optional[str] = typer.Option(
        None, "--url", "-u", help="Direct YouTube URL (skip search)"
    ),
    skip_confirm: bool = typer.Option(
        False, "--yes", "-y", help="Skip confirmation prompt"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Replace existing recording if it exists"
    ),
    analyze: bool = typer.Option(
        False, "--analyze", "-a", help="Submit for analysis after download"
    ),
    lrc: bool = typer.Option(
        False, "--lrc", "-l", help="Submit for LRC generation after download"
    ),
    all: bool = typer.Option(
        False, "--all", "-A", help="Submit for both analysis and LRC after download"
    ),
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config file"
    ),
) -> None:
    """Download audio from YouTube for a song.

    Searches YouTube using the song's title, composer and album, downloads
    the top result as MP3, hashes it, uploads to R2, and persists a
    recording entry in the local database.

    Use --analyze, --lrc, or --all to automatically submit for processing
    after successful download.
    """
    # If --all is set, enable both analyze and lrc
    if all:
        analyze = True
        lrc = True

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

    # Initialize R2 client early (needed for --force and upload)
    try:
        r2_client = R2Client(
            bucket=config.r2_bucket,
            endpoint_url=config.r2_endpoint_url,
            region=config.r2_region,
        )
    except ValueError as e:
        console.print(f"[red]R2 configuration error: {e}[/red]")
        raise typer.Exit(1)

    # Check for existing recording
    existing = db_client.get_recording_by_song_id(song_id)
    if existing:
        if not force:
            console.print(
                f"[yellow]Recording already exists for this song "
                f"(hash: {existing.hash_prefix}). Use --force to replace.[/yellow]"
            )
            raise typer.Exit(0)
        else:
            # Delete existing recording
            console.print(f"[cyan]Deleting existing recording {existing.hash_prefix}...[/cyan]")
            _delete_recording_and_files(db_client, r2_client, existing, console)
            console.print("[green]Existing recording deleted. Proceeding with download...[/green]")

    if dry_run:
        console.print("[yellow]Dry run - no download will occur[/yellow]")
        return

    # Initialize downloader
    downloader = YouTubeDownloader()

    # Step 1: Determine URL or search query
    if url:
        # Use provided URL directly
        search_or_url = url
        console.print(f"[dim]Using provided URL: {url}[/dim]")
    else:
        # Build search query with official lyrics suffix
        query = downloader.build_search_query(
            title=song.title,
            composer=song.composer,
            album=song.album_name,
            suffix=OFFICIAL_LYRICS_SUFFIX,
        )
        console.print(f"[dim]Search query: {query}[/dim]")
        search_or_url = query

    # Step 2: Preview video
    console.print("[cyan]Previewing video...[/cyan]")
    try:
        video_info = downloader.preview_video(search_or_url)
    except RuntimeError as e:
        console.print(f"[red]Failed to preview video: {e}[/red]")
        raise typer.Exit(1)

    if video_info is None:
        console.print("[red]No results found.[/red]")
        console.print("[dim]Try using --url to provide a direct YouTube URL.[/dim]")
        raise typer.Exit(1)

    # Step 3: Display video preview
    _display_video_preview(video_info, console)

    # Step 4: Confirmation prompt
    download_confirmed = skip_confirm
    if not skip_confirm:
        download_confirmed = _prompt_confirmation("Download this video?")

    if not download_confirmed:
        # Step 5: Manual URL fallback
        console.print("[yellow]Auto-selected video rejected.[/yellow]")
        manual_url = _prompt_manual_url()

        if not manual_url:
            console.print("[yellow]Download cancelled.[/yellow]")
            raise typer.Exit(0)

        # Re-preview the manual URL
        console.print("[cyan]Previewing manual URL...[/cyan]")
        try:
            video_info = downloader.preview_video(manual_url)
        except RuntimeError as e:
            console.print(f"[red]Failed to preview video: {e}[/red]")
            raise typer.Exit(1)

        if video_info is None:
            console.print("[red]No results found for manual URL.[/red]")
            raise typer.Exit(1)

        _display_video_preview(video_info, console)

        # Confirm the manual URL
        if not _prompt_confirmation("Download this video?"):
            console.print("[yellow]Download cancelled.[/yellow]")
            raise typer.Exit(0)

        # Use the manual URL for download
        search_or_url = manual_url

    # Step 6: Download
    console.print("[cyan]Downloading audio from YouTube...[/cyan]")
    try:
        if search_or_url.startswith(("http://", "https://", "www.", "youtube.com", "youtu.be")):
            audio_path = downloader.download_by_url(search_or_url)
        else:
            audio_path = downloader.download(search_or_url)
    except RuntimeError as e:
        console.print(f"[red]Download failed: {e}[/red]")
        raise typer.Exit(1)

    file_size = audio_path.stat().st_size
    console.print(f"[green]Downloaded: {audio_path.name}[/green]")
    console.print(f"[dim]File size: {_format_size_mb(file_size)}[/dim]")

    # Compute content hash
    content_hash = compute_file_hash(audio_path)
    prefix = get_hash_prefix(content_hash)
    console.print(f"[dim]Hash prefix: {prefix}[/dim]")

    # Upload to R2
    console.print("[cyan]Uploading to R2...[/cyan]")
    try:
        r2_url = r2_client.upload_audio(audio_path, prefix)
        console.print(f"[green]Uploaded: {r2_url}[/green]")
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
        youtube_url=video_info.get("webpage_url"),
    )
    db_client.insert_recording(recording)
    console.print(f"[green]Recording saved (hash_prefix: {prefix})[/green]")

    # Clean up temp file
    audio_path.unlink(missing_ok=True)

    # Submit for analysis if requested
    if analyze:
        console.print("[cyan]Submitting for analysis...[/cyan]")
        _submit_analysis_job(
            recording=recording,
            analysis_url=config.analysis_url,
            db_client=db_client,
            console=console,
            force=False,
            no_stems=False,
        )

    # Submit for LRC if requested
    if lrc:
        console.print("[cyan]Submitting for LRC generation...[/cyan]")
        _submit_lrc_job(
            song_id=song_id,
            recording=recording,
            analysis_url=config.analysis_url,
            db_client=db_client,
            console=console,
            force=False,
            whisper_model="large-v3",
            language="zh",
            no_vocals=False,
        )


@app.command("delete")
def delete_recording(
    song_id: str = typer.Argument(..., help="Song ID to delete recording for"),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip confirmation prompt"
    ),
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config file"
    ),
) -> None:
    """Delete a recording and all associated R2 files.

    Removes the recording from the database and deletes associated files
    from R2 (audio, stems, LRC). Use this when the wrong audio was
    downloaded and you want to re-download the correct version.
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

    # Look up recording by song_id
    recording = db_client.get_recording_by_song_id(song_id)
    if not recording:
        console.print(f"[red]No recording found for song: {song_id}[/red]")
        raise typer.Exit(1)

    # Get song info for display
    song = db_client.get_song(song_id)
    song_title = song.title if song else "Unknown"

    # Initialize R2 client
    try:
        r2_client = R2Client(
            bucket=config.r2_bucket,
            endpoint_url=config.r2_endpoint_url,
            region=config.r2_region,
        )
    except ValueError as e:
        console.print(f"[red]R2 configuration error: {e}[/red]")
        raise typer.Exit(1)

    # Display what will be deleted
    info_lines = [
        f"[cyan]Song ID:[/cyan] {song_id}",
        f"[cyan]Song Title:[/cyan] {song_title}",
        f"[cyan]Hash Prefix:[/cyan] {recording.hash_prefix}",
        f"[cyan]Filename:[/cyan] {recording.original_filename}",
        f"[cyan]Size:[/cyan] {_format_size_mb(recording.file_size_bytes)}" if recording.file_size_bytes else "[cyan]Size:[/cyan] -- MB",
    ]

    # List R2 resources
    info_lines.append("")
    info_lines.append("[bold]R2 Resources to delete:[/bold]")

    if recording.r2_audio_url:
        info_lines.append(f"  [green]✓[/green] Audio file: {recording.r2_audio_url}")
    else:
        info_lines.append("  [dim]✗ No audio file[/dim]")

    if recording.r2_stems_url:
        info_lines.append(f"  [green]✓[/green] Stems file: {recording.r2_stems_url}")
    else:
        info_lines.append("  [dim]✗ No stems file[/dim]")

    if recording.r2_lrc_url:
        info_lines.append(f"  [green]✓[/green] LRC file: {recording.r2_lrc_url}")
    else:
        info_lines.append("  [dim]✗ No LRC file[/dim]")

    console.print(
        Panel.fit(
            "\n".join(info_lines),
            title="Recording to Delete",
            border_style="yellow",
        )
    )

    # Confirmation prompt
    if not yes:
        console.print("[red bold]Warning: This action cannot be undone![/red bold]")
        confirmed = _prompt_confirmation("Delete this recording and all associated files?")
        if not confirmed:
            console.print("[yellow]Deletion cancelled.[/yellow]")
            raise typer.Exit(0)

    # Perform deletion
    console.print("[cyan]Deleting recording...[/cyan]")
    _delete_recording_and_files(db_client, r2_client, recording, console)

    console.print(f"[green]Recording {recording.hash_prefix} deleted successfully.[/green]")


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
    Use ``--format ids`` for one song ID per line (pipeable).
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
            console.print(rec.song_id if rec.song_id else rec.hash_prefix)
    else:
        table = Table(title=f"Recordings ({len(recordings)} total)")
        table.add_column("Song Title", style="green")
        table.add_column("Size", style="magenta", justify="right")
        table.add_column("Status", style="blue", justify="center")
        table.add_column("Duration", style="cyan", no_wrap=True)
        table.add_column("Song ID", style="dim", no_wrap=True)
        table.add_column("Job ID", style="dim", no_wrap=True)
        table.add_column("Filename", style="yellow")
        table.add_column("Hash Prefix", style="dim", no_wrap=True)

        for rec in recordings:
            song_id = rec.song_id or "-"
            song_title = "-"
            if rec.song_id:
                song = db_client.get_song(rec.song_id)
                if song:
                    song_title = song.title

            size_str = _format_size_mb(rec.file_size_bytes) if rec.file_size_bytes else "-- MB"

            if rec.analysis_status == "completed":
                status_text = f"[green]{rec.analysis_status}[/green]"
            elif rec.analysis_status == "failed":
                status_text = f"[red]{rec.analysis_status}[/red]"
            elif rec.analysis_status == "processing":
                status_text = f"[yellow]{rec.analysis_status}[/yellow]"
            else:
                status_text = rec.analysis_status

            job_id = rec.analysis_job_id or "-"

            # Format duration if available (from analysis results)
            duration_str = _format_duration(rec.duration_seconds) if rec.duration_seconds else "--:--"

            table.add_row(
                song_title,
                size_str,
                status_text,
                duration_str,
                song_id,
                job_id,
                rec.original_filename,
                rec.hash_prefix,
            )

        console.print(table)


@app.command("show")
def show_recording(
    song_id: str = typer.Argument(
        ..., help="Song ID to show recording for"
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

    # Look up recording by song_id
    recording = db_client.get_recording_by_song_id(song_id)
    if not recording:
        console.print(
            f"[red]No recording found for {song_id}. "
            f"Run 'sow-admin audio download {song_id}'[/red]"
        )
        raise typer.Exit(1)

    # Get song info
    song = db_client.get_song(song_id)

    info_lines = [
        f"[cyan]Song ID:[/cyan] {song_id}",
    ]

    if song:
        info_lines.append(f"[cyan]Song Title:[/cyan] {song.title}")

    info_lines.extend([
        f"[cyan]Hash Prefix:[/cyan] {recording.hash_prefix}",
        f"[cyan]Full Hash:[/cyan] {recording.content_hash}",
    ])

    info_lines.extend([
        f"[cyan]Filename:[/cyan] {recording.original_filename}",
        f"[cyan]Size:[/cyan] {_format_size_mb(recording.file_size_bytes)}",
        f"[cyan]Duration:[/cyan] {_format_duration(recording.duration_seconds)}",
        f"[cyan]Imported:[/cyan] {recording.imported_at}",
    ])

    if recording.r2_audio_url:
        info_lines.append(f"[cyan]Audio URL:[/cyan] {recording.r2_audio_url}")

    if recording.youtube_url:
        info_lines.append(f"[cyan]YouTube URL:[/cyan] {recording.youtube_url}")

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
        title=f"Recording: {song_id}",
        border_style="green",
    ))


@app.command("analyze")
def analyze_recording(
    song_id: str = typer.Argument(..., help="Song ID to analyze"),
    force: bool = typer.Option(False, "--force", "-f", help="Force re-analysis"),
    no_stems: bool = typer.Option(
        False, "--no-stems", help="Skip stem separation"
    ),
    wait: bool = typer.Option(
        False, "--wait", "-w", help="Wait for analysis to complete"
    ),
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config file"
    ),
) -> None:
    """Submit a recording for analysis.

    Looks up the recording by song_id and submits it to the analysis service
    for tempo/key/beats/sections detection.
    """
    # Standard config/db boilerplate
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    if not config.db_path.exists():
        console.print(f"[red]Database not found at {config.db_path}[/red]")
        raise typer.Exit(1)

    db_client = DatabaseClient(config.db_path)

    # Look up recording by song_id
    recording = db_client.get_recording_by_song_id(song_id)
    if not recording:
        console.print(
            f"[red]No recording found for {song_id}. "
            f"Run 'sow-admin audio download {song_id}' first.[/red]"
        )
        raise typer.Exit(1)

    # Validate r2_audio_url exists
    if not recording.r2_audio_url:
        console.print(
            f"[red]Recording {recording.hash_prefix} has no audio URL.[/red]"
        )
        raise typer.Exit(1)

    # Check if already analyzed
    if recording.analysis_status == "completed" and not force:
        console.print(
            f"[yellow]Recording {recording.hash_prefix} is already analyzed. "
            f"Use --force to re-analyze.[/yellow]"
        )
        raise typer.Exit(0)

    # Check if already processing
    if recording.analysis_status == "processing" and recording.analysis_job_id and not force:
        if not wait:
            console.print(
                f"[yellow]Analysis already in progress for "
                f"{recording.hash_prefix} (job: {recording.analysis_job_id})[/yellow]"
            )
            raise typer.Exit(0)
        # With --wait, we'll poll the existing job
        job_id = recording.analysis_job_id
        skip_submission = True
    else:
        skip_submission = False

    # Create analysis client
    try:
        client = AnalysisClient(config.analysis_url)
    except ValueError as e:
        console.print(f"[red]Analysis service not configured: {e}[/red]")
        raise typer.Exit(1)

    # Submit analysis (unless we're polling an existing job)
    if not skip_submission:
        try:
            job = client.submit_analysis(
                audio_url=recording.r2_audio_url,
                content_hash=recording.content_hash,
                generate_stems=not no_stems,
                force=force,
            )
        except AnalysisServiceError as e:
            if e.status_code == 401:
                console.print(f"[red]Authentication failed: {e}[/red]")
            else:
                console.print(f"[red]Failed to submit analysis: {e}[/red]")
            raise typer.Exit(1)

        job_id = job.job_id

        # Update DB
        db_client.update_recording_status(
            hash_prefix=recording.hash_prefix,
            analysis_status="processing",
            analysis_job_id=job_id,
        )

        console.print(f"[green]Analysis submitted (job: {job_id})[/green]")
    else:
        console.print(f"[cyan]Polling existing job: {job_id}[/cyan]")

    # Wait mode with progress
    if wait:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("{task.fields[stage]}"),
            console=console,
        ) as progress:
            task = progress.add_task(
                "Analyzing...", total=100, stage="", completed=0
            )

            def update_progress(job_info: JobInfo) -> None:
                pct = int(job_info.progress * 100)
                progress.update(
                    task, completed=pct, stage=f"[{job_info.stage}]"
                )

            try:
                final_job = client.wait_for_completion(
                    job_id,
                    poll_interval=30.0,
                    timeout=600.0,
                    callback=update_progress,
                )
            except AnalysisServiceError as e:
                console.print(f"[red]{e}[/red]")
                db_client.update_recording_status(
                    hash_prefix=recording.hash_prefix,
                    analysis_status="failed",
                )
                raise typer.Exit(1)

        if final_job.status == "failed":
            error_msg = final_job.error_message or "Unknown error"
            console.print(f"[red]Analysis failed: {error_msg}[/red]")
            db_client.update_recording_status(
                hash_prefix=recording.hash_prefix,
                analysis_status="failed",
            )
            raise typer.Exit(1)

        # Store results
        if final_job.result:
            result = final_job.result
            db_client.update_recording_analysis(
                hash_prefix=recording.hash_prefix,
                duration_seconds=result.duration_seconds,
                tempo_bpm=result.tempo_bpm,
                musical_key=result.musical_key,
                musical_mode=result.musical_mode,
                key_confidence=result.key_confidence,
                loudness_db=result.loudness_db,
                beats=json.dumps(result.beats) if result.beats else None,
                downbeats=json.dumps(result.downbeats)
                if result.downbeats
                else None,
                sections=json.dumps(result.sections) if result.sections else None,
                embeddings_shape=json.dumps(result.embeddings_shape)
                if result.embeddings_shape
                else None,
                r2_stems_url=result.stems_url,
            )

        console.print(f"[green]Analysis completed for {song_id}[/green]")
        if final_job.result:
            if final_job.result.tempo_bpm:
                console.print(f"  Tempo: {final_job.result.tempo_bpm:.1f} BPM")
            if final_job.result.musical_key:
                console.print(f"  Key: {final_job.result.musical_key}")
            if final_job.result.duration_seconds:
                console.print(
                    f"  Duration: {_format_duration(final_job.result.duration_seconds)}"
                )


@app.command("lrc")
def lrc_recording(
    song_id: str = typer.Argument(..., help="Song ID to generate LRC for"),
    force: bool = typer.Option(False, "--force", "-f", help="Force re-generation"),
    whisper_model: str = typer.Option("large-v3", "--model", "-m", help="Whisper model to use"),
    language: str = typer.Option("zh", "--lang", help="Language hint"),
    no_vocals: bool = typer.Option(False, "--no-vocals", help="Don't use vocals stem"),
    wait: bool = typer.Option(
        False, "--wait", "-w", help="Wait for LRC generation to complete"
    ),
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config file"
    ),
) -> None:
    """Submit a recording for lyrics alignment (LRC generation).

    Looks up the recording and its associated song lyrics, then submits
    to the analysis service for Whisper-based alignment.
    """
    # Standard config/db boilerplate
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    if not config.db_path.exists():
        console.print(f"[red]Database not found at {config.db_path}[/red]")
        raise typer.Exit(1)

    db_client = DatabaseClient(config.db_path)

    # Look up recording by song_id
    recording = db_client.get_recording_by_song_id(song_id)
    if not recording:
        console.print(
            f"[red]No recording found for {song_id}.[/red]"
        )
        raise typer.Exit(1)

    # Look up song for lyrics
    song = db_client.get_song(song_id)
    if not song or not song.lyrics_raw:
        console.print(f"[red]No lyrics found for song {song_id}.[/red]")
        raise typer.Exit(1)

    # Validate r2_audio_url exists
    if not recording.r2_audio_url:
        console.print(
            f"[red]Recording {recording.hash_prefix} has no audio URL.[/red]"
        )
        raise typer.Exit(1)

    # Check if already has LRC
    if recording.lrc_status == "completed" and not force:
        console.print(
            f"[yellow]Recording {recording.hash_prefix} already has LRC. "
            f"Use --force to re-generate.[/yellow]"
        )
        raise typer.Exit(0)

    # Check if already processing
    if recording.lrc_status == "processing" and recording.lrc_job_id and not force:
        if not wait:
            console.print(
                f"[yellow]LRC generation already in progress for "
                f"{recording.hash_prefix} (job: {recording.lrc_job_id})[/yellow]"
            )
            raise typer.Exit(0)
        job_id = recording.lrc_job_id
        skip_submission = True
    else:
        skip_submission = False

    # Create analysis client
    try:
        client = AnalysisClient(config.analysis_url)
    except ValueError as e:
        console.print(f"[red]Analysis service not configured: {e}[/red]")
        raise typer.Exit(1)

    # Submit LRC (unless we're polling an existing job)
    if not skip_submission:
        try:
            job = client.submit_lrc(
                audio_url=recording.r2_audio_url,
                content_hash=recording.content_hash,
                lyrics_text=song.lyrics_raw,
                whisper_model=whisper_model,
                language=language,
                use_vocals_stem=not no_vocals,
                force=force,
                youtube_url=recording.youtube_url or "",
            )
        except AnalysisServiceError as e:
            console.print(f"[red]Failed to submit LRC job: {e}[/red]")
            raise typer.Exit(1)

        job_id = job.job_id

        # Update DB
        db_client.update_recording_status(
            hash_prefix=recording.hash_prefix,
            lrc_status="processing",
            lrc_job_id=job_id,
        )

        console.print(f"[green]LRC job submitted (job: {job_id})[/green]")
    else:
        console.print(f"[cyan]Polling existing job: {job_id}[/cyan]")

    # Wait mode with progress
    if wait:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("{task.fields[stage]}"),
            console=console,
        ) as progress:
            task = progress.add_task(
                "Generating LRC...", total=100, stage="", completed=0
            )

            def update_progress(job_info: JobInfo) -> None:
                pct = int(job_info.progress * 100)
                progress.update(
                    task, completed=pct, stage=f"[{job_info.stage}]"
                )

            try:
                final_job = client.wait_for_completion(
                    job_id,
                    poll_interval=30.0,
                    timeout=600.0,
                    callback=update_progress,
                )
            except AnalysisServiceError as e:
                console.print(f"[red]{e}[/red]")
                db_client.update_recording_status(
                    hash_prefix=recording.hash_prefix,
                    lrc_status="failed",
                )
                raise typer.Exit(1)

        if final_job.status == "failed":
            error_msg = final_job.error_message or "Unknown error"
            console.print(f"[red]LRC generation failed: {error_msg}[/red]")
            db_client.update_recording_status(
                hash_prefix=recording.hash_prefix,
                lrc_status="failed",
            )
            raise typer.Exit(1)

        # Store results
        if final_job.result and final_job.result.lrc_url:
            db_client.update_recording_lrc(
                hash_prefix=recording.hash_prefix,
                r2_lrc_url=final_job.result.lrc_url,
            )

        console.print(f"[green]LRC generation completed for {song_id}[/green]")
        if final_job.result and final_job.result.lrc_url:
            console.print(f"  LRC URL: {final_job.result.lrc_url}")


@app.command("status")
def check_status(
    job_id: Optional[str] = typer.Argument(None, help="Job ID to check"),
    sync: bool = typer.Option(
        False, "--sync", "-s", help="Sync pending statuses from analysis service"
    ),
    force_status: Optional[str] = typer.Option(
        None, "--force-status", help="Force update status (completed, failed, pending). Use when Analysis Service has lost state."
    ),
    force_url: Optional[str] = typer.Option(
        None, "--force-url", help="URL to set when using --force-status (stems_url for analysis, lrc_url for lrc)"
    ),
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config file"
    ),
) -> None:
    """Check analysis status.

    With JOB_ID: query the service for that job's status.
    Without: list all recordings with pending/processing/failed status.
    Use --sync to update local database with latest statuses from service.
    Use --force-status when Analysis Service has restarted and lost job state.
    """
    # Standard config/db boilerplate
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    if not config.db_path.exists():
        console.print(f"[red]Database not found at {config.db_path}[/red]")
        raise typer.Exit(1)

    db_client = DatabaseClient(config.db_path)

    # Validate force_status if provided
    if force_status and force_status not in ("completed", "failed", "pending"):
        console.print("[red]--force-status must be one of: completed, failed, pending[/red]")
        raise typer.Exit(1)

    # Handle --force-status mode
    if force_status:
        if job_id:
            # Force update specific recording by job_id
            # Try analysis job first, then lrc job
            rec = db_client.get_recording_by_job_id(job_id, job_type="analysis")
            if not rec:
                rec = db_client.get_recording_by_job_id(job_id, job_type="lrc")
            if not rec:
                console.print(f"[red]No recording found with job_id: {job_id}[/red]")
                raise typer.Exit(1)

            _update_recording_status_force(
                db_client, rec, force_status, force_url, console
            )
            return
        elif sync:
            # Force update all pending recordings
            _force_sync_all_pending(db_client, force_status, force_url, console)
            return
        else:
            console.print("[red]--force-status requires either a JOB_ID or --sync flag[/red]")
            raise typer.Exit(1)

    # Mode A: Query specific job
    if job_id:
        try:
            client = AnalysisClient(config.analysis_url)
        except ValueError as e:
            console.print(f"[red]Analysis service not configured: {e}[/red]")
            raise typer.Exit(1)

        try:
            job = client.get_job(job_id)
        except AnalysisServiceError as e:
            if e.status_code == 404:
                console.print(f"[red]Job not found: {job_id}[/red]")
            elif e.status_code == 401:
                console.print(f"[red]Authentication failed: {e}[/red]")
            else:
                console.print(f"[red]Failed to get job status: {e}[/red]")
            raise typer.Exit(1)

        # Display job info in a panel
        lines = [
            f"[cyan]Job ID:[/cyan] {job.job_id}",
            f"[cyan]Type:[/cyan] {job.job_type}",
            f"[cyan]Status:[/cyan] {_colorize_status(job.status)}",
        ]

        if job.stage:
            lines.append(f"[cyan]Stage:[/cyan] {job.stage}")

        lines.append(f"[cyan]Progress:[/cyan] {int(job.progress * 100)}%")

        if job.created_at:
            lines.append(f"[cyan]Created:[/cyan] {job.created_at}")
        if job.updated_at:
            lines.append(f"[cyan]Updated:[/cyan] {job.updated_at}")

        if job.status == "failed" and job.error_message:
            lines.append("")
            lines.append(f"[red]Error: {job.error_message}[/red]")

        if job.result and job.status == "completed":
            lines.append("")
            lines.append("[bold]Results:[/bold]")
            if job.job_type == "analysis":
                if job.result.duration_seconds:
                    lines.append(
                        f"  Duration: {_format_duration(job.result.duration_seconds)}"
                    )
                if job.result.tempo_bpm:
                    lines.append(f"  Tempo: {job.result.tempo_bpm:.1f} BPM")
                if job.result.musical_key:
                    lines.append(f"  Key: {job.result.musical_key}")
                if job.result.musical_mode:
                    lines.append(f"  Mode: {job.result.musical_mode}")
                if job.result.stems_url:
                    lines.append(f"  Stems: {job.result.stems_url}")
            elif job.job_type == "lrc":
                if job.result.lrc_url:
                    lines.append(f"  LRC URL: {job.result.lrc_url}")

        console.print(Panel.fit(
            "\n".join(lines),
            title=f"Job: {job.job_id}",
            border_style="green" if job.status == "completed" else "yellow",
        ))
        return

    # Mode B: Sync and list pending recordings
    # If --sync, query analysis service for all pending jobs and update local DB
    if sync:
        try:
            client = AnalysisClient(config.analysis_url)
        except ValueError as e:
            console.print(f"[red]Analysis service not configured: {e}[/red]")
            raise typer.Exit(1)

        # Get all recordings with pending/processing analysis or LRC status
        pending_recordings = db_client.list_recordings(status="processing")
        pending_recordings.extend(db_client.list_recordings(status="pending"))
        
        # Also check for pending LRC jobs
        cursor = db_client.connection.cursor()
        cursor.execute("SELECT hash_prefix FROM recordings WHERE lrc_status IN ('pending', 'processing')")
        lrc_pending_hashes = [row[0] for row in cursor.fetchall()]
        
        # Merge hashes to sync
        hashes_to_sync = set(rec.hash_prefix for rec in pending_recordings) | set(lrc_pending_hashes)

        if hashes_to_sync:
            console.print(f"[cyan]Syncing {len(hashes_to_sync)} pending recording(s)...[/cyan]")
            synced_count = 0
            failed_count = 0

            for h_prefix in hashes_to_sync:
                rec = db_client.get_recording_by_hash(h_prefix)
                if not rec:
                    continue

                # Sync analysis job
                if rec.analysis_job_id and rec.analysis_status in ("pending", "processing"):
                    try:
                        job = client.get_job(rec.analysis_job_id)
                        if job.status == "completed":
                            db_client.update_recording_analysis(
                                hash_prefix=rec.hash_prefix,
                                duration_seconds=job.result.duration_seconds if job.result else None,
                                tempo_bpm=job.result.tempo_bpm if job.result else None,
                                musical_key=job.result.musical_key if job.result else None,
                                musical_mode=job.result.musical_mode if job.result else None,
                                key_confidence=job.result.key_confidence if job.result else None,
                                loudness_db=job.result.loudness_db if job.result else None,
                                beats=json.dumps(job.result.beats) if job.result and job.result.beats else None,
                                downbeats=json.dumps(job.result.downbeats) if job.result and job.result.downbeats else None,
                                sections=json.dumps(job.result.sections) if job.result and job.result.sections else None,
                                embeddings_shape=json.dumps(job.result.embeddings_shape) if job.result and job.result.embeddings_shape else None,
                                r2_stems_url=job.result.stems_url if job.result else None,
                            )
                            synced_count += 1
                        elif job.status == "failed":
                            db_client.update_recording_status(
                                hash_prefix=rec.hash_prefix,
                                analysis_status="failed",
                            )
                            synced_count += 1
                    except AnalysisServiceError as e:
                        console.print(f"[dim]Could not sync analysis {rec.analysis_job_id}: {e}[/dim]")
                        failed_count += 1

                # Sync LRC job
                if rec.lrc_job_id and rec.lrc_status in ("pending", "processing"):
                    try:
                        job = client.get_job(rec.lrc_job_id)
                        if job.status == "completed":
                            if job.result and job.result.lrc_url:
                                db_client.update_recording_lrc(
                                    hash_prefix=rec.hash_prefix,
                                    r2_lrc_url=job.result.lrc_url,
                                )
                                synced_count += 1
                        elif job.status == "failed":
                            db_client.update_recording_status(
                                hash_prefix=rec.hash_prefix,
                                lrc_status="failed",
                            )
                            synced_count += 1
                    except AnalysisServiceError as e:
                        console.print(f"[dim]Could not sync LRC {rec.lrc_job_id}: {e}[/dim]")
                        failed_count += 1

            if synced_count > 0:
                console.print(f"[green]Synced {synced_count} job(s)[/green]")
            if failed_count > 0:
                console.print(f"[yellow]Failed to sync {failed_count} job(s)[/yellow]")
            console.print("")

    # List pending recordings
    cursor = db_client.connection.cursor()
    cursor.execute(
        """
        SELECT r.*, s.title as song_title
        FROM recordings r
        LEFT JOIN songs s ON r.song_id = s.id
        WHERE r.analysis_status != 'completed' OR r.lrc_status != 'completed'
        ORDER BY r.imported_at DESC
        """
    )

    rows = cursor.fetchall()
    if not rows:
        console.print("[green]All recordings are fully processed.[/green]")
        return

    table = Table(title=f"Pending Recordings ({len(rows)} total)")
    table.add_column("Song Title", style="green")
    table.add_column("Analysis", style="magenta")
    table.add_column("Analysis Job", style="dim", no_wrap=True)
    table.add_column("LRC", style="blue")
    table.add_column("LRC Job", style="dim", no_wrap=True)
    table.add_column("Song ID", style="dim", no_wrap=True)
    table.add_column("Hash", style="dim", no_wrap=True)

    for row in rows:
        song_id = row[2] if row[2] else "-"
        hash_prefix = row[1]
        song_title = row[25] if row[25] else "-"
        analysis_status = row[19]
        analysis_job_id = row[20] if row[20] else "-"
        lrc_status = row[21]
        lrc_job_id = row[22] if row[22] else "-"

        table.add_row(
            song_title,
            _colorize_status(analysis_status),
            analysis_job_id,
            _colorize_status(lrc_status),
            lrc_job_id,
            song_id,
            hash_prefix,
        )

    console.print(table)


def _update_recording_status_force(
    db_client: DatabaseClient,
    rec: Any,
    status: str,
    force_url: Optional[str],
    console: Console,
) -> None:
    """Force update a recording's status."""
    from stream_of_worship.admin.db.models import Recording

    if not isinstance(rec, Recording):
        console.print("[red]Invalid recording object[/red]")
        raise typer.Exit(1)

    # Determine if this is an analysis or LRC job based on which job_id exists
    job_type = "unknown"
    if rec.analysis_job_id and not rec.lrc_job_id:
        job_type = "analysis"
    elif rec.lrc_job_id and not rec.analysis_job_id:
        job_type = "lrc"
    elif rec.analysis_job_id and rec.lrc_job_id:
        # Both exist - need to ask user or infer from context
        # For now, update both if status is the same
        job_type = "both"

    if job_type in ("analysis", "both"):
        if status == "completed":
            if force_url:
                db_client.update_recording_analysis(
                    hash_prefix=rec.hash_prefix,
                    r2_stems_url=force_url,
                )
            else:
                # Just update status without URL
                db_client.update_recording_status(
                    hash_prefix=rec.hash_prefix,
                    analysis_status=status,
                )
        else:
            db_client.update_recording_status(
                hash_prefix=rec.hash_prefix,
                analysis_status=status,
            )
        console.print(f"[green]Updated analysis status to '{status}' for {rec.hash_prefix}[/green]")

    if job_type in ("lrc", "both"):
        if status == "completed" and force_url:
            db_client.update_recording_lrc(
                hash_prefix=rec.hash_prefix,
                r2_lrc_url=force_url,
            )
        else:
            db_client.update_recording_status(
                hash_prefix=rec.hash_prefix,
                lrc_status=status,
            )
        console.print(f"[green]Updated LRC status to '{status}' for {rec.hash_prefix}[/green]")


def _force_sync_all_pending(
    db_client: DatabaseClient,
    status: str,
    force_url: Optional[str],
    console: Console,
) -> None:
    """Force update all pending recordings."""
    # Get all non-completed recordings
    cursor = db_client.connection.cursor()
    cursor.execute(
        """
        SELECT * FROM recordings
        WHERE analysis_status IN ('pending', 'processing', 'failed')
           OR lrc_status IN ('pending', 'processing', 'failed')
        """
    )
    rows = cursor.fetchall()

    if not rows:
        console.print("[green]No pending recordings to update.[/green]")
        return

    from stream_of_worship.admin.db.models import Recording

    updated = 0
    for row in rows:
        rec = Recording.from_row(row)

        # Update analysis if pending/processing/failed
        if rec.analysis_status in ("pending", "processing", "failed"):
            if status == "completed" and force_url:
                db_client.update_recording_analysis(
                    hash_prefix=rec.hash_prefix,
                    r2_stems_url=force_url,
                )
            else:
                db_client.update_recording_status(
                    hash_prefix=rec.hash_prefix,
                    analysis_status=status,
                )
            updated += 1

        # Update LRC if pending/processing/failed
        if rec.lrc_status in ("pending", "processing", "failed"):
            if status == "completed" and force_url:
                db_client.update_recording_lrc(
                    hash_prefix=rec.hash_prefix,
                    r2_lrc_url=force_url,
                )
            else:
                db_client.update_recording_status(
                    hash_prefix=rec.hash_prefix,
                    lrc_status=status,
                )
            updated += 1

    console.print(f"[green]Force updated {updated} recording(s) to status '{status}'[/green]")
    if force_url:
        console.print(f"[dim]URL set: {force_url}[/dim]")


@app.command("view-lrc")
def view_lrc(
    song_id: str = typer.Argument(..., help="Song ID to view LRC for"),
    raw: bool = typer.Option(False, "--raw", "-r", help="Display raw LRC file"),
    no_timestamps: bool = typer.Option(
        False, "--no-timestamps", "-t", help="Show lyrics text only"
    ),
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config file"
    ),
) -> None:
    """View LRC (synchronized lyrics) contents for a recording."""
    # Load config
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        console.print(
            "[red]Config file not found. Please create it using 'sow-admin config init'[/red]"
        )
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error loading config: {e}[/red]")
        raise typer.Exit(1)

    # Validate database exists
    if not config.db_path.exists():
        console.print(f"[red]Database not found at {config.db_path}[/red]")
        console.print("[yellow]Run 'sow-admin catalog init' first[/yellow]")
        raise typer.Exit(1)

    # Get database client
    db_client = DatabaseClient(config.db_path)

    # Get recording
    recording = db_client.get_recording_by_song_id(song_id)
    if not recording:
        console.print(f"[red]No recording found for song ID: {song_id}[/red]")
        raise typer.Exit(1)

    # Get song for display
    song = db_client.get_song(recording.song_id)
    if not song:
        console.print(f"[red]No song found for ID: {recording.song_id}[/red]")
        raise typer.Exit(1)

    # Check LRC status
    if recording.lrc_status == "pending":
        console.print(f"[yellow]LRC not yet generated for {song_id}[/yellow]")
        console.print(f"[dim]Run 'sow-admin audio lrc {song_id}' to generate LRC[/dim]")
        raise typer.Exit(1)
    elif recording.lrc_status == "processing":
        console.print(f"[yellow]LRC generation in progress for {song_id}[/yellow]")
        if recording.lrc_job_id:
            console.print(f"[dim]Job ID: {recording.lrc_job_id}[/dim]")
        console.print("[dim]Check status with 'sow-admin audio status'[/dim]")
        raise typer.Exit(1)
    elif recording.lrc_status == "failed":
        console.print(f"[red]LRC generation failed for {song_id}[/red]")
        console.print(f"[dim]Retry with 'sow-admin audio lrc {song_id} --force'[/dim]")
        raise typer.Exit(1)
    elif recording.lrc_status != "completed":
        console.print(f"[red]Unknown LRC status: {recording.lrc_status}[/red]")
        raise typer.Exit(1)

    # Check R2 URL exists
    if not recording.r2_lrc_url:
        console.print(f"[red]LRC marked as completed but no R2 URL found[/red]")
        console.print("[dim]This is a data integrity issue. Please contact support.[/dim]")
        raise typer.Exit(1)

    # Initialize R2 client
    r2_client = R2Client(
        bucket=config.r2_bucket,
        endpoint_url=config.r2_endpoint_url,
        region=config.r2_region,
    )

    # Parse S3 URL to get key
    try:
        _, s3_key = R2Client.parse_s3_url(recording.r2_lrc_url)
    except ValueError as e:
        console.print(f"[red]Error parsing R2 URL: {e}[/red]")
        raise typer.Exit(1)

    # Download LRC file to temp location
    temp_file = None
    try:
        temp_file = tempfile.NamedTemporaryFile(mode="w+", suffix=".lrc", delete=False)
        temp_path = Path(temp_file.name)
        temp_file.close()

        # Download from R2
        try:
            r2_client.download_file(s3_key, temp_path)
        except ClientError as e:
            console.print(f"[red]Error downloading LRC from R2: {e}[/red]")
            raise typer.Exit(1)

        # Read content
        content = temp_path.read_text(encoding="utf-8")

        # Display based on mode
        if raw:
            # Raw mode: display with syntax highlighting
            syntax = Syntax(content, "lrc", theme="monokai", line_numbers=True)
            console.print(
                Panel.fit(
                    syntax, title=f"LRC Content: {song.title}", border_style="cyan"
                )
            )
        elif no_timestamps:
            # No timestamps mode: parse and display text only
            try:
                lrc_file = parse_lrc(content)
                for line in lrc_file.lines:
                    if line.text:  # Only show non-empty lines
                        console.print(line.text)
            except ValueError as e:
                console.print(f"[red]Error parsing LRC file: {e}[/red]")
                console.print("[dim]Try using --raw to view the file content[/dim]")
                raise typer.Exit(1)
        else:
            # Default mode: parse and display in table
            try:
                lrc_file = parse_lrc(content)

                # Display header info
                info_lines = [
                    f"[cyan]Song:[/cyan]     {song.title}",
                    f"[cyan]Song ID:[/cyan]  {song_id}",
                    f"[cyan]Hash:[/cyan]     {recording.hash_prefix}",
                    f"[cyan]Lines:[/cyan]    {lrc_file.line_count}",
                    f"[cyan]Duration:[/cyan] {format_duration(lrc_file.duration_seconds)}",
                ]
                info_panel = Panel(
                    "\n".join(info_lines),
                    title="LRC File Info",
                    border_style="cyan",
                )
                console.print(info_panel)
                console.print()

                # Display lyrics table
                table = Table(title="Synchronized Lyrics", show_header=True, header_style="bold")
                table.add_column("Time", style="dim", width=12)
                table.add_column("Lyrics")

                for line in lrc_file.lines:
                    table.add_row(line.raw_timestamp, line.text)

                console.print(table)

            except ValueError as e:
                console.print(f"[red]Error parsing LRC file: {e}[/red]")
                console.print("[dim]Try using --raw to view the file content[/dim]")
                raise typer.Exit(1)

    finally:
        # Cleanup temp file
        if temp_file and temp_path.exists():
            temp_path.unlink()


@app.command("cache")
def cache_assets(
    song_id: str = typer.Argument(..., help="Song ID to cache assets for"),
    audio: bool = typer.Option(
        True, "--audio/--no-audio", help="Download main audio file"
    ),
    stems: bool = typer.Option(
        True, "--stems/--no-stems", help="Download stem files (vocals, drums, bass, other)"
    ),
    lrc: bool = typer.Option(
        True, "--lrc/--no-lrc", help="Download LRC lyrics file"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Re-download even if files exist"
    ),
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config file"
    ),
) -> None:
    """Download song assets from R2 to local cache.

    Downloads audio, stems, and LRC files from R2 to the local cache directory
    for offline use. This is useful for tools like the Whisper test driver
    that need local access to audio files.
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

    # Look up recording by song_id
    recording = db_client.get_recording_by_song_id(song_id)
    if not recording:
        console.print(f"[red]No recording found for song: {song_id}[/red]")
        raise typer.Exit(1)

    # Get song info for display
    song = db_client.get_song(song_id)
    song_title = song.title if song else "Unknown"
    hash_prefix = recording.hash_prefix

    # Initialize R2 client
    try:
        r2_client = R2Client(
            bucket=config.r2_bucket,
            endpoint_url=config.r2_endpoint_url,
            region=config.r2_region,
        )
    except ValueError as e:
        console.print(f"[red]R2 configuration error: {e}[/red]")
        raise typer.Exit(1)

    # Import AssetCache here to avoid circular imports
    from stream_of_worship.app.services.asset_cache import AssetCache

    # Use the app cache directory: ~/.config/sow-app/cache
    cache_dir = Path.home() / ".config" / "sow-app" / "cache"
    cache = AssetCache(cache_dir=cache_dir, r2_client=r2_client)

    console.print(f"[cyan]Caching assets for: {song_title}[/cyan]")
    console.print(f"[dim]Hash prefix: {hash_prefix}[/dim]")
    console.print()

    downloaded = []
    skipped = []
    failed = []

    # Download audio
    if audio:
        audio_path = cache.get_audio_path(hash_prefix)
        if audio_path.exists() and not force:
            skipped.append(f"Audio: {audio_path}")
        else:
            console.print("[cyan]Downloading audio...[/cyan]")
            path = cache.download_audio(hash_prefix, force=force)
            if path:
                size_mb = path.stat().st_size / (1024 * 1024)
                downloaded.append(f"Audio: {path.name} ({size_mb:.2f} MB)")
                console.print(f"[green]  ✓ {path.name} ({size_mb:.2f} MB)[/green]")
            else:
                failed.append("Audio")
                console.print("[red]  ✗ Failed to download audio[/red]")

    # Download stems
    if stems:
        console.print("[cyan]Downloading stems...[/cyan]")
        stem_names = ["vocals", "drums", "bass", "other"]
        for stem_name in stem_names:
            stem_path = cache.get_stem_path(hash_prefix, stem_name)
            if stem_path.exists() and not force:
                skipped.append(f"Stem '{stem_name}': {stem_path}")
            else:
                path = cache.download_stem(hash_prefix, stem_name, force=force)
                if path:
                    size_mb = path.stat().st_size / (1024 * 1024)
                    downloaded.append(f"Stem '{stem_name}': {path.name} ({size_mb:.2f} MB)")
                    console.print(f"[green]  ✓ {stem_name}.wav ({size_mb:.2f} MB)[/green]")
                else:
                    # Stems might not exist for all recordings
                    console.print(f"[dim]  - {stem_name}.wav (not available)[/dim]")

    # Download LRC
    if lrc:
        lrc_path = cache.get_lrc_path(hash_prefix)
        if lrc_path.exists() and not force:
            skipped.append(f"LRC: {lrc_path}")
        else:
            console.print("[cyan]Downloading LRC...[/cyan]")
            # Check if LRC exists in R2
            if recording.r2_lrc_url:
                path = cache.download_lrc(hash_prefix, force=force)
                if path:
                    downloaded.append(f"LRC: {path.name}")
                    console.print(f"[green]  ✓ {path.name}[/green]")
                else:
                    failed.append("LRC")
                    console.print("[red]  ✗ Failed to download LRC[/red]")
            else:
                console.print("[yellow]  ! No LRC available (run 'sow-admin audio lrc' first)[/yellow]")

    # Summary
    console.print()
    console.print("[bold]Cache Summary:[/bold]")
    if downloaded:
        console.print(f"[green]Downloaded: {len(downloaded)} file(s)[/green]")
        for item in downloaded:
            console.print(f"  [green]✓[/green] {item}")
    if skipped:
        console.print(f"[dim]Skipped (already cached): {len(skipped)} file(s)[/dim]")
    if failed:
        console.print(f"[red]Failed: {len(failed)} file(s)[/red]")
        for item in failed:
            console.print(f"  [red]✗[/red] {item}")

    # Show cache location
    cache_dir = cache.cache_dir / hash_prefix
    console.print()
    console.print(f"[dim]Cache location: {cache_dir}[/dim]")


@app.command("upload-lrc")
def upload_lrc(
    song_id: str = typer.Argument(..., help="Song ID to upload LRC for"),
    lrc_file: Path = typer.Argument(..., help="Path to LRC file", exists=True),
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config file"
    ),
) -> None:
    """Upload a manually created LRC file to R2.

    Use this when:
    1. The LRC generation service failed
    2. You have a manually crafted/corrected LRC file
    3. You want to override an existing LRC file

    The LRC file format will be validated before upload.
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

    # Look up recording by song_id
    recording = db_client.get_recording_by_song_id(song_id)
    if not recording:
        console.print(
            f"[red]No recording found for song: {song_id}. "
            f"Run 'sow-admin audio download {song_id}' first.[/red]"
        )
        raise typer.Exit(1)

    # Get song info for display
    song = db_client.get_song(song_id)
    song_title = song.title if song else "Unknown"

    # Validate LRC file format
    console.print(f"[cyan]Validating LRC file: {lrc_file.name}[/cyan]")
    try:
        content = lrc_file.read_text(encoding="utf-8")
        lrc_data = parse_lrc(content)
    except ValueError as e:
        console.print(f"[red]Invalid LRC file: {e}[/red]")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error reading LRC file: {e}[/red]")
        raise typer.Exit(1)

    # Display LRC info preview
    info_lines = [
        f"[cyan]Song ID:[/cyan]     {song_id}",
        f"[cyan]Song Title:[/cyan]  {song_title}",
        f"[cyan]Hash Prefix:[/cyan] {recording.hash_prefix}",
        f"[cyan]LRC File:[/cyan]    {lrc_file}",
        f"[cyan]Line Count:[/cyan]  {lrc_data.line_count}",
        f"[cyan]Duration:[/cyan]    {format_duration(lrc_data.duration_seconds)}",
    ]

    # Show existing LRC status
    if recording.r2_lrc_url:
        info_lines.append("")
        info_lines.append(f"[yellow]Existing LRC will be: {recording.r2_lrc_url}[/yellow]")
    elif recording.lrc_status == "processing":
        info_lines.append("")
        info_lines.append(f"[yellow]Existing LRC job: {recording.lrc_job_id}[/yellow]")
    elif recording.lrc_status == "failed":
        info_lines.append("")
        info_lines.append(f"[yellow]Previous LRC generation failed[/yellow]")

    console.print(Panel.fit("\n".join(info_lines), title="LRC Upload Preview", border_style="cyan"))

    # Confirm upload
    if not _prompt_confirmation("Upload this LRC file?"):
        console.print("[yellow]Upload cancelled.[/yellow]")
        raise typer.Exit(0)

    # Initialize R2 client
    try:
        r2_client = R2Client(
            bucket=config.r2_bucket,
            endpoint_url=config.r2_endpoint_url,
            region=config.r2_region,
        )
    except ValueError as e:
        console.print(f"[red]R2 configuration error: {e}[/red]")
        raise typer.Exit(1)

    # Upload to R2
    console.print(f"[cyan]Uploading LRC to R2...[/cyan]")
    try:
        r2_url = r2_client.upload_lrc(lrc_file, recording.hash_prefix)
        console.print(f"[green]Uploaded: {r2_url}[/green]")
    except Exception as e:
        console.print(f"[red]Upload failed: {e}[/red]")
        raise typer.Exit(1)

    # Update database
    db_client.update_recording_lrc(
        hash_prefix=recording.hash_prefix,
        r2_lrc_url=r2_url,
    )

    # Display success summary
    console.print()
    console.print(Panel.fit(
        f"[green]LRC uploaded successfully![/green]\n\n"
        f"[cyan]Song:[/cyan] {song_title}\n"
        f"[cyan]Lines:[/cyan] {lrc_data.line_count}\n"
        f"[cyan]Duration:[/cyan] {format_duration(lrc_data.duration_seconds)}\n"
        f"[cyan]R2 URL:[/cyan] {r2_url}",
        title="Upload Complete",
        border_style="green",
    ))
