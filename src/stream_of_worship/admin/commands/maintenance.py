"""Maintenance commands for soft-deleted catalog data and R2 cleanup."""

import json
import logging as _stdlogging
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table

from stream_of_worship.admin.commands.catalog import get_db_client
from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.services.r2 import R2Client, R2PrefixSummary
from stream_of_worship.admin.services.r2_backup import (
    DEFAULT_CHUNK_SIZE_BYTES,
    MIN_CHUNK_SIZE_BYTES,
    BackupError,
    BackupProgress,
    BackupTracer,
    RestoreError,
    VerifyError,
    build_inventory,
    load_manifest,
    parse_size,
    plan_restore,
    restore_from_archive,
    verify_archive,
    write_backup,
)

console = Console()
app = typer.Typer(help="Safe catalog and storage maintenance")

ENTITY_VALUES = {"all", "songs", "recordings"}
FORMAT_VALUES = {"table", "json", "ids"}


def _load_clients(config_path: Optional[Path]) -> tuple[AdminConfig, DatabaseClient]:
    try:
        config = AdminConfig.load(config_path)
    except FileNotFoundError:
        console.print("[red]Config file not found. Run 'sow-admin db init' first.[/red]")
        raise typer.Exit(1)
    return config, get_db_client(config)


def _load_r2(config: AdminConfig) -> R2Client:
    try:
        return R2Client(config.r2_bucket, config.r2_endpoint_url, config.r2_region)
    except ValueError as e:
        console.print(f"[red]R2 configuration error: {e}[/red]")
        raise typer.Exit(1)


def _validate_choice(value: str, choices: set[str], name: str) -> None:
    if value not in choices:
        console.print(f"[red]{name} must be one of: {', '.join(sorted(choices))}[/red]")
        raise typer.Exit(1)


def _json_default(value: object) -> object:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dict__"):
        return value.__dict__
    return str(value)


def _print_json(rows: list[dict]) -> None:
    console.print(json.dumps(rows, default=_json_default, ensure_ascii=False, indent=2))


def _bytes_to_mb(total_bytes: Optional[int]) -> int:
    """Convert bytes to decimal MB (1 MB = 1,000,000 bytes), rounded to integer."""
    if total_bytes is None:
        return 0
    return round(total_bytes / 1_000_000)


def _format_datetime(ts: Optional[str]) -> str:
    """Truncate timestamp to seconds and format as 'YYYY-MM-DD HH:MM:SS'."""
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return str(ts)


_BYTE_FIELDS = {"total_bytes": "total_mb", "r2_bytes": "r2_mb"}
_DATETIME_FIELDS = {"last_modified", "created_at", "deleted_at"}


def _transform_rows(rows: list[dict]) -> list[dict]:
    """Apply display transformations: bytes to MB, truncate datetimes to seconds."""
    transformed = []
    for row in rows:
        new_row = dict(row)
        for src, dst in _BYTE_FIELDS.items():
            if src in new_row:
                new_row[dst] = _bytes_to_mb(new_row.pop(src))
        for field in _DATETIME_FIELDS:
            if field in new_row:
                new_row[field] = _format_datetime(new_row[field])
        transformed.append(new_row)
    return transformed


def _print_manifest(rows: list[dict], format_: str) -> None:
    rows = _transform_rows(rows)
    if format_ == "json":
        _print_json(rows)
        return
    table = Table(title="Maintenance Manifest")
    keys = sorted({key for row in rows for key in row.keys()})
    for key in keys:
        table.add_column(key)
    for row in rows:
        table.add_row(*(str(row.get(key, "")) for key in keys))
    console.print(table)


def _selected_soft_deleted_songs(
    db_client: DatabaseClient,
    entity: str,
    song_ids: list[str],
    all_: bool,
    limit: Optional[int],
) -> list[dict]:
    if entity not in ("all", "songs"):
        return []
    if not all_ and not song_ids:
        return []
    rows = db_client.list_soft_deleted_songs_with_counts(limit=limit)
    if not all_ and song_ids:
        wanted = set(song_ids)
        rows = [row for row in rows if row["song"].id in wanted]
    return rows


def _selected_soft_deleted_recordings(
    db_client: DatabaseClient,
    entity: str,
    hash_prefixes: list[str],
    all_: bool,
    limit: Optional[int],
) -> list[dict]:
    if entity not in ("all", "recordings"):
        return []
    if not all_ and not hash_prefixes:
        return []
    rows = db_client.list_soft_deleted_recordings_with_counts(limit=limit)
    if not all_ and hash_prefixes:
        wanted = set(hash_prefixes)
        rows = [row for row in rows if row["recording"].hash_prefix in wanted]
    return rows


@app.command("list-soft-deletes")
def list_soft_deletes(
    entity: str = typer.Option("all", "--entity", help="all|songs|recordings"),
    format_: str = typer.Option("table", "--format", help="table|json|ids"),
    limit: Optional[int] = typer.Option(20, "--limit", min=1),
    all_: bool = typer.Option(False, "--all", help="List all results without limit"),
    with_r2: bool = typer.Option(
        False, "--with-r2", help="Include R2 object counts for recordings"
    ),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """List soft-deleted songs and recordings."""
    _validate_choice(entity, ENTITY_VALUES, "--entity")
    _validate_choice(format_, FORMAT_VALUES, "--format")
    config, db_client = _load_clients(config_path)
    r2_client = _load_r2(config) if with_r2 else None

    effective_limit = None if all_ else limit

    rows: list[dict] = []
    if entity in ("all", "songs"):
        for row in db_client.list_soft_deleted_songs_with_counts(limit=effective_limit):
            song = row["song"]
            rows.append(
                {
                    "entity": "song",
                    "id": song.id,
                    "title": song.title,
                    "deleted_at": song.deleted_at,
                    "recording_count": row["recording_count"],
                    "songset_reference_count": row["songset_reference_count"],
                }
            )
    if entity in ("all", "recordings"):
        for row in db_client.list_soft_deleted_recordings_with_counts(limit=effective_limit):
            recording = row["recording"]
            item = {
                "entity": "recording",
                "id": recording.hash_prefix,
                "song_id": recording.song_id,
                "deleted_at": recording.deleted_at,
                "songset_reference_count": row["songset_reference_count"],
            }
            if r2_client:
                summary = r2_client.list_prefix(recording.hash_prefix)
                item.update(
                    {"r2_object_count": summary.object_count, "r2_bytes": summary.total_bytes}
                )
            rows.append(item)

    if effective_limit is not None:
        rows = rows[:effective_limit]

    if format_ == "ids":
        for row in rows:
            console.print(f"{row['entity']}:{row['id']}" if entity == "all" else row["id"])
        return
    _print_manifest(rows, format_)


@app.command("purge-soft-deletes")
def purge_soft_deletes(
    entity: str = typer.Option("all", "--entity", help="all|songs|recordings"),
    song_ids: list[str] = typer.Option([], "--song-id", help="Soft-deleted song ID"),
    hash_prefixes: list[str] = typer.Option(
        [], "--hash-prefix", help="Soft-deleted recording hash"
    ),
    all_: bool = typer.Option(False, "--all", help="Process all matching items"),
    confirm: bool = typer.Option(False, "--confirm", help="Apply destructive purge"),
    format_: str = typer.Option("table", "--format", help="table|json"),
    limit: Optional[int] = typer.Option(None, "--limit", min=1),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Purge eligible soft-deleted DB rows and recording R2 prefixes."""
    _validate_choice(entity, ENTITY_VALUES, "--entity")
    _validate_choice(format_, {"table", "json"}, "--format")
    if not (song_ids or hash_prefixes or all_):
        console.print("[red]Provide --song-id, --hash-prefix, or --all.[/red]")
        raise typer.Exit(1)
    config, db_client = _load_clients(config_path)
    r2_client = _load_r2(config)

    rows: list[dict] = []
    songs = _selected_soft_deleted_songs(db_client, entity, song_ids, all_, limit)
    recordings = _selected_soft_deleted_recordings(db_client, entity, hash_prefixes, all_, limit)

    for row in recordings:
        recording = row["recording"]
        blocked = []
        if row["songset_reference_count"]:
            blocked.append("referenced-by-songset")
        item = {
            "entity": "recording",
            "id": recording.hash_prefix,
            "action": "purge" if not blocked else "blocked",
            "blocked_reasons": ",".join(blocked),
        }
        rows.append(item)
        if confirm and not blocked:
            try:
                deleted = db_client.hard_delete_soft_deleted_recording(recording.hash_prefix)
            except ValueError as e:
                item["action"] = "blocked"
                item["blocked_reasons"] = str(e)
                continue
            if not deleted:
                item["action"] = "skipped"
                item["blocked_reasons"] = "recording-not-soft-deleted"
                continue
            try:
                summary = r2_client.delete_prefix(recording.hash_prefix)
                item["deleted_object_count"] = summary.object_count
            except Exception as e:
                item["action"] = "purged-db-r2-failed"
                item["blocked_reasons"] = f"r2-delete-failed: {e}"

    for row in songs:
        song = row["song"]
        blocked = []
        if row["recording_count"]:
            blocked.append("has-recordings")
        if row["songset_reference_count"]:
            blocked.append("referenced-by-songset")
        rows.append(
            {
                "entity": "song",
                "id": song.id,
                "action": "purge" if not blocked else "blocked",
                "blocked_reasons": ",".join(blocked),
            }
        )
        if confirm and not blocked:
            db_client.hard_delete_soft_deleted_song(song.id)

    _print_manifest(rows, format_)
    if not confirm:
        console.print("[yellow]Dry run only. Re-run with --confirm to apply.[/yellow]")


@app.command("restore-soft-deletes")
def restore_soft_deletes(
    entity: str = typer.Option("all", "--entity", help="all|songs|recordings"),
    song_ids: list[str] = typer.Option([], "--song-id"),
    hash_prefixes: list[str] = typer.Option([], "--hash-prefix"),
    all_: bool = typer.Option(False, "--all"),
    confirm: bool = typer.Option(False, "--confirm"),
    format_: str = typer.Option("table", "--format", help="table|json"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Restore soft-deleted songs or recordings."""
    _validate_choice(entity, ENTITY_VALUES, "--entity")
    if not (song_ids or hash_prefixes or all_):
        console.print("[red]Provide --song-id, --hash-prefix, or --all.[/red]")
        raise typer.Exit(1)
    _, db_client = _load_clients(config_path)
    rows: list[dict] = []
    songs = _selected_soft_deleted_songs(db_client, entity, song_ids, all_, None)
    recordings = _selected_soft_deleted_recordings(db_client, entity, hash_prefixes, all_, None)

    for row in songs:
        song = row["song"]
        rows.append({"entity": "song", "id": song.id, "action": "restore", "blocked_reasons": ""})
        if confirm:
            db_client.restore_song(song.id)

    for row in recordings:
        recording = row["recording"]
        blocked = (
            "song-soft-deleted"
            if db_client.is_recording_song_soft_deleted(recording.hash_prefix)
            else ""
        )
        rows.append(
            {
                "entity": "recording",
                "id": recording.hash_prefix,
                "action": "restore" if not blocked else "blocked",
                "blocked_reasons": blocked,
            }
        )
        if confirm and not blocked:
            db_client.restore_recording(recording.hash_prefix)

    _print_manifest(rows, format_)
    if not confirm:
        console.print("[yellow]Dry run only. Re-run with --confirm to apply.[/yellow]")


def _repair_manifest(
    db_client: DatabaseClient,
    r2_client: R2Client,
    songset_id: Optional[str],
    hash_prefix: Optional[str],
    all_: bool,
) -> list[dict]:
    if not all_ and not songset_id and not hash_prefix:
        return []
    stale = db_client.find_stale_songset_items(songset_id=songset_id, hash_prefix=hash_prefix)
    rows = []
    for item in stale:
        replacement = None
        for candidate in db_client.find_replacement_recording_candidates(item["song_id"]):
            if r2_client.audio_exists(candidate.hash_prefix):
                replacement = candidate
                break
        rows.append(
            {
                **item,
                "replacement_hash": replacement.hash_prefix if replacement else "",
                "blocked_reasons": "" if replacement else "no-replacement",
            }
        )
    return rows


@app.command("repair-songsets")
def repair_songsets(
    songset_id: Optional[str] = typer.Option(None, "--songset-id"),
    hash_prefix: Optional[str] = typer.Option(None, "--hash-prefix"),
    all_: bool = typer.Option(
        False,
        "--all",
        help="List all songsets needing repair (without --confirm); "
        "repair all stale items (with --confirm)",
    ),
    confirm: bool = typer.Option(False, "--confirm"),
    limit: Optional[int] = typer.Option(20, "--limit", min=1),
    format_: str = typer.Option("table", "--format", help="table|json"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Repair songset items that point at missing or soft-deleted recordings."""
    if confirm and not (songset_id or hash_prefix or all_):
        console.print("[red]Provide --songset-id, --hash-prefix, or --all --confirm.[/red]")
        raise typer.Exit(1)

    config, db_client = _load_clients(config_path)

    is_repair_mode = songset_id is not None or hash_prefix is not None or (all_ and confirm)

    if not is_repair_mode:
        effective_limit = None if all_ else limit
        rows = db_client.find_songsets_needing_repair(limit=effective_limit)
        _print_manifest(rows, format_)
        return

    r2_client = _load_r2(config)
    rows = _repair_manifest(db_client, r2_client, songset_id, hash_prefix, all_)
    active_jobs = db_client.find_active_render_jobs_for_songsets(
        sorted({row["songset_id"] for row in rows})
    )
    blocked_songsets = {job["songset_id"] for job in active_jobs}
    for row in rows:
        if row["songset_id"] in blocked_songsets:
            row["blocked_reasons"] = "active-render-job"

    if confirm:
        replacements = [
            (row["item_id"], row["songset_id"], row["replacement_hash"])
            for row in rows
            if row["replacement_hash"] and not row["blocked_reasons"]
        ]
        db_client.repair_songset_items(replacements)

    _print_manifest(rows, format_)
    if not confirm:
        console.print("[yellow]Dry run only. Re-run with --confirm to apply.[/yellow]")


@app.command("diagnose-render-failures")
def diagnose_render_failures(
    job_id: Optional[str] = typer.Option(None, "--job-id"),
    since_days: Optional[int] = typer.Option(None, "--since-days", min=1),
    limit: Optional[int] = typer.Option(20, "--limit", min=1),
    all_: bool = typer.Option(False, "--all", help="List all results without limit"),
    format_: str = typer.Option("table", "--format", help="table|json"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Diagnose failed render jobs against current songset state."""
    config, db_client = _load_clients(config_path)
    r2_client = _load_r2(config)
    effective_limit = None if all_ else limit
    rows: list[dict] = []
    for job in db_client.find_failed_render_jobs(
        job_id=job_id, since_days=since_days, limit=effective_limit
    ):
        stale = _repair_manifest(db_client, r2_client, job["songset_id"], None, True)
        if stale:
            for item in stale:
                rows.append({**job, **item, "diagnosis_scope": "current-state"})
        else:
            rows.append({**job, "diagnosis_scope": "current-state", "finding": "no-stale-items"})
    _print_manifest(rows, format_)


def _sort_by_last_modified_asc(rows: list[dict]) -> list[dict]:
    """Sort rows by last_modified ASC (oldest first). None/empty values sort last."""

    def sort_key(row):
        ts = row.get("last_modified")
        if not ts:
            return (1, datetime.min)
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is not None:
                dt = dt.replace(tzinfo=None)
            return (0, dt)
        except (ValueError, TypeError):
            return (1, datetime.min)

    return sorted(rows, key=sort_key)


def _orphan_r2_prefixes(
    db_client: DatabaseClient,
    r2_client: R2Client,
    blacklist: list[str],
) -> list[dict]:
    rows = []
    for summary in r2_client.scan_recording_prefixes(blacklist=blacklist):
        if db_client.recording_row_exists(summary.prefix):
            continue
        references = db_client.count_recording_songset_references(summary.prefix)
        rows.append(
            {
                "prefix": summary.prefix,
                "object_count": summary.object_count,
                "total_bytes": summary.total_bytes,
                "last_modified": summary.last_modified,
                "songset_reference_count": references,
            }
        )
    return rows


@app.command("list-r2-waste")
def list_r2_waste(
    format_: str = typer.Option("table", "--format", help="table|json"),
    limit: Optional[int] = typer.Option(20, "--limit", min=1),
    all_: bool = typer.Option(False, "--all", help="List all results without limit"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """List orphan hash-like R2 prefixes with no DB recording row."""
    config, db_client = _load_clients(config_path)
    r2_client = _load_r2(config)
    rows = _orphan_r2_prefixes(db_client, r2_client, config.r2_waste_blacklist)
    rows = _sort_by_last_modified_asc(rows)
    effective_limit = None if all_ else limit
    if effective_limit is not None:
        rows = rows[:effective_limit]
    _print_manifest(rows, format_)


@app.command("purge-r2-waste")
def purge_r2_waste(
    prefixes: list[str] = typer.Option([], "--prefix"),
    all_: bool = typer.Option(False, "--all"),
    confirm: bool = typer.Option(False, "--confirm"),
    stems_only: bool = typer.Option(
        False,
        "--stems-only",
        help="Delete only {prefix}/stems/ objects, not the entire prefix",
    ),
    format_: str = typer.Option("table", "--format", help="table|json"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Delete orphan recording-prefix objects from R2 without mutating DB rows."""
    if not (prefixes or all_):
        console.print("[red]Provide --prefix or --all.[/red]")
        raise typer.Exit(1)
    config, db_client = _load_clients(config_path)
    r2_client = _load_r2(config)
    if all_:
        rows = _orphan_r2_prefixes(db_client, r2_client, config.r2_waste_blacklist)
        if stems_only:
            for row in rows:
                stems_summary = r2_client.list_stems(row["prefix"])
                row["object_count"] = stems_summary.object_count
                row["total_bytes"] = stems_summary.total_bytes
                row["last_modified"] = stems_summary.last_modified
    else:
        rows = []
        for prefix in prefixes:
            validated = R2Client.validate_recording_hash_prefix(prefix)
            summary = (
                r2_client.list_stems(validated)
                if stems_only
                else r2_client.list_prefix(validated)
            )
            rows.append(
                {
                    "prefix": validated,
                    "object_count": summary.object_count,
                    "total_bytes": summary.total_bytes,
                    "last_modified": summary.last_modified,
                    "songset_reference_count": db_client.count_recording_songset_references(
                        validated
                    ),
                }
            )
    rows = _sort_by_last_modified_asc(rows)
    for row in rows:
        blocked = []
        if db_client.recording_row_exists(row["prefix"]):
            blocked.append("recording-row-exists")
        if row["songset_reference_count"]:
            blocked.append("referenced-by-songset")
        row["action"] = "purge" if not blocked else "blocked"
        row["blocked_reasons"] = ",".join(blocked)
        if confirm and not blocked:
            summary: R2PrefixSummary = (
                r2_client.delete_stems(row["prefix"])
                if stems_only
                else r2_client.delete_prefix(row["prefix"])
            )
            row["deleted_object_count"] = summary.object_count
    _print_manifest(rows, format_)
    if not confirm:
        console.print("[yellow]Dry run only. Re-run with --confirm to apply.[/yellow]")


# ------------------------------------------------------------------
# R2 Backup / Restore commands
# ------------------------------------------------------------------

BACKUP_FORMAT_VALUES = {"table", "json"}


def _print_json_to_stdout(data: object) -> None:
    """Print JSON to stdout only (for --format json mode)."""
    print(json.dumps(data, default=str, ensure_ascii=False, indent=2))


def _print_backup_summary_table(result, output_dir: Path) -> None:
    table = Table(title="R2 Backup Complete")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Output directory", str(output_dir))
    table.add_row("Object count", str(result.object_count))
    table.add_row("Total MB", str(_bytes_to_mb(result.total_bytes)))
    table.add_row("Chunk count", str(result.chunk_count))
    console.print(table)


def _configure_r2_backup_debug_logging(console: Console) -> None:
    """Attach a DEBUG-level stderr handler to the r2_backup module logger.

    Idempotent: if a handler tagged with the marker attribute is already
    attached, does nothing.
    """
    target = _stdlogging.getLogger(
        "stream_of_worship.admin.services.r2_backup"
    )
    target.setLevel(_stdlogging.DEBUG)
    marker_attr = "_sow_r2_backup_debug_handler"
    for h in target.handlers:
        if getattr(h, marker_attr, False):
            return
    handler = _stdlogging.StreamHandler(console.file)
    handler.setLevel(_stdlogging.DEBUG)
    handler.setFormatter(
        _stdlogging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    setattr(handler, marker_attr, True)
    target.addHandler(handler)


@app.command("backup-r2")
def backup_r2(
    output: Path = typer.Option(..., "--output", help="Output directory for backup"),
    chunk_size: str = typer.Option(
        "10GiB", "--chunk-size", help="Chunk size (e.g. 10GiB, 500MiB, raw bytes)"
    ),
    concurrency: int = typer.Option(
        8, "--concurrency", min=1, max=64,
        help="Number of concurrent download workers"
    ),
    debug_traces: bool = typer.Option(
        False, "--debug-traces",
        help="Emit per-object and per-phase performance traces to stderr at DEBUG level"
    ),
    format_: str = typer.Option("table", "--format", help="table|json"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Backup entire R2 bucket to a local directory with chunked tar archives."""
    _validate_choice(format_, BACKUP_FORMAT_VALUES, "--format")

    try:
        chunk_size_bytes = parse_size(chunk_size)
    except ValueError as e:
        console.print(f"[red]Invalid --chunk-size: {e}[/red]")
        raise typer.Exit(1)

    if chunk_size_bytes < MIN_CHUNK_SIZE_BYTES:
        console.print(
            f"[red]Chunk size {chunk_size_bytes} is below minimum "
            f"{MIN_CHUNK_SIZE_BYTES} (64MiB)[/red]"
        )
        raise typer.Exit(1)

    config, _ = _load_clients(config_path)
    r2_client = _load_r2(config)

    if format_ == "json":
        progress_console = Console(file=sys.stderr)
    else:
        progress_console = console

    tracer: Optional[BackupTracer] = None
    if debug_traces:
        _configure_r2_backup_debug_logging(progress_console)
        tracer = BackupTracer()

    progress_console.print("[cyan]Building R2 inventory...[/cyan]")
    if tracer is not None:
        tracer.phase_start("inventory")
    inventory = build_inventory(r2_client)
    if tracer is not None:
        tracer.phase_end(
            "inventory",
            objects=inventory.object_count,
            total_bytes=inventory.total_bytes,
        )
    progress_console.print(
        f"[green]Inventory complete: {inventory.object_count} objects, "
        f"{_bytes_to_mb(inventory.total_bytes)} MB[/green]"
    )

    try:
        with Progress(
            SpinnerColumn(),
            BarColumn(),
            TaskProgressColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            TimeElapsedColumn(),
            TextColumn("{task.fields[workers]} workers"),
            TextColumn("{task.fields[objects_done]}/{task.fields[object_count]} objects"),
            console=progress_console,
        ) as progress:
            task = progress.add_task(
                "Backing up...",
                total=inventory.total_bytes,
                workers=0,
                objects_done=0,
                object_count=inventory.object_count,
            )

            base_callback: Optional[Callable[[BackupProgress], None]] = None

            def _on_progress(prog: BackupProgress) -> None:
                progress.update(
                    task,
                    completed=prog.bytes_downloaded,
                    workers=prog.active_workers,
                    objects_done=prog.objects_downloaded,
                )
                if tracer is not None:
                    tracer.bytes_downloaded_sample(
                        prog.bytes_downloaded, prog.active_workers
                    )

            base_callback = _on_progress

            result = write_backup(
                r2_client=r2_client,
                output_dir=output,
                inventory=inventory,
                chunk_size_bytes=chunk_size_bytes,
                concurrency=concurrency,
                on_progress=base_callback,
                tracer=tracer,
            )
    except BackupError as e:
        console.print(f"[red]Backup failed: {e}[/red]")
        raise typer.Exit(1)

    if format_ == "json":
        _print_json_to_stdout(
            {
                "output_dir": str(result.output_dir),
                "object_count": result.object_count,
                "total_mb": _bytes_to_mb(result.total_bytes),
                "chunk_count": result.chunk_count,
            }
        )
    else:
        _print_backup_summary_table(result, output)


@app.command("verify-r2-backup")
def verify_r2_backup(
    dir: Path = typer.Option(..., "--dir", help="Backup directory to verify"),
    format_: str = typer.Option("table", "--format", help="table|json"),
) -> None:
    """Verify a local R2 backup archive without R2 credentials."""
    _validate_choice(format_, BACKUP_FORMAT_VALUES, "--format")

    if not dir.is_dir():
        console.print(f"[red]Directory not found: {dir}[/red]")
        raise typer.Exit(1)

    result = verify_archive(dir)

    if format_ == "json":
        _print_json_to_stdout(
            {
                "ok": result.ok,
                "errors": result.errors,
                "object_count": result.object_count,
                "total_mb": _bytes_to_mb(result.total_bytes),
                "chunk_count": result.chunk_count,
            }
        )
    else:
        if result.ok:
            console.print(
                f"[green]Verification OK: {result.object_count} objects, "
                f"{_bytes_to_mb(result.total_bytes)} MB, {result.chunk_count} chunks[/green]"
            )
        else:
            console.print(f"[red]Verification failed with {len(result.errors)} error(s):[/red]")
            for err in result.errors:
                console.print(f"  [red]- {err}[/red]")

    if not result.ok:
        raise typer.Exit(1)


@app.command("restore-r2")
def restore_r2(
    dir: Path = typer.Option(..., "--dir", help="Backup directory to restore from"),
    prefixes: list[str] = typer.Option(
        [], "--prefix", help="Only restore objects with this key prefix (repeatable)"
    ),
    skip_existing: bool = typer.Option(
        False, "--skip-existing", help="Skip existing target objects"
    ),
    overwrite_existing: bool = typer.Option(
        False, "--overwrite-existing", help="Overwrite existing target objects"
    ),
    confirm: bool = typer.Option(
        False, "--confirm", help="Perform restore (default is dry-run)"
    ),
    format_: str = typer.Option("table", "--format", help="table|json"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Restore R2 objects from a local backup archive."""
    _validate_choice(format_, BACKUP_FORMAT_VALUES, "--format")

    if skip_existing and overwrite_existing:
        console.print(
            "[red]--skip-existing and --overwrite-existing are mutually exclusive.[/red]"
        )
        raise typer.Exit(1)

    if not dir.is_dir():
        console.print(f"[red]Backup directory not found: {dir}[/red]")
        raise typer.Exit(1)

    # Always verify before restore
    verify_result = verify_archive(dir)
    if not verify_result.ok:
        console.print("[red]Backup verification failed. Cannot restore.[/red]")
        for err in verify_result.errors:
            console.print(f"  [red]- {err}[/red]")
        raise typer.Exit(1)

    manifest = load_manifest(dir)

    config, _ = _load_clients(config_path)
    r2_client = _load_r2(config)

    # Build restore plan
    try:
        plan = plan_restore(
            r2_client=r2_client,
            manifest=manifest,
            prefixes=prefixes if prefixes else None,
            skip_existing=skip_existing,
            overwrite_existing=overwrite_existing,
        )
    except RestoreError as e:
        console.print(f"[red]Restore planning failed: {e}[/red]")
        raise typer.Exit(1)

    if not plan.rows:
        if format_ == "json":
            _print_json_to_stdout(
                {"uploaded": 0, "skipped": 0, "conflicts": 0, "failed": 0, "message": "No objects matched"}
            )
        else:
            console.print("[yellow]No objects matched the filter. Nothing to restore.[/yellow]")
        return

    # Print plan
    rows_data = [
        {
            "key": r.key,
            "action": r.action,
            "size_mb": _bytes_to_mb(r.size),
            "chunk_index": r.chunk_index,
        }
        for r in plan.rows
    ]

    if format_ == "json":
        if not confirm:
            _print_json_to_stdout(
                {
                    "plan": rows_data,
                    "confirm": confirm,
                }
            )
    else:
        table = Table(title="Restore Plan" + (" (DRY RUN)" if not confirm else ""))
        table.add_column("Key")
        table.add_column("Action")
        table.add_column("Size (MB)")
        for row in rows_data:
            table.add_row(row["key"], row["action"], str(row["size_mb"]))
        console.print(table)

    if not confirm:
        if format_ != "json":
            console.print("[yellow]Dry run only. Re-run with --confirm to apply.[/yellow]")
        return

    # Abort on conflicts
    if plan.has_conflicts:
        conflict_count = sum(1 for r in plan.rows if r.action == "conflict")
        console.print(
            f"[red]Aborting: {conflict_count} conflict(s) detected. "
            "Use --skip-existing or --overwrite-existing.[/red]"
        )
        raise typer.Exit(1)

    # Execute restore
    try:
        result = restore_from_archive(
            r2_client=r2_client,
            dir_path=dir,
            manifest=manifest,
            plan=plan,
            confirm=confirm,
        )
    except RestoreError as e:
        console.print(f"[red]Restore failed: {e}[/red]")
        raise typer.Exit(1)

    if format_ == "json":
        _print_json_to_stdout(
            {
                "uploaded": result.uploaded,
                "skipped": result.skipped,
                "conflicts": result.conflicts,
                "failed": result.failed,
                "failures": result.failures,
            }
        )
    else:
        console.print(
            f"[green]Restore complete: {result.uploaded} uploaded, "
            f"{result.skipped} skipped, {result.conflicts} conflicts, "
            f"{result.failed} failed[/green]"
        )
        if result.failures:
            for fail in result.failures:
                console.print(f"  [red]- {fail['key']}: {fail['error']}[/red]")

    if result.failed > 0:
        raise typer.Exit(1)
