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
    JobInfo,
)
from stream_of_worship.admin.services.r2 import R2Client, R2ObjectIdentity
from stream_of_worship.db.connection import ConnectionProvider

console = Console()
progress_console = Console(stderr=True)
R2_LOOKUP_WORKERS = 20
ANALYSIS_LOOKUP_WORKERS = 10
ANALYSIS_STUCK_THRESHOLD_HOURS = 12


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
) -> Optional[JobInfo]:
    """Look up analysis job, returning JobInfo or None on error (404/exception)."""
    try:
        return analysis_client.get_job(job_id)
    except (AnalysisServiceError, Exception):
        return None


def _batch_lookup_analysis(
    analysis_client: AnalysisClient, job_ids: list[str],
) -> dict[str, Optional[JobInfo]]:
    """Concurrently look up analysis jobs for all job IDs."""
    results: dict[str, Optional[JobInfo]] = {}
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

    # from LRC JobInfo (REST, formerly discarded)
    lrc_status: Optional[str]
    lrc_stage: Optional[str]
    lrc_error: Optional[str]
    lrc_created_at: Optional[datetime]
    lrc_result_url: Optional[str]               # AnalysisResult.lrc_url
    lrc_source: Optional[str]
    lrc_line_count: Optional[int]
    lrc_result_key_detected_at: Optional[datetime]   # not present on lrc jobs in practice; None

    # from Analysis JobInfo (REST, formerly discarded)
    analyze_status: Optional[str]
    analyze_stage: Optional[str]
    analyze_error: Optional[str]
    analyze_result_key_detected_at: Optional[datetime]  # cross-check vs PG
    analyze_stems_present: Optional[bool]       # stems_url non-null

    # PG-side reference values for diagnostic drift comparison
    pg_r2_lrc_url: Optional[str]


def _compute_verdict(sig: CandidateSignals) -> tuple[str, str, dict[str, Any]]:
    """Compute verdict and recommendation based on new signal model.

    Layer A — verdict-changing branches (only override INCONCLUSIVE):
      1. LRC_JOB_FAILED — lrc_status in ("failed", "cancelled")
      2. LRC_JOB_STUCK  — lrc_status in ("processing", "queued", "waiting")
         AND lrc_created_at older than ANALYSIS_STUCK_THRESHOLD_HOURS

    Layer B — diagnostic-only signals (never alter verdict):
      - lrc_url_drift, stem_bump_attributable_to_stems,
        transcript_source_bias, key_detected_at_drift

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

    # --- Layer B diagnostics (computed for every row, never alter verdict) ---
    debug_notes: dict[str, Any] = {
        "delta_h": delta_h,
        "analyze_bump": analyze_bump,
        "bump_source": bump_source,
        "manual_edit_after_autogen": manual_edit_after_autogen,
    }

    # lrc_url_drift: lrc_result_url differs from PG pg_r2_lrc_url
    lrc_url_drift = False
    if (sig.lrc_result_url is not None
            and sig.pg_r2_lrc_url is not None
            and sig.lrc_result_url != sig.pg_r2_lrc_url):
        lrc_url_drift = True
        debug_notes["lrc_url_drift"] = True
        debug_notes["lrc_url_drift_details"] = (
            f"svc={sig.lrc_result_url} pg={sig.pg_r2_lrc_url}"
        )

    # stem_bump_attributable_to_stems: analyze_bump AND stems_url present
    stem_bump_attributable_to_stems = False
    if analyze_bump and sig.analyze_stems_present:
        stem_bump_attributable_to_stems = True
        debug_notes["stem_bump_attributable_to_stems"] = True
        debug_notes["stem_bump_attributable_to_stems_details"] = (
            "stems_url present"
        )

    # transcript_source_bias: manual_edit_after_autogen == "yes" AND lrc_source == "youtube_transcript"
    transcript_source_bias = False
    if (manual_edit_after_autogen == "yes"
            and sig.lrc_source == "youtube_transcript"):
        transcript_source_bias = True
        debug_notes["transcript_source_bias"] = True
        debug_notes["transcript_source_bias_details"] = (
            "manual_edit=yes, source=youtube_transcript"
        )

    # key_detected_at_drift: analyze_result_key_detected_at differs from PG
    key_detected_at_drift = False
    if (sig.analyze_result_key_detected_at is not None
            and sig.key_detected_at is not None
            and sig.analyze_result_key_detected_at != sig.key_detected_at):
        key_detected_at_drift = True
        debug_notes["key_detected_at_drift"] = True
        debug_notes["key_detected_at_drift_details"] = (
            f"analyze={sig.analyze_result_key_detected_at.isoformat()} "
            f"pg={sig.key_detected_at.isoformat()}"
        )

    # --- Original verdict logic (from pre-v2) ---
    if not sig.r2_lm or not sig.db_updated_at:
        verdict = "INCONCLUSIVE"
        recommendation = "eyes-on"
    elif manual_edit_after_autogen == "yes":
        verdict = "SUSPECTED_BUG_REVERT"
        recommendation = "set-visibility published"
    elif analyze_bump and manual_edit_after_autogen == "unknown":
        verdict = "SUSPECTED_POST_ANALYZE"
        recommendation = "needs --with-analysis to resolve"
    elif analyze_bump and manual_edit_after_autogen == "no":
        verdict = "NO_SIGNAL_POST_ANALYZE"
        recommendation = "likely not bug-reverted"
    else:
        verdict = "INCONCLUSIVE"
        recommendation = "eyes-on"

    # --- Layer A verdict branches (only override INCONCLUSIVE) ---
    if verdict == "INCONCLUSIVE" and sig.lrc_status in ("failed", "cancelled"):
        verdict = "LRC_JOB_FAILED"
        error_detail = sig.lrc_error or sig.lrc_status
        recommendation = f"eyes-on (lrc {sig.lrc_status})"
        debug_notes["lrc_job_failed"] = True
        debug_notes["lrc_job_failed_error"] = error_detail

    elif (verdict == "INCONCLUSIVE"
          and sig.lrc_status in ("processing", "queued", "waiting")
          and sig.lrc_created_at is not None):
        threshold = timedelta(hours=ANALYSIS_STUCK_THRESHOLD_HOURS)
        if (datetime.now(timezone.utc) - sig.lrc_created_at) > threshold:
            verdict = "LRC_JOB_STUCK"
            recommendation = "restart analysis worker"
            debug_notes["lrc_job_stuck"] = True
            debug_notes["lrc_job_stuck_stage"] = sig.lrc_status
            debug_notes["lrc_job_stuck_created_at"] = (
                sig.lrc_created_at.isoformat()
            )

    return verdict, recommendation, debug_notes


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

        # Resolve Analysis Job timestamps and extract JobInfo fields
        lrc_job_done = None
        analysis_job_done = None
        lrc_job: Optional[JobInfo] = None
        ana_job: Optional[JobInfo] = None
        if with_analysis and analysis_client:
            # LRC job
            lrc_jid = rec.get("lrc_job_id")
            if lrc_jid and lrc_jid in analysis_results:
                lrc_job = analysis_results[lrc_jid]
                if lrc_job is not None:
                    lrc_job_done = _parse_iso_datetime(lrc_job.updated_at)
            # Analysis job
            ana_jid = rec.get("analysis_job_id")
            if ana_jid and ana_jid in analysis_results:
                ana_job = analysis_results[ana_jid]
                if ana_job is not None:
                    analysis_job_done = _parse_iso_datetime(ana_job.updated_at)

        # DB timestamp
        key_detected_at_str = _to_iso_str(rec.get("key_detected_at"))
        key_detected_at = _parse_iso_datetime(key_detected_at_str) if key_detected_at_str else None

        # Extract LRC JobInfo fields
        lrc_status = lrc_job.status if lrc_job else None
        lrc_stage = lrc_job.stage if lrc_job else None
        lrc_error = lrc_job.error_message if lrc_job else None
        lrc_created_at = _parse_iso_datetime(lrc_job.created_at) if lrc_job else None
        lrc_result_url = None
        lrc_source = None
        lrc_line_count = None
        lrc_result_key_detected_at = None
        if lrc_job and lrc_job.result:
            lrc_result_url = lrc_job.result.lrc_url
            lrc_source = lrc_job.result.lrc_source
            lrc_line_count = lrc_job.result.line_count
            if lrc_job.result.key_detected_at:
                lrc_result_key_detected_at = _parse_iso_datetime(
                    lrc_job.result.key_detected_at
                )

        # Extract Analysis JobInfo fields
        analyze_status = ana_job.status if ana_job else None
        analyze_stage = ana_job.stage if ana_job else None
        analyze_error = ana_job.error_message if ana_job else None
        analyze_result_key_detected_at = None
        analyze_stems_present = None
        if ana_job and ana_job.result:
            if ana_job.result.key_detected_at:
                analyze_result_key_detected_at = _parse_iso_datetime(
                    ana_job.result.key_detected_at
                )
            analyze_stems_present = ana_job.result.stems_url is not None

        # PG-side LRC URL for drift comparison
        pg_r2_lrc_url = rec.get("r2_lrc_url")

        # Compute verdict
        sig = CandidateSignals(
            db_updated_at=db_updated_at,
            r2_lm=r2_last_modified,
            lrc_job_done=lrc_job_done,
            analyze_job_done=analysis_job_done,
            key_detected_at=key_detected_at,
            with_analysis=with_analysis,
            bump_tolerance_s=bump_tolerance_s,
            lrc_status=lrc_status,
            lrc_stage=lrc_stage,
            lrc_error=lrc_error,
            lrc_created_at=lrc_created_at,
            lrc_result_url=lrc_result_url,
            lrc_source=lrc_source,
            lrc_line_count=lrc_line_count,
            lrc_result_key_detected_at=lrc_result_key_detected_at,
            analyze_status=analyze_status,
            analyze_stage=analyze_stage,
            analyze_error=analyze_error,
            analyze_result_key_detected_at=analyze_result_key_detected_at,
            analyze_stems_present=analyze_stems_present,
            pg_r2_lrc_url=pg_r2_lrc_url,
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
            "lrc_status": lrc_status or "",
            "analyze_status": analyze_status or "",
            "lrc_error": lrc_error or "",
            "analyze_error": analyze_error or "",
            "lrc_result_url": lrc_result_url or "",
            "lrc_source": lrc_source or "",
            "lrc_line_count": lrc_line_count if lrc_line_count is not None else "",
            "key_detected_at_drift": notes.get("key_detected_at_drift", False),
            "lrc_url_drift": notes.get("lrc_url_drift", False),
            "stem_bump_attributable_to_stems": notes.get("stem_bump_attributable_to_stems", False),
            "transcript_source_bias": notes.get("transcript_source_bias", False),
            "debug_notes": notes,
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
    table.add_column("lrc_status", style="dim", width=10)
    table.add_column("analyze_status", style="dim", width=10)
    table.add_column("lrc_error", style="dim", max_width=20)
    table.add_column("analyze_error", style="dim", max_width=20)
    table.add_column("lrc_result_url", style="dim", max_width=20)
    table.add_column("lrc_source", style="dim", width=14)
    table.add_column("key_detected_at_drift", style="dim", width=6)
    table.add_column("lrc_url_drift", style="dim", width=6)
    table.add_column("stem_bump", style="dim", width=6)
    table.add_column("transcript_bias", style="dim", width=6)
    table.add_column("verdict", style="bold", width=22)
    table.add_column("recommendation", style="green", max_width=26)

    for row in rows:
        verdict_style = {
            "SUSPECTED_BUG_REVERT": "red",
            "OK_FRESH_LRC": "green",
            "SUSPECTED_POST_ANALYZE": "yellow",
            "NO_SIGNAL_POST_ANALYZE": "dim",
            "INCONCLUSIVE": "yellow",
            "LRC_JOB_FAILED": "red",
            "LRC_JOB_STUCK": "yellow",
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
            row["lrc_status"],
            row["analyze_status"],
            row["lrc_error"][:18] if row["lrc_error"] and len(row["lrc_error"]) > 18 else row["lrc_error"],
            row["analyze_error"][:18] if row["analyze_error"] and len(row["analyze_error"]) > 18 else row["analyze_error"],
            row["lrc_result_url"][:18] if row["lrc_result_url"] and len(row["lrc_result_url"]) > 18 else row["lrc_result_url"],
            row["lrc_source"],
            str(row["key_detected_at_drift"]),
            str(row["lrc_url_drift"]),
            str(row["stem_bump_attributable_to_stems"]),
            str(row["transcript_source_bias"]),
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
    lrc_failed_count = sum(1 for r in rows if r["verdict"] == "LRC_JOB_FAILED")
    lrc_stuck_count = sum(1 for r in rows if r["verdict"] == "LRC_JOB_STUCK")

    console.print(Panel(
        f"[cyan]Total candidates:[/cyan] {total}  |  "
        f"[red]SUSPECTED_BUG_REVERT:[/red] {suspect_count}  |  "
        f"[yellow]SUSPECTED_POST_ANALYZE:[/yellow] {post_analyze_count}  |  "
        f"[green]OK_FRESH_LRC:[/green] {ok_count}  |  "
        f"[dim]NO_SIGNAL_POST_ANALYZE:[/dim] {no_signal_count}  |  "
        f"[yellow]INCONCLUSIVE:[/yellow] {inconclusive_count}  |  "
        f"[red]LRC_JOB_FAILED:[/red] {lrc_failed_count}  |  "
        f"[yellow]LRC_JOB_STUCK:[/yellow] {lrc_stuck_count}",
        border_style="cyan",
    ))

    if suspect_count > 0:
        console.print()
        console.print("[yellow]Recommendation:[/yellow] Review SUSPECTED_BUG_REVERT rows and run:")
        console.print("  [dim]sow-admin audio set-visibility <song_id> published[/dim]")

    if post_analyze_count > 0:
        console.print()
        console.print("[yellow]Note:[/yellow] Re-run with `--with-analysis` to resolve `SUSPECTED_POST_ANALYZE` rows.")

    if lrc_failed_count > 0:
        console.print()
        console.print("[yellow]Note:[/yellow] LRC_JOB_FAILED rows: check analysis service for job errors.")


def _print_csv(rows: list[dict[str, Any]]) -> None:
    """Print rows as CSV to stdout."""
    fieldnames = [
        "hash_prefix", "song_id", "album", "title",
        "db_updated_at", "r2_last_modified", "delta_h",
        "lrc_job_id", "analyze_job_id", "key_detected_at",
        "analyze_job_done", "job_completed_at", "analyze_bump",
        "bump_source", "manual_edit_after_autogen",
        "lrc_status", "analyze_status", "lrc_error", "analyze_error",
        "lrc_result_url", "lrc_source", "lrc_line_count",
        "key_detected_at_drift", "lrc_url_drift",
        "stem_bump_attributable_to_stems", "transcript_source_bias",
        "debug_notes",
        "verdict", "recommendation",
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

    if not with_analysis:
        inconclusive_count = sum(1 for r in rows if r["verdict"] == "INCONCLUSIVE")
        if inconclusive_count > 0:
            console.print(
                f"[dim]INCONCLUSIVE rows present; re-run with "
                f"--with-analysis to surface service-side job status "
                f"and reduce inconclusive count.[/dim]",
                file=sys.stderr,
            )


if __name__ == "__main__":
    typer.run(main)
