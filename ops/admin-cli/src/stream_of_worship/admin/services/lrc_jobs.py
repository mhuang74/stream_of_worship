"""Shared LRC job primitives for admin commands.

Helpers for resolving lyrics payloads and submitting LRC generation jobs
to the analysis service. Used by both the ``audio`` commands (download,
backfill orchestration) and the ``lyrics`` commands (generate/batch).
"""

import json
import tempfile
from pathlib import Path
from typing import Optional

import typer
from botocore.exceptions import ClientError
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table

from stream_of_worship.admin.config import AdminConfig

from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.admin.services.analysis import (
    AnalysisClient,
    AnalysisServiceError,
    JobInfo,
)
from stream_of_worship.admin.services.structured_lyrics import (
    flatten_structured_lyrics,
    parse_structured_lyrics_smart,
)
from stream_of_worship.admin.services.youtube import extract_video_metadata
from stream_of_worship.admin.services.zanmei import fetch_structured_lyrics_from_zanmei


def resolve_lyrics_text(song: Song, recording: Recording) -> str | None:
    """Pick the best lyrics payload for an LRC job.

    Prefers structured lyrics (flattened, tags preserved) from the
    recording; falls back to ``songs.lyrics_raw``. Returns ``None`` if
    neither is available.
    """
    if recording.structured_lyrics:
        try:
            structured = json.loads(recording.structured_lyrics)
            if structured and structured.get("sections"):
                return flatten_structured_lyrics(structured)
        except json.JSONDecodeError:
            pass
    return song.lyrics_raw


def submit_lrc_single(
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
    lyrics_text = resolve_lyrics_text(song, recording) if song else None
    if not song or not lyrics_text:
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
                lyrics_text=lyrics_text,
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


def submit_lrc_batch(
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
        lyrics_text = resolve_lyrics_text(song, recording) if song else None
        if not song or not lyrics_text:
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
                lyrics_text=lyrics_text,
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


def submit_lrc_job(
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
    lyrics_text = resolve_lyrics_text(song, recording) if song else None
    if not song or not lyrics_text:
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
            lyrics_text=lyrics_text,
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


def fetch_structured_lyrics(
    *,
    youtube_url: str | None,
    song_title: str,
    band: str | None,
    source: str,
    use_llm: bool,
    console: Console,
) -> tuple[str | None, str | None, str]:
    """Fetch structured lyrics according to the ``source`` preference.

    ``source`` is one of ``youtube`` (YouTube description only), ``zanmei``
    (zanmei.ai only), or ``auto`` (YouTube first, then zanmei.ai fallback
    when YouTube yields no section-tagged lyrics).

    Returns ``(structured_raw, structured_json_str, source_used)``.
    ``structured_raw`` is the raw text harvested from the winning source;
    ``structured_json_str`` is the parsed structured-lyrics JSON (or None).

    Matching the pre-existing YouTube-only path, a hard failure while parsing
    YouTube lyrics with the LLM enabled raises ``typer.Exit(1)`` so callers
    keep their historical error UX. Zanmei is best-effort: a fetch/parse
    failure is reported and results in ``(None, None, zanmei)`` rather than
    blocking the download.
    """
    if youtube_url:
        try:
            metadata = extract_video_metadata(youtube_url)
        except RuntimeError as e:
            console.print(
                f"[yellow]Could not fetch YouTube metadata for structured lyrics: {e}[/yellow]"
            )
            yt_raw = None
            yt_json = None
        else:
            yt_raw = metadata.description
            try:
                yt_structured = parse_structured_lyrics_smart(
                    metadata.description,
                    use_llm=use_llm,
                    source_desc="a YouTube video description",
                )
            except RuntimeError as e:
                if use_llm:
                    console.print(f"[red]LLM lyrics extraction failed: {e}[/red]")
                    console.print(
                        "[dim]Use --no-llm to fall back to the regex heuristic only.[/dim]"
                    )
                    raise typer.Exit(1)
                yt_structured = None
            yt_json = (
                json.dumps(yt_structured, ensure_ascii=False)
                if yt_structured and yt_structured.get("sections")
                else None
            )
    else:
        yt_raw = None
        yt_json = None

    if source == "youtube":
        return yt_raw, yt_json, "youtube"

    # Zanmei needed (forced or auto-fallback when YouTube gave no sections).
    if source == "auto" and yt_json is not None:
        return yt_raw, yt_json, "youtube"

    try:
        lyrics_text = fetch_structured_lyrics_from_zanmei(song_title, band)
    except RuntimeError as e:
        console.print(f"[yellow]Zanmei lyrics fetch failed: {e}[/yellow]")
        return (yt_raw if source == "auto" else None), yt_json, "zanmei"

    if not lyrics_text:
        console.print(
            f"[yellow]No lyrics found on zanmei.ai for {song_title!r} "
            f"{f'({band!r})' if band else ''}[/yellow]"
        )
        return (yt_raw if source == "auto" else None), yt_json, "zanmei"

    try:
        structured = parse_structured_lyrics_smart(
            lyrics_text,
            use_llm=use_llm,
            source_desc="zanmei.ai song lyrics",
        )
    except RuntimeError as e:
        console.print(f"[yellow]Zanmei lyrics parse failed: {e}[/yellow]")
        structured = None
    structured_json = json.dumps(structured, ensure_ascii=False) if structured else None
    return lyrics_text, structured_json, "zanmei"


def display_lrc(
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
                console.print(f"[dim]Run 'sow-admin lyrics generate {song_id}' to generate LRC[/dim]")
            else:
                console.print(f"[red]Error downloading LRC from R2: {e}[/red]")
            return False

        # Read content
        content = temp_path.read_text(encoding="utf-8")

        # Display based on mode
        if raw:
            console.print(content, end="")
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
