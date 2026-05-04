"""Audio commands for sow-admin.

Provides CLI commands for downloading audio from YouTube, listing
recordings, and viewing recording details.
"""

import json
import select
import sys
import tempfile
import termios
import time
import tty
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

import typer
from botocore.exceptions import ClientError
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table

from stream_of_worship.admin.commands.catalog import _extract_series_number
from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Recording, Song
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
        lines.append(
            f"[yellow bold]⚠ Warning: Video exceeds {threshold // 60} minutes[/yellow bold]"
        )

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


def _colorize_visibility(visibility: Optional[str]) -> str:
    """Get Rich markup for visibility status.

    Args:
        visibility: Visibility status string

    Returns:
        Rich markup string with visual indicator
    """
    if visibility == "published":
        return "[green]● published[/green]"
    elif visibility == "review":
        return "[yellow]◐ review[/yellow]"
    elif visibility == "hold":
        return "[dim]○ hold[/dim]"
    else:
        return "[dim]- none[/dim]"


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


def _submit_lrc_single(
    song_id: str,
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    force: bool,
    whisper_model: str,
    language: str,
    no_vocals: bool,
    no_youtube: bool,
    no_whisper_cache: bool,
    no_qwen3: bool,
    wait: bool,
    console: Console,
) -> None:
    """Submit LRC for a single recording (original behavior with wait support)."""
    # Look up recording by song_id
    recording = db_client.get_recording_by_song_id(song_id)
    if not recording:
        console.print(f"[red]No recording found for {song_id}.[/red]")
        raise typer.Exit(1)

    # Look up song for lyrics
    song = db_client.get_song(song_id)
    if not song or not song.lyrics_raw:
        console.print(f"[red]No lyrics found for song {song_id}.[/red]")
        raise typer.Exit(1)

    # Validate r2_audio_url exists
    if not recording.r2_audio_url:
        console.print(f"[red]Recording {recording.hash_prefix} has no audio URL.[/red]")
        raise typer.Exit(1)

    # Check if already has LRC
    if recording.lrc_status == "completed" and not force:
        console.print(
            f"[yellow]Recording {recording.hash_prefix} already has LRC. "
            f"Use --force to re-generate.[/yellow]"
        )
        raise typer.Exit(0)

    # Check if already processing
    skip_submission = False
    job_id = None
    if recording.lrc_status == "processing" and recording.lrc_job_id and not force:
        if not wait:
            console.print(
                f"[yellow]LRC generation already in progress for "
                f"{recording.hash_prefix} (job: {recording.lrc_job_id})[/yellow]"
            )
            raise typer.Exit(0)
        job_id = recording.lrc_job_id
        skip_submission = True

    # Submit LRC (unless we're polling an existing job)
    if not skip_submission:
        youtube_url = "" if no_youtube else (recording.youtube_url or "")
        try:
            job = analysis_client.submit_lrc(
                audio_url=recording.r2_audio_url,
                content_hash=recording.content_hash,
                lyrics_text=song.lyrics_raw,
                whisper_model=whisper_model,
                language=language,
                use_vocals_stem=not no_vocals,
                force=force,
                force_whisper=no_whisper_cache,
                youtube_url=youtube_url,
                use_qwen3=not no_qwen3,
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
            task = progress.add_task("Generating LRC...", total=100, stage="", completed=0)

            def update_progress(job_info: JobInfo) -> None:
                pct = int(job_info.progress * 100)
                progress.update(task, completed=pct, stage=f"[{job_info.stage}]")

            try:
                final_job = analysis_client.wait_for_completion(
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


def _submit_lrc_batch(
    song_ids: list[str],
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    force: bool,
    whisper_model: str,
    language: str,
    no_vocals: bool,
    no_youtube: bool,
    no_whisper_cache: bool,
    no_qwen3: bool,
    console: Console,
) -> None:
    """Submit LRC for multiple recordings (batch mode, no wait)."""
    submitted = 0
    skipped = 0
    errors = 0

    for i, song_id in enumerate(song_ids, 1):
        console.print(f"[{i}/{len(song_ids)}] Processing {song_id}...")

        # Look up recording by song_id
        recording = db_client.get_recording_by_song_id(song_id)
        if not recording:
            console.print("  [red]No recording found[/red]")
            errors += 1
            continue

        # Look up song for lyrics
        song = db_client.get_song(song_id)
        if not song or not song.lyrics_raw:
            console.print("  [red]No lyrics found[/red]")
            errors += 1
            continue

        # Validate r2_audio_url exists
        if not recording.r2_audio_url:
            console.print("  [red]No audio URL[/red]")
            errors += 1
            continue

        # Check if already has LRC
        if recording.lrc_status == "completed" and not force:
            console.print("  [yellow]Already has LRC (skipped)[/yellow]")
            skipped += 1
            continue

        # Check if already processing
        if recording.lrc_status == "processing" and recording.lrc_job_id and not force:
            console.print("  [yellow]Already in progress (skipped)[/yellow]")
            skipped += 1
            continue

        # Submit LRC
        youtube_url = "" if no_youtube else (recording.youtube_url or "")
        try:
            job = analysis_client.submit_lrc(
                audio_url=recording.r2_audio_url,
                content_hash=recording.content_hash,
                lyrics_text=song.lyrics_raw,
                whisper_model=whisper_model,
                language=language,
                use_vocals_stem=not no_vocals,
                force=force,
                force_whisper=no_whisper_cache,
                youtube_url=youtube_url,
                use_qwen3=not no_qwen3,
            )

            # Update DB
            db_client.update_recording_status(
                hash_prefix=recording.hash_prefix,
                lrc_status="processing",
                lrc_job_id=job.job_id,
            )

            console.print(f"  [green]Submitted (job: {job.job_id})[/green]")
            submitted += 1

        except AnalysisServiceError as e:
            console.print(f"  [red]Failed to submit: {e}[/red]")
            errors += 1
        except Exception as e:
            console.print(f"  [red]Unexpected error: {e}[/red]")
            errors += 1

    # Summary
    console.print("")
    console.print("[cyan]Batch Summary:[/cyan]")
    console.print(f"  Submitted: {submitted}")
    console.print(f"  Skipped: {skipped}")
    console.print(f"  Errors: {errors}")
    console.print(f"  Total: {len(song_ids)}")


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
    no_youtube: bool = False,
    no_whisper_cache: bool = False,
    use_qwen3: bool = True,
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
        no_youtube: Skip YouTube transcript, use Whisper directly
        no_whisper_cache: Bypass Whisper transcription cache
        use_qwen3: Use Qwen3 for timestamp refinement (Whisper path only)

    Returns:
        Job ID if submission succeeded, None otherwise
    """
    # Look up song for lyrics
    song = db_client.get_song(song_id)
    if not song or not song.lyrics_raw:
        console.print(
            f"[yellow]⚠ No lyrics found for song {song_id}, skipping LRC generation[/yellow]"
        )
        return None

    youtube_url = "" if no_youtube else (recording.youtube_url or "")

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
            force_whisper=no_whisper_cache,
            youtube_url=youtube_url,
            use_qwen3=use_qwen3,
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
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Preview without downloading"),
    url: Optional[str] = typer.Option(None, "--url", "-u", help="Direct YouTube URL (skip search)"),
    skip_confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    force: bool = typer.Option(
        False, "--force", "-f", help="Replace existing recording if it exists"
    ),
    analyze: bool = typer.Option(
        False, "--analyze", "-a", help="Submit for analysis after download"
    ),
    lrc: bool = typer.Option(False, "--lrc", "-l", help="Submit for LRC generation after download"),
    all: bool = typer.Option(
        False, "--all", "-A", help="Submit for both analysis and LRC after download"
    ),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
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
            use_qwen3=True,
        )


@app.command("delete")
def delete_recording(
    song_id: str = typer.Argument(..., help="Song ID to delete recording for"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
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
        (
            f"[cyan]Size:[/cyan] {_format_size_mb(recording.file_size_bytes)}"
            if recording.file_size_bytes
            else "[cyan]Size:[/cyan] -- MB"
        ),
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
        help="Filter by analysis status (pending|processing|completed|failed)",
    ),
    visibility: Optional[str] = typer.Option(
        None,
        "--visibility",
        "-v",
        help="Filter by visibility status (published|review|hold)",
    ),
    album: Optional[str] = typer.Option(
        None,
        "--album",
        "-a",
        help="Filter by album name",
    ),
    lrc: Optional[str] = typer.Option(
        None,
        "--lrc",
        help="Filter by LRC status (pending|processing|completed|failed|incomplete)",
    ),
    sort: str = typer.Option(
        "album",
        "--sort",
        "-s",
        help="Sort order (album|series|title|imported)",
    ),
    format: str = typer.Option("table", "--format", "-f", help="Output format (table|ids)"),
    limit: Optional[int] = typer.Option(None, "--limit", "-l", help="Maximum number of results"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """List audio recordings.

    Display recordings from the database with optional status filtering.
    Use ``--sort`` to change sort order (default: album). Use ``--sort series``
    to sort by album series number (e.g. 敬拜讚美15 → 15). Use ``--album`` to
    filter by album name. Use ``--format ids`` for one song ID per line (pipeable).
    """
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    if not config.db_path.exists():
        console.print(f"[red]Database not found at {config.db_path}[/red]")
        raise typer.Exit(1)

    # Validate visibility filter
    if visibility:
        valid_visibilities = {"published", "review", "hold"}
        if visibility not in valid_visibilities:
            console.print(
                f"[red]Invalid visibility: {visibility}. Must be one of: {', '.join(valid_visibilities)}[/red]"
            )
            raise typer.Exit(1)

    # Validate LRC status filter
    if lrc:
        valid_lrc_statuses = {"pending", "processing", "completed", "failed", "incomplete"}
        if lrc not in valid_lrc_statuses:
            console.print(
                f"[red]Invalid LRC status: {lrc}. Must be one of: {', '.join(valid_lrc_statuses)}[/red]"
            )
            raise typer.Exit(1)

    # Validate sort option
    valid_sorts = {"album", "series", "title", "imported"}
    if sort not in valid_sorts:
        console.print(
            f"[red]Invalid sort option: {sort}. Choose from: {', '.join(sorted(valid_sorts))}[/red]"
        )
        raise typer.Exit(1)

    db_client = DatabaseClient(config.db_path)
    # Use the efficient method with JOIN to avoid N+1 queries
    # Album filtering and sorting are now handled at the database layer
    enriched = db_client.list_recordings_with_songs(
        status=status,
        visibility=visibility,
        lrc_status=lrc,
        album=album,
        sort_by=sort,
        limit=limit,
    )

    if not enriched:
        console.print("[yellow]No recordings found.[/yellow]")
        return

    # For "series" sort, we need to re-sort because SQLite can't extract the series number
    # For "album" and "title", DB sort is sufficient but we do Python sort as fallback
    if sort == "series":
        enriched.sort(key=lambda t: (_extract_series_number(t[3] or ""), t[2] or "", t[1] or ""))
    elif sort == "album":
        # DB already sorted by album, title - but re-sort for consistency with null handling
        enriched.sort(key=lambda t: (t[2] or "", t[1] or ""))
    elif sort == "title":
        enriched.sort(key=lambda t: t[1] or "")
    # "imported" — already sorted by imported_at DESC from DB

    if format == "ids":
        for rec, _title, _album, _series in enriched:
            console.print(rec.song_id if rec.song_id else rec.hash_prefix)
    else:
        # Build title with filters
        filter_parts = []
        if status:
            filter_parts.append(f"status={status}")
        if visibility:
            filter_parts.append(f"visibility={visibility}")
        if album:
            filter_parts.append(f"album={album}")
        if lrc:
            filter_parts.append(f"lrc={lrc}")
        filter_str = f" ({', '.join(filter_parts)})" if filter_parts else ""
        table = Table(title=f"Recordings ({len(enriched)} total){filter_str}")
        table.add_column("Album", style="yellow")
        table.add_column("Song Title", style="green")
        table.add_column("Visibility", justify="center")
        table.add_column("Size", style="magenta", justify="right")
        table.add_column("LRC", justify="center")
        table.add_column("Duration", style="cyan", no_wrap=True)
        table.add_column("Song ID", style="dim", no_wrap=True)
        table.add_column("Filename", style="yellow")
        table.add_column("Hash Prefix", style="dim", no_wrap=True)

        for rec, song_title, album_name, _album_series in enriched:
            song_id = rec.song_id or "-"
            size_str = _format_size_mb(rec.file_size_bytes) if rec.file_size_bytes else "-- MB"

            # Visibility status with visual indicator
            visibility_text = _colorize_visibility(rec.visibility_status)

            # LRC status
            lrc_text = _colorize_status(rec.lrc_status)

            # Format duration if available (from analysis results)
            duration_str = (
                _format_duration(rec.duration_seconds) if rec.duration_seconds else "--:--"
            )

            table.add_row(
                album_name or "-",
                song_title or "-",
                visibility_text,
                size_str,
                lrc_text,
                duration_str,
                song_id,
                rec.original_filename,
                rec.hash_prefix,
            )

        console.print(table)


@app.command("show")
def show_recording(
    song_id: str = typer.Argument(..., help="Song ID to show recording for"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
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
            f"[red]No recording found for {song_id}. Run 'sow-admin audio download {song_id}'[/red]"
        )
        raise typer.Exit(1)

    # Get song info
    song = db_client.get_song(song_id)

    info_lines = [
        f"[cyan]Song ID:[/cyan] {song_id}",
    ]

    if song:
        info_lines.append(f"[cyan]Song Title:[/cyan] {song.title}")

    info_lines.extend(
        [
            f"[cyan]Hash Prefix:[/cyan] {recording.hash_prefix}",
            f"[cyan]Full Hash:[/cyan] {recording.content_hash}",
        ]
    )

    info_lines.extend(
        [
            f"[cyan]Filename:[/cyan] {recording.original_filename}",
            f"[cyan]Size:[/cyan] {_format_size_mb(recording.file_size_bytes)}",
            f"[cyan]Duration:[/cyan] {_format_duration(recording.duration_seconds)}",
            f"[cyan]Imported:[/cyan] {recording.imported_at}",
        ]
    )

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
    info_lines.append(
        f"[cyan]Visibility:[/cyan] {_colorize_visibility(recording.visibility_status)}"
    )

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

    console.print(
        Panel.fit(
            "\n".join(info_lines),
            title=f"Recording: {song_id}",
            border_style="green",
        )
    )


@app.command("set-visibility")
def set_visibility(
    song_id: str = typer.Argument(..., help="Song ID to update visibility for"),
    status: str = typer.Option(
        ..., "--status", "-s", help="Visibility status (published|review|hold)"
    ),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Set the visibility status for a recording.

    Controls whether a recording appears in the User App browse list.
    - published: Visible to users (auto-set when LRC completes)
    - review: Hidden, needs manual review
    - hold: Hidden, on hold
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

    # Validate status
    valid_statuses = {"published", "review", "hold"}
    if status not in valid_statuses:
        console.print(
            f"[red]Invalid status: {status}. Must be one of: {', '.join(valid_statuses)}[/red]"
        )
        raise typer.Exit(1)

    # Update visibility
    try:
        db_client.update_recording_visibility(recording.hash_prefix, status)
        console.print(
            f"[green]Updated visibility for {song_id}: {_colorize_visibility(status)}[/green]"
        )
    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@app.command("analyze")
def analyze_recording(
    song_id: str = typer.Argument(..., help="Song ID to analyze"),
    force: bool = typer.Option(False, "--force", "-f", help="Force re-analysis"),
    no_stems: bool = typer.Option(False, "--no-stems", help="Skip stem separation"),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait for analysis to complete"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
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
        console.print(f"[red]Recording {recording.hash_prefix} has no audio URL.[/red]")
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
            task = progress.add_task("Analyzing...", total=100, stage="", completed=0)

            def update_progress(job_info: JobInfo) -> None:
                pct = int(job_info.progress * 100)
                progress.update(task, completed=pct, stage=f"[{job_info.stage}]")

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
                downbeats=json.dumps(result.downbeats) if result.downbeats else None,
                sections=json.dumps(result.sections) if result.sections else None,
                embeddings_shape=(
                    json.dumps(result.embeddings_shape) if result.embeddings_shape else None
                ),
                r2_stems_url=result.stems_url,
            )

        console.print(f"[green]Analysis completed for {song_id}[/green]")
        if final_job.result:
            if final_job.result.tempo_bpm:
                console.print(f"  Tempo: {final_job.result.tempo_bpm:.1f} BPM")
            if final_job.result.musical_key:
                console.print(f"  Key: {final_job.result.musical_key}")
            if final_job.result.duration_seconds:
                console.print(f"  Duration: {_format_duration(final_job.result.duration_seconds)}")


@app.command("lrc")
def lrc_recording(
    song_id: Optional[str] = typer.Argument(None, help="Song ID to generate LRC for"),
    force: bool = typer.Option(False, "--force", "-f", help="Force re-generation"),
    stdin: bool = typer.Option(False, "--stdin", help="Read song IDs from stdin (one per line)"),
    whisper_model: str = typer.Option("large-v3", "--model", "-m", help="Whisper model to use"),
    language: str = typer.Option("zh", "--lang", help="Language hint"),
    no_vocals: bool = typer.Option(False, "--no-vocals", help="Don't use vocals stem"),
    no_youtube: bool = typer.Option(
        False, "--no-youtube", help="Skip YouTube transcript, use Whisper directly"
    ),
    no_whisper_cache: bool = typer.Option(
        False, "--no-whisper-cache", help="Bypass cached Whisper transcription, re-run Whisper"
    ),
    no_qwen3: bool = typer.Option(
        False, "--no-qwen3", help="Skip Qwen3 timestamp refinement (use LLM alignment only)"
    ),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait for LRC generation to complete"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Submit a recording for lyrics alignment (LRC generation).

    By default, tries YouTube transcript first (if a YouTube URL is stored),
    then falls back to Whisper transcription with Qwen3 timestamp refinement.
    Use --no-youtube to skip the YouTube path and use Whisper directly.
    Use --no-qwen3 to skip Qwen3 refinement and use LLM alignment only.

    For batch processing, pipe song IDs via stdin:
        sow-admin audio list --lrc incomplete --format ids | sow-admin audio lrc --stdin
    """
    # Validate mutually exclusive inputs
    if not song_id and not stdin:
        console.print("[red]Error: Either provide a song_id argument or use --stdin flag[/red]")
        raise typer.Exit(1)
    if song_id and stdin:
        console.print("[red]Error: Cannot use both song_id argument and --stdin flag[/red]")
        raise typer.Exit(1)
    if stdin and wait:
        console.print("[red]Error: --wait is not supported with --stdin (too many jobs)[/red]")
        raise typer.Exit(1)

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

    # Create analysis client (shared for batch mode)
    try:
        analysis_client = AnalysisClient(config.analysis_url)
    except ValueError as e:
        console.print(f"[red]Analysis service not configured: {e}[/red]")
        raise typer.Exit(1)

    # Collect song IDs to process
    if stdin:
        song_ids = _read_song_ids_from_stdin()
        if not song_ids:
            console.print("[yellow]No song IDs provided via stdin[/yellow]")
            raise typer.Exit(0)
    else:
        song_ids = [song_id]

    # Process all songs
    if len(song_ids) == 1:
        # Single song mode - original behavior with wait support
        _submit_lrc_single(
            song_id=song_ids[0],
            db_client=db_client,
            analysis_client=analysis_client,
            force=force,
            whisper_model=whisper_model,
            language=language,
            no_vocals=no_vocals,
            no_youtube=no_youtube,
            no_whisper_cache=no_whisper_cache,
            no_qwen3=no_qwen3,
            wait=wait,
            console=console,
        )
    else:
        # Batch mode - no wait support, process all
        _submit_lrc_batch(
            song_ids=song_ids,
            db_client=db_client,
            analysis_client=analysis_client,
            force=force,
            whisper_model=whisper_model,
            language=language,
            no_vocals=no_vocals,
            no_youtube=no_youtube,
            no_whisper_cache=no_whisper_cache,
            no_qwen3=no_qwen3,
            console=console,
        )


@app.command("vocal")
def vocal_clean(
    song_id: str = typer.Argument(..., help="Song ID or hash prefix"),
    vocal_model: str = typer.Option(
        "model_bs_roformer_ep_317_sdr_12.9755.ckpt",
        "--vocal-model",
        help="BS-Roformer model for vocal extraction",
    ),
    dereverb_model: str = typer.Option(
        "UVR-De-Echo-Normal.pth",
        "--dereverb-model",
        help="UVR model for reverb removal",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Re-generate if exists"),
    skip_upload: bool = typer.Option(False, "--skip-upload", help="Skip R2 upload (local only)"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Generate clean vocals (de-reverb) and upload to R2.

    Uses a two-stage extraction pipeline:
    1. BS-Roformer for vocal separation
    2. UVR-De-Echo for reverb removal

    Prerequisite: Run 'sow-admin audio download <song_id>' first.

    Requires the stem_separation extra:
        uv run --extra stem_separation sow-admin audio vocal ...
    """
    from stream_of_worship.app.services.asset_cache import AssetCache

    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    if not config.db_path.exists():
        console.print(f"[red]Database not found at {config.db_path}[/red]")
        raise typer.Exit(1)

    db_client = DatabaseClient(config.db_path)

    # Resolve song_id to recording (support both song_id and hash_prefix)
    recording = db_client.get_recording_by_song_id(song_id)
    if not recording:
        # Try as hash_prefix
        recording = db_client.get_recording_by_hash(song_id)
    if not recording:
        console.print(f"[red]No recording found for: {song_id}[/red]")
        raise typer.Exit(1)

    hash_prefix = recording.hash_prefix
    console.print(f"[cyan]Recording:[/cyan] {hash_prefix}")

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

    # Check if vocals_dry already exists in R2 (with fallback to legacy)
    vocals_dry_key = f"{hash_prefix}/stems/vocals_dry.flac"
    vocals_dry_exists = r2_client.file_exists(vocals_dry_key)

    if not vocals_dry_exists:
        # Fallback to legacy key for backward compatibility
        legacy_key = f"{hash_prefix}/stems/vocals_clean.flac"
        if r2_client.file_exists(legacy_key):
            vocals_dry_exists = True
            logger.info(f"Found legacy vocals stem: {legacy_key}")

    if not force and vocals_dry_exists:
        console.print(
            "[yellow]vocals_dry.flac already exists in R2. Use --force to re-generate.[/yellow]"
        )
        raise typer.Exit(0)

    # Initialize asset cache and get audio.mp3
    cache_dir = config.cache_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = AssetCache(cache_dir=cache_dir, r2_client=r2_client)
    audio_path = cache.download_audio(hash_prefix)
    if not audio_path or not audio_path.exists():
        console.print(
            f"[red]audio.mp3 not found. Run 'sow-admin audio download {song_id}' first.[/red]"
        )
        raise typer.Exit(1)

    console.print(f"[cyan]Input audio:[/cyan] {audio_path}")

    # Import audio_separator (requires stem_separation extra)
    try:
        from audio_separator.separator import Separator
    except ImportError:
        console.print(
            "[red]audio-separator not installed. "
            "Run: uv run --extra stem_separation sow-admin audio vocal ...[/red]"
        )
        raise typer.Exit(1)

    # Create output directory in cache
    output_dir = cache_dir / hash_prefix / "vocal_extraction"
    output_dir.mkdir(parents=True, exist_ok=True)

    import logging
    import os

    # Suppress tqdm bars from audio-separator (model download + processing)
    os.environ["TQDM_DISABLE"] = "1"

    # === STAGE 1: Vocal Extraction ===
    console.print(Rule("Stage 1: Vocal Extraction (BS-Roformer)"))

    stage1_dir = output_dir / "stage1_vocal_separation"
    stage1_dir.mkdir(exist_ok=True)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Loading vocal model...", total=None)

        # log_level=WARNING suppresses audio-separator's verbose info logs
        separator = Separator(
            output_dir=str(stage1_dir),
            output_format="FLAC",
            log_level=logging.WARNING,
        )
        separator.load_model(model_filename=vocal_model)

        progress.update(task, description="Separating vocals...")
        separator.separate(str(audio_path))

    # Find vocals output
    vocals_file = None
    for output_path in stage1_dir.glob("*"):
        if output_path.is_file():
            name = output_path.name.lower()
            if "vocals" in name:
                vocals_file = output_path
                break

    if not vocals_file or not vocals_file.exists():
        console.print("[red]Stage 1 failed: No vocals file found[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Stage 1 complete:[/green] {vocals_file.name}")

    # === STAGE 2: Echo/Reverb Removal ===
    console.print(Rule("Stage 2: Echo/Reverb Removal (UVR-De-Echo)"))

    stage2_dir = output_dir / "stage2_dereverb"
    stage2_dir.mkdir(exist_ok=True)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Loading dereverb model...", total=None)

        separator_dereverb = Separator(
            output_dir=str(stage2_dir),
            output_format="FLAC",  # Output as FLAC to align with analysis service
            log_level=logging.WARNING,
        )
        separator_dereverb.load_model(model_filename=dereverb_model)

        progress.update(task, description="Removing reverb...")
        stage2_outputs = separator_dereverb.separate(str(vocals_file))

    # Find the dry (no reverb) output
    dry_vocals_file = None
    for output_file in stage2_outputs:
        output_path = Path(output_file)
        if not output_path.is_absolute():
            output_path = stage2_dir / output_path
        name_lower = output_path.name.lower()
        if "no echo" in name_lower or "dry" in name_lower or "no_echo" in name_lower:
            dry_vocals_file = output_path
            break

    # Fallback: take first output
    if not dry_vocals_file and stage2_outputs:
        dry_vocals_file = Path(stage2_outputs[0])
        if not dry_vocals_file.is_absolute():
            dry_vocals_file = stage2_dir / dry_vocals_file

    if not dry_vocals_file or not dry_vocals_file.exists():
        console.print("[red]Stage 2 failed: No dry vocals file found[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Stage 2 complete:[/green] {dry_vocals_file.name}")

    # Copy to final location as vocals_dry.flac
    final_path = cache.get_stem_path(hash_prefix, "vocals_dry")
    final_path.parent.mkdir(parents=True, exist_ok=True)

    import shutil

    shutil.copy2(dry_vocals_file, final_path)
    console.print(f"[cyan]Local file:[/cyan] {final_path}")

    # Upload to R2
    if not skip_upload:
        console.print("[cyan]Uploading to R2...[/cyan]")
        r2_url = r2_client.upload_stem(final_path, hash_prefix, "vocals_dry")
        console.print(f"[green]Uploaded:[/green] {r2_url}")
        # Clean up intermediate extraction files now that upload succeeded
        vocal_extraction_dir = cache_dir / hash_prefix / "vocal_extraction"
        if vocal_extraction_dir.exists():
            import shutil as _shutil

            _shutil.rmtree(vocal_extraction_dir)
    else:
        console.print("[yellow]Skipped R2 upload (--skip-upload)[/yellow]")

    console.print(f"[green]Clean vocals generated for {song_id}[/green]")


@app.command("status")
def check_status(
    job_id: Optional[str] = typer.Argument(None, help="Job ID to check"),
    sync: bool = typer.Option(
        False, "--sync", "-s", help="Sync pending statuses from analysis service"
    ),
    force_status: Optional[str] = typer.Option(
        None,
        "--force-status",
        help="Force update status (completed, failed, pending). Use when Analysis Service has lost state.",
    ),
    force_url: Optional[str] = typer.Option(
        None,
        "--force-url",
        help="URL to set when using --force-status (stems_url for analysis, lrc_url for lrc)",
    ),
    reconcile: bool = typer.Option(
        False,
        "--reconcile",
        "-r",
        help="Reconcile LRC and analysis status by scanning R2 (robust against service restarts)",
    ),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Check analysis status.

    With JOB_ID: query the service for that job's status.
    Without: list all recordings with pending/processing/failed status.
    Use --reconcile to update LRC and analysis status by scanning R2 (robust against service restarts).
    Use --sync to poll the analysis service (unreliable if service restarted).
    Use --force-status when you need to manually override status.
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

            _update_recording_status_force(db_client, rec, force_status, force_url, console)
            return
        elif sync:
            # Force update all pending recordings
            _force_sync_all_pending(db_client, force_status, force_url, console)
            return
        else:
            console.print("[red]--force-status requires either a JOB_ID or --sync flag[/red]")
            raise typer.Exit(1)

    # Handle --reconcile mode
    if reconcile:
        try:
            r2_client = R2Client(config.r2_bucket, config.r2_endpoint_url, config.r2_region)
        except ValueError as e:
            console.print(f"[red]R2 not configured: {e}[/red]")
            raise typer.Exit(1)

        incomplete_lrc = db_client.list_recordings(lrc_status="incomplete")
        incomplete_analysis = db_client.list_recordings(status="incomplete")

        # Deduplicate: a recording may be incomplete on both
        all_hashes = set()
        reconcile_queue = []
        for rec in incomplete_lrc:
            if rec.hash_prefix not in all_hashes:
                all_hashes.add(rec.hash_prefix)
                reconcile_queue.append(rec)
        for rec in incomplete_analysis:
            if rec.hash_prefix not in all_hashes:
                all_hashes.add(rec.hash_prefix)
                reconcile_queue.append(rec)

        if not reconcile_queue:
            console.print("[green]No recordings with incomplete LRC or analysis status.[/green]")
        else:
            console.print(f"[cyan]Scanning R2 across {len(reconcile_queue)} recording(s)...[/cyan]")
            reconciled_lrc = 0
            reconciled_analysis = 0
            error_count = 0

            for rec in reconcile_queue:
                # LRC reconciliation
                if rec.lrc_status in ("pending", "processing", "failed"):
                    try:
                        lrc_url = r2_client.lrc_exists(rec.hash_prefix)
                        if lrc_url:
                            db_client.update_recording_lrc(
                                hash_prefix=rec.hash_prefix,
                                r2_lrc_url=lrc_url,
                            )
                            reconciled_lrc += 1
                            console.print(
                                f"  [green]✓[/green] {rec.song_id or rec.hash_prefix}: "
                                f"LRC {rec.lrc_status} → completed"
                            )
                    except ClientError as e:
                        error_count += 1
                        console.print(
                            f"  [red]✗[/red] {rec.hash_prefix}: R2 error checking LRC: {e}"
                        )

                # Analysis reconciliation
                if rec.analysis_status in ("pending", "processing", "failed"):
                    try:
                        analysis_url = r2_client.analysis_exists(rec.hash_prefix)
                        if analysis_url:
                            analysis_data = r2_client.download_analysis_json(rec.hash_prefix)
                            db_client.update_recording_analysis(
                                hash_prefix=rec.hash_prefix,
                                duration_seconds=analysis_data.get("duration_seconds"),
                                tempo_bpm=analysis_data.get("tempo_bpm"),
                                musical_key=analysis_data.get("musical_key"),
                                musical_mode=analysis_data.get("musical_mode"),
                                key_confidence=analysis_data.get("key_confidence"),
                                loudness_db=analysis_data.get("loudness_db"),
                                beats=(
                                    json.dumps(analysis_data["beats"])
                                    if "beats" in analysis_data
                                    else None
                                ),
                                downbeats=(
                                    json.dumps(analysis_data["downbeats"])
                                    if "downbeats" in analysis_data
                                    else None
                                ),
                                sections=(
                                    json.dumps(analysis_data["sections"])
                                    if "sections" in analysis_data
                                    else None
                                ),
                                embeddings_shape=(
                                    json.dumps(analysis_data["embeddings_shape"])
                                    if "embeddings_shape" in analysis_data
                                    else None
                                ),
                                r2_stems_url=analysis_data.get("stems_url"),
                            )
                            reconciled_analysis += 1
                            console.print(
                                f"  [green]✓[/green] {rec.song_id or rec.hash_prefix}: "
                                f"analysis {rec.analysis_status} → completed"
                            )
                    except ClientError as e:
                        error_count += 1
                        console.print(
                            f"  [red]✗[/red] {rec.hash_prefix}: R2 error checking analysis: {e}"
                        )
                    except (json.JSONDecodeError, KeyError) as e:
                        error_count += 1
                        console.print(
                            f"  [red]✗[/red] {rec.hash_prefix}: error parsing analysis.json: {e}"
                        )

            parts = []
            if reconciled_lrc > 0:
                parts.append(f"{reconciled_lrc} LRC")
            if reconciled_analysis > 0:
                parts.append(f"{reconciled_analysis} analysis")
            if parts:
                console.print(
                    f"[green]Reconciled {' and '.join(parts)} status(es) from R2.[/green]"
                )
            else:
                console.print("[dim]No completed files found in R2 for pending recordings.[/dim]")
            if error_count > 0:
                console.print(
                    f"[yellow]{error_count} R2 error(s) encountered (see above).[/yellow]"
                )
            console.print("")
    # Fall through to list pending recordings table

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
                    lines.append(f"  Duration: {_format_duration(job.result.duration_seconds)}")
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

        console.print(
            Panel.fit(
                "\n".join(lines),
                title=f"Job: {job.job_id}",
                border_style="green" if job.status == "completed" else "yellow",
            )
        )
        return

    # Mode B: Sync and list pending recordings
    # If --sync, query analysis service for all pending jobs and update local DB
    if sync:
        console.print(
            "[yellow]Warning: --sync is unreliable if the Analysis Service has restarted. "
            "For LRC and analysis status, consider using --reconcile instead, "
            "which scans R2 directly.[/yellow]"
        )
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
        cursor.execute(
            "SELECT hash_prefix FROM recordings WHERE lrc_status IN ('pending', 'processing')"
        )
        lrc_pending_hashes = [row[0] for row in cursor.fetchall()]

        # Merge hashes to sync
        hashes_to_sync = set(rec.hash_prefix for rec in pending_recordings) | set(
            lrc_pending_hashes
        )

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
                                duration_seconds=(
                                    job.result.duration_seconds if job.result else None
                                ),
                                tempo_bpm=job.result.tempo_bpm if job.result else None,
                                musical_key=job.result.musical_key if job.result else None,
                                musical_mode=job.result.musical_mode if job.result else None,
                                key_confidence=job.result.key_confidence if job.result else None,
                                loudness_db=job.result.loudness_db if job.result else None,
                                beats=(
                                    json.dumps(job.result.beats)
                                    if job.result and job.result.beats
                                    else None
                                ),
                                downbeats=(
                                    json.dumps(job.result.downbeats)
                                    if job.result and job.result.downbeats
                                    else None
                                ),
                                sections=(
                                    json.dumps(job.result.sections)
                                    if job.result and job.result.sections
                                    else None
                                ),
                                embeddings_shape=(
                                    json.dumps(job.result.embeddings_shape)
                                    if job.result and job.result.embeddings_shape
                                    else None
                                ),
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
                        console.print(
                            f"[dim]Could not sync analysis {rec.analysis_job_id}: {e}[/dim]"
                        )
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
    cursor.execute("""
        SELECT r.*, s.title as song_title
        FROM recordings r
        LEFT JOIN songs s ON r.song_id = s.id
        WHERE r.analysis_status != 'completed' OR r.lrc_status != 'completed'
        ORDER BY r.imported_at DESC
        """)

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

    description = cursor.description
    col_names = [desc[0] for desc in description]

    for row in rows:
        row_dict = dict(zip(col_names, row))
        song_id = row_dict.get("song_id") or "-"
        hash_prefix = row_dict.get("hash_prefix", "")
        song_title = row_dict.get("song_title") or "-"
        analysis_status = row_dict.get("analysis_status", "pending")
        analysis_job_id = row_dict.get("analysis_job_id") or "-"
        lrc_status = row_dict.get("lrc_status", "pending")
        lrc_job_id = row_dict.get("lrc_job_id") or "-"

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
    cursor.execute("""
        SELECT * FROM recordings
        WHERE analysis_status IN ('pending', 'processing', 'failed')
           OR lrc_status IN ('pending', 'processing', 'failed')
        """)
    rows = cursor.fetchall()

    if not rows:
        console.print("[green]No pending recordings to update.[/green]")
        return

    from stream_of_worship.admin.db.models import Recording

    updated = 0
    for row in rows:
        rec = Recording.from_row(row, cursor.description)

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


def _display_lrc(
    console: Console,
    song: Song,
    recording: Recording,
    song_id: str,
    raw: bool,
    no_timestamps: bool,
) -> bool:
    """Display LRC content for a single recording.

    Args:
        console: Rich console for output
        song: Song object for display
        recording: Recording object with LRC URL
        song_id: Song ID string
        raw: Display raw LRC file
        no_timestamps: Show lyrics text only

    Returns:
        True if successful, False if error occurred
    """
    # Get config for R2 access
    try:
        config = AdminConfig.load()
    except Exception as e:
        console.print(f"[red]Error loading config: {e}[/red]")
        return False

    # Initialize R2 client
    r2_client = R2Client(
        bucket=config.r2_bucket,
        endpoint_url=config.r2_endpoint_url,
        region=config.r2_region,
    )

    # Determine S3 key - use cached URL if available, otherwise construct from hash_prefix
    if recording.r2_lrc_url:
        try:
            _, s3_key = R2Client.parse_s3_url(recording.r2_lrc_url)
        except ValueError as e:
            console.print(f"[red]Error parsing R2 URL: {e}[/red]")
            return False
    else:
        # Construct S3 key directly from hash_prefix (predictable naming convention)
        s3_key = f"{recording.hash_prefix}/lyrics.lrc"

    # Download LRC file to temp location
    temp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(mode="w+", suffix=".lrc", delete=False) as temp_file:
            temp_path = Path(temp_file.name)

        # Download from R2
        try:
            r2_client.download_file(s3_key, temp_path)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "404" or error_code == "NoSuchKey":
                console.print(f"[yellow]No LRC file found in R2 for {song_id}[/yellow]")
                console.print(f"[dim]Run 'sow-admin audio lrc {song_id}' to generate LRC[/dim]")
            else:
                console.print(f"[red]Error downloading LRC from R2: {e}[/red]")
            return False

        # Read content
        content = temp_path.read_text(encoding="utf-8")

        # Display based on mode
        if raw:
            # Raw mode: display with syntax highlighting
            syntax = Syntax(content, "lrc", theme="monokai", line_numbers=True)
            console.print(
                Panel.fit(syntax, title=f"LRC Content: {song.title}", border_style="cyan")
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
                return False
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
                return False

        return True

    finally:
        # Cleanup temp file
        if temp_path and temp_path.exists():
            temp_path.unlink()


@app.command("view-lrc")
def view_lrc(
    song_id: list[str] = typer.Argument(
        ..., help="Song ID(s) to view LRC for. Use '-' to read from stdin."
    ),
    raw: bool = typer.Option(False, "--raw", "-r", help="Display raw LRC file"),
    no_timestamps: bool = typer.Option(
        False, "--no-timestamps", "-t", help="Show lyrics text only"
    ),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """View LRC (synchronized lyrics) contents for one or more recordings.

    Accepts multiple song IDs to view LRC for multiple recordings:

        sow-admin audio view-lrc song_001 song_002 song_003

    Or pipe from audio list using '-' to read from stdin:

        sow-admin audio list --visibility published --format ids | sow-admin audio view-lrc -
    """
    import sys

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

    # Handle stdin input if '-' is provided
    song_ids = song_id
    if song_id == ["-"]:
        # Read song IDs from stdin using the helper function
        song_ids = _read_song_ids_from_stdin()
        if not song_ids:
            console.print("[yellow]No song IDs provided via stdin[/yellow]")
            raise typer.Exit(0)

    # Track success/failure counts
    success_count = 0
    error_count = 0

    # Process each song ID
    for idx, sid in enumerate(song_ids):
        # Add separator between songs (but not before first)
        if idx > 0:
            console.print()
            console.print(Rule(style="dim"))
            console.print()

        # Get recording
        recording = db_client.get_recording_by_song_id(sid)
        if not recording:
            console.print(f"[red]No recording found for song ID: {sid}[/red]")
            error_count += 1
            continue

        # Get song for display
        song = db_client.get_song(recording.song_id)
        if not song:
            console.print(f"[red]No song found for ID: {recording.song_id}[/red]")
            error_count += 1
            continue

        # Display LRC
        if _display_lrc(console, song, recording, sid, raw, no_timestamps):
            success_count += 1
        else:
            error_count += 1

    # Summary
    if len(song_id) > 1:
        console.print()
        console.print(Rule(style="dim"))
        if error_count == 0:
            console.print(
                f"[green]✓ Successfully displayed LRC for {success_count} recording(s)[/green]"
            )
        else:
            console.print(
                f"[yellow]Completed: {success_count} succeeded, {error_count} failed[/yellow]"
            )
            raise typer.Exit(1)


@app.command("cache")
def cache_assets(
    song_id: str = typer.Argument(..., help="Song ID to cache assets for"),
    audio: bool = typer.Option(True, "--audio/--no-audio", help="Download main audio file"),
    stems: bool = typer.Option(
        True, "--stems/--no-stems", help="Download stem files (vocals, drums, bass, other)"
    ),
    lrc: bool = typer.Option(True, "--lrc/--no-lrc", help="Download LRC lyrics file"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-download even if files exist"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
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

    # Use the admin cache directory
    cache_dir = config.cache_dir
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
            # Always attempt download - download_lrc checks R2 existence internally
            path = cache.download_lrc(hash_prefix, force=force)
            if path:
                downloaded.append(f"LRC: {path.name}")
                console.print(f"[green]  ✓ {path.name}[/green]")
            else:
                console.print(
                    "[yellow]  ! No LRC available (run 'sow-admin audio lrc' first)[/yellow]"
                )

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
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
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
        info_lines.append("[yellow]Previous LRC generation failed[/yellow]")

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
    console.print("[cyan]Uploading LRC to R2...[/cyan]")
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
    console.print(
        Panel.fit(
            f"[green]LRC uploaded successfully![/green]\n\n"
            f"[cyan]Song:[/cyan] {song_title}\n"
            f"[cyan]Lines:[/cyan] {lrc_data.line_count}\n"
            f"[cyan]Duration:[/cyan] {format_duration(lrc_data.duration_seconds)}\n"
            f"[cyan]R2 URL:[/cyan] {r2_url}",
            title="Upload Complete",
            border_style="green",
        )
    )


def _read_key_nonblocking() -> Optional[str]:
    """Read a key press without blocking.

    Returns:
        Key name ('left', 'right', 'q', etc.) or None if no key pressed.
    """
    # Check if input is available
    if not select.select([sys.stdin], [], [], 0)[0]:
        return None

    # Read the key
    ch = sys.stdin.read(1)

    # Handle escape sequences (arrow keys)
    if ch == "\x1b":
        # Check for more characters in the sequence
        if select.select([sys.stdin], [], [], 0.05)[0]:
            ch2 = sys.stdin.read(1)
            if ch2 == "[":
                if select.select([sys.stdin], [], [], 0.05)[0]:
                    ch3 = sys.stdin.read(1)
                    if ch3 == "C":
                        return "right"
                    elif ch3 == "D":
                        return "left"
                    elif ch3 == "A":
                        return "up"
                    elif ch3 == "B":
                        return "down"
        return "escape"

    return ch


def _drain_input_buffer() -> None:
    """Drain any buffered input to prevent key lag."""
    while select.select([sys.stdin], [], [], 0)[0]:
        sys.stdin.read(1)


@app.command("playback")
def playback_audio(
    song_id: str = typer.Argument(..., help="Song ID to play"),
    start: float = typer.Option(0.0, "--start", "-s", help="Start position in seconds"),
    volume: float = typer.Option(0.8, "--volume", "-v", help="Volume (0.0-1.0)"),
    force_download: bool = typer.Option(False, "--force", "-f", help="Re-download audio"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Play audio for a song directly from the terminal.

    Downloads audio from R2 if not cached locally, then plays it using
    miniaudio. Shows a progress bar during playback.

    Controls: Left/Right arrows to skip -/+5s, Ctrl+C to stop.
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
        console.print(f"[dim]Run 'sow-admin audio download {song_id}' first.[/dim]")
        raise typer.Exit(1)

    # Get song info for display
    song = db_client.get_song(song_id)
    song_title = song.title if song else "Unknown"
    artist = song.composer if song else "Unknown"

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

    # Initialize asset cache
    from stream_of_worship.app.services.asset_cache import AssetCache

    cache_dir = config.cache_dir
    cache = AssetCache(cache_dir=cache_dir, r2_client=r2_client)

    hash_prefix = recording.hash_prefix

    # Check local cache and download if needed
    audio_path = cache.get_audio_path(hash_prefix)
    if not audio_path.exists() or force_download:
        console.print("[cyan]Downloading audio from R2...[/cyan]")
        downloaded_path = cache.download_audio(hash_prefix, force=force_download)
        if not downloaded_path:
            console.print("[red]Failed to download audio from R2.[/red]")
            raise typer.Exit(1)
        audio_path = downloaded_path
        size_mb = audio_path.stat().st_size / (1024 * 1024)
        console.print(f"[green]Downloaded: {audio_path.name} ({size_mb:.2f} MB)[/green]")

    # Initialize playback service
    from stream_of_worship.app.services.playback import PlaybackService

    playback = PlaybackService(volume=volume)

    # Load the audio file
    if not playback.load(audio_path):
        console.print(f"[red]Failed to load audio file: {audio_path}[/red]")
        raise typer.Exit(1)

    # Validate start position
    if start >= playback.duration_seconds:
        console.print(
            f"[red]Start position ({start:.1f}s) exceeds duration ({playback.duration_seconds:.1f}s)[/red]"
        )
        raise typer.Exit(1)

    # Start playback
    if not playback.play(start_seconds=start):
        console.print("[red]Failed to start playback.[/red]")
        raise typer.Exit(1)

    # Display progress using Rich Live
    def format_time(seconds: float) -> str:
        """Format seconds as MM:SS."""
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}:{secs:02d}"

    def create_display(current: float, total: float) -> Panel:
        """Create the playback display panel."""
        # Calculate progress
        progress_pct = (current / total * 100) if total > 0 else 0
        bar_width = 40
        filled = int(bar_width * progress_pct / 100)
        empty = bar_width - filled

        # Build progress bar
        progress_bar = f"[green]{'█' * filled}[/green][dim]{'░' * empty}[/dim]"

        # Build display text
        lines = [
            f"[bold cyan]Playing:[/bold cyan] {song_title} - {artist}",
            f"{progress_bar} {format_time(current)} / {format_time(total)}",
            "",
            "[dim]← -5s | → +5s | Ctrl+C stop[/dim]",
        ]

        return Panel(
            "\n".join(lines),
            border_style="cyan",
            padding=(0, 1),
        )

    stopped_by_user = False
    total_duration = playback.duration_seconds

    # Save terminal settings and switch to raw mode for key input
    old_settings = termios.tcgetattr(sys.stdin)
    try:
        tty.setcbreak(sys.stdin.fileno())

        with Live(
            create_display(start, total_duration),
            console=console,
            refresh_per_second=4,
            transient=True,
        ) as live:
            while playback.is_playing:
                # Check for key input
                key = _read_key_nonblocking()
                if key == "right":
                    playback.skip_forward(5.0)
                    _drain_input_buffer()  # Clear buffered keys after skip
                elif key == "left":
                    playback.skip_backward(5.0)
                    _drain_input_buffer()  # Clear buffered keys after skip

                # Update display
                current = playback.position_seconds
                live.update(create_display(current, total_duration))
                time.sleep(0.05)

    except KeyboardInterrupt:
        stopped_by_user = True
    finally:
        # Restore terminal settings
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)
        playback.stop()

    if stopped_by_user:
        console.print("[yellow]Playback stopped.[/yellow]")
    else:
        console.print("[green]Playback finished.[/green]")


@app.command("batch")
def batch(
    album: Optional[str] = typer.Option(None, "--album", help="Filter by album name (exact match)"),
    song: Optional[str] = typer.Option(None, "--song", help="Filter by song name (partial match)"),
    lrc_status: Optional[str] = typer.Option(None, "--lrc-status", help="Filter by LRC status"),
    download_status: Optional[str] = typer.Option(
        None, "--download-status", help="Filter by download status"
    ),
    analysis_status: Optional[str] = typer.Option(
        None, "--analysis-status", help="Filter by analysis status"
    ),
    stdin: bool = typer.Option(False, "--stdin", help="Read song IDs from stdin (pipe-friendly)"),
    limit: Optional[int] = typer.Option(None, "--limit", help="Maximum number of songs to process"),
    skip_download: bool = typer.Option(
        False, "--skip-download", help="Skip download step (assume audio already on R2)"
    ),
    skip_lrc: bool = typer.Option(False, "--skip-lrc", help="Skip LRC step"),
    stale_after: int = typer.Option(
        120,
        "--stale-after",
        help="Minutes after which a 'processing' song is treated as lost (default: 120)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be processed without executing"
    ),
    format: str = typer.Option("rich", "--format", help="Output format (rich, json)"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Batch process songs: download audio and generate LRC.

    Processes multiple songs end-to-end: downloads audio (if needed),
    submits LRC jobs, polls for completion, and prints summary stats.
    Uses submit-all-then-poll with hybrid polling (service-first, R2-fallback).

    Examples:
        sow-admin audio batch --album "敬拜精选"
        sow-admin audio list --lrc-status failed --format ids | sow-admin audio batch --stdin
    """
    # Validate format
    if format not in ("rich", "json"):
        console.print(f"[red]Invalid format: {format}. Must be 'rich' or 'json'[/red]")
        raise typer.Exit(1)

    # Validate status filters
    valid_lrc_statuses = {"pending", "processing", "completed", "failed", "incomplete"}
    if lrc_status and lrc_status not in valid_lrc_statuses:
        console.print(
            f"[red]Invalid LRC status: {lrc_status}. Must be one of: {', '.join(valid_lrc_statuses)}[/red]"
        )
        raise typer.Exit(1)

    valid_download_statuses = {"pending", "processing", "completed", "failed"}
    if download_status and download_status not in valid_download_statuses:
        console.print(
            f"[red]Invalid download status: {download_status}. Must be one of: {', '.join(valid_download_statuses)}[/red]"
        )
        raise typer.Exit(1)

    valid_analysis_statuses = {"pending", "processing", "completed", "failed", "incomplete"}
    if analysis_status and analysis_status not in valid_analysis_statuses:
        console.print(
            f"[red]Invalid analysis status: {analysis_status}. Must be one of: {', '.join(valid_analysis_statuses)}[/red]"
        )
        raise typer.Exit(1)

    # Load config
    try:
        config = AdminConfig.load(config_path) if config_path else AdminConfig.load()
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    if not config.db_path.exists():
        console.print(f"[red]Database not found at {config.db_path}[/red]")
        raise typer.Exit(1)

    db_client = DatabaseClient(config.db_path)

    # Resolve song IDs to process
    song_ids = _resolve_song_ids(
        db_client, album, song, lrc_status, download_status, analysis_status, stdin, limit
    )
    if not song_ids:
        console.print("[yellow]No songs found matching the criteria.[/yellow]")
        raise typer.Exit(0)

    if dry_run:
        _print_dry_run(db_client, song_ids)
        return

    # Initialize R2 and Analysis clients
    try:
        r2_client = R2Client(
            bucket=config.r2_bucket,
            endpoint_url=config.r2_endpoint_url,
            region=config.r2_region,
        )
    except ValueError as e:
        console.print(f"[red]R2 configuration error: {e}[/red]")
        raise typer.Exit(1)

    try:
        analysis_client = AnalysisClient(config.analysis_url)
    except ValueError as e:
        console.print(f"[red]Analysis service not configured: {e}[/red]")
        raise typer.Exit(1)

    # Process all songs
    results = _process_batch(
        db_client=db_client,
        r2_client=r2_client,
        analysis_client=analysis_client,
        song_ids=song_ids,
        skip_download=skip_download,
        skip_lrc=skip_lrc,
        stale_after_minutes=stale_after,
        console=console,
    )

    # Print final stats
    _print_stats(results, db_client, console, format)


def _resolve_song_ids(
    db_client: DatabaseClient,
    album: Optional[str],
    song: Optional[str],
    lrc_status: Optional[str],
    download_status: Optional[str],
    analysis_status: Optional[str],
    stdin: bool,
    limit: Optional[int],
) -> list[str]:
    """Resolve song IDs based on filters.

    Args:
        db_client: Database client
        album: Filter by album name (exact match)
        song: Filter by song name (partial match, case-insensitive)
        lrc_status: Filter by LRC status
        download_status: Filter by download status
        analysis_status: Filter by analysis status
        stdin: Read song IDs from stdin
        limit: Maximum number of songs to return

    Returns:
        List of song IDs to process
    """
    # If stdin, read from pipe
    if stdin:
        song_ids = _read_song_ids_from_stdin()
        if limit:
            song_ids = song_ids[:limit]
        return song_ids

    # Otherwise, query database with filters
    song_ids = set()

    # Use JOIN query to avoid N+1 lookups for song data
    rows = db_client.list_recordings_with_songs(
        status=analysis_status,
        lrc_status=lrc_status,
        limit=None,
    )

    for recording, song_title, album_name, album_series in rows:
        if not recording.song_id:
            continue

        if download_status and recording.download_status != download_status:
            continue

        if album and album_name != album:
            continue

        if song and (not song_title or song.lower() not in song_title.lower()):
            continue

        song_ids.add(recording.song_id)

    result = list(song_ids)
    if limit:
        result = result[:limit]
    return result


def _print_dry_run(db_client: DatabaseClient, song_ids: list[str]) -> None:
    """Print dry run information showing what would be processed.

    Args:
        db_client: Database client
        song_ids: List of song IDs to show
    """
    console.print("[cyan]Dry run mode - would process the following songs:[/cyan]")
    console.print()

    for song_id in song_ids:
        song = db_client.get_song(song_id)
        recording = db_client.get_recording_by_song_id(song_id)

        if song and recording:
            console.print(f"  [cyan]•[/cyan] {song.title} ({recording.hash_prefix})")
            console.print(f"    [dim]Album:[/dim] {song.album_name or '-'}")
            console.print(f"    [dim]Download:[/dim] {recording.download_status}")
            console.print(f"    [dim]LRC:[/dim] {recording.lrc_status}")
        else:
            console.print(f"  [yellow]•[/yellow] {song_id} (no recording found)")

    console.print(f"\n[dim]Total: {len(song_ids)} song(s)[/dim]")


def _download_if_needed(
    song_id: str,
    recording: Recording,
    db_client: DatabaseClient,
    r2_client: R2Client,
    console: Console,
) -> dict:
    """Download audio if not on R2. Sets download_status in DB.

    Args:
        song_id: Song ID
        recording: Recording object
        db_client: Database client
        r2_client: R2 client
        console: Rich console

    Returns:
        Dict with download result: {'download': 'completed'|'skipped'|'failed', 'error': str}
    """
    hash_prefix = recording.hash_prefix

    # Check R2 first
    if r2_client.audio_exists(hash_prefix):
        db_client.update_recording_download(hash_prefix, "completed")
        return {"download": "skipped_r2"}

    # Download audio
    db_client.update_recording_download(hash_prefix, "processing")

    try:
        # Look up song for metadata
        song = db_client.get_song(song_id)
        if not song:
            raise ValueError(f"Song not found: {song_id}")

        # Initialize downloader
        downloader = YouTubeDownloader()

        # Build search query
        query = downloader.build_search_query(
            title=song.title,
            composer=song.composer,
            album_name=song.album_name,
            suffix=OFFICIAL_LYRICS_SUFFIX,
        )

        # Download
        console.print(f"[{song_id}] Downloading audio from YouTube...")
        audio_path = downloader.download(query)

        # Compute hash and upload to R2
        content_hash = compute_file_hash(audio_path)
        prefix = get_hash_prefix(content_hash)

        r2_url = r2_client.upload_audio(audio_path, prefix)
        console.print(f"[{song_id}] [green]→[/green] Uploaded to R2")

        # Update recording with R2 URL
        db_client.update_recording_status(
            hash_prefix=hash_prefix,
            r2_audio_url=r2_url,
        )
        db_client.update_recording_download(hash_prefix, "completed")

        # Clean up temp file
        audio_path.unlink(missing_ok=True)

        return {"download": "completed"}

    except Exception as e:
        db_client.update_recording_download(hash_prefix, "failed")
        console.print(f"[{song_id}] [red]✗[/red] Download failed: {e}")
        return {"download": "failed", "error": str(e)}


def _process_batch(
    db_client: DatabaseClient,
    r2_client: R2Client,
    analysis_client: AnalysisClient,
    song_ids: list[str],
    skip_download: bool,
    skip_lrc: bool,
    stale_after_minutes: int,
    console: Console,
) -> dict:
    """Process all songs in batch.

    Args:
        db_client: Database client
        r2_client: R2 client
        analysis_client: Analysis service client
        song_ids: List of song IDs to process
        skip_download: Skip download step
        skip_lrc: Skip LRC step
        stale_after_minutes: Staleness threshold for processing jobs
        console: Rich console

    Returns:
        Dict with results for each song
    """
    results = {}

    # Phase 1: Download (if needed)
    if not skip_download:
        console.print("[cyan]Phase 1: Downloading audio[/cyan]")
        downloaded_count = 0
        skipped_count = 0
        failed_count = 0

        for i, song_id in enumerate(song_ids, 1):
            console.print(f"[{i}/{len(song_ids)}] {song_id}...")

            recording = db_client.get_recording_by_song_id(song_id)
            if not recording:
                console.print(f"  [red]✗ No recording found[/red]")
                results[song_id] = {
                    "download": "failed",
                    "error": "No recording found",
                }
                failed_count += 1
                continue

            result = _download_if_needed(song_id, recording, db_client, r2_client, console)
            results[song_id] = result

            if result["download"] == "completed":
                downloaded_count += 1
            elif result["download"] == "skipped_r2":
                skipped_count += 1
            else:
                failed_count += 1

        console.print(f"  [green]Downloaded: {downloaded_count}[/green]")
        console.print(f"  [dim]Skipped: {skipped_count}[/dim]")
        console.print(f"  [red]Failed: {failed_count}[/red]")
        console.print()

    # Phase 2: Submit LRC jobs
    active_jobs = {}  # song_id -> job_id
    if not skip_lrc:
        console.print("[cyan]Phase 2: Submitting LRC jobs[/cyan]")

        songs_needing_lrc = [
            sid
            for sid in song_ids
            if results.get(sid, {}).get("download") != "failed"
            and results.get(sid, {}).get("lrc") != "completed"
        ]

        for song_id in songs_needing_lrc:
            recording = db_client.get_recording_by_song_id(song_id)
            if not recording:
                continue

            # Get song for lyrics
            song = db_client.get_song(song_id)
            if not song or not song.lyrics_raw:
                console.print(f"  [yellow]→ {song_id} (no lyrics, skipping)[/yellow]")
                continue

            # Check if R2 already has LRC
            lrc_url = r2_client.lrc_exists(recording.hash_prefix)
            if lrc_url:
                db_client.update_recording_lrc(recording.hash_prefix, lrc_url)
                results[song_id] = results.get(song_id, {})
                results[song_id]["lrc"] = "completed"
                results[song_id]["lrc_source"] = "r2_preexisting"
                console.print(f"  [green]→ {song_id} (LRC already on R2, skipping)[/green]")
                continue

            # Check for existing processing job (not stale)
            if recording.lrc_status == "processing" and recording.lrc_job_id:
                from datetime import datetime, timezone, timedelta

                updated_at = (
                    datetime.fromisoformat(recording.updated_at) if recording.updated_at else None
                )
                if updated_at:
                    if updated_at.tzinfo is None:
                        updated_at = updated_at.replace(tzinfo=timezone.utc)
                    staleness = datetime.now(timezone.utc) - updated_at
                    if staleness < timedelta(minutes=stale_after_minutes):
                        active_jobs[song_id] = recording.lrc_job_id
                        console.print(
                            f"  [yellow]→ {song_id} (reusing existing job: {recording.lrc_job_id})[/yellow]"
                        )
                        continue

            # Submit new job
            try:
                youtube_url = recording.youtube_url or ""

                job = analysis_client.submit_lrc(
                    audio_url=recording.r2_audio_url,
                    content_hash=recording.content_hash,
                    lyrics_text=song.lyrics_raw,
                    whisper_model="large-v3",
                    language="zh",
                    use_vocals_stem=True,
                    force=False,
                    force_whisper=False,
                    youtube_url=youtube_url,
                    use_qwen3=True,
                )

                db_client.update_recording_status(
                    hash_prefix=recording.hash_prefix,
                    lrc_status="processing",
                    lrc_job_id=job.job_id,
                )

                active_jobs[song_id] = job.job_id
                console.print(f"  [green]→ {song_id} (submitted: {job.job_id})[/green]")

            except AnalysisServiceError as e:
                console.print(f"  [red]✗ {song_id} failed to submit: {e}[/red]")
                results[song_id] = results.get(song_id, {})
                results[song_id]["lrc"] = "failed"
                results[song_id]["lrc_error"] = str(e)

        console.print(f"  Submitted: {len(active_jobs)} job(s)")
        console.print()

    # Phase 3: Poll all jobs
    if active_jobs and not skip_lrc:
        console.print("[cyan]Phase 3: Polling LRC jobs[/cyan]")
        _poll_all_jobs(
            active_jobs=active_jobs,
            results=results,
            db_client=db_client,
            analysis_client=analysis_client,
            r2_client=r2_client,
            stale_after_minutes=stale_after_minutes,
            console=console,
        )

    # Phase 4: Print stats (done by caller)
    return results


def _poll_all_jobs(
    active_jobs: dict,
    results: dict,
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    r2_client: R2Client,
    stale_after_minutes: int,
    console: Console,
) -> None:
    """Poll all active jobs until terminal state or Ctrl+C.

    Args:
        active_jobs: Dict of song_id -> job_id
        results: Results dict to update
        db_client: Database client
        analysis_client: Analysis service client
        r2_client: R2 client
        stale_after_minutes: Staleness threshold
        console: Rich console
    """
    poll_interval = 30.0
    last_completion_time = time.time()
    stale_warning_seconds = stale_after_minutes * 60
    job_timing = {}  # song_id -> (start_time, elapsed)
    batch_start_time = time.time()
    resubmit_counts = {}  # song_id -> count of resubmissions
    max_resubmits = 3

    # Record start times for all jobs
    for song_id, job_id in active_jobs.items():
        job_timing[song_id] = (time.time(), 0)

    try:
        while active_jobs:
            any_completed_this_cycle = False

            for song_id in list(active_jobs.keys()):
                job_id = active_jobs[song_id]
                try:
                    job = analysis_client.get_job(job_id)

                    if job.status == "completed":
                        # R2 confirmation check
                        recording = db_client.get_recording_by_song_id(song_id)
                        lrc_url = _confirm_r2_lrc(r2_client, recording.hash_prefix, console)

                        if lrc_url:
                            db_client.update_recording_lrc(
                                recording.hash_prefix,
                                lrc_url,
                            )
                            results[song_id]["lrc"] = "completed"

                            # Track timing
                            start_time, _ = job_timing.get(song_id, (time.time(), 0))
                            elapsed = time.time() - start_time
                            job_timing[song_id] = (start_time, elapsed)

                            # Store timing in results for stats
                            results[song_id]["start_time"] = start_time
                            results[song_id]["elapsed"] = elapsed

                            del active_jobs[song_id]
                            any_completed_this_cycle = True

                            # Get song for display
                            song = db_client.get_song(song_id)
                            song_name = song.title if song else song_id
                            console.print(
                                f"  [green]✓[/green] {song_name} — LRC completed ({elapsed:.1f}s)"
                            )
                        else:
                            console.print(
                                f"  [yellow]→ {song_id}: Job completed but LRC not on R2 yet, retrying...[/yellow]"
                            )
                            continue

                    elif job.status == "failed":
                        db_client.update_recording_status(
                            hash_prefix=recording.hash_prefix,
                            lrc_status="failed",
                        )
                        results[song_id]["lrc"] = "failed"
                        results[song_id]["lrc_error"] = job.error_message or "Unknown error"
                        del active_jobs[song_id]
                        any_completed_this_cycle = True

                        song = db_client.get_song(song_id)
                        song_name = song.title if song else song_id
                        console.print(
                            f"  [red]✗[/red] {song_name} — LRC failed: {job.error_message or 'Unknown error'}"
                        )

                except AnalysisServiceError as e:
                    if e.status_code == 404:
                        # Job lost — check R2, resubmit if needed
                        recording = db_client.get_recording_by_song_id(song_id)
                        lrc_url = r2_client.lrc_exists(recording.hash_prefix)

                        if lrc_url:
                            db_client.update_recording_lrc(
                                recording.hash_prefix,
                                lrc_url,
                            )
                            results[song_id]["lrc"] = "completed"
                            del active_jobs[song_id]
                            any_completed_this_cycle = True

                            song = db_client.get_song(song_id)
                            song_name = song.title if song else song_id
                            console.print(
                                f"  [green]✓[/green] {song_name} — LRC found on R2 (job was lost)"
                            )
                        else:
                            resubmit_count = resubmit_counts.get(song_id, 0)
                            if resubmit_count >= max_resubmits:
                                console.print(
                                    f"  [red]✗ {song_id}: Job lost (404) after "
                                    f"{max_resubmits} resubmits, marking as failed[/red]"
                                )
                                results[song_id]["lrc"] = "failed"
                                results[song_id][
                                    "lrc_error"
                                ] = f"Job lost (404) and not found on R2 after {max_resubmits} resubmits"
                                db_client.update_recording_status(
                                    hash_prefix=recording.hash_prefix,
                                    lrc_status="failed",
                                )
                                del active_jobs[song_id]
                                any_completed_this_cycle = True
                            else:
                                console.print(
                                    f"  [yellow]→ {song_id}: Job lost (404), "
                                    f"resubmitting (attempt {resubmit_count + 1}/{max_resubmits})...[/yellow]"
                                )
                                try:
                                    song = db_client.get_song(song_id)
                                    if not song or not song.lyrics_raw:
                                        results[song_id]["lrc"] = "failed"
                                        results[song_id][
                                            "lrc_error"
                                        ] = "Job lost and no lyrics available for resubmit"
                                        db_client.update_recording_status(
                                            hash_prefix=recording.hash_prefix,
                                            lrc_status="failed",
                                        )
                                        del active_jobs[song_id]
                                        any_completed_this_cycle = True
                                        continue

                                    new_job = analysis_client.submit_lrc(
                                        audio_url=recording.r2_audio_url,
                                        content_hash=recording.content_hash,
                                        lyrics_text=song.lyrics_raw,
                                        whisper_model="large-v3",
                                        language="zh",
                                        use_vocals_stem=True,
                                        force=False,
                                        force_whisper=False,
                                        youtube_url=recording.youtube_url or "",
                                        use_qwen3=True,
                                    )
                                    db_client.update_recording_status(
                                        hash_prefix=recording.hash_prefix,
                                        lrc_status="processing",
                                        lrc_job_id=new_job.job_id,
                                    )
                                    active_jobs[song_id] = new_job.job_id
                                    resubmit_counts[song_id] = resubmit_count + 1
                                    job_timing[song_id] = (time.time(), 0)
                                except AnalysisServiceError as submit_err:
                                    console.print(
                                        f"  [red]✗ {song_id}: Resubmit failed: {submit_err}[/red]"
                                    )
                                    results[song_id]["lrc"] = "failed"
                                    results[song_id]["lrc_error"] = f"Resubmit failed: {submit_err}"
                                    db_client.update_recording_status(
                                        hash_prefix=recording.hash_prefix,
                                        lrc_status="failed",
                                    )
                                    del active_jobs[song_id]
                                    any_completed_this_cycle = True
                    else:
                        console.print(f"  [yellow]→ Error polling {job_id}: {e}[/yellow]")
                except Exception as e:
                    console.print(f"  [yellow]→ Error polling {job_id}: {e}[/yellow]")

            if any_completed_this_cycle:
                last_completion_time = time.time()

            # Staleness warning
            elapsed_since_completion = time.time() - last_completion_time
            if elapsed_since_completion > stale_warning_seconds and active_jobs:
                hours = int(elapsed_since_completion // 3600)
                mins = int((elapsed_since_completion % 3600) // 60)
                console.print()
                console.print(
                    f"[yellow]⚠ WARNING: No jobs completed in {hours}h {mins}m. "
                    f"Analysis Service may be hung or restarting.[/yellow]"
                )
                console.print(
                    "[yellow]Press Ctrl+C to stop and reconcile, "
                    "or wait for service recovery.[/yellow]"
                )
                console.print()
                last_completion_time = time.time()

            # Print progress
            if active_jobs:
                _print_progress(active_jobs, results, start_time=batch_start_time)

            if active_jobs:
                time.sleep(poll_interval)

    except KeyboardInterrupt:
        _reconcile_on_interrupt(active_jobs, results, db_client, r2_client, console)


def _confirm_r2_lrc(
    r2_client: R2Client,
    hash_prefix: str,
    console: Console,
    max_retries: int = 3,
    retry_delay: float = 5.0,
) -> Optional[str]:
    """Confirm LRC file exists on R2 after service reports completion.

    Args:
        r2_client: R2 client
        hash_prefix: Recording hash prefix
        console: Rich console
        max_retries: Maximum number of retries
        retry_delay: Delay between retries in seconds

    Returns:
        LRC URL if found, None if not confirmed
    """
    for attempt in range(max_retries):
        lrc_url = r2_client.lrc_exists(hash_prefix)
        if lrc_url:
            return lrc_url
        if attempt < max_retries - 1:
            time.sleep(retry_delay)
    return None


def _reconcile_on_interrupt(
    active_jobs: dict,
    results: dict,
    db_client: DatabaseClient,
    r2_client: R2Client,
    console: Console,
) -> None:
    """Reconcile status for in-progress jobs on Ctrl+C.

    Args:
        active_jobs: Dict of song_id -> job_id still in progress
        results: Results dict to update
        db_client: Database client
        r2_client: R2 client
        console: Rich console
    """
    console.print()
    console.print("[yellow]Batch interrupted. Reconciling status...[/yellow]")

    for song_id, job_id in active_jobs.items():
        recording = db_client.get_recording_by_song_id(song_id)
        if not recording:
            continue

        hash_prefix = recording.hash_prefix

        # Check R2 for LRC
        lrc_url = r2_client.lrc_exists(hash_prefix)
        if lrc_url:
            db_client.update_recording_lrc(hash_prefix, lrc_url)
            results[song_id]["lrc"] = "completed"

            song = db_client.get_song(song_id)
            song_name = song.title if song else song_id
            console.print(f"  [green]✓[/green] {song_name}: LRC found on R2 (completed)")
        else:
            db_client.update_recording_status(
                hash_prefix=hash_prefix,
                lrc_status="failed",
            )
            results[song_id]["lrc"] = "failed"
            results[song_id]["lrc_error"] = "Batch interrupted, LRC not on R2"

            song = db_client.get_song(song_id)
            song_name = song.title if song else song_id
            console.print(f"  [red]✗[/red] {song_name}: LRC not on R2 (marked failed)")

    console.print()
    console.print(
        "[dim]Tip: Run 'sow-admin audio status --reconcile' later to catch "
        "late completions after the service finishes processing.[/dim]"
    )


def _print_progress(
    active_jobs: dict,
    results: dict,
    start_time: float,
    console: Console,
) -> None:
    """Print polling progress.

    Args:
        active_jobs: Dict of song_id -> job_id still in progress
        results: Results dict
        start_time: Batch start time
        console: Rich console
    """
    completed_count = sum(1 for r in results.values() if r.get("lrc") == "completed")
    failed_count = sum(1 for r in results.values() if r.get("lrc") == "failed")
    in_progress_count = len(active_jobs)

    elapsed = time.time() - start_time
    elapsed_mins = int(elapsed // 60)
    elapsed_secs = int(elapsed % 60)

    console.print(
        f"⏳ Polling {len(active_jobs)} job(s)... "
        f"{completed_count} completed, {failed_count} failed, {in_progress_count} in progress "
        f"(elapsed: {elapsed_mins}m {elapsed_secs}s)"
    )


def _print_stats(
    results: dict,
    db_client: DatabaseClient,
    console: Console,
    format: str,
) -> None:
    """Print final batch statistics.

    Args:
        results: Results dict from batch processing
        db_client: Database client for looking up song info
        console: Rich console
        format: Output format (rich or json)
    """
    if format == "json":
        import json

        console.print(json.dumps(results, indent=2))
        return

    # Rich format
    total = len(results)

    # Download stats
    download_completed = sum(1 for r in results.values() if r.get("download") == "completed")
    download_skipped = sum(1 for r in results.values() if r.get("download") == "skipped_r2")
    download_failed = sum(1 for r in results.values() if r.get("download") == "failed")

    # LRC stats
    lrc_completed = sum(1 for r in results.values() if r.get("lrc") == "completed")
    lrc_failed = sum(1 for r in results.values() if r.get("lrc") == "failed")
    lrc_skipped_existing = sum(
        1 for r in results.values() if r.get("lrc_source") == "r2_preexisting"
    )
    lrc_skipped_download = sum(1 for r in results.values() if r.get("download") == "failed")

    # LRC timing stats (only for ASR-generated LRCs)
    lrc_timings = []
    for song_id, t in results.items():
        # Only track timings for jobs that were actually generated by ASR
        if "elapsed" in t and t.get("lrc_source") != "r2_preexisting":
            # Add to timings list
            lrc_timings.append(t["elapsed"])

    if lrc_timings:
        avg_lrc_time = sum(lrc_timings) / len(lrc_timings)
        med_lrc_time = sorted(lrc_timings)[len(lrc_timings) // 2]
        min_lrc_time = min(lrc_timings)
        max_lrc_time = max(lrc_timings)
    else:
        avg_lrc_time = med_lrc_time = min_lrc_time = max_lrc_time = None

    # Build summary table
    lines = [
        f"╭─ {'Batch Summary':^50} ╮",
        f"│ {'Songs processed:':<30} {total:>18} │",
        f"│ {'':<30} {'':>18} │",
        f"│ {'Downloads:':<30} {'':>18} │",
        f"│ {'  Completed:':<30} {download_completed:>18} │",
        f"│ {'  Skipped (R2):':<30} {download_skipped:>18} │",
        f"│ {'  Failed:':<30} {download_failed:>18} │",
        f"│ {'':<30} {'':>18} │",
        f"│ {'LRC:':<30} {'':>18} │",
        f"│ {'  Completed:':<30} {lrc_completed:>18} │",
        f"│ {'  Failed:':<30} {lrc_failed:>18} │",
        f"│ {'  Skipped (R2):':<30} {lrc_skipped_existing:>18} │",
        f"│ {'  Skipped (dl failed):':<30} {lrc_skipped_download:>18} │",
    ]

    if lrc_completed > 0 and lrc_skipped_existing < lrc_completed:
        asr_count = lrc_completed - lrc_skipped_existing
        lines.extend(
            [
                f"│ {'':<30} {'':>18} │",
                f"│ {'LRC source:':<30} {'':>18} │",
                f"│ {'  R2 pre-existing:':<30} {lrc_skipped_existing:>18} │",
                f"│ {'  ASR (generated):':<30} {asr_count:>18} │",
            ]
        )

    if avg_lrc_time is not None:
        lines.extend(
            [
                f"│ {'':<30} {'':>18} │",
                f"│ {'LRC timing (ASR jobs only):':<30} {'':>18} │",
                f"│ {'  Average:':<30} {avg_lrc_time:.1f}s{'':>12} │",
                f"│ {'  Median:':<30} {med_lrc_time:.1f}s{'':>12} │",
                f"│ {'  Min/Max:':<30} {min_lrc_time:.1f}s / {max_lrc_time:.1f}s   │",
            ]
        )

    lines.append("╰" + "─" * 52 + "╯")

    console.print("\n".join(lines))

    # Print failed downloads
    failed_downloads = [
        (song_id, r.get("error")) for song_id, r in results.items() if r.get("download") == "failed"
    ]
    if failed_downloads:
        console.print("\n[bold red]Failed downloads:[/bold red]")
        for song_id, error in failed_downloads:
            song = db_client.get_song(song_id)
            song_name = song.title if song else song_id
            console.print(f"  - {song_name}: {error}")

    # Print failed LRCs
    failed_lrcs = [
        (song_id, r.get("lrc_error")) for song_id, r in results.items() if r.get("lrc") == "failed"
    ]
    if failed_lrcs:
        console.print("\n[bold red]Failed LRC:[/bold red]")
        for song_id, error in failed_lrcs:
            song = db_client.get_song(song_id)
            song_name = song.title if song else song_id
            console.print(f"  - {song_name}: {error}")
