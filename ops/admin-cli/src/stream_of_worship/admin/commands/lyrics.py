"""Lyrics commands for sow-admin.

Hosts the LRC lifecycle commands (``lyrics generate``, ``align``,
``view``, ``upload``, ``edit``) plus the Lyrics Feedback triage queue
(issue #194): list open feedback grouped by Recording, and bulk
resolve/unresolve per Recording or Song.

Per ADR 0007, feedback is advisory: feedback commands only write
``lyrics_feedback.resolved_at`` and never touch pipeline status fields
(``recordings.lrc_status``).
"""

from pathlib import Path
from typing import List, Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.table import Table

from stream_of_worship.admin.commands.catalog import get_db_client
from stream_of_worship.admin.config import AdminConfig, get_cache_dir
from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.admin.services.analysis import (
    AnalysisClient,
    AnalysisServiceError,
    JobInfo,
)
from stream_of_worship.admin.services.lrc_jobs import (
    display_lrc,
    resolve_lyrics_text,
    submit_lrc_batch,
    submit_lrc_single,
)
from stream_of_worship.admin.services.lrc_parser import (
    build_draft_from_catalog,
    format_duration,
    parse_lrc,
    parse_lrc_full,
    serialize_lrc,
)
from stream_of_worship.admin.services.prompts import (
    prompt_choice,
    prompt_confirmation,
    read_song_ids_from_stdin,
)
from stream_of_worship.admin.services.r2 import R2Client, R2ObjectIdentity
from stream_of_worship.db.connection import ConnectionProvider

console = Console()
app = typer.Typer(help="Lyrics operations")
feedback_app = typer.Typer(help="Lyrics feedback triage queue")
app.add_typer(feedback_app, name="feedback")

REASON_ORDER = ["missing", "timing", "wrong_text", "other"]

# Presentation-only mapping: which pipeline command addresses the complaint.
SUGGESTED_ACTIONS = {
    "missing": "generate Lyrics (lyrics generate <song-id>)",
    "timing": "re-align (lyrics align <song-id>)",
    "wrong_text": "manual text review",
    "other": "manual text review",
}


def _load_connection_provider(config_path: Optional[Path]) -> AdminConfig:
    try:
        return AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)


def _truncate(value, width: int = 30) -> str:
    if value is None:
        return ""
    text = str(value)
    if len(text) > width:
        return text[:width] + "…"
    return text


def _suggested_action(reason_counts: dict[str, int]) -> str:
    """Pick the suggested action from the dominant open reason.

    Tie-break by REASON_ORDER; empty open rows → manual review.
    """
    if not reason_counts:
        return SUGGESTED_ACTIONS["other"]
    dominant = max(
        REASON_ORDER,
        key=lambda r: (reason_counts.get(r, 0), -REASON_ORDER.index(r)),
    )
    if reason_counts.get(dominant, 0) == 0:
        return SUGGESTED_ACTIONS["other"]
    return SUGGESTED_ACTIONS[dominant]


def _reason_breakdown(open_counts: dict[str, int]) -> str:
    """Render open reason counts as e.g. ``missing×2 timing×1``."""
    parts = []
    for reason in REASON_ORDER:
        count = open_counts.get(reason, 0)
        if count:
            parts.append(f"{reason}×{count}")
    return " ".join(parts)


@feedback_app.command("list")
def feedback_list(
    rating: Optional[str] = typer.Option(None, "--rating", help="Filter: happy | sad"),
    reason: Optional[str] = typer.Option(
        None, "--reason", help="Filter: missing | timing | wrong_text | other"
    ),
    include_all: bool = typer.Option(
        False, "--all", help="Include recordings whose feedback is fully resolved"
    ),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """List recordings with lyrics feedback, grouped by Recording.

    Default shows only recordings with OPEN (unresolved) feedback;
    --all includes fully resolved ones.
    """
    if rating is not None and rating not in ("happy", "sad"):
        console.print("[red]--rating must be happy or sad[/red]")
        raise typer.Exit(1)
    if reason is not None and reason not in REASON_ORDER:
        console.print(f"[red]--reason must be one of: {', '.join(REASON_ORDER)}[/red]")
        raise typer.Exit(1)

    config = _load_connection_provider(config_path)
    provider = ConnectionProvider(config.get_connection_url())
    conn = provider.get_connection()
    clauses: list[str] = []
    params: dict = {}
    if rating is not None:
        clauses.append("f.rating = %(rating)s")
        params["rating"] = rating
    if reason is not None:
        clauses.append("(f.resolved_at IS NULL AND f.reason = %(reason)s)")
        params["reason"] = reason
    where = " AND ".join(clauses) if clauses else "TRUE"
    having = "" if include_all else "HAVING COUNT(*) FILTER (WHERE f.resolved_at IS NULL) > 0"

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                r.content_hash,
                r.hash_prefix,
                r.lrc_status,
                s.id AS song_id,
                s.title,
                COUNT(*) FILTER (WHERE f.resolved_at IS NULL) AS open_count,
                COUNT(*) FILTER (WHERE f.resolved_at IS NULL AND f.rating = 'happy') AS open_happy,
                COUNT(*) FILTER (WHERE f.resolved_at IS NULL AND f.rating = 'sad' AND f.reason = 'missing') AS open_missing,
                COUNT(*) FILTER (WHERE f.resolved_at IS NULL AND f.rating = 'sad' AND f.reason = 'timing') AS open_timing,
                COUNT(*) FILTER (WHERE f.resolved_at IS NULL AND f.rating = 'sad' AND f.reason = 'wrong_text') AS open_wrong_text,
                COUNT(*) FILTER (WHERE f.resolved_at IS NULL AND f.rating = 'sad' AND f.reason = 'other') AS open_other,
                MAX(f.created_at) FILTER (WHERE f.resolved_at IS NULL) AS latest_report
            FROM lyrics_feedback f
            LEFT JOIN recordings r ON r.content_hash = f.recording_content_hash
            LEFT JOIN songs s ON s.id = r.song_id
            WHERE {where}
            GROUP BY r.content_hash, r.hash_prefix, r.lrc_status, s.id, s.title
            {having}
            ORDER BY MAX(f.created_at) FILTER (WHERE f.resolved_at IS NULL) DESC NULLS LAST,
                     r.hash_prefix
            """,
            params,
        )
        rows = cur.fetchall()
    provider.close()

    if not rows:
        console.print("[yellow]No lyrics feedback found.[/yellow]")
        return

    table = Table(title="Lyrics Feedback Queue")
    table.add_column("Song", style="cyan")
    table.add_column("Recording", style="magenta")
    table.add_column("LRC", style="dim")
    table.add_column("Feedback", justify="center")
    table.add_column("Open", justify="right")
    table.add_column("Reasons (neg)")
    table.add_column("Latest report")
    table.add_column("Suggested action")

    for row in rows:
        (
            content_hash,
            hash_prefix,
            lrc_status,
            song_id,
            title,
            open_count,
            open_happy,
            open_missing,
            open_timing,
            open_wrong_text,
            open_other,
            latest_report,
        ) = row
        open_counts = {
            "missing": open_missing,
            "timing": open_timing,
            "wrong_text": open_wrong_text,
            "other": open_other,
        }
        table.add_row(
            _truncate(title),
            hash_prefix,
            lrc_status or "",
            "👍" if open_happy == open_count else "👎",
            str(open_count),
            _reason_breakdown(open_counts),
            latest_report.strftime("%Y-%m-%d") if latest_report else "",
            _suggested_action(open_counts),
        )
    console.print(table)


def _resolve_target(conn, target: str) -> Optional[str]:
    """Resolve the CLI target to a recording content_hash filter.

    Returns 'content_hash=<value>' if target is a recording content hash,
    or a song-level sub-select clause if it is a songs.id. Returns None
    when the target matches neither.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM recordings WHERE content_hash = %s", (target,))
        if cur.fetchone():
            return "recording_content_hash = %(target)s"
        cur.execute(
            """
            SELECT 1 FROM songs
            WHERE id = %s AND deleted_at IS NULL
            """,
            (target,),
        )
        if cur.fetchone():
            return (
                "recording_content_hash IN (SELECT content_hash FROM recordings "
                "WHERE song_id = %(target)s AND deleted_at IS NULL)"
            )
    return None


def _set_resolved(
    provider: ConnectionProvider,
    target: str,
    resolved: bool,
) -> int:
    """Bulk set/clear resolved_at for open (or resolved) rows of the target.

    Returns affected row count. Raises ValueError when the target matches
    neither a recording nor a song.
    """
    conn = provider.get_connection()
    clause = _resolve_target(conn, target)
    if clause is None:
        provider.close()
        raise ValueError(f"No recording or song matches {target!r}")

    with conn.cursor() as cur:
        if resolved:
            cur.execute(
                f"""
                UPDATE lyrics_feedback
                SET resolved_at = NOW()
                WHERE resolved_at IS NULL AND {clause}
                """,
                {"target": target},
            )
        else:
            cur.execute(
                f"""
                UPDATE lyrics_feedback
                SET resolved_at = NULL
                WHERE resolved_at IS NOT NULL AND {clause}
                """,
                {"target": target},
            )
        affected = cur.rowcount
    conn.commit()
    return affected


@feedback_app.command("resolve")
def feedback_resolve(
    target: str = typer.Argument(
        ..., help="Recording content hash OR song id to resolve feedback for"
    ),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Mark all open feedback for a Recording (or Song) as resolved.

    Advisory only (ADR 0007): does not touch pipeline lrc_status.
    """
    config = _load_connection_provider(config_path)
    provider = ConnectionProvider(config.get_connection_url())
    try:
        affected = _set_resolved(provider, target, resolved=True)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    provider.close()
    console.print(f"[green]Resolved {affected} feedback row(s) for {target}.[/green]")


@feedback_app.command("unresolve")
def feedback_unresolve(
    target: str = typer.Argument(
        ..., help="Recording content hash OR song id to unresolve feedback for"
    ),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Reopen (clear resolved_at on) feedback for a Recording (or Song)."""
    config = _load_connection_provider(config_path)
    provider = ConnectionProvider(config.get_connection_url())
    try:
        affected = _set_resolved(provider, target, resolved=False)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)
    provider.close()
    console.print(f"[green]Unresolved {affected} feedback row(s) for {target}.[/green]")


@app.command("generate")
def lyrics_generate(
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
        sow-admin audio list --lrc incomplete --format ids | sow-admin lyrics generate --stdin
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
        song_ids = read_song_ids_from_stdin()
        if not song_ids:
            console.print("[yellow]No song IDs provided via stdin[/yellow]")
            raise typer.Exit(0)
    else:
        song_ids = [song_id]

    # Process all songs
    if len(song_ids) == 1:
        # Single song mode - original behavior with wait support
        submit_lrc_single(
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
        submit_lrc_batch(
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

@app.command("align")
def lyrics_align(
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
        sow-admin audio list --lrc incomplete --format ids | sow-admin lyrics align --stdin
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
        song_ids = read_song_ids_from_stdin()
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

@app.command("view")
def lyrics_view(
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

        sow-admin lyrics view song_001 song_002 song_003

    Or pipe from audio list using '-' to read from stdin:

        sow-admin audio list --visibility published --format ids | sow-admin lyrics view -
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
        song_ids = read_song_ids_from_stdin()
        if not song_ids:
            console.print("[yellow]No song IDs provided via stdin[/yellow]")
            raise typer.Exit(0)

    # Track success/failure counts
    success_count = 0
    error_count = 0

    # Process each song ID
    for idx, sid in enumerate(song_ids):
        if not raw:
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
        if display_lrc(console, song, recording, sid, raw, no_timestamps):
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

@app.command("upload")
def lyrics_upload(
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
    if not prompt_confirmation("Upload this LRC file?"):
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

@app.command("edit")
def lyrics_edit(
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
        choice = prompt_choice("Choose:", ["Resume", "Discard", "Save aside and start fresh"])
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
