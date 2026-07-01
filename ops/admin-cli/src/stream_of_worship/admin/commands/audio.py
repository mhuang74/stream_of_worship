"""Audio commands for sow-admin.

Provides CLI commands for downloading audio from YouTube, listing
recordings, and viewing recording details.
"""

import json
import logging
import os
import select
import sys
import tempfile
import termios
import threading
import time
import tty
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import typer
from botocore.exceptions import ClientError
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.syntax import Syntax
from rich.table import Table

from stream_of_worship.admin.commands.catalog import _extract_series_sort_key, get_db_client
from stream_of_worship.admin.config import AdminConfig, get_cache_dir
from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.db.connection import ConnectionProvider
from stream_of_worship.admin.services.analysis import (
    AnalysisClient,
    AnalysisServiceError,
    JobInfo,
)
from stream_of_worship.admin.services.ffprobe import is_ffprobe_available, probe_duration
from stream_of_worship.admin.services.hasher import compute_file_hash, get_hash_prefix
from stream_of_worship.admin.services.lrc_parser import (
    build_draft_from_catalog,
    format_duration,
    parse_lrc,
    parse_lrc_full,
    serialize_lrc,
)
from stream_of_worship.admin.services.r2 import R2Client, R2ObjectIdentity
from stream_of_worship.admin.services.youtube import (
    DURATION_WARNING_THRESHOLD,
    OFFICIAL_LYRICS_SUFFIX,
    YouTubeDownloader,
)

console = Console()
logger = logging.getLogger("sow_admin.audio")
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


def _soft_delete_recording_only(
    db_client: DatabaseClient,
    recording: Recording,
    console: Console,
) -> None:
    """Soft-delete a recording row while preserving R2 assets."""
    db_client.delete_recording(recording.hash_prefix)
    console.print(
        f"[green]✓ Soft-deleted {recording.hash_prefix}; R2 assets were preserved for maintenance review.[/green]"
    )


def _get_single_active_recording_for_song(
    db_client: DatabaseClient,
    song_id: str,
    console: Console,
) -> Optional[Recording]:
    """Return the only active recording for a song or refuse ambiguous matches."""
    recordings = db_client.list_active_recordings_by_song(song_id)
    if len(recordings) > 1:
        hashes = ", ".join(recording.hash_prefix for recording in recordings)
        console.print(
            f"[red]Multiple active recordings found for {song_id}: {hashes}. "
            "Use a hash-prefix targeted command where available.[/red]"
        )
        raise typer.Exit(1)
    return recordings[0] if recordings else None


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
    no_qwen3_asr: bool,
    force_qwen3_asr: bool,
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
                song_title=song.title,
                whisper_model=whisper_model,
                language=language,
                use_vocals_stem=not no_vocals,
                force=force,
                force_whisper=no_whisper_cache,
                youtube_url=youtube_url,
                use_qwen3_asr=not no_qwen3_asr,
                force_qwen3_asr=force_qwen3_asr,
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
                visibility_status="review",
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
    no_qwen3_asr: bool,
    force_qwen3_asr: bool,
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
                song_title=song.title,
                whisper_model=whisper_model,
                language=language,
                use_vocals_stem=not no_vocals,
                force=force,
                force_whisper=no_whisper_cache,
                youtube_url=youtube_url,
                use_qwen3_asr=not no_qwen3_asr,
                force_qwen3_asr=force_qwen3_asr,
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
    language: str = "auto",
    no_vocals: bool = False,
    no_youtube: bool = False,
    no_whisper_cache: bool = False,
    use_qwen3_asr: bool = True,
    force_qwen3_asr: bool = False,
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
        use_qwen3_asr: Use DashScope Qwen3 ASR before Whisper fallback
        force_qwen3_asr: Bypass only the Qwen3 ASR cache

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
            song_title=song.title,
            whisper_model=whisper_model,
            language=language,
            use_vocals_stem=not no_vocals,
            force=force,
            force_whisper=no_whisper_cache,
            youtube_url=youtube_url,
            use_qwen3_asr=use_qwen3_asr,
            force_qwen3_asr=force_qwen3_asr,
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


def import_youtube_audio_for_song(
    *,
    song_id: str,
    youtube_url: str | None,
    config: AdminConfig,
    db_client: DatabaseClient,
    console: Console,
    force: bool = False,
    skip_video_confirm: bool = False,
    analyze: bool = False,
    lrc: bool = False,
) -> Recording | None:
    """Import a YouTube-backed recording for an existing song."""
    song = db_client.get_song(song_id)
    if not song:
        console.print(f"[red]Song not found: {song_id}[/red]")
        raise typer.Exit(1)

    console.print(f"[cyan]Song:[/cyan] {song.title}")
    if song.composer:
        console.print(f"[cyan]Composer:[/cyan] {song.composer}")
    if song.album_name:
        console.print(f"[cyan]Album:[/cyan] {song.album_name}")

    try:
        r2_client = R2Client(
            bucket=config.r2_bucket,
            endpoint_url=config.r2_endpoint_url,
            region=config.r2_region,
        )
    except ValueError as e:
        console.print(f"[red]R2 configuration error: {e}[/red]")
        raise typer.Exit(1)

    existing = _get_single_active_recording_for_song(db_client, song_id, console)
    if existing:
        if not force:
            console.print(
                f"[yellow]Recording already exists for this song "
                f"(hash: {existing.hash_prefix}). Use --force to replace.[/yellow]"
            )
            raise typer.Exit(0)
        console.print(
            f"[cyan]Replacement mode: existing recording {existing.hash_prefix} will stay active "
            "until the new recording is safely persisted.[/cyan]"
        )

    downloader = YouTubeDownloader()
    if youtube_url:
        search_or_url = youtube_url
        console.print(f"[dim]Using provided URL: {youtube_url}[/dim]")
    else:
        album_for_query = song.album_name
        if song.title == song.album_name:
            album_for_query = None
        query = downloader.build_search_query(
            title=song.title,
            composer=song.composer,
            album=album_for_query,
            suffix=OFFICIAL_LYRICS_SUFFIX,
        )
        console.print(f"[dim]Search query: {query}[/dim]")
        search_or_url = query

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

    _display_video_preview(video_info, console)

    video_title = video_info.get("title") if video_info else None
    chinese_title = _extract_chinese_title_from_youtube(video_title)
    if chinese_title and chinese_title != song.title:
        console.print(
            f"[yellow]⚠ Title mismatch: expected '{song.title}', got '{chinese_title}' from video '{video_title}'[/yellow]"
        )
        console.print(
            "[yellow]  This may be the wrong video. Consider using --url to specify the correct video.[/yellow]"
        )

    download_confirmed = skip_video_confirm
    if not skip_video_confirm:
        download_confirmed = _prompt_confirmation("Download this video?")

    if not download_confirmed:
        console.print("[yellow]Auto-selected video rejected.[/yellow]")
        manual_url = _prompt_manual_url()
        if not manual_url:
            console.print("[yellow]Download cancelled.[/yellow]")
            raise typer.Exit(0)

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
        video_title = video_info.get("title") if video_info else None
        chinese_title = _extract_chinese_title_from_youtube(video_title)
        if chinese_title and chinese_title != song.title:
            console.print(
                f"[yellow]⚠ Title mismatch: expected '{song.title}', got '{chinese_title}' from video '{video_title}'[/yellow]"
            )
            console.print("[yellow]  This may be the wrong video.[/yellow]")

        if not _prompt_confirmation("Download this video?"):
            console.print("[yellow]Download cancelled.[/yellow]")
            raise typer.Exit(0)

        search_or_url = manual_url

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

    content_hash = compute_file_hash(audio_path)
    prefix = get_hash_prefix(content_hash)
    console.print(f"[dim]Hash prefix: {prefix}[/dim]")

    duration = probe_duration(audio_path)
    if duration:
        console.print(f"[dim]Duration: {duration:.1f}s[/dim]")
    else:
        console.print("[yellow]Could not probe audio duration[/yellow]")

    console.print("[cyan]Uploading to R2...[/cyan]")
    try:
        r2_url = r2_client.upload_audio(audio_path, prefix)
        console.print(f"[green]Uploaded: {r2_url}[/green]")
    except Exception as e:
        console.print(f"[red]Upload failed: {e}[/red]")
        raise typer.Exit(1)
    finally:
        audio_path.unlink(missing_ok=True)

    recording = Recording(
        content_hash=content_hash,
        hash_prefix=prefix,
        song_id=song_id,
        original_filename=audio_path.name,
        file_size_bytes=file_size,
        imported_at=datetime.now().isoformat(),
        r2_audio_url=r2_url,
        download_status="completed",
        youtube_url=video_info.get("webpage_url"),
        duration_seconds=duration,
    )
    if existing and force:
        updated_items = db_client.replace_recording_after_import(existing.hash_prefix, recording)
        if existing.hash_prefix == recording.hash_prefix:
            console.print(
                f"[green]Recording refreshed; same hash {recording.hash_prefix}, "
                "no songset references changed.[/green]"
            )
        else:
            console.print(
                f"[green]Replacement saved; updated {updated_items} songset item reference(s) and "
                f"soft-deleted old recording {existing.hash_prefix}.[/green]"
            )
    else:
        db_client.insert_recording(recording)
    console.print(f"[green]Recording saved (hash_prefix: {prefix})[/green]")

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
            language="auto",
            no_vocals=False,
            use_qwen3_asr=True,
            force_qwen3_asr=False,
        )

    return recording


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
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)

    if dry_run:
        song = db_client.get_song(song_id)
        if not song:
            console.print(f"[red]Song not found: {song_id}[/red]")
            raise typer.Exit(1)
        console.print(f"[cyan]Song:[/cyan] {song.title}")
        if song.composer:
            console.print(f"[cyan]Composer:[/cyan] {song.composer}")
        if song.album_name:
            console.print(f"[cyan]Album:[/cyan] {song.album_name}")
        console.print("[yellow]Dry run - no download will occur[/yellow]")
        return

    import_youtube_audio_for_song(
        song_id=song_id,
        youtube_url=url,
        config=config,
        db_client=db_client,
        console=console,
        force=force,
        skip_video_confirm=skip_confirm,
        analyze=analyze,
        lrc=lrc,
    )


@app.command("delete")
def delete_recording(
    song_id: Optional[str] = typer.Argument(None, help="Song ID to delete recording for"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    stdin: bool = typer.Option(False, "--stdin", help="Read song IDs from stdin (one per line)"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Soft-delete a recording while preserving associated R2 files.

    Marks the recording as deleted in the database. R2 assets are preserved
    so they can be reviewed, restored, or purged by maintenance commands.

    For batch deletion, pipe song IDs via stdin:

        sow-admin audio list --album album1 --format ids | sow-admin audio delete --stdin
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
        _delete_recordings_batch(db_client, yes, console)
    else:
        _delete_recording_single(song_id, db_client, yes, console)


def _delete_recording_single(
    song_id: str,
    db_client: DatabaseClient,
    yes: bool,
    console: Console,
) -> None:
    """Delete a single recording by song_id."""
    recording = _get_single_active_recording_for_song(db_client, song_id, console)
    if not recording:
        console.print(f"[red]No recording found for song: {song_id}[/red]")
        raise typer.Exit(1)

    song = db_client.get_song(song_id)
    song_title = song.title if song else "Unknown"

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

    info_lines.append("")
    info_lines.append("[bold]R2 Resources preserved after soft-delete:[/bold]")

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

    if not yes:
        console.print(
            "[yellow]This soft-deletes the DB row only; R2 assets remain for maintenance review.[/yellow]"
        )
        confirmed = _prompt_confirmation("Soft-delete this recording?")
        if not confirmed:
            console.print("[yellow]Deletion cancelled.[/yellow]")
            raise typer.Exit(0)

    console.print("[cyan]Soft-deleting recording...[/cyan]")
    _soft_delete_recording_only(db_client, recording, console)
    console.print(f"[green]Recording {recording.hash_prefix} soft-deleted successfully.[/green]")


def _delete_recordings_batch(
    db_client: DatabaseClient,
    yes: bool,
    console: Console,
) -> None:
    """Delete multiple recordings from stdin."""
    song_ids = _read_song_ids_from_stdin()

    if not song_ids:
        console.print("[yellow]No song IDs provided via stdin[/yellow]")
        raise typer.Exit(0)

    console.print(f"[cyan]Looking up {len(song_ids)} recording(s)...[/cyan]")

    recordings_to_delete: list[tuple[str, Recording, str]] = []
    not_found: list[str] = []

    for sid in song_ids:
        recording = _get_single_active_recording_for_song(db_client, sid, console)
        if recording:
            song = db_client.get_song(sid)
            title = song.title if song else "Unknown"
            recordings_to_delete.append((sid, recording, title))
        else:
            not_found.append(sid)

    if not_found:
        console.print(
            f"[yellow]No recording found for {len(not_found)} song(s): {', '.join(not_found[:5])}{'...' if len(not_found) > 5 else ''}[/yellow]"
        )

    if not recordings_to_delete:
        console.print("[yellow]No valid recordings to delete.[/yellow]")
        raise typer.Exit(0)

    total_size = sum(r.file_size_bytes or 0 for _, r, _ in recordings_to_delete)
    info_lines = [
        f"[cyan]Count:[/cyan] {len(recordings_to_delete)} recording(s)",
        f"[cyan]Total Size:[/cyan] {_format_size_mb(total_size)}",
        "",
        "[bold]Recordings to delete:[/bold]",
    ]
    for sid, recording, title in recordings_to_delete[:10]:
        info_lines.append(f"  • {sid}: {title}")
    if len(recordings_to_delete) > 10:
        info_lines.append(f"  ... and {len(recordings_to_delete) - 10} more")

    console.print(
        Panel.fit(
            "\n".join(info_lines),
            title="Batch Delete Recordings",
            border_style="yellow",
        )
    )

    if not yes:
        console.print(
            "[yellow]This soft-deletes DB rows only; R2 assets remain for maintenance review.[/yellow]"
        )
        confirmed = _prompt_confirmation(f"Soft-delete {len(recordings_to_delete)} recording(s)?")
        if not confirmed:
            console.print("[yellow]Deletion cancelled.[/yellow]")
            raise typer.Exit(0)

    deleted_count = 0
    failed_count = 0

    for sid, recording, title in recordings_to_delete:
        try:
            _soft_delete_recording_only(db_client, recording, console)
            deleted_count += 1
            console.print(f"[green]✓ Deleted: {sid} ({title})[/green]")
        except Exception as e:
            failed_count += 1
            console.print(f"[red]✗ Failed to delete {sid}: {e}[/red]")

    console.print()
    console.print(f"[bold]Summary:[/bold] {deleted_count} deleted, {failed_count} failed")


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
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
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

    db_client = get_db_client(config)
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
        enriched.sort(key=lambda t: (_extract_series_sort_key(t[3]), t[2] or "", t[1] or ""))
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
        table.add_column("Duration", style="cyan", no_wrap=True)
        table.add_column("Key", style="cyan", no_wrap=True)
        table.add_column("BPM", style="magenta", justify="right", no_wrap=True)
        table.add_column("Song ID", style="dim", no_wrap=True)
        table.add_column("Filename", style="yellow")
        table.add_column("Hash Prefix", style="dim", no_wrap=True)

        for rec, song_title, album_name, _album_series in enriched:
            song_id = rec.song_id or "-"

            # Visibility status with visual indicator
            visibility_text = _colorize_visibility(rec.visibility_status)

            # Format duration if available (from analysis results)
            duration_str = (
                _format_duration(rec.duration_seconds) if rec.duration_seconds else "--:--"
            )

            # Musical key (combine key + mode, e.g. "C Major")
            key_parts = [p for p in (rec.musical_key, rec.musical_mode) if p]
            key_str = " ".join(key_parts) if key_parts else "--"

            # BPM rounded to nearest integer
            bpm_str = str(round(rec.tempo_bpm)) if rec.tempo_bpm is not None else "--"

            table.add_row(
                album_name or "-",
                song_title or "-",
                visibility_text,
                duration_str,
                key_str,
                bpm_str,
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
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)

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
    if recording.r2_stems_url:
        info_lines.append(f"[cyan]Stems URL:[/cyan] {recording.r2_stems_url}")
    if recording.r2_lrc_url:
        info_lines.append(f"[cyan]LRC URL:[/cyan] {recording.r2_lrc_url}")

    info_lines.append(f"[cyan]YouTube URL:[/cyan] {recording.youtube_url or '- none -'}")

    # Status
    info_lines.append("")
    info_lines.append(f"[cyan]Download Status:[/cyan] {recording.download_status}")
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
        if recording.embeddings_shape:
            info_lines.append(f"[cyan]Embeddings:[/cyan] {recording.embeddings_shape}")

    if recording.updated_at:
        info_lines.append("")
        info_lines.append(f"[cyan]Last Updated:[/cyan] {recording.updated_at}")

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
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)

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
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)

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
    language: str = typer.Option("auto", "--lang", help="Language mode: auto, zh, or en"),
    no_vocals: bool = typer.Option(False, "--no-vocals", help="Don't use vocals stem"),
    no_youtube: bool = typer.Option(
        False, "--no-youtube", help="Skip YouTube transcript, use Whisper directly"
    ),
    no_whisper_cache: bool = typer.Option(
        False, "--no-whisper-cache", help="Bypass cached Whisper transcription, re-run Whisper"
    ),
    no_qwen3_asr: bool = typer.Option(
        False, "--no-qwen3-asr", help="Skip DashScope Qwen3 ASR and use Whisper fallback"
    ),
    force_qwen3_asr: bool = typer.Option(
        False, "--force-qwen3-asr", help="Bypass cached Qwen3 ASR transcription only"
    ),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait for LRC generation to complete"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Submit a recording for lyrics alignment (LRC generation).

    By default, tries YouTube transcript first (if a YouTube URL is stored),
    then falls back to DashScope Qwen3 ASR and finally Whisper transcription.
    Use --no-youtube to skip the YouTube path and use Whisper directly.
    Use --no-qwen3-asr to skip Qwen3 ASR and use Whisper.

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
    if language not in {"auto", "zh", "en"}:
        console.print("[red]Error: --lang must be one of: auto, zh, en[/red]")
        raise typer.Exit(1)

    # Standard config/db boilerplate
    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)

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
            no_qwen3_asr=no_qwen3_asr,
            force_qwen3_asr=force_qwen3_asr,
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
            no_qwen3_asr=no_qwen3_asr,
            force_qwen3_asr=force_qwen3_asr,
            console=console,
        )


def _compute_content_hash(
    title: str, composer: str, lyrics_raw: str, lyrics_lines: list[str]
) -> str:
    import hashlib

    content = f"{title}\0{composer}\0{lyrics_raw}\0{'|'.join(lyrics_lines)}"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _get_alignment_lyrics_text(
    recording: "Recording",
    song: "Song",
    r2_client: Optional[R2Client],
    console: Console,
) -> str:
    """Return lyrics text for forced alignment, preferring existing LRC over nominal lyrics.

    If an official lyrics.lrc exists in R2, download and parse it to extract the
    transcribed lyrics text (timestamps stripped). This ensures forced alignment
    only updates timestamps without changing the lyrics text.

    Falls back to song.lyrics_raw with a console warning if the LRC is missing
    or cannot be parsed.
    """
    if r2_client and recording.r2_lrc_url:
        try:
            lrc_content = r2_client.download_lrc_content(recording.hash_prefix)
            if lrc_content:
                lrc_file = parse_lrc(lrc_content)
                return "\n".join(line.text for line in lrc_file.lines)
        except Exception as e:
            console.print(
                f"[yellow]Warning: Could not read existing LRC for {recording.hash_prefix} "
                f"({e}), using nominal lyrics[/yellow]"
            )
    return song.lyrics_raw


def _submit_forced_alignment_single(
    song_id: str,
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    language: str,
    force: bool,
    use_vocals_stem: bool,
    wait: bool,
    console: Console,
    r2_client: Optional[R2Client] = None,
) -> None:
    """Submit forced alignment for a single recording."""
    recording = db_client.get_recording_by_song_id(song_id)
    if not recording:
        console.print(f"[red]No recording found for {song_id}.[/red]")
        raise typer.Exit(1)

    song = db_client.get_song(song_id)
    if not song or not song.lyrics_raw:
        console.print(f"[red]No lyrics found for song {song_id}.[/red]")
        raise typer.Exit(1)

    if not recording.r2_audio_url:
        console.print(f"[red]Recording {recording.hash_prefix} has no audio URL.[/red]")
        raise typer.Exit(1)

    if recording.lrc_status == "completed" and not force:
        console.print(
            f"[yellow]Recording {recording.hash_prefix} already has LRC. "
            f"Use --force to re-align.[/yellow]"
        )
        raise typer.Exit(0)

    if recording.lrc_status == "processing" and recording.lrc_job_id and not force:
        console.print(
            f"[yellow]LRC generation already in progress for "
            f"{recording.hash_prefix} (job: {recording.lrc_job_id})[/yellow]"
        )
        raise typer.Exit(0)

    if recording.duration_seconds and recording.duration_seconds > 300:
        console.print(
            f"[red]Recording {recording.hash_prefix} is too long "
            f"({recording.duration_seconds:.0f}s > 300s limit).[/red]"
        )
        raise typer.Exit(1)

    lyrics_text = _get_alignment_lyrics_text(recording, song, r2_client, console)

    try:
        job = analysis_client.submit_forced_alignment(
            audio_url=recording.r2_audio_url,
            content_hash=recording.content_hash,
            lyrics_text=lyrics_text,
            song_title=song.title,
            language=language,
            force=force,
            use_vocals_stem=use_vocals_stem,
        )
    except AnalysisServiceError as e:
        console.print(f"[red]Failed to submit forced alignment job: {e}[/red]")
        raise typer.Exit(1)

    job_id = job.job_id

    db_client.update_recording_status(
        hash_prefix=recording.hash_prefix,
        lrc_status="processing",
        lrc_job_id=job_id,
    )

    console.print(f"[green]Forced alignment job submitted (job: {job_id})[/green]")

    if wait:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TextColumn("{task.fields[stage]}"),
            console=console,
        ) as progress:
            task = progress.add_task("Forced aligning...", total=100, stage="", completed=0)

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
            console.print(f"[red]Forced alignment failed: {error_msg}[/red]")
            db_client.update_recording_status(
                hash_prefix=recording.hash_prefix,
                lrc_status="failed",
            )
            raise typer.Exit(1)

        if final_job.result and final_job.result.lrc_url:
            db_client.update_recording_lrc(
                hash_prefix=recording.hash_prefix,
                r2_lrc_url=final_job.result.lrc_url,
                visibility_status="review",
            )

        console.print(f"[green]Forced alignment completed for {song_id}[/green]")
        if final_job.result and final_job.result.lrc_url:
            console.print(f"  LRC URL: {final_job.result.lrc_url}")


def _submit_forced_alignment_batch(
    song_ids: list[str],
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    language: str,
    force: bool,
    use_vocals_stem: bool,
    console: Console,
    r2_client: Optional[R2Client] = None,
) -> None:
    """Submit forced alignment for multiple recordings (batch mode, no wait)."""
    submitted = 0
    skipped = 0
    errors = 0

    for i, song_id in enumerate(song_ids, 1):
        console.print(f"[{i}/{len(song_ids)}] Processing {song_id}...")

        recording = db_client.get_recording_by_song_id(song_id)
        if not recording:
            console.print("  [red]No recording found[/red]")
            errors += 1
            continue

        song = db_client.get_song(song_id)
        if not song or not song.lyrics_raw:
            console.print("  [red]No lyrics found[/red]")
            errors += 1
            continue

        if not recording.r2_audio_url:
            console.print("  [red]No audio URL[/red]")
            errors += 1
            continue

        if recording.lrc_status == "completed" and not force:
            console.print("  [yellow]Already has LRC (skipped)[/yellow]")
            skipped += 1
            continue

        if recording.lrc_status == "processing" and recording.lrc_job_id and not force:
            console.print("  [yellow]Already in progress (skipped)[/yellow]")
            skipped += 1
            continue

        if recording.duration_seconds and recording.duration_seconds > 300:
            console.print("  [yellow]Too long (>5 min, skipped)[/yellow]")
            skipped += 1
            continue

        lyrics_text = _get_alignment_lyrics_text(recording, song, r2_client, console)

        try:
            job = analysis_client.submit_forced_alignment(
                audio_url=recording.r2_audio_url,
                content_hash=recording.content_hash,
                lyrics_text=lyrics_text,
                song_title=song.title,
                language=language,
                force=force,
                use_vocals_stem=use_vocals_stem,
            )

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

    console.print("")
    console.print("[cyan]Batch Summary:[/cyan]")
    console.print(f"  Submitted: {submitted}")
    console.print(f"  Skipped: {skipped}")
    console.print(f"  Errors: {errors}")


@app.command("align-lrc")
def align_lrc_recording(
    song_id: Optional[str] = typer.Argument(None, help="Song ID to force-align LRC for"),
    language: str = typer.Option("auto", "--lang", help="Language: auto, zh, en"),
    force: bool = typer.Option(False, "--force", "-f", help="Force re-alignment"),
    use_vocals_stem: bool = typer.Option(
        True,
        "--use-vocals-stem/--no-vocals-stem",
        help="Use clean vocal stem for better accuracy",
    ),
    stdin: bool = typer.Option(False, "--stdin", help="Read song IDs from stdin"),
    wait: bool = typer.Option(False, "--wait", "-w", help="Wait for alignment to complete"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Submit a recording for forced LRC alignment using Qwen3ForcedAligner.

    Uses the Qwen3ForcedAligner model to align lyrics to audio timestamps.
    Best for songs with known lyrics that need precise timing.

    For batch processing, pipe song IDs via stdin:
        sow-admin audio list --lrc incomplete --format ids | sow-admin audio align-lrc --stdin
    """
    if not song_id and not stdin:
        console.print("[red]Error: Either provide a song_id argument or use --stdin flag[/red]")
        raise typer.Exit(1)
    if song_id and stdin:
        console.print("[red]Error: Cannot use both song_id argument and --stdin flag[/red]")
        raise typer.Exit(1)
    if stdin and wait:
        console.print("[red]Error: --wait is not supported with --stdin (too many jobs)[/red]")
        raise typer.Exit(1)
    if language not in {"auto", "zh", "en"}:
        console.print("[red]Error: --lang must be one of: auto, zh, en[/red]")
        raise typer.Exit(1)

    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)

    try:
        analysis_client = AnalysisClient(config.analysis_url)
    except ValueError as e:
        console.print(f"[red]Analysis service not configured: {e}[/red]")
        raise typer.Exit(1)

    try:
        r2_client = R2Client(config.r2_bucket, config.r2_endpoint_url, config.r2_region)
    except ValueError:
        r2_client = None

    if stdin:
        song_ids = _read_song_ids_from_stdin()
        if not song_ids:
            console.print("[yellow]No song IDs provided via stdin[/yellow]")
            raise typer.Exit(0)
    else:
        song_ids = [song_id]

    if len(song_ids) == 1:
        _submit_forced_alignment_single(
            song_id=song_ids[0],
            db_client=db_client,
            analysis_client=analysis_client,
            language=language,
            force=force,
            use_vocals_stem=use_vocals_stem,
            wait=wait,
            console=console,
            r2_client=r2_client,
        )
    else:
        _submit_forced_alignment_batch(
            song_ids=song_ids,
            db_client=db_client,
            analysis_client=analysis_client,
            language=language,
            force=force,
            use_vocals_stem=use_vocals_stem,
            console=console,
            r2_client=r2_client,
        )


def _submit_embedding_single(
    song: "Song",
    analysis_client: "AnalysisClient",
    db_client: "DatabaseClient",
    console: "Console",
    force: bool = False,
) -> Optional[str]:
    from stream_of_worship.admin.db.models import Song

    lyrics_list = song.lyrics_list
    current_hash = _compute_content_hash(
        song.title, song.composer or "", song.lyrics_raw or "", lyrics_list
    )

    if not force:
        existing_hash = db_client.get_embedding_content_hash(song.id)
        if existing_hash == current_hash:
            console.print(f"  [dim]Skipping {song.id}: embedding up-to-date[/dim]")
            return None

    try:
        job_info = analysis_client.submit_embedding(
            song_id=song.id,
            title=song.title,
            composer=song.composer or "",
            lyrics_raw=song.lyrics_raw or "",
            lyrics_lines=lyrics_list,
        )
        console.print(f"  [green]Submitted[/green] embedding job {job_info.job_id} for {song.id}")
        return job_info.job_id
    except Exception as e:
        console.print(f"  [red]Failed[/red] to submit embedding for {song.id}: {e}")
        return None


def _write_embedding_result(
    job_info: "JobInfo",
    db_client: "DatabaseClient",
    console: "Console",
) -> bool:
    if not job_info.result or not hasattr(job_info.result, "embedding"):
        console.print(f"  [red]No embedding result[/red] for job {job_info.job_id}")
        return False

    result = job_info.result
    try:
        db_client.upsert_song_embedding(
            song_id=result.song_id,
            embedding=result.embedding,
            model_version=result.model_version,
            content_hash=result.content_hash,
        )
        db_client.upsert_song_line_embeddings(
            song_id=result.song_id,
            model_version=result.model_version,
            line_embeddings=[
                {
                    "line_index": le.line_index,
                    "line_text": le.line_text,
                    "embedding": le.embedding,
                }
                for le in result.line_embeddings
            ],
        )
        console.print(
            f"  [green]Wrote[/green] embedding for {result.song_id} "
            f"({len(result.line_embeddings)} lines)"
        )
        return True
    except Exception as e:
        console.print(f"  [red]Failed[/red] to write embedding for {result.song_id}: {e}")
        return False


@app.command("embed")
def embed_songs(
    song_id: Optional[str] = typer.Argument(None, help="Song ID to embed"),
    all_songs: bool = typer.Option(False, "--all", help="Embed all songs without embeddings"),
    force: bool = typer.Option(False, "--force", help="Re-embed even if content hash matches"),
    wait: bool = typer.Option(False, "--wait", help="Wait for all jobs to complete"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Generate text embeddings for songs using OpenAI text-embedding-3-small.

    Embeddings power semantic search in the webapp. Run this after scraping
    new songs or when lyrics are corrected.

    Examples:
        sow-admin audio embed song_0001
        sow-admin audio embed --all
        sow-admin audio embed --all --force
        sow-admin audio embed --all --wait
    """
    from rich.console import Console

    from stream_of_worship.admin.db.models import Song
    from stream_of_worship.admin.services.analysis import AnalysisClient, AnalysisServiceError

    console = Console()

    if not song_id and not all_songs:
        console.print("[red]Error:[/red] Provide a song_id or use --all")
        raise typer.Exit(1)

    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)
    try:
        analysis_client = AnalysisClient(config.analysis_url)
    except ValueError as e:
        console.print(f"[red]Analysis service not configured: {e}[/red]")
        raise typer.Exit(1)

    if song_id:
        song = db_client.get_song(song_id)
        if not song:
            console.print(f"[red]Error:[/red] Song {song_id} not found")
            raise typer.Exit(1)
        songs_to_embed = [song]
    elif all_songs:
        if force:
            songs_to_embed = db_client.get_all_songs_with_lyrics()
            console.print(
                f"Found {len(songs_to_embed)} songs with published recordings (force mode)"
            )
        else:
            songs_to_embed = db_client.get_songs_without_embeddings()
            console.print(
                f"Found {len(songs_to_embed)} songs without embeddings (with published recordings)"
            )
    else:
        songs_to_embed = []

    if not songs_to_embed:
        console.print("[dim]No songs to embed[/dim]")
        return

    job_ids: list[str] = []
    for song in songs_to_embed:
        jid = _submit_embedding_single(song, analysis_client, db_client, console, force=force)
        if jid:
            job_ids.append(jid)

    if not job_ids:
        console.print("[dim]No embedding jobs submitted[/dim]")
        return

    console.print(f"\nSubmitted {len(job_ids)} embedding jobs")

    if not wait:
        console.print("Use --wait to poll until jobs complete")
        return

    console.print("Waiting for jobs to complete...")
    failed = 0
    for jid in job_ids:
        try:
            result = analysis_client.wait_for_completion(jid, poll_interval=2.0, timeout=120.0)
            if result.status == "completed":
                _write_embedding_result(result, db_client, console)
            else:
                console.print(f"  [red]Job {jid} failed:[/red] {result.error_message}")
                failed += 1
        except AnalysisServiceError as e:
            console.print(f"  [red]Job {jid} error:[/red] {e}")
            failed += 1

    console.print(f"\nDone: {len(job_ids) - failed} succeeded, {failed} failed")
    db_client.close()


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
    from stream_of_worship.admin.services.asset_cache import AssetCache

    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)

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
    cache_dir = get_cache_dir()
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
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)

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
        incomplete_download = db_client.list_recordings(download_status="incomplete")

        # Deduplicate: a recording may be incomplete on multiple statuses
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
        for rec in incomplete_download:
            if rec.hash_prefix not in all_hashes:
                all_hashes.add(rec.hash_prefix)
                reconcile_queue.append(rec)

        if not reconcile_queue:
            console.print(
                "[green]No recordings with incomplete LRC, analysis, or download status.[/green]"
            )
        else:
            console.print(f"[cyan]Scanning R2 across {len(reconcile_queue)} recording(s)...[/cyan]")
            reconciled_lrc = 0
            reconciled_analysis = 0
            reconciled_download = 0
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
                                visibility_status="review",
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

                # Download status reconciliation
                if rec.download_status in ("pending", "processing", "failed"):
                    try:
                        audio_url = r2_client.audio_exists(rec.hash_prefix)
                        if audio_url:
                            db_client.update_recording_download(
                                hash_prefix=rec.hash_prefix,
                                download_status="completed",
                            )
                            if not rec.r2_audio_url:
                                db_client.update_recording_status(
                                    hash_prefix=rec.hash_prefix,
                                    r2_audio_url=audio_url,
                                )
                            reconciled_download += 1
                            console.print(
                                f"  [green]✓[/green] {rec.song_id or rec.hash_prefix}: "
                                f"download {rec.download_status} → completed"
                            )
                    except ClientError as e:
                        error_count += 1
                        console.print(
                            f"  [red]✗[/red] {rec.hash_prefix}: R2 error checking audio: {e}"
                        )

            parts = []
            if reconciled_lrc > 0:
                parts.append(f"{reconciled_lrc} LRC")
            if reconciled_analysis > 0:
                parts.append(f"{reconciled_analysis} analysis")
            if reconciled_download > 0:
                parts.append(f"{reconciled_download} download")
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

        # Also check for pending LRC jobs (exclude soft-deleted recordings and songs)
        cursor = db_client.connection.cursor()
        cursor.execute(
            "SELECT r.hash_prefix FROM recordings r "
            "LEFT JOIN songs s ON r.song_id = s.id "
            "WHERE r.lrc_status IN ('pending', 'processing') "
            "AND r.deleted_at IS NULL "
            "AND (s.deleted_at IS NULL OR s.id IS NULL)"
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
                                    visibility_status="review",
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

    # List pending recordings (exclude soft-deleted)
    cursor = db_client.connection.cursor()
    cursor.execute("""
        SELECT r.*, s.title as song_title
        FROM recordings r
        LEFT JOIN songs s ON r.song_id = s.id
        WHERE (r.analysis_status != 'completed' OR r.lrc_status != 'completed')
          AND r.deleted_at IS NULL
          AND (s.deleted_at IS NULL OR s.id IS NULL)
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


@app.command("cancel")
def cancel_jobs(
    job_id: Optional[str] = typer.Argument(None, help="Job ID to cancel"),
    all: bool = typer.Option(False, "--all", "-a", help="Cancel all queued and processing jobs"),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show what would be cancelled without cancelling"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Cancel jobs on the Analysis Service.

    Cancel a specific job by ID, or cancel all queued/processing jobs with --all.

    Examples:
        sow-admin audio cancel job_abc123           # Cancel a specific job
        sow-admin audio cancel --all                # Cancel all jobs (with confirmation)
        sow-admin audio cancel --all --yes          # Cancel all jobs (skip confirmation)
        sow-admin audio cancel --all --dry-run      # Preview what would be cancelled

    Requires SOW_ADMIN_API_KEY environment variable to be set.
    """
    if not job_id and not all:
        console.print(
            "[red]Error: Provide a JOB_ID argument or use --all to cancel all jobs.[/red]"
        )
        raise typer.Exit(1)

    if job_id and all:
        console.print("[red]Error: Cannot use both JOB_ID and --all together.[/red]")
        raise typer.Exit(1)

    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    try:
        client = AnalysisClient(config.analysis_url)
    except ValueError as e:
        console.print(f"[red]Analysis service not configured: {e}[/red]")
        raise typer.Exit(1)

    if all:
        _cancel_all_jobs(client, dry_run, yes, console)
    else:
        _cancel_single_job(client, job_id, dry_run, yes, console)


def _cancel_single_job(
    client: AnalysisClient,
    job_id: str,
    dry_run: bool,
    yes: bool,
    console: Console,
) -> None:
    """Cancel a single job by ID."""
    if dry_run:
        try:
            job = client.get_job(job_id)
        except AnalysisServiceError as e:
            if e.status_code == 404:
                console.print(f"[red]Job not found: {job_id}[/red]")
                raise typer.Exit(1)
            console.print(f"[red]Failed to get job status: {e}[/red]")
            raise typer.Exit(1)

        if job.status in ("completed", "failed", "cancelled"):
            console.print(
                f"[yellow]Job {job_id} is already {job.status} (nothing to cancel)[/yellow]"
            )
            return

        console.print(
            Panel.fit(
                f"[cyan]Job ID:[/cyan] {job_id}\n"
                f"[cyan]Type:[/cyan] {job.job_type}\n"
                f"[cyan]Status:[/cyan] {_colorize_status(job.status)}\n"
                f"[cyan]Progress:[/cyan] {int(job.progress * 100)}%\n"
                f"[cyan]Stage:[/cyan] {job.stage or '-'}",
                title="Dry Run - Would Cancel Job",
                border_style="yellow",
            )
        )
        return

    try:
        job = client.cancel_job(job_id)
    except AnalysisServiceError as e:
        if e.status_code == 404:
            console.print(f"[red]Job not found: {job_id}[/red]")
        elif e.status_code == 401:
            console.print(f"[red]Authentication failed: Invalid admin API key[/red]")
        elif e.status_code == 503:
            console.print(f"[red]Admin API key not configured on server[/red]")
        else:
            console.print(f"[red]Failed to cancel job: {e}[/red]")
        raise typer.Exit(1)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    console.print(
        Panel.fit(
            f"[green]Job cancelled successfully![/green]\n\n"
            f"[cyan]Job ID:[/cyan] {job.job_id}\n"
            f"[cyan]Type:[/cyan] {job.job_type}\n"
            f"[cyan]Status:[/cyan] {_colorize_status(job.status)}",
            title="Job Cancelled",
            border_style="green",
        )
    )


def _cancel_all_jobs(
    client: AnalysisClient,
    dry_run: bool,
    yes: bool,
    console: Console,
) -> None:
    """Cancel all queued and processing jobs."""
    try:
        jobs = client.list_jobs()
    except AnalysisServiceError as e:
        console.print(f"[red]Failed to list jobs: {e}[/red]")
        raise typer.Exit(1)

    cancellable_jobs = [j for j in jobs if j.status in ("queued", "processing")]

    if not cancellable_jobs:
        console.print("[green]No queued or processing jobs to cancel.[/green]")
        return

    if dry_run:
        table = Table(title=f"Dry Run - Would Cancel {len(cancellable_jobs)} Job(s)")
        table.add_column("Job ID", style="dim", no_wrap=True)
        table.add_column("Type", style="cyan")
        table.add_column("Status", style="yellow")
        table.add_column("Progress", justify="right")
        table.add_column("Stage")

        for job in cancellable_jobs:
            table.add_row(
                job.job_id,
                job.job_type,
                _colorize_status(job.status),
                f"{int(job.progress * 100)}%",
                job.stage or "-",
            )

        console.print(table)
        return

    console.print(f"[cyan]Found {len(cancellable_jobs)} job(s) to cancel:[/cyan]")
    for job in cancellable_jobs[:10]:
        console.print(f"  • {job.job_id} ({job.job_type}, {job.status})")
    if len(cancellable_jobs) > 10:
        console.print(f"  ... and {len(cancellable_jobs) - 10} more")

    if not yes:
        if not _prompt_confirmation(f"Cancel {len(cancellable_jobs)} job(s)?"):
            console.print("[yellow]Cancelled.[/yellow]")
            raise typer.Exit(0)

    try:
        result = client.cancel_all_jobs()
    except AnalysisServiceError as e:
        if e.status_code == 401:
            console.print(f"[red]Authentication failed: Invalid admin API key[/red]")
        elif e.status_code == 503:
            console.print(f"[red]Admin API key not configured on server[/red]")
        else:
            console.print(f"[red]Failed to cancel jobs: {e}[/red]")
        raise typer.Exit(1)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    cancelled_count = result.get("cancelled_count", 0)
    cancelled_ids = result.get("cancelled_job_ids", [])

    console.print(
        Panel.fit(
            f"[green]Successfully cancelled {cancelled_count} job(s)![/green]\n\n"
            f"[dim]Cancelled job IDs:[/dim]\n"
            + "\n".join(f"  • {jid}" for jid in cancelled_ids[:20])
            + (f"\n  ... and {len(cancelled_ids) - 20} more" if len(cancelled_ids) > 20 else ""),
            title="Jobs Cancelled",
            border_style="green",
        )
    )


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
    # Get all non-completed recordings (exclude soft-deleted)
    cursor = db_client.connection.cursor()
    cursor.execute("""
        SELECT * FROM recordings
        WHERE (analysis_status IN ('pending', 'processing', 'failed')
           OR lrc_status IN ('pending', 'processing', 'failed'))
          AND deleted_at IS NULL
        """)
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

    # Load config
    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print(
            "[red]Config file not found. Please create it using 'sow-admin config init'[/red]"
        )
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[red]Error loading config: {e}[/red]")
        raise typer.Exit(1)

    # Get database client
    db_client = get_db_client(config)

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
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)

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
    from stream_of_worship.admin.services.asset_cache import AssetCache

    # Use the admin cache directory
    cache_dir = get_cache_dir()
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
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)

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

    # Capture ETag before upload for stale-object protection
    expected_etag: Optional[str] = None
    try:
        identity = r2_client.get_lrc_identity(recording.hash_prefix)
        if identity.exists:
            expected_etag = identity.etag
    except Exception as e:
        console.print(
            f"[yellow]Warning: Could not capture ETag for stale-object check: {e}[/yellow]"
        )

    # Upload to R2 with backup + ETag protection
    console.print("[cyan]Uploading LRC to R2...[/cyan]")
    try:
        from stream_of_worship.admin.services.r2 import StaleObjectError, BackupFailedError

        r2_url = r2_client.upload_official_lrc(
            recording.hash_prefix, lrc_file, expected_etag=expected_etag
        )
        console.print(f"[green]Uploaded: {r2_url}[/green]")
    except StaleObjectError as e:
        console.print(
            f"[red]Upload failed: {e}. The official LRC was modified after you started.[/red]"
        )
        raise typer.Exit(1)
    except BackupFailedError as e:
        console.print(f"[red]Upload failed: {e}. Backup of existing LRC failed.[/red]")
        raise typer.Exit(1)
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


@app.command("edit-lrc")
def edit_lrc(
    song_id: str = typer.Argument(..., help="Song ID to edit LRC for"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Interactively edit LRC timestamps for a song recording.

    Downloads/caches the song recording and transcribed LRC, then launches
    a Textual TUI editor for live timestamp alignment, text editing, and
    upload to R2.
    """
    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)
    cache_dir = get_cache_dir()

    recording = db_client.get_recording_by_song_id(song_id)
    if not recording:
        console.print(
            f"[red]No recording found for song: {song_id}. "
            f"Run 'sow-admin audio download {song_id}' first.[/red]"
        )
        raise typer.Exit(1)

    song = db_client.get_song(song_id)
    song_title = song.title if song else "Unknown"

    try:
        r2_client = R2Client(
            bucket=config.r2_bucket,
            endpoint_url=config.r2_endpoint_url,
            region=config.r2_region,
        )
    except ValueError as e:
        console.print(f"[red]R2 configuration error: {e}[/red]")
        raise typer.Exit(1)

    console.print(f"[cyan]Downloading audio for: {song_title}[/cyan]")
    audio_cache_dir = cache_dir / recording.hash_prefix / "audio"
    audio_cache_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_cache_dir / "audio.mp3"

    if not audio_path.exists():
        try:
            r2_client.download_audio(recording.hash_prefix, audio_path)
        except Exception as e:
            console.print(f"[red]Failed to download audio: {e}[/red]")
            console.print(
                "[red]Audio is required for timestamp alignment. Cannot open editor.[/red]"
            )
            raise typer.Exit(1)
    else:
        try:
            r2_client.audio_exists(recording.hash_prefix)
        except ClientError:
            console.print(
                f"[yellow]Warning: Could not verify audio in R2. Using cached file.[/yellow]"
            )

    transcribed_content: Optional[str] = None
    transcribed_identity = r2_client.get_lrc_identity(recording.hash_prefix)
    source_mode = "catalog"

    if transcribed_identity.exists:
        console.print("[cyan]Downloading transcribed LRC from R2...[/cyan]")
        try:
            transcribed_content = r2_client.download_lrc_content(recording.hash_prefix)
            if transcribed_content:
                source_mode = "r2"

                lrc_cache_path = cache_dir / recording.hash_prefix / "lrc" / "lyrics.lrc"
                lrc_cache_path.parent.mkdir(parents=True, exist_ok=True)
                lrc_cache_path.write_text(transcribed_content, encoding="utf-8")
        except Exception as e:
            console.print(f"[red]Failed to download transcribed LRC: {e}[/red]")
            raise typer.Exit(1)

    from stream_of_worship.admin.editor.autosave import (
        autosave_exists,
        load_autosave,
        AutosaveState,
    )
    from stream_of_worship.admin.editor.state import EditorState
    from stream_of_worship.admin.services.lrc_parser import LRCPreservedLine

    if autosave_exists(cache_dir, recording.hash_prefix):
        console.print("[yellow]Autosave recovery file found![/yellow]")
        console.print("[dim]Resume previous editing session, discard it, or save it aside?[/dim]")
        choice = _prompt_choice("Choose:", ["Resume", "Discard", "Save aside and start fresh"])
        if choice == 0:
            autosave_state = load_autosave(cache_dir, recording.hash_prefix)
            if autosave_state:
                editor_state = EditorState(
                    timed_lines=autosave_state.timed_lines,
                    preserved_lines=autosave_state.preserved_lines,
                    original_serialized=transcribed_content or "",
                    original_preserved_lines=[],
                    transcribed_identity=autosave_state.transcribed_identity,
                    dirty=autosave_state.dirty,
                    source_mode=autosave_state.source_mode,
                    selected_index=autosave_state.selected_index,
                    song_title=song_title,
                    hash_prefix=recording.hash_prefix,
                    audio_path=str(audio_path),
                    audio_duration=recording.duration_seconds,
                    tempo_bpm=autosave_state.tempo_bpm,
                    padding_quarters=autosave_state.padding_quarters,
                    original_timestamps=autosave_state.original_timestamps,
                )
                if editor_state.padding_quarters != 0:
                    offset = editor_state.padding_offset_seconds
                    for i, line in enumerate(editor_state.timed_lines):
                        if i < len(editor_state.original_timestamps):
                            line.time_seconds = max(
                                0.0, editor_state.original_timestamps[i] + offset
                            )
            else:
                console.print("[red]Failed to load autosave. Starting fresh.[/red]")
                editor_state = _build_fresh_editor_state(
                    transcribed_content,
                    song,
                    recording,
                    song_title,
                    audio_path,
                    transcribed_identity,
                    source_mode,
                )
        elif choice == 1:
            from stream_of_worship.admin.editor.autosave import clear_autosave

            clear_autosave(cache_dir, recording.hash_prefix)
            editor_state = _build_fresh_editor_state(
                transcribed_content,
                song,
                recording,
                song_title,
                audio_path,
                transcribed_identity,
                source_mode,
            )
        else:
            from stream_of_worship.admin.editor.upload import save_local_draft

            autosave_state = load_autosave(cache_dir, recording.hash_prefix)
            if autosave_state:
                draft_content = serialize_lrc(
                    autosave_state.timed_lines, autosave_state.preserved_lines
                )
                save_local_draft(cache_dir, recording.hash_prefix, draft_content)
                console.print("[green]Autosave saved as local draft.[/green]")
            from stream_of_worship.admin.editor.autosave import clear_autosave

            clear_autosave(cache_dir, recording.hash_prefix)
            editor_state = _build_fresh_editor_state(
                transcribed_content,
                song,
                recording,
                song_title,
                audio_path,
                transcribed_identity,
                source_mode,
            )
    else:
        editor_state = _build_fresh_editor_state(
            transcribed_content,
            song,
            recording,
            song_title,
            audio_path,
            transcribed_identity,
            source_mode,
        )

    console.print(f"[cyan]Launching LRC editor for: {song_title}[/cyan]")
    console.print("[dim]Press Ctrl+C in the editor to quit.[/dim]")

    from stream_of_worship.admin.editor.app import LRCEditorApp
    from stream_of_worship.admin.services.playback import PlaybackService

    playback = PlaybackService()
    app = LRCEditorApp(
        editor_state=editor_state,
        playback_service=playback,
        cache_dir=cache_dir,
        r2_client=r2_client,
        db_client=db_client,
        hash_prefix=recording.hash_prefix,
        original_transcribed_content=transcribed_content,
    )
    app.run()

    playback.stop()


def _build_fresh_editor_state(
    transcribed_content: Optional[str],
    song: Optional[Song],
    recording: Recording,
    song_title: str,
    audio_path: Path,
    transcribed_identity: R2ObjectIdentity,
    source_mode: str,
) -> "EditorState":
    """Build a fresh EditorState from transcribed content or catalog lyrics."""
    from stream_of_worship.admin.editor.state import EditorState
    from stream_of_worship.admin.services.lrc_parser import LRCPreservedLine

    if transcribed_content:
        parsed = parse_lrc_full(transcribed_content)
        timed_lines = parsed.timed_lines
        preserved_lines = parsed.preserved_lines
        original_serialized = serialize_lrc(timed_lines, preserved_lines)
        original_preserved_lines = list(preserved_lines)
        dirty = False
    else:
        lyrics_lines = song.lyrics_lines if song else None
        lyrics_raw = song.lyrics_raw if song else None
        timed_lines = build_draft_from_catalog(lyrics_lines, lyrics_raw)
        preserved_lines = []
        original_serialized = ""
        original_preserved_lines = []
        dirty = True
        source_mode = "catalog"

    return EditorState(
        timed_lines=timed_lines,
        preserved_lines=preserved_lines,
        original_serialized=original_serialized,
        original_preserved_lines=original_preserved_lines,
        transcribed_identity=transcribed_identity,
        dirty=dirty,
        source_mode=source_mode,
        selected_index=0,
        song_title=song_title,
        hash_prefix=recording.hash_prefix,
        audio_path=str(audio_path),
        audio_duration=recording.duration_seconds,
        tempo_bpm=recording.tempo_bpm,
    )


def _prompt_choice(prompt: str, choices: list[str]) -> int:
    """Prompt the user to choose from a list of options.

    Returns:
        Index of the chosen option
    """
    console.print(f"\n[bold]{prompt}[/bold]")
    for i, choice in enumerate(choices):
        console.print(f"  [{i + 1}] {choice}")

    while True:
        try:
            selection = int(input("Enter choice: ")) - 1
            if 0 <= selection < len(choices):
                return selection
            console.print(f"[red]Please enter a number between 1 and {len(choices)}[/red]")
        except (ValueError, EOFError):
            console.print(f"[red]Please enter a number between 1 and {len(choices)}[/red]")


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
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)

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
    from stream_of_worship.admin.services.asset_cache import AssetCache

    cache_dir = get_cache_dir()
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
    from stream_of_worship.admin.services.playback import PlaybackService

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
    download: bool = typer.Option(False, "--download", help="Run the download step"),
    lrc: bool = typer.Option(False, "--lrc", help="Run the LRC step"),
    analyze: bool = typer.Option(False, "--analyze", help="Run the analysis step"),
    embedding: bool = typer.Option(False, "--embedding", help="Run the embedding step"),
    all_steps: bool = typer.Option(False, "--all-steps", help="Run all steps in order"),
    analysis_tier: str = typer.Option(
        "fast", "--analysis-tier", help="Analysis tier: fast (default) or full"
    ),
    force: bool = typer.Option(
        False, "--force", help="Force re-run of a single step (requires exactly one step flag)"
    ),
    resume: Optional[Path] = typer.Option(
        None, "--resume", help="Resume from a manifest file (skip submission, only re-poll)"
    ),
    stale_after: int = typer.Option(
        120,
        "--stale-after",
        help="Minutes after which a 'processing' song is treated as lost (default: 120)",
    ),
    download_concurrency: int = typer.Option(
        3, "--download-concurrency", help="Max concurrent downloads (default: 3)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be processed without executing"
    ),
    format: str = typer.Option("rich", "--format", help="Output format (rich, json)"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Batch process songs: download audio, generate LRC, analyze, and embed.

    Each phase is gated strictly by its step flag: --download, --lrc,
    --analyze, --embedding, or --all-steps. No phase runs as a side effect
    of another.

    Examples:
        sow-admin audio batch --analysis-status incomplete --analyze \\
            --analysis-tier fast --limit 500
        sow-admin audio batch --all-steps
        sow-admin audio batch --resume ~/.local/share/sow-admin/batch/2026-06-30T0215_manifest.json
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

    valid_analysis_statuses = {
        "pending",
        "processing",
        "partial",
        "completed",
        "failed",
        "incomplete",
    }
    if analysis_status and analysis_status not in valid_analysis_statuses:
        console.print(
            f"[red]Invalid analysis status: {analysis_status}. Must be one of: "
            f"{', '.join(sorted(valid_analysis_statuses))}[/red]"
        )
        raise typer.Exit(1)

    # Validate analysis tier
    if analysis_tier not in ("fast", "full"):
        console.print(
            f"[red]Invalid analysis tier: {analysis_tier}. Must be 'fast' or 'full'[/red]"
        )
        raise typer.Exit(1)

    # --resume mutual exclusivity
    if resume is not None:
        resume_conflicts = [
            ("--album", album),
            ("--song", song),
            ("--lrc-status", lrc_status),
            ("--download-status", download_status),
            ("--analysis-status", analysis_status),
            ("--stdin", stdin),
            ("--limit", limit),
            ("--download", download),
            ("--lrc", lrc),
            ("--analyze", analyze),
            ("--embedding", embedding),
            ("--all-steps", all_steps),
            ("--force", force),
        ]
        for flag_name, flag_val in resume_conflicts:
            if flag_val:
                console.print(f"[red]--resume is mutually exclusive with {flag_name}.[/red]")
                raise typer.Exit(1)

    # Resolve selected steps
    step_flags = {
        "download": download,
        "lrc": lrc,
        "analyze": analyze,
        "embedding": embedding,
    }
    selected_steps: List[str] = [s for s, v in step_flags.items() if v]

    if all_steps:
        selected_steps = ["download", "lrc", "analyze", "embedding"]
    elif not selected_steps and resume is None:
        console.print(
            "[red]No step flags selected. Specify at least one of "
            "--download, --lrc, --analyze, --embedding, or --all-steps.[/red]"
        )
        raise typer.Exit(1)

    # Force scoping
    if force:
        if all_steps:
            console.print(
                "[red]--force with --all-steps is not supported. Cascading overrides "
                "across download/LRC/analysis/embedding are unsafe; specify exactly "
                "one step flag alongside --force.[/red]"
            )
            raise typer.Exit(1)
        if len(selected_steps) != 1:
            console.print(
                "[red]--force requires exactly one step flag "
                "(--download, --lrc, --analyze, or --embedding).[/red]"
            )
            raise typer.Exit(1)
        if "download" in selected_steps:
            console.print(
                "[red]--force --download is not supported. Re-download changes "
                "content_hash/hash_prefix and orphans downstream R2 artifacts. "
                "Use the two-step soft-delete + purge workflow:[/red]\n"
                "  sow-admin audio delete --recording --hash-prefix <old>\n"
                "  sow-admin maintenance purge-soft-deletes --entity recordings "
                "--hash-prefix <old> --confirm\n"
                "  sow-admin audio batch --song <song_id> --download"
            )
            raise typer.Exit(1)

    # Warn if analysis tier given without analyze step
    if analysis_tier and "analyze" not in selected_steps and resume is None:
        console.print(
            f"[yellow]--analysis-tier {analysis_tier} ignored (no --analyze step selected).[/yellow]"
        )

    # Load config
    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)

    # --resume path: skip selection, re-poll manifest
    if resume is not None:
        manifest_data = _load_manifest(resume)
        if manifest_data is None:
            console.print(f"[red]Could not read manifest: {resume}[/red]")
            raise typer.Exit(1)

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

        results = _resume_from_manifest(
            manifest_data=manifest_data,
            manifest_path=resume,
            db_client=db_client,
            r2_client=r2_client,
            analysis_client=analysis_client,
            stale_after_minutes=stale_after,
            console=console,
            database_url=config.get_connection_url(),
            download_concurrency=download_concurrency,
        )
        _print_stats(results, db_client, console, format)

        # Exit nonzero if any step has a failure
        failed_any = any(v == "failed" for r in results.values() for v in r.values())
        if failed_any:
            raise typer.Exit(1)
        return

    # Normal path: resolve song IDs
    song_ids = _resolve_song_ids(
        db_client, album, song, lrc_status, download_status, analysis_status, stdin, limit
    )
    if not song_ids:
        console.print("[yellow]No songs found matching the criteria.[/yellow]")
        raise typer.Exit(0)

    if dry_run:
        _print_dry_run_v4(
            db_client,
            song_ids,
            selected_steps,
            force,
            analysis_tier,
            stale_after,
        )
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
        selected_steps=selected_steps,
        force=force,
        analysis_tier=analysis_tier,
        stale_after_minutes=stale_after,
        console=console,
        database_url=config.get_connection_url(),
        download_concurrency=download_concurrency,
    )

    # Print final stats
    _print_stats(results, db_client, console, format)

    # Exit nonzero if any selected step has a failure
    failed_any = any(
        results.get(sid, {}).get(step) == "failed" for sid in song_ids for step in selected_steps
    )
    if failed_any:
        raise typer.Exit(1)


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
    if stdin:
        song_ids = _read_song_ids_from_stdin()
        if limit:
            song_ids = song_ids[:limit]
        return song_ids

    song_ids: list[str] = []

    rows = db_client.list_recordings_with_songs(
        status=analysis_status,
        lrc_status=lrc_status,
        limit=None,
        sort_by="created",
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

        if recording.song_id not in song_ids:
            song_ids.append(recording.song_id)

    has_status_filters = download_status or lrc_status or analysis_status
    if not has_status_filters:
        songs = db_client.list_songs(album=album, limit=None)
        for s in songs:
            if s.id in song_ids:
                continue
            if song and (not s.title or song.lower() not in s.title.lower()):
                continue
            existing_recording = db_client.get_recording_by_song_id(s.id)
            if not existing_recording:
                song_ids.append(s.id)

    if limit:
        song_ids = song_ids[:limit]
    return song_ids


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
        elif song:
            console.print(f"  [yellow]•[/yellow] {song.title} (no recording - will download)")
            console.print(f"    [dim]Album:[/dim] {song.album_name or '-'}")
        else:
            console.print(f"  [red]•[/red] {song_id} (song not found)")

    console.print(f"\n[dim]Total: {len(song_ids)} song(s)[/dim]")


def _print_dry_run_v4(
    db_client: DatabaseClient,
    song_ids: list[str],
    selected_steps: List[str],
    force: bool,
    analysis_tier: str,
    stale_after: int,
) -> None:
    """Print dry run information for the v4 batch command.

    Args:
        db_client: Database client
        song_ids: List of song IDs to show
        selected_steps: Steps that will run
        force: Whether force is set
        analysis_tier: fast or full
        stale_after: Staleness threshold in minutes
    """
    batch_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S") + "_batch"
    console.print("[cyan]Dry run mode[/cyan]")
    console.print()
    console.print(f"[dim]Batch ID:[/dim] {batch_id}")
    console.print(f"[dim]Selected steps:[/dim] {', '.join(selected_steps)}")
    console.print(f"[dim]Force:[/dim] {force}")
    if "analyze" in selected_steps:
        console.print(f"[dim]Analysis tier:[/dim] {analysis_tier}")
    console.print(f"[dim]Stale after:[/dim] {stale_after} minutes")
    console.print(f"[dim]Ordering:[/dim] recordings.created_at ASC, hash_prefix ASC")
    console.print(f"[dim]Count:[/dim] {len(song_ids)} song(s)")
    console.print()

    for song_id in song_ids:
        song = db_client.get_song(song_id)
        recording = db_client.get_recording_by_song_id(song_id)

        if song and recording:
            console.print(f"  [cyan]•[/cyan] {song.title} ({recording.hash_prefix})")
            console.print(f"    [dim]Album:[/dim] {song.album_name or '-'}")
            console.print(f"    [dim]Download:[/dim] {recording.download_status}")
            console.print(f"    [dim]LRC:[/dim] {recording.lrc_status}")
            console.print(f"    [dim]Analysis:[/dim] {recording.analysis_status}")
        elif song:
            console.print(f"  [yellow]•[/yellow] {song.title} (no recording - will download)")
            console.print(f"    [dim]Album:[/dim] {song.album_name or '-'}")
        else:
            console.print(f"  [red]•[/red] {song_id} (song not found)")


import re


def _extract_chinese_title_from_youtube(video_title: Optional[str]) -> Optional[str]:
    """Extract the Chinese title from YouTube video title format.

    YouTube MV titles are typically formatted as:
    "【一生敬拜祢 All the Days of My Life】官方歌詞版MV ..."

    This function extracts the Chinese portion from the first bracketed segment.

    Args:
        video_title: YouTube video title

    Returns:
        Chinese title or None if not found
    """
    if not video_title:
        return None

    match = re.match(r"【([^】\s]+)", video_title)
    if match:
        return match.group(1)

    return None


def _download_and_create_recording(
    song_id: str,
    song: Song,
    db_client: DatabaseClient,
    r2_client: R2Client,
    console: Console,
) -> tuple[Optional[Recording], Optional[str]]:
    """Download audio from YouTube, upload to R2, and create a Recording entry.

    Args:
        song_id: Song ID
        song: Song object with metadata
        db_client: Database client
        r2_client: R2 client
        console: Rich console

    Returns:
        Tuple of (Recording or None, error message or None)
    """
    try:
        downloader = YouTubeDownloader()

        album_for_query = song.album_name
        if song.title == song.album_name:
            album_for_query = None

        query = downloader.build_search_query(
            title=song.title,
            composer=song.composer,
            album=album_for_query,
            suffix=OFFICIAL_LYRICS_SUFFIX,
        )

        console.print(f"  Downloading from YouTube...")
        audio_path, youtube_url, video_title = downloader.download_with_info(query)

        chinese_title = _extract_chinese_title_from_youtube(video_title)
        if chinese_title and chinese_title != song.title:
            console.print(
                f"  [yellow]⚠ Title mismatch: expected '{song.title}', got '{chinese_title}' from video '{video_title}'[/yellow]"
            )
            console.print(
                f"  [yellow]  Use 'sow_admin audio download {song_id} --youtube-url <url>' to manually specify the correct video.[/yellow]"
            )
            audio_path.unlink(missing_ok=True)
            return (
                None,
                f"title mismatch: expected '{song.title}', got '{chinese_title}' from video '{video_title}'",
            )

        file_size = audio_path.stat().st_size
        console.print(f"  [dim]Downloaded: {audio_path.name} ({_format_size_mb(file_size)})[/dim]")

        content_hash = compute_file_hash(audio_path)
        prefix = get_hash_prefix(content_hash)

        duration = probe_duration(audio_path)
        if duration:
            console.print(f"  [dim]Duration: {duration:.1f}s[/dim]")

        existing_recording = db_client.get_recording_by_hash(prefix)
        if existing_recording:
            existing_song = (
                db_client.get_song(existing_recording.song_id)
                if existing_recording.song_id
                else None
            )
            existing_song_title = (
                existing_song.title if existing_song else existing_recording.song_id
            )
            console.print(
                f"  [yellow]⚠ Duplicate hash: audio matches existing recording for song '{existing_song_title}'[/yellow]"
            )
            console.print(
                f"  [yellow]  This song likely downloaded the wrong video. Use 'sow_admin audio download {song_id} --youtube-url <url>' to manually specify the correct video.[/yellow]"
            )
            audio_path.unlink(missing_ok=True)
            return None, f"duplicate hash: shares audio with song '{existing_song_title}'"

        console.print(f"  Uploading to R2...")
        r2_url = r2_client.upload_audio(audio_path, prefix)
        console.print(f"  [green]→ Uploaded: {r2_url}[/green]")

        recording = Recording(
            content_hash=content_hash,
            hash_prefix=prefix,
            song_id=song_id,
            original_filename=audio_path.name,
            file_size_bytes=file_size,
            imported_at=datetime.now().isoformat(),
            r2_audio_url=r2_url,
            download_status="completed",
            youtube_url=youtube_url,
            duration_seconds=duration,
        )
        db_client.insert_recording(recording)
        console.print(f"  [green]✓ Recording created (hash_prefix: {prefix})[/green]")

        audio_path.unlink(missing_ok=True)

        return recording, None

    except Exception as e:
        console.print(f"  [red]✗ Download failed: {e}[/red]")
        return None, str(e)


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

    if recording.download_status == "completed" and recording.r2_audio_url:
        return {"download": "skipped_r2", "skip_reason": "download completed, audio on R2"}

    if r2_client.audio_exists(hash_prefix):
        db_client.update_recording_download(hash_prefix, "completed")
        return {"download": "skipped_r2", "skip_reason": "audio found on R2"}

    db_client.update_recording_download(hash_prefix, "processing")

    try:
        song = db_client.get_song(song_id)
        if not song:
            raise ValueError(f"Song not found: {song_id}")

        downloader = YouTubeDownloader()

        album_for_query = song.album_name
        if song.title == song.album_name:
            album_for_query = None

        query = downloader.build_search_query(
            title=song.title,
            composer=song.composer,
            album=album_for_query,
            suffix=OFFICIAL_LYRICS_SUFFIX,
        )

        console.print(f"[{song_id}] Downloading audio from YouTube...")
        audio_path, youtube_url, video_title = downloader.download_with_info(query)

        chinese_title = _extract_chinese_title_from_youtube(video_title)
        if chinese_title and chinese_title != song.title:
            console.print(
                f"[{song_id}] [yellow]⚠ Title mismatch: expected '{song.title}', got '{chinese_title}' from video '{video_title}'[/yellow]"
            )
            console.print(
                f"[{song_id}] [yellow]  Use 'sow_admin audio download {song_id} --youtube-url <url>' to manually specify the correct video.[/yellow]"
            )
            audio_path.unlink(missing_ok=True)
            db_client.update_recording_download(hash_prefix, "failed")
            return {
                "download": "failed",
                "error": f"title mismatch: expected '{song.title}', got '{chinese_title}' from video '{video_title}'",
            }

        content_hash = compute_file_hash(audio_path)
        prefix = get_hash_prefix(content_hash)

        r2_url = r2_client.upload_audio(audio_path, prefix)
        console.print(f"[{song_id}] [green]→[/green] Uploaded to R2")

        duration = probe_duration(audio_path)

        db_client.update_recording_r2_url(hash_prefix, r2_url)
        if youtube_url:
            db_client.update_recording_youtube_url(hash_prefix, youtube_url)
        db_client.update_recording_download(hash_prefix, "completed")
        if duration is not None:
            db_client.update_recording_duration(hash_prefix, duration)

        audio_path.unlink(missing_ok=True)

        return {"download": "completed"}

    except Exception as e:
        db_client.update_recording_download(hash_prefix, "failed")
        console.print(f"[{song_id}] [red]✗[/red] Download failed: {e}")
        return {"download": "failed", "error": str(e)}


def _get_manifest_dir() -> Path:
    """Get the manifest directory (XDG-aware, overridable via env)."""
    env_dir = os.environ.get("SOW_BATCH_MANIFEST_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".local" / "share" / "sow-admin" / "batch"


def _write_manifest(
    batch_id: str,
    results: dict,
    manifest_dir: Path,
    selected_steps: List[str],
    analysis_tier: str,
    stale_after_minutes: int,
    started_at: str,
    manifest_entries: List[dict],
) -> Optional[Path]:
    """Write (or rewrite) the batch manifest to disk.

    Args:
        batch_id: Batch identifier
        results: In-memory results dict
        manifest_dir: Directory for manifest files
        selected_steps: Steps selected for this batch
        analysis_tier: fast or full
        stale_after_minutes: Staleness threshold
        started_at: ISO timestamp of batch start
        manifest_entries: Per-(song_id, step, tier) manifest rows

    Returns:
        Path to the manifest file, or None on failure
    """
    try:
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / f"{batch_id}_manifest.json"
        manifest = {
            "batch_id": batch_id,
            "started_at": started_at,
            "selected_steps": selected_steps,
            "analysis_tier": analysis_tier,
            "stale_after_minutes": stale_after_minutes,
            "songs": manifest_entries,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
        return manifest_path
    except OSError as e:
        logger.warning(f"Failed to write manifest: {e}")
        return None


def _load_manifest(manifest_path: Path) -> Optional[dict]:
    """Load a manifest from disk.

    Args:
        manifest_path: Path to the manifest file

    Returns:
        Parsed manifest dict, or None on failure
    """
    try:
        return json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"Failed to load manifest {manifest_path}: {e}")
        return None


def _submit_lrc_for_song(
    song_id: str,
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    r2_client: R2Client,
    force: bool,
    stale_after_minutes: int,
    console: Console,
    results: dict,
    active_lrc_jobs: Dict[str, str],
    lrc_attempted: set,
    _add_manifest_entry: Any,
) -> str:
    """Submit (or reuse) the LRC job for a single song.

    Encapsulates the per-song LRC submission logic shared by the Phase 2
    submit loop and the eager download→LRC handoff. The caller is
    responsible for skipping songs whose download failed or whose LRC is
    already marked completed in ``results``.

    Args:
        song_id: Song ID
        db_client: Database client
        analysis_client: Analysis service client
        r2_client: R2 client
        force: Force re-generate LRC
        stale_after_minutes: Staleness threshold for reusing processing jobs
        console: Rich console
        results: Results dict to update
        active_lrc_jobs: Dict of song_id -> job_id to populate
        lrc_attempted: Set of song_ids that have had a submit attempt
        _add_manifest_entry: Callback to record a manifest entry

    Returns:
        Status string: 'submitted', 'reused', 'skipped_r2',
        'skipped_no_recording', 'skipped_no_lyrics', or 'failed'.
    """
    lrc_attempted.add(song_id)

    recording = db_client.get_recording_by_song_id(song_id)
    if not recording:
        console.print(f"  [yellow]→ {song_id} (skipped: no recording)[/yellow]")
        return "skipped_no_recording"

    song = db_client.get_song(song_id)
    if not song or not song.lyrics_raw:
        console.print(f"  [yellow]→ {song_id} (skipped: no lyrics)[/yellow]")
        return "skipped_no_lyrics"

    # Check R2 (skip when force)
    if not force:
        lrc_url = r2_client.lrc_exists(recording.hash_prefix)
        if lrc_url:
            db_client.update_recording_lrc(
                recording.hash_prefix,
                lrc_url,
                visibility_status="review",
            )
            results[song_id]["lrc"] = "completed"
            results[song_id]["lrc_source"] = "r2_preexisting"
            console.print(f"  [yellow]→ {song_id} (skipped: LRC on R2)[/yellow]")
            return "skipped_r2"

    # Reuse non-stale processing job (skip when force)
    if not force and recording.lrc_status == "processing" and recording.lrc_job_id:
        updated_at = datetime.fromisoformat(recording.updated_at) if recording.updated_at else None
        if updated_at:
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            staleness = datetime.now(timezone.utc) - updated_at
            if staleness < timedelta(minutes=stale_after_minutes):
                active_lrc_jobs[song_id] = recording.lrc_job_id
                _add_manifest_entry(
                    song_id,
                    recording.hash_prefix,
                    "lrc",
                    "lrc",
                    recording.lrc_job_id,
                    "processing",
                    submitted_at=datetime.now(timezone.utc).isoformat(),
                )
                console.print(
                    f"  [yellow]→ {song_id} (reusing job: {recording.lrc_job_id})[/yellow]"
                )
                return "reused"

    # Submit new job
    try:
        youtube_url = recording.youtube_url or ""
        job = analysis_client.submit_lrc(
            audio_url=recording.r2_audio_url,
            content_hash=recording.content_hash,
            lyrics_text=song.lyrics_raw,
            song_title=song.title,
            whisper_model="large-v3",
            language="auto",
            use_vocals_stem=True,
            force=force,
            force_whisper=False,
            youtube_url=youtube_url,
            use_qwen3_asr=True,
            force_qwen3_asr=False,
        )

        db_client.update_recording_status(
            hash_prefix=recording.hash_prefix,
            lrc_status="processing",
            lrc_job_id=job.job_id,
        )

        active_lrc_jobs[song_id] = job.job_id
        _add_manifest_entry(
            song_id,
            recording.hash_prefix,
            "lrc",
            "lrc",
            job.job_id,
            "submitted",
            submitted_at=datetime.now(timezone.utc).isoformat(),
        )
        console.print(f"  [green]→ {song_id} (submitted: {job.job_id})[/green]")
        return "submitted"

    except AnalysisServiceError as e:
        console.print(f"  [red]✗ {song_id} failed to submit: {e}[/red]")
        results[song_id]["lrc"] = "failed"
        results[song_id]["lrc_error"] = str(e)
        _add_manifest_entry(
            song_id,
            recording.hash_prefix if recording else "",
            "lrc",
            "lrc",
            None,
            "failed",
            error_class=type(e).__name__,
            error_message=str(e),
        )
        return "failed"


# ---------------------------------------------------------------------------
# Unified poll loop helpers (v2)
#
# The functions below implement the interleaved main-loop design from
# specs/batch-unified-poll-loop-v2.md.  They replace the three separate
# phase-barrier poll functions (_poll_all_jobs / _poll_analysis_jobs /
# _poll_embedding_jobs) with a single loop that advances each song
# independently through the step chain: download → lrc → analyze → embedding.
# ---------------------------------------------------------------------------

_STEP_CHAIN = ["download", "lrc", "analyze", "embedding"]

_FAST_INTERVAL = 5.0
_SLOW_INTERVAL = 30.0
_STALENESS_THRESHOLD = 180.0  # 3 minutes without a completion → slow mode

# Thread-local storage for per-thread DatabaseClient in download workers.
_worker_state = threading.local()


def adaptive_interval(
    last_completion_time: float,
    active_jobs: dict,
) -> float:
    """Return the poll interval based on recent completion activity.

    Returns the fast interval (5 s) when jobs are completing frequently or
    when there are no active jobs, and the slow interval (30 s) after
    ``_STALENESS_THRESHOLD`` seconds without a completion.
    """
    if not active_jobs:
        return _FAST_INTERVAL
    elapsed = time.time() - last_completion_time
    if elapsed > _STALENESS_THRESHOLD:
        return _SLOW_INTERVAL
    return _FAST_INTERVAL


def _submit_analysis_for_song(
    song_id: str,
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    r2_client: R2Client,
    force: bool,
    analysis_tier: str,
    stale_after_minutes: int,
    console: Console,
    results: dict,
    _add_manifest_entry: Any,
) -> Tuple[Optional[str], str]:
    """Submit (or reuse) an analysis job for a single song.

    Returns ``(job_id, status)`` where *status* is one of:
    ``submitted``, ``reused``, ``skipped_completed``, ``skipped_no_recording``,
    or ``failed``.
    """
    recording = db_client.get_recording_by_song_id(song_id)
    if not recording or not recording.r2_audio_url:
        console.print(f"  [yellow]→ {song_id} (skipped: no recording/audio)[/yellow]")
        results[song_id]["analyze"] = "failed"
        results[song_id]["analyze_error"] = "No recording or audio URL"
        _add_manifest_entry(
            song_id,
            recording.hash_prefix if recording else "",
            "analyze",
            analysis_tier,
            None,
            "failed",
            error_message="No recording or audio URL",
        )
        return (None, "skipped_no_recording")

    # Non-force skip logic
    if not force:
        if analysis_tier == "fast" and recording.analysis_status in (
            "partial",
            "completed",
        ):
            console.print(
                f"  [yellow]→ {song_id} (skipped: analysis {recording.analysis_status})[/yellow]"
            )
            results[song_id]["analyze"] = "completed"
            return (None, "skipped_completed")
        if analysis_tier == "full" and recording.analysis_status == "completed":
            console.print(f"  [yellow]→ {song_id} (skipped: analysis completed)[/yellow]")
            results[song_id]["analyze"] = "completed"
            return (None, "skipped_completed")

    # Reuse non-stale processing job (skip when force)
    if not force and recording.analysis_status == "processing" and recording.analysis_job_id:
        updated_at = datetime.fromisoformat(recording.updated_at) if recording.updated_at else None
        if updated_at:
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            staleness = datetime.now(timezone.utc) - updated_at
            if staleness < timedelta(minutes=stale_after_minutes):
                # Check job type matches requested tier to avoid cross-tier reuse
                try:
                    existing_job = analysis_client.get_job(recording.analysis_job_id)
                    job_is_fast = existing_job.job_type == "fast_analyze"
                    tier_is_fast = analysis_tier == "fast"
                    if job_is_fast != tier_is_fast:
                        console.print(
                            f"  [yellow]→ {song_id} (skipped reuse: job tier "
                            f"{'fast' if job_is_fast else 'full'} != "
                            f"requested {analysis_tier})[/yellow]"
                        )
                    else:
                        _add_manifest_entry(
                            song_id,
                            recording.hash_prefix,
                            "analyze",
                            analysis_tier,
                            recording.analysis_job_id,
                            "processing",
                            submitted_at=datetime.now(timezone.utc).isoformat(),
                        )
                        console.print(
                            f"  [yellow]→ {song_id} (reusing job: "
                            f"{recording.analysis_job_id})[/yellow]"
                        )
                        return (recording.analysis_job_id, "reused")
                except Exception:
                    pass

    # Submit new job
    try:
        if analysis_tier == "fast":
            job = analysis_client.submit_fast_analysis(
                audio_url=recording.r2_audio_url,
                content_hash=recording.content_hash,
                force=force,
            )
        else:
            job = analysis_client.submit_analysis(
                audio_url=recording.r2_audio_url,
                content_hash=recording.content_hash,
                generate_stems=False,
                force=force,
            )

        db_client.update_recording_status(
            hash_prefix=recording.hash_prefix,
            analysis_status="processing",
            analysis_job_id=job.job_id,
        )

        _add_manifest_entry(
            song_id,
            recording.hash_prefix,
            "analyze",
            analysis_tier,
            job.job_id,
            "submitted",
            submitted_at=datetime.now(timezone.utc).isoformat(),
        )
        console.print(f"  [green]→ {song_id} (submitted: {job.job_id})[/green]")
        return (job.job_id, "submitted")

    except AnalysisServiceError as e:
        console.print(f"  [red]✗ {song_id} failed to submit: {e}[/red]")
        results[song_id]["analyze"] = "failed"
        results[song_id]["analyze_error"] = str(e)
        _add_manifest_entry(
            song_id,
            recording.hash_prefix if recording else "",
            "analyze",
            analysis_tier,
            None,
            "failed",
            error_class=type(e).__name__,
            error_message=str(e),
        )
        return (None, "failed")


def _submit_embedding_for_song(
    song_id: str,
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    r2_client: R2Client,
    force: bool,
    analysis_tier: str,
    stale_after_minutes: int,
    console: Console,
    results: dict,
    _add_manifest_entry: Any,
) -> Tuple[Optional[str], str]:
    """Submit an embedding job for a single song.

    Returns ``(job_id, status)`` where *status* is one of:
    ``submitted``, ``skipped_up_to_date``, ``skipped_no_lyrics``,
    ``skipped_no_recording``, or ``failed``.
    """
    song = db_client.get_song(song_id)
    if not song or not song.lyrics_raw:
        console.print(f"  [yellow]→ {song_id} (skipped: no lyrics)[/yellow]")
        results[song_id]["embedding"] = "failed"
        results[song_id]["embedding_error"] = "No lyrics"
        recording = db_client.get_recording_by_song_id(song_id)
        _add_manifest_entry(
            song_id,
            recording.hash_prefix if recording else "",
            "embedding",
            "embedding",
            None,
            "failed",
            error_message="No lyrics",
        )
        return (None, "skipped_no_lyrics")

    recording = db_client.get_recording_by_song_id(song_id)
    hash_prefix = recording.hash_prefix if recording else ""

    # Non-force: skip if content hash matches
    if not force:
        existing_hash = db_client.get_embedding_content_hash(song_id)
        lyrics_list = song.lyrics_list
        current_hash = _compute_content_hash(
            song.title, song.composer or "", song.lyrics_raw or "", lyrics_list
        )
        if existing_hash == current_hash:
            console.print(f"  [dim]→ {song_id} (skipped: embedding up-to-date)[/dim]")
            results[song_id]["embedding"] = "completed"
            return (None, "skipped_up_to_date")

    try:
        job_info = analysis_client.submit_embedding(
            song_id=song.id,
            title=song.title,
            composer=song.composer or "",
            lyrics_raw=song.lyrics_raw or "",
            lyrics_lines=song.lyrics_list,
        )
        _add_manifest_entry(
            song_id,
            hash_prefix,
            "embedding",
            "embedding",
            job_info.job_id,
            "submitted",
            submitted_at=datetime.now(timezone.utc).isoformat(),
        )
        console.print(f"  [green]→ {song_id} (submitted: {job_info.job_id})[/green]")
        return (job_info.job_id, "submitted")
    except Exception as e:
        console.print(f"  [red]✗ {song_id} failed to submit: {e}[/red]")
        results[song_id]["embedding"] = "failed"
        results[song_id]["embedding_error"] = str(e)
        _add_manifest_entry(
            song_id,
            hash_prefix,
            "embedding",
            "embedding",
            None,
            "failed",
            error_class=type(e).__name__,
            error_message=str(e),
        )
        return (None, "failed")


def _submit_step(
    song_id: str,
    step: str,
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    r2_client: R2Client,
    force: bool,
    analysis_tier: str,
    stale_after_minutes: int,
    console: Console,
    results: dict,
    lrc_attempted: set,
    _add_manifest_entry: Any,
) -> Tuple[Optional[str], str]:
    """Dispatch to the appropriate submit helper for *step*.

    Returns ``(job_id, status)``.
    """
    if step == "lrc":
        # _submit_lrc_for_song uses a Dict[str, str] for active_lrc_jobs and
        # returns a bare status string; adapt to the (job_id, status) contract.
        tmp_active: Dict[str, str] = {}
        status = _submit_lrc_for_song(
            song_id,
            db_client,
            analysis_client,
            r2_client,
            force,
            stale_after_minutes,
            console,
            results,
            tmp_active,
            lrc_attempted,
            _add_manifest_entry,
        )
        return (tmp_active.get(song_id), status)
    elif step == "analyze":
        return _submit_analysis_for_song(
            song_id,
            db_client,
            analysis_client,
            r2_client,
            force,
            analysis_tier,
            stale_after_minutes,
            console,
            results,
            _add_manifest_entry,
        )
    elif step == "embedding":
        return _submit_embedding_for_song(
            song_id,
            db_client,
            analysis_client,
            r2_client,
            force,
            analysis_tier,
            stale_after_minutes,
            console,
            results,
            _add_manifest_entry,
        )
    return (None, "failed")


def _advance_song(
    song_id: str,
    completed_step: str,
    selected_steps: List[str],
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    r2_client: R2Client,
    force: bool,
    analysis_tier: str,
    stale_after_minutes: int,
    console: Console,
    results: dict,
    active_jobs: dict,
    lrc_attempted: set,
    _add_manifest_entry: Any,
) -> None:
    """Walk the step chain forward from *completed_step* and submit the next
    ready step, skipping steps that are already done or not selected.

    When the chain is exhausted, marks ``results[song_id]["_pipeline"]`` as
    ``"completed"``.
    """
    next_idx = _STEP_CHAIN.index(completed_step) + 1
    for step in _STEP_CHAIN[next_idx:]:
        if step not in selected_steps:
            continue

        # Skip if this step is already active (e.g. eagerly submitted by the
        # download worker) or already completed.
        if (song_id, step) in active_jobs:
            return
        if results.get(song_id, {}).get(step) in ("completed", "failed"):
            continue

        job_id, status = _submit_step(
            song_id,
            step,
            db_client,
            analysis_client,
            r2_client,
            force,
            analysis_tier,
            stale_after_minutes,
            console,
            results,
            lrc_attempted,
            _add_manifest_entry,
        )

        if status == "submitted" or status == "reused":
            if job_id is not None:
                active_jobs[(song_id, step)] = job_id
            return
        elif status in (
            "skipped_r2",
            "skipped_completed",
            "skipped_no_lyrics",
            "skipped_no_recording",
            "skipped_up_to_date",
        ):
            continue  # try next step in chain
        else:  # failed
            results[song_id][step] = "failed"
            return

    # Chain exhausted — no further work for this song
    results[song_id]["_pipeline"] = "completed"


def _handle_lrc_completion(
    song_id: str,
    job_id: str,
    job: JobInfo,
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    r2_client: R2Client,
    force: bool,
    stale_after_minutes: int,
    console: Console,
    results: dict,
    _add_manifest_entry: Any,
    resubmit_counts: dict,
) -> Tuple[bool, Optional[str]]:
    """Process a completed/failed LRC job.

    Returns ``(is_terminal, new_job_id)``.  *new_job_id* is non-None when the
    job was lost (404) and a resubmit was issued; the caller should update
    ``active_jobs`` with the new id.
    """
    if job.status == "completed":
        recording = db_client.get_recording_by_song_id(song_id)
        lrc_url = _confirm_r2_lrc(r2_client, recording.hash_prefix, console)

        if lrc_url:
            db_client.update_recording_lrc(
                recording.hash_prefix,
                lrc_url,
                visibility_status="review",
            )
            results[song_id]["lrc"] = "completed"
            if job.result and job.result.lrc_source:
                results[song_id]["lrc_source"] = job.result.lrc_source

            _add_manifest_entry(
                song_id,
                recording.hash_prefix,
                "lrc",
                "lrc",
                job_id,
                "completed",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )

            song = db_client.get_song(song_id)
            song_name = song.title if song else song_id
            console.print(f"  [green]✓[/green] {song_name} — LRC completed")
            return (True, None)
        else:
            console.print(
                f"  [yellow]→ {song_id}: Job completed but LRC not on R2 yet, "
                f"retrying...[/yellow]"
            )
            return (False, None)

    elif job.status == "failed":
        recording = db_client.get_recording_by_song_id(song_id)
        hash_prefix = recording.hash_prefix if recording else ""
        db_client.update_recording_status(
            hash_prefix=hash_prefix,
            lrc_status="failed",
        )
        results[song_id]["lrc"] = "failed"
        results[song_id]["lrc_error"] = job.error_message or "Unknown error"

        _add_manifest_entry(
            song_id,
            hash_prefix,
            "lrc",
            "lrc",
            job_id,
            "failed",
            error_message=job.error_message or "Unknown error",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

        song = db_client.get_song(song_id)
        song_name = song.title if song else song_id
        console.print(
            f"  [red]✗[/red] {song_name} — LRC failed: " f"{job.error_message or 'Unknown error'}"
        )
        return (True, None)

    elif job.status == "cancelled":
        recording = db_client.get_recording_by_song_id(song_id)
        hash_prefix = recording.hash_prefix if recording else ""
        db_client.update_recording_status(
            hash_prefix=hash_prefix,
            lrc_status="failed",
        )
        results[song_id]["lrc"] = "failed"
        results[song_id]["lrc_error"] = "Job cancelled"

        _add_manifest_entry(
            song_id,
            hash_prefix,
            "lrc",
            "lrc",
            job_id,
            "failed",
            error_message="Job cancelled",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        return (True, None)

    # Still processing
    return (False, None)


def _handle_lrc_404(
    song_id: str,
    job_id: str,
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    r2_client: R2Client,
    force: bool,
    console: Console,
    results: dict,
    _add_manifest_entry: Any,
    resubmit_counts: dict,
) -> Tuple[bool, Optional[str]]:
    """Handle a 404 (lost job) for an LRC job.

    Checks R2 for an existing LRC; if found, marks completed.  Otherwise
    resubmits up to ``max_resubmits`` times, then marks failed.
    """
    max_resubmits = 3
    recording = db_client.get_recording_by_song_id(song_id)
    lrc_url = r2_client.lrc_exists(recording.hash_prefix)

    if lrc_url:
        db_client.update_recording_lrc(
            recording.hash_prefix,
            lrc_url,
            visibility_status="review",
        )
        results[song_id]["lrc"] = "completed"

        _add_manifest_entry(
            song_id,
            recording.hash_prefix,
            "lrc",
            "lrc",
            job_id,
            "completed",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

        song = db_client.get_song(song_id)
        song_name = song.title if song else song_id
        console.print(f"  [green]✓[/green] {song_name} — LRC found on R2 (job was lost)")
        return (True, None)

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
        _add_manifest_entry(
            song_id,
            recording.hash_prefix,
            "lrc",
            "lrc",
            job_id,
            "failed",
            error_message=results[song_id]["lrc_error"],
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        return (True, None)

    console.print(
        f"  [yellow]→ {song_id}: Job lost (404), resubmitting "
        f"(attempt {resubmit_count + 1}/{max_resubmits})...[/yellow]"
    )
    try:
        song = db_client.get_song(song_id)
        if not song or not song.lyrics_raw:
            results[song_id]["lrc"] = "failed"
            results[song_id]["lrc_error"] = "Job lost and no lyrics available for resubmit"
            db_client.update_recording_status(
                hash_prefix=recording.hash_prefix,
                lrc_status="failed",
            )
            _add_manifest_entry(
                song_id,
                recording.hash_prefix,
                "lrc",
                "lrc",
                job_id,
                "failed",
                error_message=results[song_id]["lrc_error"],
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            return (True, None)

        new_job = analysis_client.submit_lrc(
            audio_url=recording.r2_audio_url,
            content_hash=recording.content_hash,
            lyrics_text=song.lyrics_raw,
            song_title=song.title,
            whisper_model="large-v3",
            language="auto",
            use_vocals_stem=True,
            force=force,
            force_whisper=False,
            youtube_url=recording.youtube_url or "",
            use_qwen3_asr=True,
            force_qwen3_asr=False,
        )
        db_client.update_recording_status(
            hash_prefix=recording.hash_prefix,
            lrc_status="processing",
            lrc_job_id=new_job.job_id,
        )
        _add_manifest_entry(
            song_id,
            recording.hash_prefix,
            "lrc",
            "lrc",
            new_job.job_id,
            "submitted",
            previous_job_id=job_id,
            submitted_at=datetime.now(timezone.utc).isoformat(),
        )
        resubmit_counts[song_id] = resubmit_count + 1
        return (False, new_job.job_id)
    except AnalysisServiceError as submit_err:
        console.print(f"  [red]✗ {song_id}: Resubmit failed: {submit_err}[/red]")
        results[song_id]["lrc"] = "failed"
        results[song_id]["lrc_error"] = f"Resubmit failed: {submit_err}"
        db_client.update_recording_status(
            hash_prefix=recording.hash_prefix,
            lrc_status="failed",
        )
        _add_manifest_entry(
            song_id,
            recording.hash_prefix,
            "lrc",
            "lrc",
            job_id,
            "failed",
            error_message=results[song_id]["lrc_error"],
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        return (True, None)


def _handle_analysis_completion(
    song_id: str,
    job_id: str,
    job: JobInfo,
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    analysis_tier: str,
    console: Console,
    results: dict,
    _add_manifest_entry: Any,
) -> Tuple[bool, Optional[str]]:
    """Process a completed/failed analysis job.

    Returns ``(is_terminal, new_job_id)``.
    """
    if job.status == "completed":
        recording = db_client.get_recording_by_song_id(song_id)
        if not recording:
            console.print(f"  [red]✗ {song_id}: recording vanished[/red]")
            results[song_id]["analyze"] = "failed"
            _add_manifest_entry(
                song_id,
                "",
                "analyze",
                analysis_tier,
                job_id,
                "failed",
                error_message="Recording vanished",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            return (True, None)

        effective_tier = "fast" if job.job_type == "fast_analyze" else "full"
        if effective_tier != analysis_tier:
            console.print(
                f"  [yellow]→ {song_id}: job type '{job.job_type}' does not "
                f"match requested tier '{analysis_tier}', treating as "
                f"'{effective_tier}'[/yellow]"
            )

        if job.result:
            result = job.result
            status_to_set = "partial" if effective_tier == "fast" else "completed"
            if effective_tier == "fast" and recording.analysis_status == "completed":
                status_to_set = "completed"
            db_client.update_recording_analysis(
                hash_prefix=recording.hash_prefix,
                duration_seconds=result.duration_seconds,
                tempo_bpm=result.tempo_bpm,
                musical_key=result.musical_key,
                musical_mode=result.musical_mode,
                key_confidence=result.key_confidence,
                loudness_db=result.loudness_db,
                beats=(
                    json.dumps(result.beats) if effective_tier == "full" and result.beats else None
                ),
                downbeats=(
                    json.dumps(result.downbeats)
                    if effective_tier == "full" and result.downbeats
                    else None
                ),
                sections=(
                    json.dumps(result.sections)
                    if effective_tier == "full" and result.sections
                    else None
                ),
                embeddings_shape=(
                    json.dumps(result.embeddings_shape)
                    if effective_tier == "full" and result.embeddings_shape
                    else None
                ),
                r2_stems_url=result.stems_url,
                analysis_status=status_to_set,
            )

        results[song_id]["analyze"] = "completed"
        results[song_id]["analysis_tier"] = effective_tier

        _add_manifest_entry(
            song_id,
            recording.hash_prefix,
            "analyze",
            effective_tier,
            job_id,
            "completed",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

        song = db_client.get_song(song_id)
        song_name = song.title if song else song_id
        console.print(f"  [green]✓[/green] {song_name} — analysis completed ({effective_tier})")
        return (True, None)

    elif job.status == "failed":
        recording = db_client.get_recording_by_song_id(song_id)
        hash_prefix = recording.hash_prefix if recording else ""
        db_client.update_recording_status(
            hash_prefix=hash_prefix,
            analysis_status="failed",
        )
        results[song_id]["analyze"] = "failed"
        results[song_id]["analyze_error"] = job.error_message or "Unknown error"

        _add_manifest_entry(
            song_id,
            hash_prefix,
            "analyze",
            analysis_tier,
            job_id,
            "failed",
            error_message=job.error_message or "Unknown error",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

        song = db_client.get_song(song_id)
        song_name = song.title if song else song_id
        console.print(
            f"  [red]✗[/red] {song_name} — analysis failed: "
            f"{job.error_message or 'Unknown error'}"
        )
        return (True, None)

    elif job.status == "cancelled":
        recording = db_client.get_recording_by_song_id(song_id)
        hash_prefix = recording.hash_prefix if recording else ""
        db_client.update_recording_status(
            hash_prefix=hash_prefix,
            analysis_status="failed",
        )
        results[song_id]["analyze"] = "failed"
        results[song_id]["analyze_error"] = "Job cancelled"

        _add_manifest_entry(
            song_id,
            hash_prefix,
            "analyze",
            analysis_tier,
            job_id,
            "failed",
            error_message="Job cancelled",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        return (True, None)

    # Still processing
    return (False, None)


def _handle_embedding_completion(
    song_id: str,
    job_id: str,
    job: JobInfo,
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    console: Console,
    results: dict,
    _add_manifest_entry: Any,
) -> Tuple[bool, Optional[str]]:
    """Process a completed/failed embedding job.

    Returns ``(is_terminal, new_job_id)``.
    """
    if job.status == "completed":
        write_ok = True
        if job.result and hasattr(job.result, "embedding"):
            write_ok = _write_embedding_result(job, db_client, console)
        if write_ok:
            results[song_id]["embedding"] = "completed"
        else:
            results[song_id]["embedding"] = "failed"
            results[song_id]["embedding_error"] = "DB write failed"

        recording = db_client.get_recording_by_song_id(song_id)
        _add_manifest_entry(
            song_id,
            recording.hash_prefix if recording else "",
            "embedding",
            "embedding",
            job_id,
            results[song_id]["embedding"],
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

        song = db_client.get_song(song_id)
        song_name = song.title if song else song_id
        if write_ok:
            console.print(f"  [green]✓[/green] {song_name} — embedding completed")
        else:
            console.print(f"  [red]✗[/red] {song_name} — embedding DB write failed")
        return (True, None)

    elif job.status == "failed":
        results[song_id]["embedding"] = "failed"
        results[song_id]["embedding_error"] = job.error_message or "Unknown error"

        recording = db_client.get_recording_by_song_id(song_id)
        _add_manifest_entry(
            song_id,
            recording.hash_prefix if recording else "",
            "embedding",
            "embedding",
            job_id,
            "failed",
            error_message=job.error_message or "Unknown error",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )

        song = db_client.get_song(song_id)
        song_name = song.title if song else song_id
        console.print(
            f"  [red]✗[/red] {song_name} — embedding failed: "
            f"{job.error_message or 'Unknown error'}"
        )
        return (True, None)

    elif job.status == "cancelled":
        results[song_id]["embedding"] = "failed"
        results[song_id]["embedding_error"] = "Job cancelled"

        recording = db_client.get_recording_by_song_id(song_id)
        _add_manifest_entry(
            song_id,
            recording.hash_prefix if recording else "",
            "embedding",
            "embedding",
            job_id,
            "failed",
            error_message="Job cancelled",
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
        return (True, None)

    # Still processing
    return (False, None)


def _init_download_worker(database_url: str) -> None:
    """Per-thread initializer: create a ConnectionProvider + DatabaseClient.

    Called once per worker thread by ``ThreadPoolExecutor(initializer=...)``.
    """
    provider = ConnectionProvider(database_url)
    _worker_state.db = DatabaseClient(provider)
    _worker_state.provider = provider


def _download_worker(
    song_id: str,
    song: Song,
    r2_client: R2Client,
    force: bool,
    analysis_tier: str,
    stale_after_minutes: int,
    analysis_client: AnalysisClient,
    results: dict,
    results_lock: threading.Lock,
    lrc_attempted: set,
    _add_manifest_entry: Any,
    manifest_lock: threading.Lock,
    eager_lrc: bool,
) -> dict:
    """Download a single song in a worker thread.

    Wraps ``_download_and_create_recording`` (or ``_download_if_needed``) and,
    when *eager_lrc* is True, eagerly submits the LRC job so the slow step
    overlaps with remaining downloads.

    Returns a result dict with keys: ``song_id``, ``status``, ``updates``,
    and optionally ``recording`` (the Recording or None).
    """
    quiet_console = Console(quiet=True)
    thread_db: DatabaseClient = _worker_state.db

    try:
        recording = thread_db.get_recording_by_song_id(song_id)
        if not recording:
            # No recording yet → download + create
            recording, error = _download_and_create_recording(
                song_id, song, thread_db, r2_client, quiet_console
            )
            if not recording:
                return {
                    "song_id": song_id,
                    "status": "failed",
                    "updates": {"download": "failed", "error": error},
                    "recording": None,
                }
            updates = {"download": "completed"}
        else:
            result = _download_if_needed(song_id, recording, thread_db, r2_client, quiet_console)
            updates = result
            if result["download"] == "failed":
                return {
                    "song_id": song_id,
                    "status": "failed",
                    "updates": updates,
                    "recording": None,
                }

        # Eager LRC handoff
        submitted_lrc = None
        if eager_lrc and updates.get("download") in ("completed", "skipped_r2"):
            with results_lock:
                tmp_active: Dict[str, str] = {}
                status = _submit_lrc_for_song(
                    song_id,
                    thread_db,
                    analysis_client,
                    r2_client,
                    force,
                    stale_after_minutes,
                    quiet_console,
                    results,
                    tmp_active,
                    lrc_attempted,
                    _add_manifest_entry,
                )
            if status == "submitted" and tmp_active.get(song_id):
                submitted_lrc = tmp_active[song_id]

        return {
            "song_id": song_id,
            "status": "ok",
            "updates": updates,
            "recording": recording,
            "lrc_job_id": submitted_lrc,
        }
    except Exception as e:
        return {
            "song_id": song_id,
            "status": "failed",
            "updates": {"download": "failed", "error": str(e)},
            "recording": None,
        }


def _print_unified_progress(
    pending_futures: Set[Future],
    active_jobs: dict,
    results: dict,
    start_time: float,
    console: Console,
) -> None:
    """Print a one-line progress summary for the unified loop."""
    lrc_active = sum(1 for (_, s) in active_jobs if s == "lrc")
    analyze_active = sum(1 for (_, s) in active_jobs if s == "analyze")
    embedding_active = sum(1 for (_, s) in active_jobs if s == "embedding")

    lrc_done = sum(1 for r in results.values() if r.get("lrc") == "completed")
    analyze_done = sum(1 for r in results.values() if r.get("analyze") == "completed")
    embedding_done = sum(1 for r in results.values() if r.get("embedding") == "completed")
    failed = sum(1 for r in results.values() for v in r.values() if v == "failed")
    completed = sum(1 for r in results.values() if r.get("_pipeline") == "completed")

    elapsed = time.time() - start_time
    console.print(
        f"⏳ pending(down/lrc/ana/emb)={len(pending_futures)}/{lrc_active}/"
        f"{analyze_active}/{embedding_active}  "
        f"✓(lrc/ana/emb)={lrc_done}/{analyze_done}/{embedding_done}  "
        f"pipeline={completed}  "
        f"✗={failed}  "
        f"(elapsed: {int(elapsed // 60)}m {int(elapsed % 60)}s)"
    )


def _poll_one_cycle(
    pending_futures: Set[Future],
    active_jobs: Dict[Tuple[str, str], str],
    results: dict,
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    r2_client: R2Client,
    selected_steps: List[str],
    force: bool,
    analysis_tier: str,
    stale_after_minutes: int,
    console: Console,
    _add_manifest_entry: Any,
    results_lock: threading.Lock,
    lrc_attempted: set,
    resubmit_counts: dict,
    last_completion_time: float,
    batch_start_time: float,
) -> Tuple[Set[Future], float]:
    """Run one iteration of the interleaved loop.

    Returns ``(pending_futures, last_completion_time)``.
    """
    # A. Check downloads
    interval = adaptive_interval(last_completion_time, active_jobs)
    if pending_futures:
        done, pending_futures = wait(pending_futures, timeout=interval, return_when=FIRST_COMPLETED)
        for f in done:
            result = f.result()
            sid = result["song_id"]
            with results_lock:
                results[sid].update(result["updates"])
            if result.get("lrc_job_id"):
                active_jobs[(sid, "lrc")] = result["lrc_job_id"]
            if result.get("recording") and result["status"] != "failed":
                _advance_song(
                    sid,
                    "download",
                    selected_steps,
                    db_client,
                    analysis_client,
                    r2_client,
                    force,
                    analysis_tier,
                    stale_after_minutes,
                    console,
                    results,
                    active_jobs,
                    lrc_attempted,
                    _add_manifest_entry,
                )
            elif result["status"] == "failed":
                results[sid]["_pipeline"] = "completed"
            last_completion_time = time.time()
    else:
        # No pending downloads; just sleep for the adaptive interval
        time.sleep(interval)

    # B. Poll active service jobs
    for key in list(active_jobs.keys()):
        song_id, step = key
        job_id = active_jobs[key]
        try:
            job = analysis_client.get_job(job_id)

            if step == "lrc":
                is_terminal, new_job_id = _handle_lrc_completion(
                    song_id,
                    job_id,
                    job,
                    db_client,
                    analysis_client,
                    r2_client,
                    force,
                    stale_after_minutes,
                    console,
                    results,
                    _add_manifest_entry,
                    resubmit_counts,
                )
            elif step == "analyze":
                is_terminal, new_job_id = _handle_analysis_completion(
                    song_id,
                    job_id,
                    job,
                    db_client,
                    analysis_client,
                    analysis_tier,
                    console,
                    results,
                    _add_manifest_entry,
                )
            elif step == "embedding":
                is_terminal, new_job_id = _handle_embedding_completion(
                    song_id,
                    job_id,
                    job,
                    db_client,
                    analysis_client,
                    console,
                    results,
                    _add_manifest_entry,
                )
            else:
                continue

            if is_terminal:
                del active_jobs[key]
                last_completion_time = time.time()
                _advance_song(
                    song_id,
                    step,
                    selected_steps,
                    db_client,
                    analysis_client,
                    r2_client,
                    force,
                    analysis_tier,
                    stale_after_minutes,
                    console,
                    results,
                    active_jobs,
                    lrc_attempted,
                    _add_manifest_entry,
                )
            elif new_job_id:
                active_jobs[key] = new_job_id
        except AnalysisServiceError as e:
            if e.status_code == 404:
                if step == "lrc":
                    is_terminal, new_job_id = _handle_lrc_404(
                        song_id,
                        job_id,
                        db_client,
                        analysis_client,
                        r2_client,
                        force,
                        console,
                        results,
                        _add_manifest_entry,
                        resubmit_counts,
                    )
                    if is_terminal:
                        del active_jobs[key]
                        last_completion_time = time.time()
                        _advance_song(
                            song_id,
                            step,
                            selected_steps,
                            db_client,
                            analysis_client,
                            r2_client,
                            force,
                            analysis_tier,
                            stale_after_minutes,
                            console,
                            results,
                            active_jobs,
                            lrc_attempted,
                            _add_manifest_entry,
                        )
                    elif new_job_id:
                        active_jobs[key] = new_job_id
                else:
                    # No R2 fallback for analysis/embedding
                    recording = db_client.get_recording_by_song_id(song_id)
                    hash_prefix = recording.hash_prefix if recording else ""
                    db_client.update_recording_status(
                        hash_prefix=hash_prefix,
                        **{f"{step}_status": "failed"},
                    )
                    results[song_id][step] = "failed"
                    results[song_id][f"{step}_error"] = "Job lost (404)"
                    del active_jobs[key]
                    last_completion_time = time.time()
                    _add_manifest_entry(
                        song_id,
                        hash_prefix,
                        step,
                        analysis_tier if step == "analyze" else "embedding",
                        job_id,
                        "failed",
                        error_message="Job lost (404)",
                        completed_at=datetime.now(timezone.utc).isoformat(),
                    )
            else:
                console.print(f"  [yellow]→ Error polling {song_id}/{step}: {e}[/yellow]")
        except Exception as e:
            console.print(f"  [yellow]→ Error polling {song_id}/{step}: {e}[/yellow]")

    # C. Print progress
    _print_unified_progress(pending_futures, active_jobs, results, batch_start_time, console)

    return pending_futures, last_completion_time


def _process_batch(
    db_client: DatabaseClient,
    r2_client: R2Client,
    analysis_client: AnalysisClient,
    song_ids: list[str],
    selected_steps: List[str],
    force: bool,
    analysis_tier: str,
    stale_after_minutes: int,
    console: Console,
    database_url: str,
    download_concurrency: int,
) -> dict:
    """Process all songs in batch with an interleaved unified poll loop.

    A single main loop manages both pending downloads (via a
    ``ThreadPoolExecutor``) and active service jobs (LRC / analyze /
    embedding).  Each song advances independently through the step chain:
    download → lrc → analyze → embedding.  This eliminates the phase
    barriers of the previous design where one slow song blocked every
    other song's downstream work.

    Args:
        db_client: Database client (main thread)
        r2_client: R2 client
        analysis_client: Analysis service client
        song_ids: List of song IDs to process
        selected_steps: Steps to run (download/lrc/analyze/embedding)
        force: Force re-run of the single selected step
        analysis_tier: fast or full
        stale_after_minutes: Staleness threshold for processing jobs
        console: Rich console
        database_url: Database URL for per-thread connections
        download_concurrency: Max parallel downloads

    Returns:
        Dict with results for each song
    """
    results: Dict[str, dict] = {sid: {} for sid in song_ids}
    active_jobs: Dict[Tuple[str, str], str] = {}
    lrc_attempted: set = set()
    resubmit_counts: dict = {}
    results_lock = threading.Lock()
    manifest_lock = threading.Lock()

    batch_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S") + "_batch"
    started_at = datetime.now(timezone.utc).isoformat()
    manifest_dir = _get_manifest_dir()
    manifest_entries: List[dict] = []

    def _flush_manifest() -> Optional[Path]:
        return _write_manifest(
            batch_id,
            results,
            manifest_dir,
            selected_steps,
            analysis_tier,
            stale_after_minutes,
            started_at,
            manifest_entries,
        )

    def _add_manifest_entry(
        song_id: str,
        hash_prefix: str,
        step: str,
        tier: str,
        job_id: Optional[str],
        status: str,
        attempts: int = 1,
        previous_job_id: Optional[str] = None,
        error_class: Optional[str] = None,
        error_message: Optional[str] = None,
        submitted_at: Optional[str] = None,
        completed_at: Optional[str] = None,
    ) -> None:
        entry = {
            "song_id": song_id,
            "hash_prefix": hash_prefix,
            "step": step,
            "tier": tier,
            "job_id": job_id,
            "status": status,
            "attempts": attempts,
            "previous_job_id": previous_job_id,
            "error_class": error_class,
            "error_message": error_message,
            "submitted_at": submitted_at,
            "completed_at": completed_at,
        }
        with manifest_lock:
            for i, existing in enumerate(manifest_entries):
                if (
                    existing["song_id"] == song_id
                    and existing["step"] == step
                    and existing["tier"] == tier
                ):
                    manifest_entries[i] = entry
                    return
            manifest_entries.append(entry)

    eager_lrc = "download" in selected_steps and "lrc" in selected_steps
    pending_futures: Set[Future] = set()
    batch_start_time = time.time()
    last_completion_time = time.time()

    try:
        # Submit all download tasks (if download step selected) or advance
        # songs directly to the first non-download step.
        if "download" in selected_steps:
            console.print(
                f"[cyan]Submitting {len(song_ids)} download(s) "
                f"(concurrency: {download_concurrency})[/cyan]"
            )
            executor = ThreadPoolExecutor(
                max_workers=download_concurrency,
                initializer=_init_download_worker,
                initargs=(database_url,),
            )
            for song_id in song_ids:
                song = db_client.get_song(song_id)
                if not song:
                    console.print(f"  [red]✗ {song_id}: Song not found[/red]")
                    results[song_id]["download"] = "failed"
                    results[song_id]["error"] = "Song not found"
                    results[song_id]["_pipeline"] = "completed"
                    continue
                future = executor.submit(
                    _download_worker,
                    song_id,
                    song,
                    r2_client,
                    force,
                    analysis_tier,
                    stale_after_minutes,
                    analysis_client,
                    results,
                    results_lock,
                    lrc_attempted,
                    _add_manifest_entry,
                    manifest_lock,
                    eager_lrc,
                )
                pending_futures.add(future)
        else:
            executor = None
            # No download phase — advance each song from the "download" step
            # so the cascade picks up the first selected step (lrc/analyze/
            # embedding).
            for song_id in song_ids:
                _advance_song(
                    song_id,
                    "download",
                    selected_steps,
                    db_client,
                    analysis_client,
                    r2_client,
                    force,
                    analysis_tier,
                    stale_after_minutes,
                    console,
                    results,
                    active_jobs,
                    lrc_attempted,
                    _add_manifest_entry,
                )

        # Interleaved main loop
        while pending_futures or active_jobs:
            pending_futures, last_completion_time = _poll_one_cycle(
                pending_futures,
                active_jobs,
                results,
                db_client,
                analysis_client,
                r2_client,
                selected_steps,
                force,
                analysis_tier,
                stale_after_minutes,
                console,
                _add_manifest_entry,
                results_lock,
                lrc_attempted,
                resubmit_counts,
                last_completion_time,
                batch_start_time,
            )
            _flush_manifest()

        if executor:
            executor.shutdown(wait=True)

    except KeyboardInterrupt:
        console.print("\n[yellow]Batch interrupted. Flushing manifest...[/yellow]")
        # Cancel pending download futures
        for fut in pending_futures:
            fut.cancel()
        if executor:
            executor.shutdown(wait=False, cancel_futures=True)
        # Reconcile active service jobs
        _reconcile_on_interrupt(
            {sid: jid for (sid, step), jid in active_jobs.items()},
            results,
            db_client,
            r2_client,
            console,
        )
        _flush_manifest()
        raise

    _flush_manifest()
    return results


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
            db_client.update_recording_lrc(
                hash_prefix,
                lrc_url,
                visibility_status="review",
            )
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


def _is_retryable_poll_error(e: Exception) -> bool:
    """Whether a polling failure should be retried once (transient)."""
    import requests as _requests

    if isinstance(e, AnalysisServiceError):
        if e.status_code and e.status_code in {502, 503, 504}:
            return True
        if (
            e.status_code is None
            and e.__cause__
            and isinstance(
                e.__cause__,
                (_requests.exceptions.ConnectionError, _requests.exceptions.Timeout),
            )
        ):
            return True
        return False
    return isinstance(e, (_requests.exceptions.ConnectionError, _requests.exceptions.Timeout))


def _resume_from_manifest(
    manifest_data: dict,
    manifest_path: Path,
    db_client: DatabaseClient,
    r2_client: R2Client,
    analysis_client: AnalysisClient,
    stale_after_minutes: int,
    console: Console,
    database_url: str,
    download_concurrency: int,
) -> dict:
    """Resume a batch from a manifest file using the unified interleaved loop.

    Reconstructs the unified ``active_jobs`` dict keyed by
    ``(song_id, step)`` and enters the same interleaved loop that fresh
    batches use.  Entries with status 'completed'/'failed'/'abandoned' are
    skipped (with DB writeback for completed entries).  This means a
    manifest with LRC jobs in 'processing' alongside analysis jobs in
    'processing' will poll both concurrently, and completions cascade
    immediately.

    Args:
        manifest_data: Parsed manifest dict
        manifest_path: Path to the manifest file
        db_client: Database client
        r2_client: R2 client
        analysis_client: Analysis service client
        stale_after_minutes: Staleness threshold
        console: Rich console
        database_url: Database URL (for API symmetry; resume has no downloads)
        download_concurrency: Max parallel downloads (unused on resume)

    Returns:
        Results dict
    """
    songs = manifest_data.get("songs", [])
    results: Dict[str, dict] = {}
    manifest_entries: List[dict] = list(songs)
    active_jobs: Dict[Tuple[str, str], str] = {}
    lrc_attempted: set = set()
    resubmit_counts: dict = {}
    results_lock = threading.Lock()
    manifest_lock = threading.Lock()

    def _add_manifest_entry(
        song_id: str,
        hash_prefix: str,
        step: str,
        tier: str,
        job_id: Optional[str],
        status: str,
        **kwargs,
    ) -> None:
        entry = {
            "song_id": song_id,
            "hash_prefix": hash_prefix,
            "step": step,
            "tier": tier,
            "job_id": job_id,
            "status": status,
            **kwargs,
        }
        with manifest_lock:
            for i, existing in enumerate(manifest_entries):
                if (
                    existing["song_id"] == song_id
                    and existing["step"] == step
                    and existing["tier"] == tier
                ):
                    manifest_entries[i] = {**existing, **entry}
                    return
            manifest_entries.append(entry)

    skipped = 0

    for entry in songs:
        song_id = entry["song_id"]
        step = entry["step"]
        tier = entry["tier"]
        job_id = entry.get("job_id")
        status = entry.get("status", "")
        hash_prefix = entry.get("hash_prefix", "")

        results.setdefault(song_id, {})

        if status in ("completed", "failed", "abandoned"):
            skipped += 1
            if status == "failed":
                results[song_id][step] = "failed"
                results[song_id][f"{step}_error"] = entry.get("error_message", "")
            if status == "completed":
                _apply_manifest_writeback(
                    song_id,
                    step,
                    tier,
                    job_id,
                    hash_prefix,
                    db_client,
                    analysis_client,
                    console,
                )
            continue

        if not job_id:
            skipped += 1
            continue

        # Reconstruct active job
        active_jobs[(song_id, step)] = job_id

    if skipped:
        console.print(f"[dim]Skipped {skipped} terminal entries from manifest.[/dim]")

    batch_id = manifest_data.get("batch_id", manifest_path.stem)
    started_at = manifest_data.get("started_at", "")
    selected_steps = manifest_data.get("selected_steps", [])
    analysis_tier = manifest_data.get("analysis_tier", "fast")

    def _flush() -> None:
        manifest = {
            "batch_id": batch_id,
            "started_at": started_at,
            "selected_steps": selected_steps,
            "analysis_tier": analysis_tier,
            "stale_after_minutes": stale_after_minutes,
            "songs": manifest_entries,
        }
        try:
            manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
        except OSError as e:
            logger.warning(f"Failed to flush manifest on resume: {e}")

    pending_futures: Set[Future] = set()
    batch_start_time = time.time()
    last_completion_time = time.time()

    try:
        console.print(
            f"[cyan]Resume: polling {len(active_jobs)} active job(s) " f"(unified loop)[/cyan]"
        )
        while pending_futures or active_jobs:
            pending_futures, last_completion_time = _poll_one_cycle(
                pending_futures,
                active_jobs,
                results,
                db_client,
                analysis_client,
                r2_client,
                selected_steps,
                force=False,
                analysis_tier=analysis_tier,
                stale_after_minutes=stale_after_minutes,
                console=console,
                _add_manifest_entry=_add_manifest_entry,
                results_lock=results_lock,
                lrc_attempted=lrc_attempted,
                resubmit_counts=resubmit_counts,
                last_completion_time=last_completion_time,
                batch_start_time=batch_start_time,
            )
            _flush()
    except KeyboardInterrupt:
        console.print("\n[yellow]Resume interrupted. Flushing manifest...[/yellow]")
        _reconcile_on_interrupt(
            {sid: jid for (sid, step), jid in active_jobs.items()},
            results,
            db_client,
            r2_client,
            console,
        )
        _flush()
        raise

    _flush()
    return results


def _apply_manifest_writeback(
    song_id: str,
    step: str,
    tier: str,
    job_id: Optional[str],
    hash_prefix: str,
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    console: Console,
) -> None:
    """Apply idempotent DB writeback for a completed manifest entry on resume.

    Checks the DB first to skip the HTTP call if the result is already written.
    """
    try:
        if not job_id:
            return

        # DB-first short-circuit: skip HTTP if already written
        recording = db_client.get_recording_by_song_id(song_id)
        if recording:
            if step == "analyze" and recording.analysis_status in (
                "partial",
                "completed",
            ):
                return
            if step == "lrc" and recording.lrc_status == "completed":
                return
            if step == "embedding":
                existing_hash = db_client.get_embedding_content_hash(song_id)
                if existing_hash:
                    return

        job = analysis_client.get_job(job_id)
        if job.status != "completed":
            return

        if step == "lrc":
            # LRC writeback is R2-driven; skip if no R2 URL in result
            pass
        elif step == "analyze":
            if not recording or not job.result:
                return
            result = job.result
            # Derive effective tier from job type to guard against mismatch
            effective_tier = "fast" if job.job_type == "fast_analyze" else "full"
            status_to_set = "partial" if effective_tier == "fast" else "completed"
            db_client.update_recording_analysis(
                hash_prefix=recording.hash_prefix,
                duration_seconds=result.duration_seconds,
                tempo_bpm=result.tempo_bpm,
                musical_key=result.musical_key,
                musical_mode=result.musical_mode,
                key_confidence=result.key_confidence,
                loudness_db=result.loudness_db,
                beats=(
                    json.dumps(result.beats) if effective_tier == "full" and result.beats else None
                ),
                downbeats=(
                    json.dumps(result.downbeats)
                    if effective_tier == "full" and result.downbeats
                    else None
                ),
                sections=(
                    json.dumps(result.sections)
                    if effective_tier == "full" and result.sections
                    else None
                ),
                embeddings_shape=(
                    json.dumps(result.embeddings_shape)
                    if effective_tier == "full" and result.embeddings_shape
                    else None
                ),
                r2_stems_url=result.stems_url,
                analysis_status=status_to_set,
            )
        elif step == "embedding":
            if job.result and hasattr(job.result, "embedding"):
                _write_embedding_result(job, db_client, console)
    except Exception as e:
        logger.warning(f"Manifest writeback failed for {song_id}/{step}: {e}")


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

    # LRC source breakdown
    lrc_youtube = sum(1 for r in results.values() if r.get("lrc_source") == "youtube_transcript")
    lrc_qwen_asr = sum(1 for r in results.values() if r.get("lrc_source") == "qwen3_asr")
    lrc_whisper_asr = sum(1 for r in results.values() if r.get("lrc_source") == "whisper_asr")
    lrc_unknown = (
        lrc_completed - lrc_skipped_existing - lrc_youtube - lrc_qwen_asr - lrc_whisper_asr
    )

    # LRC timing stats (only for ASR/Whisper jobs, not YouTube or R2 pre-existing)
    lrc_timings = []
    for song_id, t in results.items():
        # Only track timings for jobs that were generated by ASR, not YouTube or R2 pre-existing
        if "elapsed" in t and t.get("lrc_source") in {"qwen3_asr", "whisper_asr"}:
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
        lines.extend(
            [
                f"│ {'':<30} {'':>18} │",
                f"│ {'LRC source:':<30} {'':>18} │",
                f"│ {'  R2 pre-existing:':<30} {lrc_skipped_existing:>18} │",
                f"│ {'  YouTube Transcription:':<30} {lrc_youtube:>18} │",
                f"│ {'  ASR (Qwen3):':<30} {lrc_qwen_asr:>18} │",
                f"│ {'  ASR (Whisper):':<30} {lrc_whisper_asr:>18} │",
            ]
        )
        if lrc_unknown > 0:
            lines.append(f"│ {'  Generated (unknown):':<30} {lrc_unknown:>18} │")

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

    # Analysis stats
    analysis_completed = sum(1 for r in results.values() if r.get("analyze") == "completed")
    analysis_failed = sum(1 for r in results.values() if r.get("analyze") == "failed")
    if analysis_completed or analysis_failed:
        lines.extend(
            [
                f"│ {'':<30} {'':>18} │",
                f"│ {'Analysis:':<30} {'':>18} │",
                f"│ {'  Completed:':<30} {analysis_completed:>18} │",
                f"│ {'  Failed:':<30} {analysis_failed:>18} │",
            ]
        )

    # Embedding stats
    embedding_completed = sum(1 for r in results.values() if r.get("embedding") == "completed")
    embedding_failed = sum(1 for r in results.values() if r.get("embedding") == "failed")
    embedding_skipped = sum(1 for r in results.values() if r.get("embedding") == "skipped")
    if embedding_completed or embedding_failed or embedding_skipped:
        lines.extend(
            [
                f"│ {'':<30} {'':>18} │",
                f"│ {'Embedding:':<30} {'':>18} │",
                f"│ {'  Completed:':<30} {embedding_completed:>18} │",
                f"│ {'  Failed:':<30} {embedding_failed:>18} │",
                f"│ {'  Skipped:':<30} {embedding_skipped:>18} │",
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

    # Print failed analysis
    failed_analysis = [
        (song_id, r.get("analyze_error"))
        for song_id, r in results.items()
        if r.get("analyze") == "failed"
    ]
    if failed_analysis:
        console.print("\n[bold red]Failed analysis:[/bold red]")
        for song_id, error in failed_analysis:
            song = db_client.get_song(song_id)
            song_name = song.title if song else song_id
            console.print(f"  - {song_name}: {error}")

    # Print failed embeddings
    failed_embeddings = [
        (song_id, r.get("embedding_error"))
        for song_id, r in results.items()
        if r.get("embedding") == "failed"
    ]
    if failed_embeddings:
        console.print("\n[bold red]Failed embedding:[/bold red]")
        for song_id, error in failed_embeddings:
            song = db_client.get_song(song_id)
            song_name = song.title if song else song_id
            console.print(f"  - {song_name}: {error}")


@app.command("probe")
def probe(
    song_id: str = typer.Argument(..., help="Song ID to probe"),
    force: bool = typer.Option(
        False, "--force", "-f", help="Re-probe even if duration_seconds is already set"
    ),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Probe audio duration via ffprobe and update the recording in the database.

    Downloads the audio file from R2 (using the local cache), runs ffprobe
    to determine duration, and updates recordings.duration_seconds.

    Use --force to re-probe recordings that already have a duration.
    """
    from stream_of_worship.admin.services.asset_cache import AssetCache

    if not is_ffprobe_available():
        console.print("[red]ffprobe is not installed or not on PATH.[/red]")
        console.print("[dim]Install FFmpeg: https://ffmpeg.org/download.html[/dim]")
        raise typer.Exit(1)

    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)

    recording = db_client.get_recording_by_song_id(song_id)
    if not recording:
        recording = db_client.get_recording_by_hash(song_id)
    if not recording:
        console.print(f"[red]No recording found for: {song_id}[/red]")
        raise typer.Exit(1)

    hash_prefix = recording.hash_prefix

    if recording.duration_seconds is not None and not force:
        console.print(
            f"[yellow]Duration already set: {recording.duration_seconds:.1f}s. "
            f"Use --force to re-probe.[/yellow]"
        )
        raise typer.Exit(0)

    try:
        r2_client = R2Client(
            bucket=config.r2_bucket,
            endpoint_url=config.r2_endpoint_url,
            region=config.r2_region,
        )
    except ValueError as e:
        console.print(f"[red]R2 configuration error: {e}[/red]")
        raise typer.Exit(1)

    cache_dir = get_cache_dir(config)
    cache = AssetCache(cache_dir=cache_dir, r2_client=r2_client)

    console.print("[cyan]Downloading audio from R2...[/cyan]")
    audio_path = cache.download_audio(hash_prefix)
    if not audio_path:
        console.print(f"[red]Failed to download audio for {hash_prefix}[/red]")
        raise typer.Exit(1)

    console.print("[cyan]Probing audio duration...[/cyan]")
    duration = probe_duration(audio_path)
    if duration is None:
        console.print("[red]ffprobe failed to determine duration[/red]")
        raise typer.Exit(1)

    db_client.update_recording_duration(hash_prefix, duration)
    console.print(f"[green]Duration updated: {duration:.1f}s[/green]")


@app.command("probe-batch")
def probe_batch(
    album: Optional[str] = typer.Option(None, "--album", help="Filter by album name"),
    song: Optional[str] = typer.Option(None, "--song", help="Filter by song name (partial match)"),
    analysis_status: Optional[str] = typer.Option(
        None, "--analysis-status", help="Filter by analysis status"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Re-probe even if duration_seconds is already set"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show what would be probed without executing"
    ),
    limit: Optional[int] = typer.Option(None, "--limit", help="Maximum number of songs to process"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Batch probe audio durations for recordings missing duration_seconds.

    Downloads audio from R2, runs ffprobe, and updates duration_seconds
    for all recordings that have NULL duration_seconds (or all if --force).
    """
    from stream_of_worship.admin.services.asset_cache import AssetCache

    if not is_ffprobe_available():
        console.print("[red]ffprobe is not installed or not on PATH.[/red]")
        console.print("[dim]Install FFmpeg: https://ffmpeg.org/download.html[/dim]")
        raise typer.Exit(1)

    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)

    if force:
        recordings = db_client.list_recordings(limit=limit)
    else:
        recordings = db_client.get_recordings_without_duration()
        if limit:
            recordings = recordings[:limit]

    if album:
        recordings = [
            r
            for r in recordings
            if r.song_id
            and db_client.get_song(r.song_id)
            and album.lower() in (db_client.get_song(r.song_id).album_name or "").lower()
        ]

    if song:
        recordings = [
            r
            for r in recordings
            if r.song_id
            and db_client.get_song(r.song_id)
            and song.lower() in (db_client.get_song(r.song_id).title or "").lower()
        ]

    if analysis_status:
        recordings = [r for r in recordings if r.analysis_status == analysis_status]

    if not recordings:
        console.print("[yellow]No recordings to probe.[/yellow]")
        raise typer.Exit(0)

    console.print(f"[cyan]Found {len(recordings)} recording(s) to probe[/cyan]")

    if dry_run:
        table = Table(title="Recordings to probe")
        table.add_column("Hash Prefix", style="cyan")
        table.add_column("Song ID", style="green")
        table.add_column("Duration", style="yellow")
        table.add_column("Analysis Status", style="dim")
        for r in recordings:
            song_obj = db_client.get_song(r.song_id) if r.song_id else None
            song_name = song_obj.title if song_obj else r.song_id or "—"
            table.add_row(
                r.hash_prefix,
                song_name,
                f"{r.duration_seconds:.1f}s" if r.duration_seconds else "NULL",
                r.analysis_status,
            )
        console.print(table)
        raise typer.Exit(0)

    try:
        r2_client = R2Client(
            bucket=config.r2_bucket,
            endpoint_url=config.r2_endpoint_url,
            region=config.r2_region,
        )
    except ValueError as e:
        console.print(f"[red]R2 configuration error: {e}[/red]")
        raise typer.Exit(1)

    cache_dir = get_cache_dir(config)
    cache = AssetCache(cache_dir=cache_dir, r2_client=r2_client)

    probed = 0
    skipped = 0
    failed = 0

    for i, recording in enumerate(recordings, 1):
        hash_prefix = recording.hash_prefix
        song_obj = db_client.get_song(recording.song_id) if recording.song_id else None
        song_name = song_obj.title if song_obj else recording.song_id or hash_prefix

        console.print(f"\n[{i}/{len(recordings)}] {song_name} ({hash_prefix})")

        audio_path = cache.download_audio(hash_prefix)
        if not audio_path:
            console.print("  [red]Failed to download audio[/red]")
            failed += 1
            continue

        duration = probe_duration(audio_path)
        if duration is None:
            console.print("  [red]ffprobe failed[/red]")
            failed += 1
            continue

        db_client.update_recording_duration(hash_prefix, duration)
        console.print(f"  [green]Duration: {duration:.1f}s[/green]")
        probed += 1

    console.print(f"\n[bold]Summary:[/bold] {probed} probed, {skipped} skipped, {failed} failed")
