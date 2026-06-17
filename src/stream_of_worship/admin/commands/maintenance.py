"""Maintenance commands for soft-deleted catalog data and R2 cleanup."""

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from stream_of_worship.admin.commands.catalog import get_db_client
from stream_of_worship.admin.config import AdminConfig
from stream_of_worship.admin.db.client import DatabaseClient
from stream_of_worship.admin.services.r2 import R2Client, R2PrefixSummary

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


def _print_manifest(rows: list[dict], format_: str) -> None:
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
    limit: Optional[int] = typer.Option(None, "--limit", min=1),
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

    rows: list[dict] = []
    if entity in ("all", "songs"):
        for row in db_client.list_soft_deleted_songs_with_counts(limit=limit):
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
        for row in db_client.list_soft_deleted_recordings_with_counts(limit=limit):
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
    all_: bool = typer.Option(False, "--all"),
    confirm: bool = typer.Option(False, "--confirm"),
    format_: str = typer.Option("table", "--format", help="table|json"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Repair songset items that point at missing or soft-deleted recordings."""
    if not (songset_id or hash_prefix or all_):
        console.print("[red]Provide --songset-id, --hash-prefix, or --all.[/red]")
        raise typer.Exit(1)
    config, db_client = _load_clients(config_path)
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
    limit: Optional[int] = typer.Option(None, "--limit", min=1),
    format_: str = typer.Option("table", "--format", help="table|json"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """Diagnose failed render jobs against current songset state."""
    config, db_client = _load_clients(config_path)
    r2_client = _load_r2(config)
    rows: list[dict] = []
    for job in db_client.find_failed_render_jobs(job_id=job_id, since_days=since_days, limit=limit):
        stale = _repair_manifest(db_client, r2_client, job["songset_id"], None, True)
        if stale:
            for item in stale:
                rows.append({**job, **item, "diagnosis_scope": "current-state"})
        else:
            rows.append({**job, "diagnosis_scope": "current-state", "finding": "no-stale-items"})
    _print_manifest(rows, format_)


def _orphan_r2_prefixes(
    db_client: DatabaseClient,
    r2_client: R2Client,
    blacklist: list[str],
    limit: Optional[int],
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
        if limit is not None and len(rows) >= limit:
            break
    return rows


@app.command("list-r2-waste")
def list_r2_waste(
    format_: str = typer.Option("table", "--format", help="table|json"),
    limit: Optional[int] = typer.Option(None, "--limit", min=1),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c"),
) -> None:
    """List orphan hash-like R2 prefixes with no DB recording row."""
    config, db_client = _load_clients(config_path)
    r2_client = _load_r2(config)
    rows = _orphan_r2_prefixes(db_client, r2_client, config.r2_waste_blacklist, limit)
    _print_manifest(rows, format_)


@app.command("purge-r2-waste")
def purge_r2_waste(
    prefixes: list[str] = typer.Option([], "--prefix"),
    all_: bool = typer.Option(False, "--all"),
    confirm: bool = typer.Option(False, "--confirm"),
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
        rows = _orphan_r2_prefixes(db_client, r2_client, config.r2_waste_blacklist, None)
    else:
        rows = []
        for prefix in prefixes:
            validated = R2Client.validate_recording_hash_prefix(prefix)
            summary = r2_client.list_prefix(validated)
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
    for row in rows:
        blocked = []
        if db_client.recording_row_exists(row["prefix"]):
            blocked.append("recording-row-exists")
        if row["songset_reference_count"]:
            blocked.append("referenced-by-songset")
        row["action"] = "purge" if not blocked else "blocked"
        row["blocked_reasons"] = ",".join(blocked)
        if confirm and not blocked:
            summary: R2PrefixSummary = r2_client.delete_prefix(row["prefix"])
            row["deleted_object_count"] = summary.object_count
    _print_manifest(rows, format_)
    if not confirm:
        console.print("[yellow]Dry run only. Re-run with --confirm to apply.[/yellow]")
