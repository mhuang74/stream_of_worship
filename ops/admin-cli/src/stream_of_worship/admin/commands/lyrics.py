"""Lyrics curation commands for sow-admin.

Top-level ``sow-admin lyrics`` command group — future home of lyrics
curation commands. Currently holds the Lyrics Feedback triage queue
(issue #194): list open feedback grouped by Recording, and bulk
resolve/unresolve per Recording or Song.

Per ADR 0007, feedback is advisory: these commands only write
``lyrics_feedback.resolved_at`` and never touch pipeline status fields
(``recordings.lrc_status``).
"""

from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.db.connection import ConnectionProvider

console = Console()
app = typer.Typer(help="Lyrics curation operations")
feedback_app = typer.Typer(help="Lyrics feedback triage queue")
app.add_typer(feedback_app, name="feedback")

REASON_ORDER = ["missing", "timing", "wrong_text", "other"]

# Presentation-only mapping: which pipeline command addresses the complaint.
SUGGESTED_ACTIONS = {
    "missing": "generate Lyrics (audio lrc <song-id>)",
    "timing": "re-align (audio align-lrc <song-id>)",
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
    all: bool = typer.Option(
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
    having = "" if all else "HAVING COUNT(*) FILTER (WHERE f.resolved_at IS NULL) > 0"

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
            JOIN recordings r ON r.content_hash = f.recording_content_hash
            JOIN songs s ON s.id = r.song_id
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
    table.add_column("Open", justify="right")
    table.add_column("Reasons")
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
