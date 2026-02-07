"""Audio commands for sow-admin.

Provides CLI commands for downloading audio from YouTube, listing
recordings, and viewing recording details.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
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
from stream_of_worship.admin.services.r2 import R2Client
from stream_of_worship.admin.services.youtube import YouTubeDownloader

console = Console()
app = typer.Typer(help="Audio recording operations")


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
        table.add_column("Song ID", style="cyan", no_wrap=True)
        table.add_column("Song Title", style="green")
        table.add_column("Hash Prefix", style="dim", no_wrap=True)
        table.add_column("Filename", style="yellow")
        table.add_column("Size", style="magenta", justify="right")
        table.add_column("Status", style="blue", justify="center")
        table.add_column("Job ID", style="dim", no_wrap=True)

        for rec in recordings:
            song_id = rec.song_id or "-"
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

            job_id = rec.analysis_job_id or "-"

            table.add_row(
                song_id,
                song_title,
                rec.hash_prefix,
                rec.original_filename,
                size_str,
                status_text,
                job_id,
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
                    poll_interval=3.0,
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
                    poll_interval=3.0,
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
    config_path: Optional[Path] = typer.Option(
        None, "--config", "-c", help="Path to config file"
    ),
) -> None:
    """Check analysis status.

    With JOB_ID: query the service for that job's status.
    Without: list all recordings with pending/processing/failed status.
    Use --sync to update local database with latest statuses from service.
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
    table.add_column("Song ID", style="cyan", no_wrap=True)
    table.add_column("Song Title", style="green")
    table.add_column("Hash Prefix", style="dim", no_wrap=True)
    table.add_column("Analysis", style="magenta")
    table.add_column("Analysis Job", style="dim", no_wrap=True)
    table.add_column("LRC", style="blue")
    table.add_column("LRC Job", style="dim", no_wrap=True)

    for row in rows:
        song_id = row[2] if row[2] else "-"
        hash_prefix = row[1]
        song_title = row[25] if row[25] else "-"
        analysis_status = row[19]
        analysis_job_id = row[20] if row[20] else "-"
        lrc_status = row[21]
        lrc_job_id = row[22] if row[22] else "-"

        table.add_row(
            song_id,
            song_title,
            hash_prefix,
            _colorize_status(analysis_status),
            analysis_job_id,
            _colorize_status(lrc_status),
            lrc_job_id,
        )

    console.print(table)
