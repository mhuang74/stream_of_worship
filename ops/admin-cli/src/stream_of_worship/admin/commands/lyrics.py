"""Lyrics commands for sow-admin.

Hosts the LRC lifecycle commands (``lyrics generate``, ``align``,
``view``, ``upload``, ``edit``) plus the Lyrics Feedback triage queue
(issue #194): list open feedback grouped by Recording, and bulk
resolve/unresolve per Recording or Song.

Per ADR 0007, feedback is advisory: feedback commands only write
``lyrics_feedback.resolved_at`` and never touch pipeline status fields
(``recordings.lrc_status``).
"""

import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from botocore.exceptions import ClientError
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.table import Table

from stream_of_worship.admin.audit import log_change
from stream_of_worship.admin.commands.catalog import get_db_client
from stream_of_worship.admin.config import AdminConfig, get_cache_dir
from stream_of_worship.admin.constants import FORCED_ALIGN_MAX_DURATION_SECONDS
from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.db.models import Recording, Song
from stream_of_worship.admin.editor.state import EditorState
from stream_of_worship.admin.editor.upload import UploadResult
from stream_of_worship.admin.services.analysis import (
    AnalysisClient,
    AnalysisServiceError,
    JobInfo,
)
from stream_of_worship.admin.services.hasher import get_hash_prefix
from stream_of_worship.admin.services.lrc_jobs import (
    alignment_wait_timeout,
    display_lrc,
    resolve_alignment_lyrics_text,
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
    prompt_choice_or_none,
    prompt_enter_or_no,
    prompt_typed_yes,
    read_song_ids_from_stdin,
)
from stream_of_worship.admin.services.r2 import R2Client, R2ObjectIdentity
from stream_of_worship.admin.services.structured_lyrics import (
    format_structured_lyrics_canonical,
    parse_structured_lyrics,
)
from stream_of_worship.admin.services.visibility import (
    ensure_reprocessable,
    is_published,
    visibility_alternatives,
    visibility_badge,
    visibility_label,
)
from stream_of_worship.db.connection import ConnectionProvider
from stream_of_worship.db.user_client import UserClient

console = Console()
app = typer.Typer(help="Lyrics operations")
feedback_app = typer.Typer(help="Lyrics feedback triage queue")
app.add_typer(feedback_app, name="feedback")

REASON_ORDER = ["missing", "timing", "wrong_text", "other"]

RATING_FILTER = {"good": "happy", "poor": "sad"}


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
    rating: Optional[str] = typer.Option(
        None, "--rating", help="Filter: good | poor | all (all = no rating filter)"
    ),
    reason: Optional[str] = typer.Option(
        None, "--reason", help="Filter: missing | timing | wrong_text | other"
    ),
    include_all: bool = typer.Option(
        False, "--all", help="Include recordings whose feedback is fully resolved"
    ),
    format: str = typer.Option("table", "--format", "-f", help="Output format (table|ids|json)"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """List recordings with lyrics feedback, grouped by Recording.

    Default shows only recordings with OPEN (unresolved) feedback;
    --all includes fully resolved ones.

    Feedback attached to a soft-deleted recording is always excluded: this
    queue is grouped by song ID, and every song-level consumer (feedback show,
    feedback resolve, lyrics generate) resolves through recordings with
    `recordings.deleted_at IS NULL`, so such rows would advertise work no
    song-level command can perform. Recording-hash targets still reach them
    (`feedback show <content_hash>`); the exclusion is about the song-ID stream
    that --format ids emits.

    Residual gap: feedback on a live recording whose *song* is soft-deleted is
    still listed here, while `feedback show <song_id>` reports nothing (song
    targets also require `songs.deleted_at IS NULL`).

    Feedback rows with no matching recordings row at all stay listed
    (reported via hash_prefix).

    --rating all is an alias for no rating filter: it lists recordings that have
    both good and poor feedback (unresolved-only by default). --rating all --all
    includes fully-resolved recordings, same as plain --all.

    Use --format ids to print one song ID per line (pipeable):
        sow-admin lyrics feedback list --rating poor --format ids | sow-admin lyrics generate --force --stdin
        sow-admin lyrics feedback list --rating good --format ids | sow-admin audio set-visibility --status published --stdin

    Use --format json to print a machine-readable summary wrapper with the
    full text of every open note (all ratings, each tagged):
        sow-admin lyrics feedback list --rating poor --format json

    Footgun: --rating all --format ids includes 👍-only recordings. Pipe
    --rating poor into lyrics generate --force to avoid needlessly
    regenerating well-liked recordings.
    """
    if rating is not None and rating not in ("good", "poor", "all"):
        console.print("[red]--rating must be good, poor, or all[/red]")
        raise typer.Exit(1)
    if reason is not None and reason not in REASON_ORDER:
        console.print(f"[red]--reason must be one of: {', '.join(REASON_ORDER)}[/red]")
        raise typer.Exit(1)
    if format not in ("table", "ids", "json"):
        console.print(f"[red]Invalid format: {format}. Must be one of: table, ids, json[/red]")
        raise typer.Exit(1)

    config = _load_connection_provider(config_path)
    provider = ConnectionProvider(config.get_connection_url())
    conn = provider.get_connection()
    # Always exclude feedback that hangs on a soft-deleted recording: the queue
    # is grouped by song ID and song-ID targets resolve with
    # `recordings.deleted_at IS NULL` (see _resolve_target / the generate
    # feedback lookup), so those rows are unreachable from song-level commands.
    # `r.content_hash IS NULL` keeps the LEFT JOIN's rows whose hash has no
    # recordings row at all visible.
    clauses: list[str] = ["(r.content_hash IS NULL OR r.deleted_at IS NULL)"]
    params: dict = {}
    if rating is not None and rating != "all":
        # Open-only: with --rating poor, the group must be built from open sad
        # rows, so recordings whose sad rows are all resolved (but which have an
        # open happy row) drop out instead of being regenerated needlessly.
        clauses.append("(f.resolved_at IS NULL AND f.rating = %(rating)s)")
        params["rating"] = RATING_FILTER[rating]
    if reason is not None:
        clauses.append("(f.resolved_at IS NULL AND f.reason = %(reason)s)")
        params["reason"] = reason
    where = " AND ".join(clauses)
    having = "" if include_all else "HAVING COUNT(*) FILTER (WHERE f.resolved_at IS NULL) > 0"

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                r.content_hash,
                r.hash_prefix,
                s.id AS song_id,
                s.title,
                COUNT(*) FILTER (WHERE f.resolved_at IS NULL) AS open_count,
                COUNT(*) FILTER (WHERE f.resolved_at IS NULL AND f.rating = 'happy') AS open_happy,
                COUNT(*) FILTER (WHERE f.resolved_at IS NULL AND f.rating = 'sad' AND f.reason = 'missing') AS open_missing,
                COUNT(*) FILTER (WHERE f.resolved_at IS NULL AND f.rating = 'sad' AND f.reason = 'timing') AS open_timing,
                COUNT(*) FILTER (WHERE f.resolved_at IS NULL AND f.rating = 'sad' AND f.reason = 'wrong_text') AS open_wrong_text,
                COUNT(*) FILTER (WHERE f.resolved_at IS NULL AND f.rating = 'sad' AND f.reason = 'other') AS open_other,
                COUNT(*) FILTER (WHERE f.resolved_at IS NULL AND f.note IS NOT NULL) AS open_notes,
                MAX(f.created_at) FILTER (WHERE f.resolved_at IS NULL) AS latest_report
            FROM lyrics_feedback f
            LEFT JOIN recordings r ON r.content_hash = f.recording_content_hash
            LEFT JOIN songs s ON s.id = r.song_id
            WHERE {where}
            GROUP BY r.content_hash, r.hash_prefix, s.id, s.title
            {having}
            ORDER BY MAX(f.created_at) FILTER (WHERE f.resolved_at IS NULL) DESC NULLS LAST,
                      r.hash_prefix
            """,
            params,
        )
        rows = cur.fetchall()
    note_rows: list = []
    if rows:
        hashes = [row[0] for row in rows]
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT f.recording_content_hash, u.email, f.rating, f.reason, f.note,
                       f.created_at
                FROM lyrics_feedback f
                JOIN "user" u ON u.id = f.user_id
                WHERE f.resolved_at IS NULL
                  AND f.note IS NOT NULL
                  AND f.recording_content_hash = ANY(%(hashes)s)
                ORDER BY f.created_at ASC
                """,
                {"hashes": hashes},
            )
            note_rows = cur.fetchall()
    provider.close()

    if format == "json":
        notes_by_hash: dict[str, list] = {}
        for content_hash, email, note_rating, note_reason, note, created_at in note_rows:
            notes_by_hash.setdefault(content_hash, []).append(
                {
                    "user": email,
                    "rating": note_rating,
                    "reason": note_reason,
                    "note": note,
                    "created_at": created_at.isoformat(),
                }
            )
        recordings = []
        for row in rows:
            (
                content_hash,
                hash_prefix,
                song_id,
                title,
                open_count,
                open_happy,
                open_missing,
                open_timing,
                open_wrong_text,
                open_other,
                open_notes,
                latest_report,
            ) = row
            recordings.append(
                {
                    "content_hash": content_hash,
                    "hash_prefix": hash_prefix,
                    "song_id": song_id,
                    "title": title,
                    "open_count": open_count,
                    "open_happy": open_happy,
                    "reasons": {
                        "missing": open_missing,
                        "timing": open_timing,
                        "wrong_text": open_wrong_text,
                        "other": open_other,
                    },
                    "open_notes": open_notes,
                    "latest_report": (latest_report.isoformat() if latest_report else None),
                    "notes": notes_by_hash.get(content_hash, []),
                }
            )
        total_open = sum(r["open_count"] for r in recordings)
        print(
            json.dumps(
                {
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "total_open": total_open,
                    "recordings": recordings,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return

    if format == "ids":
        stderr_console = Console(stderr=True)
        seen: set[str] = set()
        songless: list[str] = []
        for row in rows:
            song_id = row[2]
            if song_id:
                if song_id not in seen:
                    seen.add(song_id)
                    console.print(song_id)
            else:
                # Songless recordings cannot be consumed by `lyrics generate`;
                # report them to stderr, keeping stdout a clean id stream.
                songless.append(row[1])
        if songless:
            stderr_console.print(
                f"[yellow]Skipped {len(songless)} recording(s) with no song "
                f"(not pipeable to lyrics generate):[/yellow]"
            )
            for hash_prefix in songless:
                stderr_console.print(f"  {hash_prefix}")
        return
    if not rows:
        console.print("[yellow]No lyrics feedback found.[/yellow]")
        return

    table = Table(title="Lyrics Feedback Queue")
    table.add_column("Song", style="cyan")
    table.add_column("Song ID", style="magenta")
    table.add_column("Like", justify="center")
    table.add_column("Open", justify="right")
    table.add_column("Reasons (neg)")
    table.add_column("Notes", justify="right")
    table.add_column("Latest")

    for row in rows:
        (
            content_hash,
            hash_prefix,
            song_id,
            title,
            open_count,
            open_happy,
            open_missing,
            open_timing,
            open_wrong_text,
            open_other,
            open_notes,
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
            song_id if song_id else hash_prefix,
            "👍" if open_happy == open_count else "👎",
            str(open_count),
            _reason_breakdown(open_counts),
            f"📝{open_notes}" if open_notes else "",
            latest_report.strftime("%Y-%m-%d") if latest_report else "",
        )
    console.print(table)


def _resolve_target(conn, target: str) -> Optional[tuple[str, Optional[str]]]:
    """Resolve the CLI target to a recording content_hash filter.

    Returns ``(clause, song_id)``: ``clause`` is
    ``'content_hash=<value>'`` if target is a recording content hash, or a
    song-level sub-select clause if it is a songs.id. ``song_id`` is the
    owning song id (the recording's or the target's own) for audit lines.
    Returns None when the target matches neither.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT song_id FROM recordings WHERE content_hash = %s", (target,))
        row = cur.fetchone()
        if row:
            return "recording_content_hash = %(target)s", row[0]
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
            ), target
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
    resolved_target = _resolve_target(conn, target)
    if resolved_target is None:
        provider.close()
        raise ValueError(f"No recording or song matches {target!r}")
    clause, song_id = resolved_target

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
    log_change(
        "feedback_clear",
        song_id=song_id,
        mode="bulk_resolve" if resolved else "bulk_unresolve",
        rows=affected,
        target=target,
    )
    return affected


@feedback_app.command("show")
def feedback_show(
    target: str = typer.Argument(
        ..., help="Recording content hash OR song id to show feedback for"
    ),
    all: bool = typer.Option(False, "--all", help="Include resolved feedback"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Show lyrics feedback rows for a Recording (or Song), with notes.

    Default shows only OPEN (unresolved) feedback; --all includes resolved.
    """
    config = _load_connection_provider(config_path)
    provider = ConnectionProvider(config.get_connection_url())
    conn = provider.get_connection()
    resolved_clause = "" if all else "f.resolved_at IS NULL AND "
    resolved_target = _resolve_target(conn, target)
    if resolved_target is None:
        provider.close()
        console.print(f"[red]No recording or song matches {target!r}[/red]")
        raise typer.Exit(1)
    clause, _song_id = resolved_target
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                u.email,
                f.rating,
                f.reason,
                f.note,
                f.created_at,
                f.resolved_at
            FROM lyrics_feedback f
            JOIN "user" u ON u.id = f.user_id
            WHERE {resolved_clause}{clause}
            ORDER BY f.created_at DESC
            """,
            {"target": target},
        )
        rows = cur.fetchall()
    provider.close()

    if not rows:
        console.print("[yellow]No lyrics feedback found.[/yellow]")
        return

    table = Table(title=f"Lyrics Feedback — {target}")
    table.add_column("User", style="cyan")
    table.add_column("Rating", justify="center")
    table.add_column("Reason")
    table.add_column("Note")
    table.add_column("Reported")
    table.add_column("Resolved")
    for email, rating, reason, note, created_at, resolved_at in rows:
        table.add_row(
            email,
            "👍" if rating == "happy" else "👎",
            reason or "",
            note or "",
            created_at.strftime("%Y-%m-%d") if created_at else "",
            resolved_at.strftime("%Y-%m-%d") if resolved_at else "",
        )
    console.print(table)


def _run_bulk_set_resolved(
    provider: ConnectionProvider,
    targets: list[str],
    resolved: bool,
) -> int:
    """Resolve/unresolve open feedback for each target; per-target report; count failures.

    The caller owns the provider and must close it (also on the error path);
    this helper intentionally leaves it open so subsequent targets proceed.
    """
    failures = 0
    verb = "Resolved" if resolved else "Unresolved"
    for target in targets:
        try:
            affected = _set_resolved(provider, target, resolved=resolved)
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            failures += 1
            continue
        console.print(f"[green]{verb} {affected} feedback row(s) for {target}.[/green]")
    return failures


@feedback_app.command("resolve")
def feedback_resolve(
    target: Optional[str] = typer.Argument(
        None, help="Recording content hash OR song id to resolve feedback for"
    ),
    stdin: bool = typer.Option(
        False, "--stdin", help="Read targets (song ids or content hashes) from stdin, one per line"
    ),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Mark all open feedback for a Recording (or Song) as resolved.

    Advisory only (ADR 0007): does not touch pipeline lrc_status.

    For batch processing, pipe targets via stdin:
        sow-admin lyrics feedback list --format ids | sow-admin lyrics feedback resolve --stdin
    """
    if not target and not stdin:
        console.print("[red]Error: Either provide a target argument or use --stdin flag[/red]")
        raise typer.Exit(1)
    if target and stdin:
        console.print("[red]Error: Cannot use both target argument and --stdin flag[/red]")
        raise typer.Exit(1)

    config = _load_connection_provider(config_path)
    provider = ConnectionProvider(config.get_connection_url())
    try:
        if stdin:
            targets = read_song_ids_from_stdin()
            if not targets:
                console.print("[yellow]No targets provided via stdin[/yellow]")
                raise typer.Exit(0)
        else:
            targets = [target]
        failures = _run_bulk_set_resolved(provider, targets, resolved=True)
    finally:
        provider.close()
    if failures:
        raise typer.Exit(1)


@feedback_app.command("unresolve")
def feedback_unresolve(
    target: Optional[str] = typer.Argument(
        None, help="Recording content hash OR song id to unresolve feedback for"
    ),
    stdin: bool = typer.Option(
        False, "--stdin", help="Read targets (song ids or content hashes) from stdin, one per line"
    ),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Reopen (clear resolved_at on) feedback for a Recording (or Song).

    For batch processing, pipe targets via stdin:
        sow-admin lyrics feedback list --format ids | sow-admin lyrics feedback unresolve --stdin
    """
    if not target and not stdin:
        console.print("[red]Error: Either provide a target argument or use --stdin flag[/red]")
        raise typer.Exit(1)
    if target and stdin:
        console.print("[red]Error: Cannot use both target argument and --stdin flag[/red]")
        raise typer.Exit(1)

    config = _load_connection_provider(config_path)
    provider = ConnectionProvider(config.get_connection_url())
    try:
        if stdin:
            targets = read_song_ids_from_stdin()
            if not targets:
                console.print("[yellow]No targets provided via stdin[/yellow]")
                raise typer.Exit(0)
        else:
            targets = [target]
        failures = _run_bulk_set_resolved(provider, targets, resolved=False)
    finally:
        provider.close()
    if failures:
        raise typer.Exit(1)


@app.command("generate")
def lyrics_generate(
    song_id: Optional[str] = typer.Argument(None, help="Song ID to generate LRC for"),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Re-generate even if LRC already exists (also bypasses the LRC result cache)",
    ),
    stdin: bool = typer.Option(False, "--stdin", help="Read song IDs from stdin (one per line)"),
    whisper_model: str = typer.Option("large-v3", "--model", "-m", help="Whisper model to use"),
    llm_model: Optional[str] = typer.Option(
        None,
        "--llm-model",
        help="LLM model for the LLM align/correct step (e.g. openai/gpt-4o-mini, or "
        "model:effort like deepseek-v4.1-flash:low; effort: none|minimal|low|medium|"
        "high|xhigh|max). Validated against the provider at job start. Default: "
        "analysis service's SOW_LLM_MODEL.",
    ),
    language: str = typer.Option("auto", "--lang", help="Language mode: auto, zh, or en"),
    no_vocals: bool = typer.Option(False, "--no-vocals", help="Don't use vocals stem"),
    generator: str = typer.Option(
        "auto",
        "--generator",
        "-g",
        help="LRC generator: auto | youtube | qwen3 | whisper. auto = YouTube "
        "transcript, then Qwen3 ASR, then Whisper. Any other value runs ONLY that "
        "generator and fails the job instead of falling back.",
    ),
    no_cache: bool = typer.Option(
        False,
        "--no-cache",
        help="Bypass all caches: the LRC result cache and the Qwen3/Whisper "
        "transcription caches (implies --force; YouTube transcripts are never cached).",
    ),
    no_wait: bool = typer.Option(
        False, "--no-wait", help="Submit without waiting for job completion"
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Print the selection and guard results without submitting (batch mode only)",
    ),
    resume: Optional[Path] = typer.Option(
        None,
        "--resume",
        help="Resume an interrupted batch run from its manifest (re-polls without resubmitting)",
    ),
    dump_llm_trace: bool = typer.Option(
        False,
        "--dump-llm-trace",
        help="Dump per-step LLM/ASR debug trace (JSON+HTML) on the analysis service host",
    ),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Submit a recording for lyrics alignment (LRC generation).

    Generators (--generator/-g; default auto):
      auto     YouTube transcript, then Qwen3 ASR, then Whisper (fallback chain)
      youtube  YouTube transcript only; fails if unavailable (no ASR tail fill)
      qwen3    DashScope Qwen3 ASR only; fails instead of falling back to Whisper
      whisper  Whisper ASR only

    --no-cache bypasses the LRC result cache and the selected ASR transcription
    cache (implies --force; YouTube transcripts are never cached).

    By default the command waits for job completion. Single-song mode polls with
    live progress (20-minute timeout); --stdin batch mode writes a run manifest
    and polls every job (Ctrl+C-safe, resumable with --resume). Use --no-wait
    to submit and exit immediately.

    Use --dump-llm-trace to dump per-step LLM/ASR traces to SOW_DEBUG_DUMP_DIR
    on the analysis service (auto-bypasses the LRC result cache).

    For batch processing, pipe song IDs via stdin (always batch mode, with
    feedback guard, manifest, and polling):
        sow-admin audio list --lrc incomplete --format ids | sow-admin lyrics generate --stdin
        sow-admin lyrics feedback list --rating poor --format ids | sow-admin lyrics generate --force --stdin
    """
    # Validate mutually exclusive inputs
    if not song_id and not stdin and not resume:
        console.print("[red]Error: Either provide a song_id argument or use --stdin flag[/red]")
        raise typer.Exit(1)
    if song_id and stdin:
        console.print("[red]Error: Cannot use both song_id argument and --stdin flag[/red]")
        raise typer.Exit(1)
    if song_id and resume:
        console.print("[red]Error: Cannot use both song_id argument and --resume[/red]")
        raise typer.Exit(1)
    if stdin and resume:
        console.print("[red]Error: Cannot use both --stdin and --resume[/red]")
        raise typer.Exit(1)
    if dry_run and resume:
        console.print("[red]Error: --dry-run is not valid with --resume[/red]")
        raise typer.Exit(1)
    if language not in {"auto", "zh", "en"}:
        console.print("[red]Error: --lang must be one of: auto, zh, en[/red]")
        raise typer.Exit(1)
    if generator not in {"auto", "youtube", "qwen3", "whisper"}:
        console.print(
            "[red]Error: --generator must be one of: auto, youtube, qwen3, whisper[/red]"
        )
        raise typer.Exit(1)
    # --no-cache must also clear the already-completed submit gate, otherwise a
    # completed recording would skip before any cache is consulted.
    force = force or no_cache

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

    if resume:
        # A resumed run always waits: it is a poll continuation; --force was
        # already applied at the original submission.
        _resume_batch(resume, db_client, analysis_client, config, console)
        return

    # Collect song IDs to process
    if stdin:
        song_ids = read_song_ids_from_stdin()
        if not song_ids:
            console.print("[yellow]No song IDs provided via stdin[/yellow]")
            raise typer.Exit(0)
    else:
        song_ids = [song_id]

    if stdin:
        # Batch flow for ALL stdin input regardless of id count: pre-flight
        # feedback guard, manifest, optional poll loop, run report.
        _run_stdin_batch(
            song_ids=song_ids,
            db_client=db_client,
            analysis_client=analysis_client,
            config=config,
            force=force,
            whisper_model=whisper_model,
            llm_model=llm_model or "",
            language=language,
            no_vocals=no_vocals,
            generator=generator,
            no_cache=no_cache,
            wait=not no_wait,
            dry_run=dry_run,
            dump_llm_trace=dump_llm_trace,
        )
        return

    # Single song mode (positional argument) - original behavior with wait support
    submit_lrc_single(
        song_id=song_ids[0],
        db_client=db_client,
        analysis_client=analysis_client,
        force=force,
        whisper_model=whisper_model,
        llm_model=llm_model or "",
        language=language,
        no_vocals=no_vocals,
        generator=generator,
        no_cache=no_cache,
        wait=not no_wait,
        console=console,
        dump_llm_trace=dump_llm_trace,
    )


def _fetch_open_feedback_hashes(provider: ConnectionProvider, song_id: str) -> dict[str, list[str]]:
    """Return open feedback content hashes for a song, grouped by rating.

    Queries lyrics_feedback joined via recordings.song_id, mirroring the
    `lyrics feedback list` open-only selection: only rows with
    ``resolved_at IS NULL`` count.
    """
    conn = provider.get_connection()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT f.rating, f.recording_content_hash
            FROM lyrics_feedback f
            JOIN recordings r ON r.content_hash = f.recording_content_hash
            WHERE r.song_id = %s
              AND r.deleted_at IS NULL
              AND f.resolved_at IS NULL
            """,
            (song_id,),
        )
        rows = cur.fetchall()
    grouped: dict[str, list[str]] = {}
    for rating, content_hash in rows:
        grouped.setdefault(rating, []).append(content_hash)
    return grouped


def _preflight_guard(
    song_ids: list[str],
    db_client: DatabaseClient,
    provider: ConnectionProvider,
    console: Console,
) -> tuple[list[str], list[dict]]:
    """Feedback-aware pre-flight target guard.

    For each piped song id with open sad feedback, check that the recording
    `submit_lrc_single`/`submit_lrc_batch` would target (the
    ``get_recording_by_song_id`` lookup) matches one of the feedback's
    recording content hashes. Mismatched songs are skipped so a run never
    regenerates a recording users did not complain about. Songs with no open
    sad feedback at all pass unconditionally. Songs with no recording at all
    are reported as ``skipped-songless``.

    Returns ``(accepted_song_ids, skipped_rows)``.
    """
    accepted: list[str] = []
    skipped: list[dict] = []
    for song_id in song_ids:
        recording = db_client.get_recording_by_song_id(song_id)
        if recording is None:
            # No (non-deleted) recording to target — nothing to regenerate.
            # Report it rather than letting it vanish into a submit error.
            skipped.append(
                {
                    "song_id": song_id,
                    "reason": "skipped-songless",
                    "target_hash": None,
                    "feedback_hashes": [],
                }
            )
            console.print(
                f"  [yellow]→ {song_id} (skipped-songless: no recording found "
                f"for this song id)[/yellow]"
            )
            continue
        if is_published(recording):
            skipped.append(
                {
                    "song_id": song_id,
                    "reason": "skipped-published",
                    "target_hash": recording.content_hash,
                    "feedback_hashes": [],
                }
            )
            console.print(
                f"  [yellow]→ {song_id} (skipped-published: demote with "
                f"'sow-admin audio set-visibility {song_id} --status review')[/yellow]"
            )
            continue
        feedback = _fetch_open_feedback_hashes(provider, song_id)
        sad_hashes = feedback.get("sad", [])
        if not sad_hashes:
            accepted.append(song_id)
            continue
        target_hash = recording.content_hash
        if target_hash in sad_hashes:
            accepted.append(song_id)
        else:
            skipped.append(
                {
                    "song_id": song_id,
                    "target_hash": target_hash,
                    "feedback_hashes": sad_hashes,
                }
            )
            console.print(
                f"  [yellow]→ {song_id} (skipped-guard: target recording "
                f"{target_hash or 'none'} does not match open sad feedback on "
                f"{', '.join(sad_hashes)})[/yellow]"
            )
    return accepted, skipped


def _get_lyrics_manifest_dir() -> Path:
    """Get the manifest directory (XDG-aware, overridable via env)."""
    import os

    env_dir = os.environ.get("SOW_BATCH_MANIFEST_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.home() / ".local" / "share" / "sow-admin" / "batch"


def _write_lyrics_manifest(
    batch_id: str,
    manifest_dir: Path,
    started_at: str,
    entries: list[dict],
    kind: str = "lyrics_generate",
) -> Optional[Path]:
    """Write (or rewrite) the lyrics-batch run manifest to disk.

    Records ``finished_at`` once every job has settled. Returns the manifest
    path, or None on failure.
    """
    import logging

    logger = logging.getLogger(__name__)
    try:
        manifest_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = manifest_dir / f"{batch_id}_manifest.json"
        manifest = {
            "batch_id": batch_id,
            "kind": kind,
            "started_at": started_at,
            "jobs": entries,
        }
        if entries and all(e.get("outcome") != "pending" for e in entries):
            manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
        return manifest_path
    except OSError as e:
        logger.warning(f"Failed to write manifest: {e}")
        return None


def _load_lyrics_manifest(manifest_path: Path) -> Optional[dict]:
    """Load a lyrics-batch run manifest from disk."""
    import logging

    logger = logging.getLogger(__name__)
    try:
        return json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"Failed to load manifest {manifest_path}: {e}")
        return None


def _r2_lrc_lookup(r2_client: Optional[R2Client], hash_prefix: str) -> Optional[str]:
    """Return the R2 LRC URL for a prefix, or None when R2 is unavailable."""
    if r2_client is None:
        return None
    return r2_client.lrc_exists(hash_prefix)


def _poll_lrc_batch(
    submissions: list[tuple[str, str, str]],
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    r2_client: R2Client,
    console: Console,
    manifest_path: Optional[Path],
    existing_entries: Optional[list[dict]] = None,
    *,
    kind: str = "lyrics_generate",
    default_lrc_source: Optional[str] = None,
) -> list[dict]:
    """Poll every submitted LRC job to completion (no overall timeout).

    Unified service-first + R2-fallback reconciliation per job, mirroring
    `audio batch`'s poll loop semantics:

    - completed: confirm the LRC on R2 (service-first, R2 fallback), then
      ``update_recording_lrc(..., visibility_status='review', ...)`` — the
      explicit ``'review'`` kwarg also demotes an existing ``'published'``
      visibility; the method's NULL default is now ``'review'`` as well.
    - failed/cancelled: ``lrc_status='failed'``.
    - 404 (job lost): fall back to R2; if not there, mark failed.

    Manifest entries are updated in place and re-flushed after each settle.
    ``kind`` is stamped into every flush so a rewritten manifest keeps its
    owner command; ``default_lrc_source`` fills in ``lrc_source`` when the
    service reports none (forced alignment does not).

    Returns the manifest entries list.
    """
    entries = (
        existing_entries
        if existing_entries is not None
        else [
            {
                "song_id": song_id,
                "content_hash": content_hash,
                "job_id": job_id,
                "outcome": "pending",
            }
            for song_id, content_hash, job_id in submissions
        ]
    )
    by_job = {entry["job_id"]: entry for entry in entries if entry.get("outcome") == "pending"}
    if not by_job:
        return entries

    if manifest_path and manifest_path.exists():
        try:
            started_at = json.loads(manifest_path.read_text())["started_at"]
        except (OSError, json.JSONDecodeError, KeyError):
            started_at = datetime.now(timezone.utc).isoformat()
    else:
        started_at = datetime.now(timezone.utc).isoformat()
    batch_id = manifest_path.name.replace("_manifest.json", "") if manifest_path else None
    # The resume hint must name the command that owns this manifest kind:
    # `lyrics generate --resume` rejects a `lyrics_align` manifest.
    resume_command = (
        "sow-admin lyrics align --resume"
        if kind == "lyrics_align"
        else "sow-admin lyrics generate --resume"
    )

    def _flush() -> None:
        if manifest_path:
            _write_lyrics_manifest(
                batch_id, manifest_path.parent, started_at, entries, kind=kind
            )

    poll_interval = 30.0
    try:
        while by_job:
            for job_id in list(by_job.keys()):
                entry = by_job[job_id]
                song_id = entry["song_id"]
                content_hash = entry["content_hash"]
                hash_prefix = get_hash_prefix(content_hash)
                try:
                    job = analysis_client.get_job(job_id)
                except AnalysisServiceError as e:
                    if e.status_code == 404:
                        # Job lost: R2 fallback.
                        lrc_url = _r2_lrc_lookup(r2_client, hash_prefix)
                        if lrc_url:
                            db_client.update_recording_lrc(
                                hash_prefix=hash_prefix,
                                r2_lrc_url=lrc_url,
                                visibility_status="review",
                                lrc_source=entry.get("lrc_source") or "r2_preexisting",
                            )
                            log_change(
                                "lrc_save",
                                song_id=song_id,
                                hash_prefix=hash_prefix,
                                via="batch_poll",
                                r2_url=lrc_url,
                            )
                            entry["outcome"] = "completed"
                            entry["lrc_url"] = lrc_url
                            console.print(
                                f"  [green]✓[/green] {song_id}: LRC found on R2 (job lost)"
                            )
                        else:
                            db_client.update_recording_status(
                                hash_prefix=hash_prefix, lrc_status="failed"
                            )
                            entry["outcome"] = "failed"
                            entry["error"] = "Job lost (404) and not found on R2"
                            console.print(
                                f"  [red]✗[/red] {song_id}: job lost (404), not on R2 — marked failed"
                            )
                        del by_job[job_id]
                        _flush()
                    else:
                        console.print(f"  [yellow]→ Error polling {song_id}: {e}[/yellow]")
                    continue
                except Exception as e:
                    console.print(f"  [yellow]→ Error polling {song_id}: {e}[/yellow]")
                    continue

                if job.status == "completed":
                    # Service-first: use the reported lrc_url; fall back to R2.
                    lrc_url = job.result.lrc_url if job.result else None
                    result_lrc_source = job.result.lrc_source if job.result else None
                    lrc_source = result_lrc_source or default_lrc_source
                    if not lrc_url:
                        lrc_url = _r2_lrc_lookup(r2_client, hash_prefix)
                    if lrc_url:
                        db_client.update_recording_lrc(
                            hash_prefix=hash_prefix,
                            r2_lrc_url=lrc_url,
                            visibility_status="review",
                            lrc_source=lrc_source,
                        )
                        log_change(
                            "lrc_save",
                            song_id=song_id,
                            hash_prefix=hash_prefix,
                            via="batch_poll",
                            r2_url=lrc_url,
                        )
                        entry["outcome"] = "completed"
                        entry["lrc_url"] = lrc_url
                        entry["lrc_source"] = lrc_source
                        console.print(f"  [green]✓[/green] {song_id}: LRC completed")
                        trace_url = (
                            getattr(job.result, "trace_url", None) if job.result else None
                        )
                        if trace_url:
                            console.print(f"    LLM trace report: {trace_url}")
                    else:
                        # Completed but object not visible yet; retry next cycle.
                        console.print(
                            f"  [yellow]→ {song_id}: job completed but LRC not on R2 yet, "
                            f"retrying...[/yellow]"
                        )
                        continue
                elif job.status in ("failed", "cancelled"):
                    db_client.update_recording_status(hash_prefix=hash_prefix, lrc_status="failed")
                    entry["outcome"] = "failed"
                    entry["error"] = job.error_message or (
                        "Job cancelled" if job.status == "cancelled" else "Unknown error"
                    )
                    console.print(f"  [red]✗[/red] {song_id}: LRC failed: {entry['error']}")
                else:
                    # queued/processing
                    continue

                del by_job[job_id]
                _flush()

            if by_job:
                time.sleep(poll_interval)
    except KeyboardInterrupt:
        # Jobs already submitted stay valid server-side (WAITING-status
        # recovery); flush the manifest so --resume can finish the run.
        console.print()
        console.print("[yellow]Batch interrupted. Reconciling in-progress jobs...[/yellow]")
        for job_id, entry in by_job.items():
            hash_prefix = get_hash_prefix(entry["content_hash"])
            lrc_url = _r2_lrc_lookup(r2_client, hash_prefix)
            if lrc_url:
                db_client.update_recording_lrc(
                    hash_prefix=hash_prefix,
                    r2_lrc_url=lrc_url,
                    visibility_status="review",
                    lrc_source=entry.get("lrc_source") or "r2_preexisting",
                )
                log_change(
                    "lrc_save",
                    song_id=entry["song_id"],
                    hash_prefix=hash_prefix,
                    via="batch_poll",
                    r2_url=lrc_url,
                )
                entry["outcome"] = "completed"
                entry["lrc_url"] = lrc_url
                console.print(f"  [green]✓[/green] {entry['song_id']}: LRC found on R2 (completed)")
            else:
                entry["outcome"] = "pending"
                console.print(
                    f"  [cyan]→ {entry['song_id']}: job continues server-side; "
                    f"resume with: {resume_command} {manifest_path}[/cyan]"
                    if manifest_path
                    else f"  [cyan]→ {entry['song_id']}: job continues server-side[/cyan]"
                )
        _flush()
        console.print()
        console.print(
            "[dim]Tip: unfinished jobs stay valid server-side; resume with "
            f"'{resume_command} <manifest>' to finish the run.[/dim]"
        )
    return entries


def _print_run_report(
    submissions: list[tuple[str, str, str]],
    entries: list[dict],
    skipped: list[dict],
    db_client: DatabaseClient,
    console: Console,
) -> None:
    """Print the end-of-run report.

    Per-recording outcomes (completed / failed / skipped-guard /
    skipped-songless / pending), failures with job ids, and the exact
    `lyrics feedback resolve` commands for completed recordings. Nothing is
    auto-resolved (ADR-0007).
    """
    completed: list[dict] = []
    failed: list[dict] = []
    pending: list[dict] = []
    by_song = {entry["song_id"]: entry for entry in entries}
    for song_id, _, _ in submissions:
        entry = by_song.get(song_id)
        if entry is None:
            continue
        if entry["outcome"] == "completed":
            completed.append(entry)
        elif entry["outcome"] == "failed":
            failed.append(entry)
        else:
            pending.append(entry)

    skipped_guard = [
        s for s in skipped if s.get("reason") not in ("skipped-songless", "skipped-published")
    ]
    skipped_songless = [s for s in skipped if s.get("reason") == "skipped-songless"]
    skipped_published = [s for s in skipped if s.get("reason") == "skipped-published"]

    console.print()
    console.print("[cyan]Run Report[/cyan]")
    console.print(f"  Submitted: {len(submissions)}")
    console.print(f"  Completed: {len(completed)}")
    console.print(f"  Failed: {len(failed)}")
    console.print(f"  Pending (interrupted): {len(pending)}")
    console.print(f"  Skipped (guard): {len(skipped_guard)}")
    console.print(f"  Skipped (published): {len(skipped_published)}")
    console.print(f"  Skipped (songless): {len(skipped_songless)}")

    for entry in failed:
        console.print(
            f"  [red]✗ {entry['song_id']} (job {entry['job_id']}): {entry.get('error', 'Unknown error')}[/red]"
        )
    for entry in pending:
        console.print(
            f"  [yellow]… {entry['song_id']} (job {entry['job_id']}): still running — "
            f"resume with --resume to finish the run[/yellow]"
        )
    for skip in skipped_guard:
        console.print(
            f"  [yellow]→ skipped-guard {skip['song_id']}: target "
            f"{skip['target_hash'] or 'none'} vs feedback "
            f"{', '.join(skip['feedback_hashes'])}[/yellow]"
        )
    for skip in skipped_songless:
        console.print(
            f"  [yellow]→ skipped-songless {skip['song_id']}: no recording "
            f"found for this song id[/yellow]"
        )
    for skip in skipped_published:
        console.print(
            f"  [yellow]→ skipped-published {skip['song_id']}: demote with "
            f"'sow-admin audio set-visibility {skip['song_id']} --status review'[/yellow]"
        )

    if completed:
        console.print()
        console.print(
            "[cyan]After reviewing each regenerated recording, resolve its "
            "feedback explicitly (ADR-0007):[/cyan]"
        )
        for entry in completed:
            console.print(f"  sow-admin lyrics feedback resolve {entry['song_id']}")
    console.print()


def _confirm_llm_trace_dump(count: int, console: Console) -> bool:
    """Confirm the trace dump on an interactive terminal; auto-accept on a pipe.

    read_song_ids_from_stdin() drains the pipe, so typer.confirm would hit EOF
    and abort the whole batch in the documented piped flow — only ask when
    stdin is a TTY.
    """
    if not sys.stdin.isatty():
        console.print(
            f"[cyan]--dump-llm-trace: dumping LLM trace for all {count} song(s) "
            f"(stdin is piped; skipping confirmation).[/cyan]"
        )
        return True
    return typer.confirm(f"Dump LLM trace for all {count} song(s)?", default=False)


def _run_stdin_batch(
    song_ids: list[str],
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    config: AdminConfig,
    force: bool,
    whisper_model: str,
    language: str,
    no_vocals: bool,
    generator: str,
    no_cache: bool,
    wait: bool,
    dry_run: bool,
    llm_model: str = "",
    dump_llm_trace: bool = False,
) -> None:
    """Batch flow for all stdin input: guard → submit → (poll) → report."""
    # Pre-flight feedback guard (DB-lookup, not pipe-format).
    provider = ConnectionProvider(config.get_connection_url())
    try:
        accepted, skipped_guard = _preflight_guard(song_ids, db_client, provider, console)
    finally:
        provider.close()

    if not accepted:
        console.print("[yellow]Nothing to submit after the feedback guard.[/yellow]")
        _print_run_report([], [], skipped_guard, db_client, console)
        return

    if dry_run:
        console.print("[cyan]Dry run — would submit:[/cyan]")
        for song_id in accepted:
            console.print(f"  {song_id}")
        skipped_guard_only = [s for s in skipped_guard if s.get("reason") != "skipped-published"]
        skipped_published = [s for s in skipped_guard if s.get("reason") == "skipped-published"]
        if skipped_guard_only:
            console.print(f"  Skipped (guard): {len(skipped_guard_only)}")
        if skipped_published:
            console.print(f"  Skipped (published): {len(skipped_published)}")
        return

    if dump_llm_trace and not _confirm_llm_trace_dump(len(accepted), console=console):
        dump_llm_trace = False

    submissions = submit_lrc_batch(
        song_ids=accepted,
        db_client=db_client,
        analysis_client=analysis_client,
        force=force,
        whisper_model=whisper_model,
        language=language,
        no_vocals=no_vocals,
        generator=generator,
        no_cache=no_cache,
        console=console,
        llm_model=llm_model,
        dump_llm_trace=dump_llm_trace,
    )

    if not wait:
        _print_run_report(submissions, [], skipped_guard, db_client, console)
        return

    if not submissions:
        # Nothing actually submitted (every song errored at submit) — no
        # manifest, just the report.
        _print_run_report([], [], skipped_guard, db_client, console)
        return

    # Wait mode: manifest + poll loop.
    batch_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S") + "_lyrics"
    started_at = datetime.now(timezone.utc).isoformat()
    manifest_path = _write_lyrics_manifest(
        batch_id,
        _get_lyrics_manifest_dir(),
        started_at,
        [
            {
                "song_id": song_id,
                "content_hash": content_hash,
                "job_id": job_id,
                "outcome": "pending",
            }
            for song_id, content_hash, job_id in submissions
        ],
    )

    try:
        r2_client = R2Client(config.r2_bucket, config.r2_endpoint_url, config.r2_region)
    except ValueError as e:
        console.print(f"[yellow]R2 not configured ({e}); skipping R2 fallback checks.[/yellow]")
        r2_client = None  # type: ignore[assignment]

    entries = _poll_lrc_batch(
        submissions,
        db_client,
        analysis_client,
        r2_client,
        console,
        manifest_path,
    )
    _print_run_report(submissions, entries, skipped_guard, db_client, console)


def _resume_batch(
    manifest_file: Path,
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    config: AdminConfig,
    console: Console,
) -> None:
    """Re-poll an interrupted run from its manifest without resubmitting."""
    manifest = _load_lyrics_manifest(manifest_file)
    if not manifest or manifest.get("kind") != "lyrics_generate":
        console.print(f"[red]Invalid or missing lyrics manifest: {manifest_file}[/red]")
        raise typer.Exit(1)
    entries = manifest.get("jobs", [])
    pending = [e for e in entries if e.get("outcome") == "pending"]
    if not pending:
        console.print("[green]Nothing pending in the manifest; run already complete.[/green]")
        return
    console.print(f"[cyan]Resuming {len(pending)} pending job(s) from {manifest_file}[/cyan]")

    try:
        r2_client = R2Client(config.r2_bucket, config.r2_endpoint_url, config.r2_region)
    except ValueError as e:
        console.print(f"[yellow]R2 not configured ({e}); skipping R2 fallback checks.[/yellow]")
        r2_client = None  # type: ignore[assignment]

    submissions = [(entry["song_id"], entry["content_hash"], entry["job_id"]) for entry in pending]
    updated = _poll_lrc_batch(
        submissions,
        db_client,
        analysis_client,
        r2_client,
        console,
        manifest_file,
        existing_entries=entries,
    )
    _print_run_report(submissions, updated, [], db_client, console)


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
    or cannot be parsed. See ``resolve_alignment_lyrics_text``.
    """
    text, source = resolve_alignment_lyrics_text(song, recording, r2_client)
    if source == "catalog" and r2_client and recording.r2_lrc_url:
        console.print(
            f"[yellow]Warning: Could not read existing LRC for {recording.hash_prefix}, "
            f"using nominal lyrics[/yellow]"
        )
    return text or ""


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
    ensure_reprocessable(recording, song_id=song_id, action="re-align LRC", console=console)

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

    if (
        recording.duration_seconds
        and recording.duration_seconds > FORCED_ALIGN_MAX_DURATION_SECONDS
    ):
        console.print(
            f"[red]Recording {recording.hash_prefix} is too long "
            f"({recording.duration_seconds:.0f}s > "
            f"{FORCED_ALIGN_MAX_DURATION_SECONDS:.0f}s limit).[/red]"
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
    log_change(
        "lrc_submit",
        song_id=song_id,
        hash_prefix=recording.hash_prefix,
        via="align_single",
        job_id=job_id,
        force=force,
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
                    timeout=alignment_wait_timeout(recording.duration_seconds),
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
                lrc_source="forced_alignment",
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
) -> tuple[list[tuple[str, str, str]], int, int]:
    """Submit forced alignment for multiple recordings.

    Returns ``(submissions, skipped, errors)`` where each submission is
    ``(song_id, content_hash, job_id)``.
    """
    submissions: list[tuple[str, str, str]] = []
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

        if is_published(recording):
            console.print(
                "  [yellow]Published recording — skipped; demote with "
                f"'sow-admin audio set-visibility {song_id} --status review'[/yellow]"
            )
            skipped += 1
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

        if (
            recording.duration_seconds
            and recording.duration_seconds > FORCED_ALIGN_MAX_DURATION_SECONDS
        ):
            console.print("  [yellow]Too long (>15 min, skipped)[/yellow]")
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
            log_change(
                "lrc_submit",
                song_id=song_id,
                hash_prefix=recording.hash_prefix,
                via="align_batch",
                job_id=job.job_id,
                force=force,
            )

            console.print(f"  [green]Submitted (job: {job.job_id})[/green]")
            submissions.append((song_id, recording.content_hash, job.job_id))
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
    return submissions, skipped, errors


def _report_alignment_outcomes(
    submissions: list[tuple[str, str, str]],
    entries: list[dict],
    manifest_path: Optional[Path],
    console: Console,
) -> None:
    """Print the end-of-run report for a forced-alignment batch."""
    by_song = {entry["song_id"]: entry for entry in entries}
    completed = [
        by_song[song_id]
        for song_id, _, _ in submissions
        if by_song.get(song_id, {}).get("outcome") == "completed"
    ]
    failed = [
        by_song[song_id]
        for song_id, _, _ in submissions
        if by_song.get(song_id, {}).get("outcome") == "failed"
    ]
    pending = [
        by_song[song_id]
        for song_id, _, _ in submissions
        if by_song.get(song_id, {}).get("outcome") == "pending"
    ]

    console.print()
    console.print("[cyan]Run Report[/cyan]")
    console.print(f"  Completed: {len(completed)}")
    console.print(f"  Failed: {len(failed)}")
    console.print(f"  Pending (interrupted): {len(pending)}")

    for entry in failed:
        console.print(
            f"  [red]✗ {entry['song_id']} (job {entry['job_id']}): "
            f"{entry.get('error', 'Unknown error')}[/red]"
        )
    for entry in pending:
        console.print(
            f"  [yellow]… {entry['song_id']} (job {entry['job_id']}): still running[/yellow]"
        )
        if manifest_path:
            console.print(
                f"  [yellow]— resume with: sow-admin lyrics align "
                f"--resume {manifest_path}[/yellow]"
            )
    console.print()


def _run_alignment_batch(
    *,
    song_ids: list[str],
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    r2_client: Optional[R2Client],
    language: str,
    force: bool,
    use_vocals_stem: bool,
    console: Console,
    no_wait: bool,
) -> None:
    """Batch flow for ``lyrics align --stdin``: submit → (manifest + poll) → report."""
    submissions, _skipped, _errors = _submit_forced_alignment_batch(
        song_ids=song_ids,
        db_client=db_client,
        analysis_client=analysis_client,
        language=language,
        force=force,
        use_vocals_stem=use_vocals_stem,
        console=console,
        r2_client=r2_client,
    )

    if no_wait:
        console.print(
            "[dim]Not waiting (--no-wait). Poll with 'sow-admin audio status <job_id>'.[/dim]"
        )
        return

    if not submissions:
        return

    batch_id = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%S") + "_align"
    started_at = datetime.now(timezone.utc).isoformat()
    manifest_path = _write_lyrics_manifest(
        batch_id,
        _get_lyrics_manifest_dir(),
        started_at,
        [
            {
                "song_id": song_id,
                "content_hash": content_hash,
                "job_id": job_id,
                "outcome": "pending",
            }
            for song_id, content_hash, job_id in submissions
        ],
        kind="lyrics_align",
    )

    entries = _poll_lrc_batch(
        submissions,
        db_client,
        analysis_client,
        r2_client,
        console,
        manifest_path,
        kind="lyrics_align",
        default_lrc_source="forced_alignment",
    )
    _report_alignment_outcomes(submissions, entries, manifest_path, console)


def _resume_alignment_batch(
    *,
    manifest_file: Path,
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    r2_client: Optional[R2Client],
    console: Console,
) -> None:
    """Re-poll an interrupted alignment run from its manifest without resubmitting."""
    manifest = _load_lyrics_manifest(manifest_file)
    if not manifest or manifest.get("kind") != "lyrics_align":
        console.print(f"[red]Invalid or missing lyrics alignment manifest: {manifest_file}[/red]")
        raise typer.Exit(1)

    entries = manifest.get("jobs", [])
    pending = [e for e in entries if e.get("outcome") == "pending"]
    if not pending:
        console.print("[green]Nothing pending in the manifest; run already complete.[/green]")
        return

    console.print(f"[cyan]Resuming {len(pending)} pending job(s) from {manifest_file}[/cyan]")

    submissions = [(entry["song_id"], entry["content_hash"], entry["job_id"]) for entry in pending]
    updated = _poll_lrc_batch(
        submissions,
        db_client,
        analysis_client,
        r2_client,
        console,
        manifest_file,
        existing_entries=entries,
        kind="lyrics_align",
        default_lrc_source="forced_alignment",
    )
    _report_alignment_outcomes(submissions, updated, manifest_file, console)


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
    no_wait: bool = typer.Option(
        False, "--no-wait", help="Submit without waiting for job completion"
    ),
    resume: Optional[Path] = typer.Option(
        None,
        "--resume",
        help="Resume an interrupted batch run from its manifest (re-polls without resubmitting)",
    ),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Submit a recording for forced LRC alignment using Qwen3ForcedAligner.

    Uses the Qwen3ForcedAligner model to align lyrics to audio timestamps.
    Best for songs with known lyrics that need precise timing.

    For batch processing, pipe song IDs via stdin:
        sow-admin audio list --lrc incomplete --format ids | sow-admin lyrics align --stdin

    By default the command waits for job completion. Single-song mode polls
    with live progress (20-minute timeout); --stdin batch mode writes a run
    manifest and polls every job (Ctrl+C-safe, resumable with --resume). Use
    --no-wait to submit and exit immediately.
    """
    if not song_id and not stdin and not resume:
        console.print(
            "[red]Error: Either provide a song_id argument, use --stdin, or use --resume[/red]"
        )
        raise typer.Exit(1)
    if song_id and stdin:
        console.print("[red]Error: Cannot use both song_id argument and --stdin flag[/red]")
        raise typer.Exit(1)
    if song_id and resume:
        console.print("[red]Error: Cannot use both song_id argument and --resume[/red]")
        raise typer.Exit(1)
    if stdin and resume:
        console.print("[red]Error: Cannot use both --stdin and --resume[/red]")
        raise typer.Exit(1)
    if no_wait and resume:
        console.print("[red]Error: --no-wait is not valid with --resume[/red]")
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

    if resume:
        _resume_alignment_batch(
            manifest_file=resume,
            db_client=db_client,
            analysis_client=analysis_client,
            r2_client=r2_client,
            console=console,
        )
        return

    if stdin:
        song_ids = read_song_ids_from_stdin()
        if not song_ids:
            console.print("[yellow]No song IDs provided via stdin[/yellow]")
            raise typer.Exit(0)
        _run_alignment_batch(
            song_ids=song_ids,
            db_client=db_client,
            analysis_client=analysis_client,
            r2_client=r2_client,
            language=language,
            force=force,
            use_vocals_stem=use_vocals_stem,
            console=console,
            no_wait=no_wait,
        )
        return

    _submit_forced_alignment_single(
        song_id=song_id,
        db_client=db_client,
        analysis_client=analysis_client,
        language=language,
        force=force,
        use_vocals_stem=use_vocals_stem,
        wait=not no_wait,
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
    ensure_reprocessable(recording, song_id=song_id, action="upload LRC", console=console)

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
    if not prompt_typed_yes("Upload this LRC file?"):
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
        from stream_of_worship.admin.services.r2 import BackupFailedError, StaleObjectError

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
        lrc_source="manual_upload",
    )
    log_change(
        "lrc_save",
        song_id=song_id,
        hash_prefix=recording.hash_prefix,
        via="manual_upload",
        r2_url=r2_url,
        bytes=len(content.encode("utf-8")),
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


@app.command("upload-structured")
def lyrics_upload_structured(
    song_id: str = typer.Argument(..., help="Song ID to store structured lyrics for"),
    lyrics_file: Path = typer.Argument(
        ..., help="Path to section-tagged lyrics text file", exists=True
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing structured lyrics"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Parse a section-tagged lyrics text file and store it on the recording.

    Does NOT upload to R2 — structured lyrics live in the recordings table
    (structured_lyrics_raw + structured_lyrics columns), unlike
    ``lyrics upload`` which uploads a timed LRC file to R2.
    """
    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)

    recording = db_client.get_recording_by_song_id(song_id)
    if not recording:
        console.print(
            f"[red]No recording found for song: {song_id}. "
            f"Run 'sow-admin audio download {song_id}' first.[/red]"
        )
        raise typer.Exit(1)
    ensure_reprocessable(
        recording, song_id=song_id, action="replace structured lyrics", console=console
    )

    song = db_client.get_song(song_id)
    song_title = song.title if song else "Unknown"

    try:
        content = lyrics_file.read_text(encoding="utf-8")
    except Exception as e:
        console.print(f"[red]Error reading lyrics file: {e}[/red]")
        raise typer.Exit(1)

    parsed = parse_structured_lyrics(content)
    if parsed is None or not parsed.get("sections"):
        console.print("[red]No section tags (e.g. [Verse], [Chorus]) found in file[/red]")
        raise typer.Exit(1)

    overwriting = bool(recording.structured_lyrics)
    if overwriting and not force:
        console.print(
            f"[red]Recording {recording.hash_prefix} already has structured lyrics. "
            f"Use --force to overwrite.[/red]"
        )
        raise typer.Exit(1)

    sections = parsed["sections"]
    formatted = format_structured_lyrics_canonical(parsed)
    preview_lines = [
        f"[cyan]Song ID:[/cyan]     {song_id}",
        f"[cyan]Song Title:[/cyan]  {song_title}",
        f"[cyan]Hash Prefix:[/cyan] {recording.hash_prefix}",
        f"[cyan]Lyrics File:[/cyan] {lyrics_file}",
        f"[cyan]Sections:[/cyan]    {len(sections)}",
        "",
    ]
    preview_lines.extend(
        f"  [{s.get('raw_label') or s.get('label', '')}] {len(s.get('lines', []))} line(s)"
        for s in sections
    )
    if formatted != content:
        preview_lines.append(
            "[yellow]Raw text will be reformatted to canonical style (lowercase labels, blank line between sections)[/yellow]"
        )
    if overwriting:
        preview_lines.append("")
        preview_lines.append("[yellow]Existing structured lyrics will be overwritten[/yellow]")

    console.print(
        Panel.fit(
            "\n".join(preview_lines),
            title="Structured Lyrics Upload Preview",
            border_style="cyan",
        )
    )

    if not prompt_typed_yes("Store these structured lyrics?"):
        console.print("[yellow]Upload cancelled.[/yellow]")
        raise typer.Exit(0)

    db_client.update_recording_structured_lyrics(
        hash_prefix=recording.hash_prefix,
        structured_lyrics_raw=formatted,
        structured_lyrics=json.dumps(parsed, ensure_ascii=False),
    )
    log_change(
        "structured_lyrics_save",
        song_id=song_id,
        hash_prefix=recording.hash_prefix,
        via="upload_structured",
        sections=len(sections),
        overwritten=overwriting,
    )

    console.print()
    console.print(
        Panel.fit(
            f"[green]Structured lyrics stored![/green]\n\n"
            f"[cyan]Song:[/cyan] {song_title}\n"
            f"[cyan]Sections:[/cyan] {len(sections)}",
            title="Upload Complete",
            border_style="green",
        )
    )

    # Advisory hints (read-only checks; never abort the success report)
    if recording.lrc_status != "completed":
        console.print(
            f"[yellow]No completed LRC yet — run 'sow-admin lyrics generate {song_id}' "
            f"to generate one.[/yellow]"
        )
    try:
        components = db_client.get_song_components(song_id)
        if not components:
            console.print(
                f"[yellow]No song components yet — run 'sow-admin audio components {song_id}' "
                f"to generate them.[/yellow]"
            )
    except Exception as e:
        console.print(f"[dim]Skipped components check: {e}[/dim]")


@app.command("view-structured")
def lyrics_view_structured(
    song_id: str = typer.Argument(..., help="Song ID to view structured lyrics for"),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        "-o",
        help="Write raw section-tagged text to this file instead of printing",
    ),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """View or export the structured lyrics stored on a recording."""
    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)

    recording = db_client.get_recording_by_song_id(song_id)
    if not recording:
        console.print(
            f"[red]No recording found for song: {song_id}. "
            f"Run 'sow-admin audio download {song_id}' first.[/red]"
        )
        raise typer.Exit(1)

    if recording.structured_lyrics:
        try:
            text = format_structured_lyrics_canonical(json.loads(recording.structured_lyrics))
        except (json.JSONDecodeError, TypeError):
            text = None
        if text is None:
            text = recording.structured_lyrics_raw or ""
        else:
            console.print(
                "[dim]Note: raw section-tagged text unavailable — re-rendered from stored "
                "structured lyrics JSON.[/dim]"
            )
    elif recording.structured_lyrics_raw:
        text = recording.structured_lyrics_raw
    else:
        console.print(
            "[yellow]No structured lyrics stored for this recording. "
            "Use 'lyrics upload-structured' or 'audio download --backfill-lyrics'.[/yellow]"
        )
        raise typer.Exit(0)

    if output:
        output.write_text(text, encoding="utf-8")
        console.print(f"[green]Wrote structured lyrics to {output}[/green]")
    else:
        # markup=False: canonical lowercase labels like [verse] would otherwise
        # be consumed as rich markup and vanish from the display.
        console.print(text, markup=False)


@app.command("edit")
def lyrics_edit(
    song_id: Optional[str] = typer.Argument(None, help="Song ID to edit LRC for"),
    review_next_song: bool = typer.Option(
        False,
        "--review-next-song",
        help="Pick the next review-visibility song from the ranked queue "
        "(most recent open poor feedback first) and loop after upload",
    ),
    review_fresh: bool = typer.Option(
        False,
        "--review-fresh",
        help="Sweep never-reviewed (no feedback of any rating) review-visibility "
        "songs, ranked by songset/favorites/album",
    ),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Interactively edit LRC timestamps for a song recording.

    Downloads/caches the song recording and transcribed LRC, then launches
    a Textual TUI editor for live timestamp alignment, text editing, and
    upload to R2.

    With --review-next-song, picks the next review-visibility song from an
    automatically ranked queue (most recent open poor lyrics feedback first,
    then songset appearances, favorites, newest album), shows why it was
    picked, and after a successful upload offers to resolve the open poor
    ratings, change the recording's visibility status
    (published/review/hold), and continue to the next review song in one
    sitting.

    With --review-fresh, sweeps songs nobody has ever reviewed (no
    lyrics_feedback row of any rating on the picked recording), ranked by
    songset appearances, favorites, and newest album. The admin's own
    Happy/Sad feedback can be recorded in the editor via the `f` hotkey
    (rows attributed to admin.feedback_email).

    Press `G` in the editor to force-regenerate the LRC through the
    analysis service and refresh the editor from the new R2 LRC without
    leaving the editor. `G` opens a generator picker — `a` auto
    (YouTube → Qwen3 ASR → Whisper fallback, default), `y` youtube,
    `q` qwen3, `w` whisper — mirroring `lyrics generate --generator`.

    Press `L` in the editor to reload the latest LRC from R2 without
    regenerating (e.g. after running `lyrics generate` in another
    terminal). Unchanged R2 content is a no-op; unsaved edits made
    while the fetch runs are kept as a local draft.
    """
    if not song_id and not review_next_song and not review_fresh:
        console.print(
            "[red]Error: provide a song_id argument or use --review-next-song/--review-fresh[/red]"
        )
        raise typer.Exit(1)
    if song_id and review_next_song:
        console.print("[red]Error: cannot combine song_id argument with --review-next-song[/red]")
        raise typer.Exit(1)
    if song_id and review_fresh:
        console.print("[red]Error: cannot combine song_id argument with --review-fresh[/red]")
        raise typer.Exit(1)
    if review_next_song and review_fresh:
        console.print("[red]Error: cannot combine --review-next-song with --review-fresh[/red]")
        raise typer.Exit(1)

    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    db_client = get_db_client(config)
    cache_dir = get_cache_dir()

    try:
        r2_client = R2Client(
            bucket=config.r2_bucket,
            endpoint_url=config.r2_endpoint_url,
            region=config.r2_region,
        )
    except ValueError as e:
        console.print(f"[red]R2 configuration error: {e}[/red]")
        raise typer.Exit(1)

    # Analysis service client for the in-editor force-regenerate hotkey (G).
    # A missing API key only disables that hotkey; the editor still opens.
    try:
        analysis_client: Optional[AnalysisClient] = AnalysisClient(config.analysis_url)
    except ValueError as e:
        analysis_client = None
        console.print(f"[yellow]Regenerate hotkey (G) disabled: {e}[/yellow]")

    # Reviewer identity for the in-editor feedback hotkey (resolved once,
    # before any session starts; None disables the hotkey with a warning).
    feedback_user_id = _resolve_feedback_user_id(config)

    if review_next_song:
        _run_review_session(
            db_client, cache_dir, r2_client, feedback_user_id, analysis_client=analysis_client
        )
        return

    if review_fresh:
        _run_fresh_review_session(
            db_client, cache_dir, r2_client, feedback_user_id, analysis_client=analysis_client
        )
        return

    # Single-song mode: resolve the recording the legacy way and run one
    # plain session (banner without pause, same post-upload offers).
    recording = db_client.get_recording_by_song_id(song_id)
    if not recording:
        console.print(
            f"[red]No recording found for song: {song_id}. "
            f"Run 'sow-admin audio download {song_id}' first.[/red]"
        )
        raise typer.Exit(1)
    ensure_reprocessable(recording, song_id=song_id, action="edit LRC", console=console)

    song = db_client.get_song(song_id)

    # Story 9: open poor feedback is shown before every lyrics editing
    # session, so the admin sees what users complained about while editing.
    _print_feedback_banner(db_client, recording.content_hash)

    outcome = _run_editor_session(
        db_client,
        cache_dir,
        r2_client,
        song,
        recording,
        feedback_user_id,
        analysis_client=analysis_client,
    )
    if outcome.no_audio:
        # Audio unavailable — clear error (spec story 12).
        console.print(
            f"[red]No audio found for {song_id} in object storage. "
            f"Audio is required for timestamp alignment. Cannot open editor.[/red]"
        )
        raise typer.Exit(1)

    if outcome.quit_clean:
        _offer_visibility_on_clean_quit(db_client, recording)

    if outcome.upload_attempted and outcome.upload_result is not None:
        _run_single_mode_offers(db_client, outcome.upload_result, recording)


def plan_post_upload_offers(
    upload_ok: bool,
    open_sad_count: int,
    loop_mode: bool,
) -> list[str]:
    """Compute the ordered post-upload offer plan (pure; issue #260).

    Inputs are the upload outcome, the open-feedback state, and whether we
    are in a review loop session. Output is the ordered list of prompts:

    - ``clear``: "Clear poor ratings?" — resolve open sad rows (ADR 0007).
    - ``visibility``: numbered status menu offering every valid status
      except the recording's current one (published/review/hold).
    - ``skip-if-visibility-declined``: "Skip this song in the next pick?" —
      loop mode only, offered when visibility was left unchanged and
      ratings were not cleared.
    - ``continue``: "Continue to next review song?" — loop mode only.

    Local-draft saves (upload_ok=False) produce no offers at all.
    """
    if not upload_ok:
        return []
    plan: list[str] = []
    if open_sad_count > 0:
        plan.append("clear")
    # Every valid status has at least one alternative, so the menu is
    # always worth offering. An upload turns NULL visibility into
    # 'review'; a published recording is not demoted.
    plan.append("visibility")
    if loop_mode:
        plan.append("skip-if-visibility-declined")
        plan.append("continue")
    return plan


def _resolve_feedback_user_id(config: AdminConfig) -> Optional[int]:
    """Resolve admin.feedback_email to a user id for feedback attribution.

    Returns None (with a warning) when the key is unset or the email has
    no Better Auth user row; the editor's feedback hotkey stays disabled
    in that case (existing users' feedback still displays).
    """
    email = config.admin_feedback_email
    if not email:
        console.print(
            "[yellow]Feedback disabled: set "
            "'sow-admin config set admin.feedback_email <email>'[/yellow]"
        )
        return None
    # with-block: close the transient connection instead of leaking it into
    # the long-lived editor session (matches the provider's __exit__ contract).
    with UserClient(ConnectionProvider(config.get_connection_url())) as user_client:
        user = user_client.get_user_by_email(email)
    if user is None:
        console.print(f"[yellow]Feedback disabled: unknown user: {email}[/yellow]")
        return None
    return user.id


def _run_review_session(
    db_client,
    cache_dir,
    r2_client,
    feedback_user_id: Optional[int],
    analysis_client: Optional[AnalysisClient] = None,
) -> None:
    """Run the --review-next-song loop: pick, edit, post-upload offers, repeat.

    The skip set lives in process memory only — a fresh invocation starts
    clean (issue #260 user story 22).
    """
    skipped_song_ids: set[str] = set()

    while True:
        queue = db_client.get_review_queue()
        candidates = [c for c in queue if c.song_id not in skipped_song_ids]
        if not candidates:
            console.print(
                f"[yellow]No review-visibility songs available "
                f"({len(skipped_song_ids)} skipped this session)[/yellow]"
            )
            return

        candidate = candidates[0]
        recording = db_client.get_recording_by_hash(candidate.hash_prefix)
        song = db_client.get_song(candidate.song_id)
        if recording is None or song is None:
            console.print(
                f"[red]Recording or song disappeared for {candidate.song_id} "
                f"({candidate.hash_prefix}); skipping.[/red]"
            )
            skipped_song_ids.add(candidate.song_id)
            continue

        if not _print_review_pick_reason(candidate, pause=True):
            console.print("[dim]Aborted; editor not opened.[/dim]")
            return
        _print_feedback_banner_from_summary(
            candidate.open_sad_count,
            candidate.reason_counts,
            candidate.latest_open_sad_at,
            candidate.sample_reason,
        )

        outcome = _run_editor_session(
            db_client,
            cache_dir,
            r2_client,
            song,
            recording,
            feedback_user_id,
            analysis_client=analysis_client,
        )
        if outcome.no_audio:
            # No audio in object storage: skip message + move on.
            console.print(
                f"[yellow]Skipping {candidate.song_id} ({candidate.song_title}): "
                f"no audio available in object storage.[/yellow]"
            )
            skipped_song_ids.add(candidate.song_id)
            continue

        if not outcome.upload_attempted:
            # Quit without saving/uploading (the editor advertises Ctrl+C
            # to quit): no offers, song stays eligible, but give the admin
            # an explicit way out instead of silently re-launching the
            # same editor.
            if outcome.quit_clean:
                _offer_visibility_on_clean_quit(db_client, recording)
            console.print(
                f"[dim]No upload for {candidate.song_id} "
                f"({candidate.song_title}); it stays in the queue.[/dim]"
            )
            if not prompt_enter_or_no("Continue to next review song?"):
                return
        else:
            upload_result = outcome.upload_result
            plan = plan_post_upload_offers(
                upload_ok=upload_result is not None and upload_result.success,
                open_sad_count=candidate.open_sad_count,
                loop_mode=True,
            )
            accepted = _run_post_upload_prompts(
                db_client,
                plan,
                candidate,
                recording,
                skipped_song_ids,
            )
            if not accepted:
                return


def _open_sad_count(db_client, content_hash: str) -> int:
    """Count open sad feedback rows for a recording."""
    return db_client.get_recording_feedback_summary(content_hash).open_sad_count


def _run_fresh_review_session(
    db_client,
    cache_dir,
    r2_client,
    feedback_user_id: Optional[int],
    analysis_client: Optional[AnalysisClient] = None,
) -> None:
    """Run the --review-fresh sweep: pick never-reviewed songs, edit, repeat.

    Structural copy of ``_run_review_session`` over the fresh queue:
    songs with any lyrics_feedback row (any rating, open or resolved) on
    the picked recording are excluded by the SQL itself, so there is no
    poor-feedback banner and the clear-ratings offer never fires.
    """
    skipped_song_ids: set[str] = set()

    while True:
        queue = db_client.get_fresh_review_queue()
        candidates = [c for c in queue if c.song_id not in skipped_song_ids]
        if not candidates:
            console.print(
                f"[yellow]No never-reviewed songs available "
                f"({len(skipped_song_ids)} skipped this session)[/yellow]"
            )
            return

        candidate = candidates[0]
        recording = db_client.get_recording_by_hash(candidate.hash_prefix)
        song = db_client.get_song(candidate.song_id)
        if recording is None or song is None:
            console.print(
                f"[red]Recording or song disappeared for {candidate.song_id} "
                f"({candidate.hash_prefix}); skipping.[/red]"
            )
            skipped_song_ids.add(candidate.song_id)
            continue

        if not _print_fresh_pick_reason(candidate, pause=True):
            console.print("[dim]Aborted; editor not opened.[/dim]")
            return

        outcome = _run_editor_session(
            db_client,
            cache_dir,
            r2_client,
            song,
            recording,
            feedback_user_id,
            analysis_client=analysis_client,
        )
        if outcome.no_audio:
            # No audio in object storage: skip message + move on.
            console.print(
                f"[yellow]Skipping {candidate.song_id} ({candidate.song_title}): "
                f"no audio available in object storage.[/yellow]"
            )
            skipped_song_ids.add(candidate.song_id)
            continue

        if not outcome.upload_attempted:
            # Quit without saving/uploading (the editor advertises Ctrl+C
            # to quit): no offers, song stays eligible, but give the admin
            # an explicit way out instead of silently re-launching the
            # same editor.
            if outcome.quit_clean:
                _offer_visibility_on_clean_quit(db_client, recording)
            console.print(
                f"[dim]No upload for {candidate.song_id} "
                f"({candidate.song_title}); it stays in the queue.[/dim]"
            )
            if not prompt_enter_or_no("Continue to next review song?"):
                return
        else:
            upload_result = outcome.upload_result
            plan = plan_post_upload_offers(
                upload_ok=upload_result is not None and upload_result.success,
                # Fresh-mode invariant: the picked recording had no feedback
                # when picked. Pass 0 — never a live count — so a sad row the
                # admin just wrote via the in-editor `f` hotkey can't trigger
                # the clear-ratings offer and resolve their own fresh entry.
                open_sad_count=0,
                loop_mode=True,
            )
            accepted = _run_post_upload_prompts(
                db_client,
                plan,
                candidate,
                recording,
                skipped_song_ids,
            )
            if not accepted:
                return


def _confirm_open_editor() -> bool:
    """Pause gate before launching the editor.

    Returns True when the caller should proceed; False when the admin
    aborted at the pause (non-empty reply, EOF, or Ctrl+C).
    """
    try:
        reply = input("Press Enter to open the editor, or type anything + Enter to abort: ")
    except (EOFError, KeyboardInterrupt):
        console.print()
        return False
    return reply.strip() == ""


def _print_review_pick_reason(candidate, pause: bool) -> bool:
    """Print why this song was picked, then optionally pause for space/enter.

    Returns True when the caller should proceed to the editor; False when the
    admin aborted at the pause (non-empty reply, EOF, or Ctrl+C).
    """
    parts: list[str] = []
    if candidate.latest_open_sad_at:
        breakdown = _reason_breakdown(candidate.reason_counts)
        sample = f', e.g. "{candidate.sample_reason}"' if candidate.sample_reason else ""
        parts.append(
            f"{candidate.open_sad_count} open poor rating(s) since "
            f"{candidate.latest_open_sad_at} ({breakdown}{sample})"
        )
    else:
        parts.append("no open poor ratings")
    parts.append(f"{candidate.songset_appearances} songset appearance(s)")
    parts.append(f"{candidate.favorite_count} favorite(s)")

    console.print()
    console.print(
        f"[bold]Next review song:[/bold] {candidate.song_title} "
        f"[dim]({candidate.song_id}, recording {candidate.hash_prefix})[/dim]"
    )
    console.print(f"[cyan]Why picked:[/cyan] {'; '.join(parts)}")

    if not pause:
        return True
    return _confirm_open_editor()


def _print_fresh_pick_reason(candidate, pause: bool) -> bool:
    """Print why this never-reviewed song was picked, then optionally pause.

    Returns True when the caller should proceed to the editor; False when the
    admin aborted at the pause (non-empty reply, EOF, or Ctrl+C).
    """
    parts: list[str] = [
        "never reviewed (no feedback on this recording)",
        f"{candidate.songset_appearances} songset appearance(s)",
        f"{candidate.favorite_count} favorite(s)",
    ]

    console.print()
    console.print(
        f"[bold]Next review song:[/bold] {candidate.song_title} "
        f"[dim]({candidate.song_id}, recording {candidate.hash_prefix})[/dim]"
    )
    console.print(f"[cyan]Why picked:[/cyan] {'; '.join(parts)}")

    if not pause:
        return True
    return _confirm_open_editor()


def _print_feedback_banner_from_summary(
    open_sad_count: int,
    reason_counts: dict[str, int],
    latest_open_sad_at: Optional[str],
    sample_reason: Optional[str],
) -> None:
    """Print the open poor-feedback banner from precomputed summary data.

    No output when there is no open poor feedback.
    """
    if open_sad_count == 0:
        return
    breakdown = _reason_breakdown(reason_counts)
    latest = latest_open_sad_at or ""
    sample = f' — e.g. "{sample_reason}"' if sample_reason else ""
    console.print(
        f"[yellow]⚠ Open poor feedback: {open_sad_count} rating(s) "
        f"({breakdown}), latest {latest}{sample}[/yellow]"
    )


def _print_feedback_banner(db_client, content_hash: str) -> None:
    """Print the open poor-feedback banner queried live from the DB."""
    summary = db_client.get_recording_feedback_summary(content_hash)
    _print_feedback_banner_from_summary(
        summary.open_sad_count,
        summary.reason_counts,
        summary.latest_open_sad_at,
        summary.sample_reason,
    )


@dataclass
class EditorSessionOutcome:
    """Result of one editor session (issue #260).

    ``no_audio``: audio unavailable in object storage — the editor never
    opened. ``upload_attempted``: the save/upload dialog opened at least
    once (True even when the user cancelled it or saved only a draft).
    ``upload_result``: outcome of the last upload attempt (None when no
    upload was attempted or the user quit before saving).
    ``quit_clean``: session ended via quit dialog with no unsaved changes
    (no upload attempted).
    """

    no_audio: bool
    upload_attempted: bool
    upload_result: Optional[UploadResult]
    quit_clean: bool = False


def _run_editor_session(
    db_client,
    cache_dir,
    r2_client,
    song,
    recording,
    feedback_user_id: Optional[int],
    analysis_client: Optional[AnalysisClient] = None,
):
    """Prepare caches + editor state and run one editor session.

    Returns an ``EditorSessionOutcome``: ``no_audio=True`` when audio is
    unavailable in object storage (single-song mode: clear error; review
    loop: skip the candidate); otherwise the app is marked
    ``upload_attempted`` when the save/upload dialog opened at least once
    and carries the resulting ``UploadResult`` (``None`` when the user
    quit without attempting a save — no post-upload offers in that case).

    ``analysis_client`` enables the editor's force-regenerate hotkey
    (``G``); ``None`` disables it.
    """
    song_title = song.title if song else "Unknown"

    console.print(f"[cyan]Downloading audio for: {song_title}[/cyan]")
    audio_cache_dir = cache_dir / recording.hash_prefix / "audio"
    audio_cache_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_cache_dir / "audio.mp3"

    try:
        if not audio_path.exists():
            try:
                r2_client.download_audio(recording.hash_prefix, audio_path)
            except ClientError as e:
                if e.response.get("Error", {}).get("Code", "") in ("404", "NoSuchKey"):
                    return EditorSessionOutcome(
                        no_audio=True, upload_attempted=False, upload_result=None
                    )
                raise
            except Exception:
                # Non-ClientError failures may still be missing-object cases
                # surfaced differently by R2 clients; verify explicitly.
                if not r2_client.audio_exists(recording.hash_prefix):
                    return EditorSessionOutcome(
                        no_audio=True, upload_attempted=False, upload_result=None
                    )
                raise
        else:
            try:
                r2_client.audio_exists(recording.hash_prefix)
            except ClientError:
                console.print(
                    "[yellow]Warning: Could not verify audio in R2. Using cached file.[/yellow]"
                )
    except Exception as e:
        console.print(f"[red]Failed to download audio: {e}[/red]")
        console.print("[red]Audio is required for timestamp alignment. Cannot open editor.[/red]")
        raise typer.Exit(1)

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
    )

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
                    song_id=song.id if song else "",
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

    from stream_of_worship.admin.logging_config import get_log_path

    console.print(f"[dim]Session log: {get_log_path()}[/dim]")

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
        feedback_user_id=feedback_user_id,
        recording_content_hash=recording.content_hash,
        visibility_status=recording.visibility_status,
        lrc_source=recording.lrc_source,
        analysis_client=analysis_client,
    )
    app.run()

    playback.stop()
    return EditorSessionOutcome(
        no_audio=False,
        upload_attempted=app.upload_attempted,
        upload_result=app.last_upload_result,
        quit_clean=app.quit_clean,
    )


def _run_post_upload_prompts(
    db_client,
    plan: list[str],
    candidate,
    recording,
    skipped_song_ids: set[str],
) -> bool:
    """Execute the ordered post-upload offer plan interactively.

    Returns True to continue the review loop, False to stop. Single-song
    mode (loop plan without ``continue``) always returns False.
    """
    visibility_declined = False
    cleared_ratings = False

    for step in plan:
        if step == "clear":
            if prompt_typed_yes("Clear poor ratings?"):
                affected = db_client.resolve_recording_sad_feedback(recording.content_hash)
                cleared_ratings = True
                console.print(
                    f"[green]Resolved {affected} poor rating(s) for "
                    f"{recording.hash_prefix} (reversible via "
                    f"'lyrics feedback unresolve').[/green]"
                )
            else:
                console.print("[dim]Ratings left open.[/dim]")
        elif step == "visibility":
            target = _prompt_visibility_change(
                recording.hash_prefix, _current_visibility(db_client, recording)
            )
            if target is None:
                visibility_declined = True
                console.print("[dim]Visibility left unchanged.[/dim]")
            else:
                db_client.update_recording_visibility(recording.hash_prefix, target)
                console.print(
                    f"[green]Recording {recording.hash_prefix} visibility set to {target}.[/green]"
                )
        elif step == "skip-if-visibility-declined":
            if (
                candidate is not None
                and visibility_declined
                and not cleared_ratings
                and prompt_typed_yes("Skip this song in the next pick?")
            ):
                skipped_song_ids.add(candidate.song_id)
                console.print(
                    f"[dim]{candidate.song_id} will be skipped for the rest of "
                    f"this session.[/dim]"
                )
        elif step == "continue":
            return prompt_enter_or_no("Continue to next review song?")

    return False


def _offer_visibility_on_clean_quit(db_client, recording) -> None:
    """Offer a visibility change when a no-edit session ends.

    Eligible only for completed-LRC recordings. Offers every valid status
    except the current one (published -> review/hold, review/NULL ->
    published/hold, hold -> published/review).
    """
    fresh = db_client.get_recording_by_hash(recording.hash_prefix)
    if fresh is None or fresh.lrc_status != "completed":
        return
    target = _prompt_visibility_change(recording.hash_prefix, fresh.visibility_status or "")
    if target is None:
        console.print("[dim]Visibility left unchanged.[/dim]")
        return
    db_client.update_recording_visibility(recording.hash_prefix, target)
    console.print(f"[green]Recording {recording.hash_prefix} visibility set to {target}.[/green]")


def _prompt_visibility_change(hash_prefix: str, current: str) -> Optional[str]:
    """Numbered menu of every valid status except the current one.

    ``current`` is "" for unset visibility. Returns the chosen status, or
    None when the admin leaves it unchanged (Enter, invalid input, EOF).
    """
    choices = visibility_alternatives(current)
    if not choices:
        return None
    index = prompt_choice_or_none(
        f"Set visibility for {hash_prefix} (current: {visibility_label(current)}):",
        [f"{visibility_badge(status)} {status}" for status in choices],
    )
    if index is None:
        return None
    return choices[index]


def _current_visibility(db_client, recording) -> str:
    """Re-read the recording's visibility status from the DB.

    ``update_recording_lrc`` COALESCEs a NULL ``visibility_status`` to
    ``'review'`` on upload, so the in-memory object held before the
    editor session may be stale by the time the offers run.
    """
    fresh = db_client.get_recording_by_hash(recording.hash_prefix)
    visibility = fresh.visibility_status if fresh else recording.visibility_status
    return visibility or ""


def _run_single_mode_offers(db_client, upload_result, recording) -> None:
    """Post-upload offers for single-song (targeted edit) sessions.

    Sequential prompts, only after a successful R2 upload — never after a
    local-draft save. Reuses the same pure plan as loop mode minus the
    loop-only steps.
    """
    if upload_result is None or not upload_result.success:
        return
    plan = plan_post_upload_offers(
        upload_ok=True,
        open_sad_count=_open_sad_count(db_client, recording.content_hash),
        loop_mode=False,
    )
    _run_post_upload_prompts(
        db_client,
        plan,
        candidate=None,
        recording=recording,
        skipped_song_ids=set(),
    )


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
        song_id=song.id if song else "",
        hash_prefix=recording.hash_prefix,
        audio_path=str(audio_path),
        audio_duration=recording.duration_seconds,
        tempo_bpm=recording.tempo_bpm,
    )
