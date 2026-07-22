"""Dry-run report to identify recordings whose visibility_status was
reverted from 'published' to 'review' by the audio-batch bug.

Cross-references R2 LRC file LastModified against DB recordings.updated_at
to flag SUSPECTED_BUG_REVERT candidates.

Usage:
    uv run --project ops/admin-cli --extra admin python ops/admin-cli/src/stream_of_worship/admin/commands/recover_visibility.py \\
      [--since 2026-07-10] [--until 2026-07-20] \\
      [--album <name>] [--min-delta-hours 1] [--max-delta-hours 1440] \\
      [--with-analysis] [--csv] [--config <path>]
"""

import csv
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional
from dataclasses import dataclass

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.services.analysis import (
    AnalysisClient,
    AnalysisServiceError,
)
from stream_of_worship.admin.services.r2 import R2Client, R2ObjectIdentity
from stream_of_worship.db.connection import ConnectionProvider

console = Console()
progress_console = Console(stderr=True)
R2_LOOKUP_WORKERS = 20
ANALYSIS_LOOKUP_WORKERS = 10


def _lookup_rlc_identity(r2_client: R2Client, hash_prefix: str) -> R2ObjectIdentity:
    """Look up R2 LRC identity, swallowing all exceptions."""
    try:
        return r2_client.get_lrc_identity(hash_prefix)
    except Exception:
        return R2ObjectIdentity(exists=False)


def _batch_lookup_r2(
    r2_client: R2Client, hash_prefixes: list[str],
) -> dict[str, R2ObjectIdentity]:
    """Concurrently look up R2 LRC identities for all hash prefixes."""
    results: dict[str, R2ObjectIdentity] = {}
    total = len(hash_prefixes)
    progress_console.print(f"[dim]R2 lookups: {total} files ({R2_LOOKUP_WORKERS} workers)[/dim]")
    with ThreadPoolExecutor(max_workers=R2_LOOKUP_WORKERS) as pool:
        futures = {
            pool.submit(_lookup_rlc_identity, r2_client, hp): hp
            for hp in hash_prefixes
        }
        completed = 0
        for future in as_completed(futures):
            hp = futures[future]
            results[hp] = future.result()
            completed += 1
            if completed % 50 == 0 or completed == total:
                progress_console.print(f"[dim]  R2 lookups: {completed}/{total}[/dim]")
    return results


def _lookup_analysis_job(
    analysis_client: AnalysisClient, job_id: str,
) -> tuple[Optional[str], Optional[str]]:
    """Look up analysis job, returning (updated_at, note) or (None, note) on error."""
    try:
        job = analysis_client.get_job(job_id)
        return (job.updated_at, None)
    except AnalysisServiceError as e:
        if getattr(e, "status_code", None) == 404:
            return (None, "job purged — relying on R2/DB timestamps only")
        return (None, f"analysis error: {e}")
    except Exception as e:
        return (None, f"unexpected error: {e}")


def _batch_lookup_analysis(
    analysis_client: AnalysisClient, job_ids: list[str],
) -> dict[str, tuple[Optional[str], Optional[str]]]:
    """Concurrently look up analysis jobs for all job IDs."""
    results: dict[str, tuple[Optional[str], Optional[str]]] = {}
    total = len(job_ids)
    progress_console.print(f"[dim]Analysis lookups: {total} jobs ({ANALYSIS_LOOKUP_WORKERS} workers)[/dim]")
    with ThreadPoolExecutor(max_workers=ANALYSIS_LOOKUP_WORKERS) as pool:
        futures = {
            pool.submit(_lookup_analysis_job, analysis_client, jid): jid
            for jid in job_ids if jid
        }
        completed = 0
        for future in as_completed(futures):
            jid = futures[future]
            results[jid] = future.result()
            completed += 1
            if completed % 50 == 0 or completed == total:
                progress_console.print(f"[dim]  Analysis lookups: {completed}/{total}[/dim]")
    return results


def _get_db_client(config: AdminConfig) -> DatabaseClient:
    """Get a database client from config."""
    provider = ConnectionProvider(config.get_connection_url())
    return DatabaseClient(provider)


def _to_iso_str(value: Any) -> Optional[str]:
    """Normalize a datetime-or-string value to an ISO-format string.

    psycopg returns `datetime` objects for TIMESTAMP columns, and boto3 returns
    `datetime` for R2 LastModified. Rich's Table and CSV writer require strings,
    so this helper guarantees a string (or None) for downstream consumers.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _parse_iso_datetime(s) -> Optional[datetime]:
    """Parse an ISO-format datetime string or datetime object to a timezone-aware UTC datetime."""
    if not s:
        return None
    if isinstance(s, datetime):
        dt = s
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _build_candidate_query(
    since: Optional[str],
    until: Optional[str],
    album: Optional[str],
) -> tuple[str, list[Any]]:
    """Build the SQL query and params for candidate recordings."""
    query = """
        SELECT r.hash_prefix, r.song_id, r.lrc_job_id, r.r2_lrc_url,
               r.visibility_status, r.lrc_status,
               r.updated_at AS db_updated_at, r.imported_at,
               r.analysis_job_id, r.key_detected_at,
               s.title AS song_title, s.album_name AS song_album
        FROM recordings r
        LEFT JOIN songs s ON r.song_id = s.id
        WHERE r.visibility_status = 'review'
          AND r.lrc_status = 'completed'
          AND r.r2_lrc_url IS NOT NULL
          AND r.deleted_at IS NULL
          AND (s.deleted_at IS NULL OR s.id IS NULL)
    """
    params: list[Any] = []

    if since:
        query += " AND r.updated_at >= %s"
        params.append(since)

    if until:
        query += " AND r.updated_at <= %s"
        params.append(until)

    if album:
        query += " AND s.album_name = %s"
        params.append(album)

    query += " ORDER BY r.updated_at DESC"

    return query, params


@dataclass
class CandidateSignals:
    db_updated_at: Optional[datetime]
    r2_lm: Optional[datetime]
    lrc_job_done: Optional[datetime]   # None if --with-analysis off OR job purged
    analyze_job_done: Optional[datetime]
    key_detected_at: Optional[datetime]
    with_analysis: bool
    bump_tolerance_s: float


def _compute_verdict(sig: CandidateSignals) -> tuple[str, str, dict[str, Any]]:
    """Compute verdict and recommendation based on new signal model.

    Returns:
        (verdict, recommendation, debug_notes)
    """
    # Informational deltas
    delta_h = 0.0
    if sig.db_updated_at and sig.r2_lm:
        delta_h = (sig.db_updated_at - sig.r2_lm).total_seconds() / 3600.0

    # OK_FRESH_LRC: abs(delta_h) <= 0.1 (kept from v1)
    if abs(delta_h) <= 0.1:
        return "OK_FRESH_LRC", "—", {"delta_h": delta_h}

    # Analyze bump detection
    analyze_bump = False
    bump_source = "none"

    if sig.analyze_job_done and sig.db_updated_at:
        if abs((sig.db_updated_at - sig.analyze_job_done).total_seconds()) <= sig.bump_tolerance_s:
            analyze_bump = True
            bump_source = "analysis_job"
    elif sig.key_detected_at and sig.db_updated_at:
        if abs((sig.db_updated_at - sig.key_detected_at).total_seconds()) <= sig.bump_tolerance_s:
            analyze_bump = True
            bump_source = "key_detected_at"

    # Manual edit check: r2_lm > lrc_job_done
    manual_edit_after_autogen = "unknown"
    if sig.with_analysis and sig.lrc_job_done and sig.r2_lm:
        if sig.r2_lm > sig.lrc_job_done:
            manual_edit_after_autogen = "yes"
        else:
            manual_edit_after_autogen = "no"

    # Verdict Matrix
    if not sig.r2_lm or not sig.db_updated_at:
        return "INCONCLUSIVE", "eyes-on", {
            "delta_h": delta_h,
            "analyze_bump": analyze_bump,
            "bump_source": bump_source,
            "manual_edit_after_autogen": manual_edit_after_autogen,
        }

    if manual_edit_after_autogen == "yes" and analyze_bump:
        return "SUSPECTED_BUG_REVERT", "set-visibility published", {
            "delta_h": delta_h,
            "analyze_bump": analyze_bump,
            "bump_source": bump_source,
            "manual_edit_after_autogen": manual_edit_after_autogen,
        }

    if manual_edit_after_autogen == "yes" and not analyze_bump:
        # This is the "smoking gun" from v1, but still a bug revert
        return "SUSPECTED_BUG_REVERT", "set-visibility published", {
            "delta_h": delta_h,
            "analyze_bump": analyze_bump,
            "bump_source": bump_source,
            "manual_edit_after_autogen": manual_edit_after_autogen,
        }

    if analyze_bump and manual_edit_after_autogen == "unknown":
        return "SUSPECTED_POST_ANALYZE", "needs --with-analysis to resolve", {
            "delta_h": delta_h,
            "analyze_bump": analyze_bump,
            "bump_source": bump_source,
            "manual_edit_after_autogen": manual_edit_after_autogen,
        }

    if analyze_bump and manual_edit_after_autogen == "no":
        return "NO_SIGNAL_POST_ANALYZE", "likely not bug-reverted", {
            "delta_h": delta_h,
            "analyze_bump": analyze_bump,
            "bump_source": bump_source,
            "manual_edit_after_autogen": manual_edit_after_autogen,
        }

    return "INCONCLUSIVE", "eyes-on", {
        "delta_h": delta_h,
        "analyze_bump": analyze_bump,
        "bump_source": bump_source,
        "manual_edit_after_autogen": manual_edit_after_autogen,
    }


def _run_report(
    config: AdminConfig,
    since: Optional[str],
    until: Optional[str],
    album: Optional[str],
    min_delta_hours: float,
    max_delta_hours: float,
    with_analysis: bool,
    bump_tolerance_s: float,
) -> list[dict[str, Any]]:
    """Run the dry-run report and return rows as dicts."""
    db_client = _get_db_client(config)

    try:
        r2_client = R2Client(config.r2_bucket, config.r2_endpoint_url, config.r2_region)
    except ValueError as e:
        console.print(f"[red]R2 not configured: {e}[/red]")
        raise typer.Exit(1)

    analysis_client: Optional[AnalysisClient] = None
    if with_analysis:
        try:
            analysis_client = AnalysisClient(config.analysis_url)
        except ValueError as e:
            console.print(f"[yellow]Analysis service not configured (--with-analysis ignored): {e}[/yellow]")
            analysis_client = None

    query, params = _build_candidate_query(since, until, album)

    rows: list[dict[str, Any]] = []

    with db_client:
        with db_client.connection.cursor() as cur:
            cur.execute(query, params)
            columns = [desc[0] for desc in cur.description]
            db_rows = cur.fetchall()

    # Batch R2 lookups concurrently
    hash_prefixes = [dict(zip(columns, row))["hash_prefix"] for row in db_rows]
    r2_identities = _batch_lookup_r2(r2_client, hash_prefixes)

    # Collect both LRC and Analysis job IDs for batch analysis lookup
    all_job_ids: set[str] = set()
    if with_analysis and analysis_client:
        for row in db_rows:
            rec = dict(zip(columns, row))
            lrc_jid = rec.get("lrc_job_id")
            analysis_jid = rec.get("analysis_job_id")
            if lrc_jid:
                all_job_ids.add(lrc_jid)
            if analysis_jid:
                all_job_ids.add(analysis_jid)

    analysis_results: dict[str, tuple[Optional[str], Optional[str]]] = {}
    if with_analysis and analysis_client and all_job_ids:
        analysis_results = _batch_lookup_analysis(analysis_client, list(all_job_ids))

    for idx, row in enumerate(db_rows):
        rec = dict(zip(columns, row))
        hash_prefix = rec["hash_prefix"]
        db_updated_at_str = _to_iso_str(rec["db_updated_at"])
        db_updated_at = _parse_iso_datetime(db_updated_at_str)

        # R2 lookup from batch result
        identity = r2_identities.get(hash_prefix, R2ObjectIdentity(exists=False))
        r2_last_modified_str: Optional[str] = None
        if identity.exists and identity.last_modified:
            r2_last_modified_str = identity.last_modified

        r2_last_modified = _parse_iso_datetime(r2_last_modified_str) if r2_last_modified_str else None

        # Resolve Analysis Job timestamps
        lrc_job_done = None
        analysis_job_done = None
        if with_analysis and analysis_client:
            # LRC job
            lrc_jid = rec.get("lrc_job_id")
            if lrc_jid and lrc_jid in analysis_results:
                lrc_job_done_str, _ = analysis_results[lrc_jid]
                lrc_job_done = _parse_iso_datetime(lrc_job_done_str)
            # Analysis job
            ana_jid = rec.get("analysis_job_id")
            if ana_jid and ana_jid in analysis_results:
                ana_job_done_str, _ = analysis_results[ana_jid]
                analysis_job_done = _parse_iso_datetime(ana_job_done_str)

        # DB timestamp
        key_detected_at_str = _to_iso_str(rec.get("key_detected_at"))
        key_detected_at = _parse_iso_datetime(key_detected_at_str) if key_detected_at_str else None

        # Compute verdict
        sig = CandidateSignals(
            db_updated_at=db_updated_at,
            r2_lm=r2_last_modified,
            lrc_job_done=lrc_job_done,
            analyze_job_done=analysis_job_done,
            key_detected_at=key_detected_at,
            with_analysis=with_analysis,
            bump_tolerance_s=bump_tolerance_s,
        )
        verdict, recommendation, notes = _compute_verdict(sig)

        # Display conversions
        db_updated_at_display = ""
        if db_updated_at is not None:
            db_updated_at_display = db_updated_at.strftime("%Y-%m-%d %H:%M:%S UTC")
        elif isinstance(db_updated_at_str, str):
            db_updated_at_display = db_updated_at_str

        analyze_job_done_display = ""
        if analysis_job_done:
            analyze_job_done_display = analysis_job_done.strftime("%Y-%m-%d %H:%M:%S UTC")

        rows.append({
            "hash_prefix": hash_prefix,
            "song_id": rec.get("song_id") or "",
            "album": rec.get("song_album") or "",
            "title": rec.get("song_title") or "",
            "db_updated_at": db_updated_at_display,
            "r2_last_modified": r2_last_modified_str or "",
            "delta_h": notes.get("delta_h", 0.0),
            "lrc_job_id": rec.get("lrc_job_id") or "",
            "analyze_job_id": rec.get("analysis_job_id") or "",
            "key_detected_at": key_detected_at_str or "",
            "analyze_job_done": analyze_job_done_display,
            "job_completed_at": _to_iso_str(lrc_job_done) if lrc_job_done else "",
            "analyze_bump": notes.get("analyze_bump", "unknown"),
            "bump_source": notes.get("bump_source", "none"),
            "manual_edit_after_autogen": notes.get("manual_edit_after_autogen", "unknown"),
            "verdict": verdict,
            "recommendation": recommendation,
        })

    return rows


def _print_table(rows: list[dict[str, Any]]) -> None:
    """Print rows as a Rich table."""
    table = Table(title="Recover visibility_status — Bug Revert Dry-Run Report", show_lines=True)

    table.add_column("hash_prefix", style="cyan", width=12)
    table.add_column("song_id", style="cyan", width=10)
    table.add_column("title", style="white", max_width=30)
    table.add_column("album", style="white", max_width=20)
    table.add_column("db_updated_at", style="dim", width=24)
    table.add_column("r2_last_modified", style="dim", width=24)
    table.add_column("delta_h", style="dim", width=8, justify="right")
    table.add_column("analyze_bump", style="dim", width=10)
    table.add_column("manual_edit", style="dim", width=10)
    table.add_column("verdict", style="bold", width=22)
    table.add_column("recommendation", style="green", max_width=26)

    for row in rows:
        verdict_style = {
            "SUSPECTED_BUG_REVERT": "red",
            "OK_FRESH_LRC": "green",
            "SUSPECTED_POST_ANALYZE": "yellow",
            "NO_SIGNAL_POST_ANALYZE": "dim",
            "INCONCLUSIVE": "yellow",
        }.get(row["verdict"], "white")

        table.add_row(
            row["hash_prefix"],
            row["song_id"],
            row["title"][:28] if len(row["title"]) > 28 else row["title"],
            row["album"][:18] if len(row["album"]) > 18 else row["album"],
            row["db_updated_at"],
            row["r2_last_modified"],
            f"{row['delta_h']:.2f}" if isinstance(row["delta_h"], float) else str(row["delta_h"]),
            str(row["analyze_bump"]),
            str(row["manual_edit_after_autogen"]),
            f"[{verdict_style}]{row['verdict']}[/{verdict_style}]",
            row["recommendation"],
        )

    console.print()

    # Summary
    total = len(rows)
    suspect_count = sum(1 for r in rows if r["verdict"] == "SUSPECTED_BUG_REVERT")
    post_analyze_count = sum(1 for r in rows if r["verdict"] == "SUSPECTED_POST_ANALYZE")
    ok_count = sum(1 for r in rows if r["verdict"] == "OK_FRESH_LRC")
    no_signal_count = sum(1 for r in rows if r["verdict"] == "NO_SIGNAL_POST_ANALYZE")
    inconclusive_count = sum(1 for r in rows if r["verdict"] == "INCONCLUSIVE")

    console.print(Panel(
        f"[cyan]Total candidates:[/cyan] {total}  |  "
        f"[red]SUSPECTED_BUG_REVERT:[/red] {suspect_count}  |  "
        f"[yellow]SUSPECTED_POST_ANALYZE:[/yellow] {post_analyze_count}  |  "
        f"[green]OK_FRESH_LRC:[/green] {ok_count}  |  "
        f"[dim]NO_SIGNAL_POST_ANALYZE:[/dim] {no_signal_count}  |  "
        f"[yellow]INCONCLUSIVE:[/yellow] {inconclusive_count}",
        border_style="cyan",
    ))

    if suspect_count > 0:
        console.print()
        console.print("[yellow]Recommendation:[/yellow] Review SUSPECTED_BUG_REVERT rows and run:")
        console.print("  [dim]sow-admin audio set-visibility <song_id> published[/dim]")

    if post_analyze_count > 0:
        console.print()
        console.print("[yellow]Note:[/yellow] Re-run with `--with-analysis` to resolve `SUSPECTED_POST_ANALYZE` rows.")


def _print_csv(rows: list[dict[str, Any]]) -> None:
    """Print rows as CSV to stdout."""
    fieldnames = [
        "hash_prefix", "song_id", "album", "title",
        "db_updated_at", "r2_last_modified", "delta_h",
        "lrc_job_id", "analyze_job_id", "key_detected_at",
        "analyze_job_done", "job_completed_at", "analyze_bump",
        "bump_source", "manual_edit_after_autogen", "verdict",
        "recommendation",
    ]
    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)


def main(
    since: Optional[str] = typer.Option(None, "--since", "-s", help="Only include recordings updated_at >= this date/time (ISO format)"),
    until: Optional[str] = typer.Option(None, "--until", "-u", help="Only include recordings updated_at <= this date/time (ISO format)"),
    album: Optional[str] = typer.Option(None, "--album", "-a", help="Filter by album name"),
    min_delta_hours: float = typer.Option(1.0, "--min-delta-hours", help="Minimum delta_hours for SUSPECTED_BUG_REVERT verdict"),
    max_delta_hours: float = typer.Option(1440.0, "--max-delta-hours", help="Maximum delta_hours for SUSPECTED_BUG_REVERT verdict"),
    with_analysis: bool = typer.Option(False, "--with-analysis", "-A", help="Cross-check with analysis service job timestamps"),
    csv_output: bool = typer.Option(False, "--csv", "-c", help="Output as CSV instead of Rich table"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-C", help="Path to config file"),
    bump_tolerance_seconds: float = typer.Option(60.0, "--bump-tolerance-seconds", help="Tolerance in seconds for detect analyze-bump"),
) -> None:
    """Identify recordings whose visibility_status was reverted by the audio-batch bug.

    This is a read-only dry-run report. It cross-references the R2 LRC file's
    LastModified timestamp against the DB recordings.updated_at timestamp to
    flag suspected bug-reverted recordings.

    No DB writes. No code changes.
    """
    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)

    console.print("[cyan]Scanning candidates...[/cyan]")
    if since:
        console.print(f"[dim]Since:[/dim] {since}")
    if until:
        console.print(f"[dim]Until:[/dim] {until}")
    if album:
        console.print(f"[dim]Album:[/dim] {album}")
    console.print(f"[dim]Delta range:[/dim] {min_delta_hours}h — {max_delta_hours}h")
    if with_analysis:
        console.print("[dim]Analysis cross-check:[/dim] enabled")
    console.print()

    try:
        rows = _run_report(config, since, until, album, min_delta_hours, max_delta_hours, with_analysis, bump_tolerance_seconds)
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)

    if not rows:
        console.print("[green]No candidate recordings found matching the filters.[/green]")
        return

    if csv_output:
        _print_csv(rows)
    else:
        _print_table(rows)


if __name__ == "__main__":
    typer.run(main)
