# CLI List & Count UX Improvements (v2)

## Context

`sow_admin catalog list | wc -l` and `sow_admin audio list | wc -l` produce misleading counts because:
1. Rich Table output adds ~5 non-data lines (borders, header, title)
2. `audio list` has 9 columns — long filenames cause cell wrapping, further inflating `wc -l`
3. `db status` shows total row counts (including soft-deleted), while `catalog list` and `audio list` filter `deleted_at IS NULL` — the numbers don't match and there's no way to see why

Additionally, `db init --force` resets from Turso without backup, health check, or confirmation — risking data loss.

---

## Improvement 1: Truncate Filenames in `audio list`

**Problem:** The 9-column Rich table wraps long filenames across multiple terminal lines, making `wc -l` unreliable and the table hard to scan.

**Change:** Truncate `original_filename` to 30 characters (27 + `...`) in table display. Guard against `None` (it's the only column in `add_row()` without an `or "-"` fallback).

### File: `src/stream_of_worship/admin/commands/audio.py`

At line ~1119, before `table.add_row()`:

```python
filename_display = rec.original_filename or "-"
if len(filename_display) > 30:
    filename_display = filename_display[:27] + "..."

table.add_row(
    album_name or "-",
    song_title or "-",
    visibility_text,
    size_str,
    lrc_text,
    duration_str,
    song_id,
    filename_display,
    rec.hash_prefix,
)
```

No other files affected. The `--format ids` path doesn't use filenames.

---

## Improvement 2: Add `--format count` Option

**Problem:** No simple way to get a pipeable record count. `--format table | wc -l` is wrong; `--format ids | wc -l` works but is wasteful for large datasets.

**Change:** Add `count` as a third format option to both `catalog list` and `audio list`. Output is a single raw integer.

### File: `src/stream_of_worship/admin/commands/catalog.py`

1. **Line ~205** — Update help text for `--format`:
   ```python
   help="Output format (table|ids|count)",
   ```

2. **Line ~291** — Add `count` branch before the `ids` branch:
   ```python
   if format == "count":
       console.print(len(songs))
   elif format == "ids":
       for song in songs:
           console.print(song.id)
   else:
       # existing table code
   ```

   Note: The `count` branch must come after the composer filter (line 280-281) and series re-sort (line 284-285) so that `--composer X --format count` returns the filtered count.

### File: `src/stream_of_worship/admin/commands/audio.py`

1. **Line ~1004** — Update help text for `--format`:
   ```python
   format: str = typer.Option("table", "--format", "-f", help="Output format (table|ids|count)"),
   ```

2. **Line ~1078** — Add `count` branch before the `ids` branch:
   ```python
   if format == "count":
       console.print(len(enriched))
   elif format == "ids":
       for rec, _title, _album, _series in enriched:
           console.print(rec.song_id if rec.song_id else rec.hash_prefix)
   else:
       # existing table code
   ```

   Note: Must come after the sorting/enrichment logic (line ~1060-1076) so filters are already applied.

---

## Improvement 3: Show Active vs Total Counts in `db status`

**Problem:** `db status` shows total row counts (including soft-deleted rows via `ROW_COUNT_QUERY`). This doesn't match what `catalog list` and `audio list` show, causing confusion.

**Change:** Add active (non-deleted) counts alongside totals, displayed as `"N active / M total"`.

### File: `src/stream_of_worship/admin/db/schema.py`

Add a new query constant after `ROW_COUNT_QUERY` (line ~175):

```python
ACTIVE_ROW_COUNT_QUERY = """
SELECT
    'songs' as table_name,
    COUNT(*) as row_count
FROM songs
WHERE deleted_at IS NULL
UNION ALL
SELECT
    'recordings' as table_name,
    COUNT(*) as row_count
FROM recordings
WHERE deleted_at IS NULL;
"""
```

### File: `src/stream_of_worship/admin/db/models.py`

Add a new field to `DatabaseStats` (line ~337):

```python
@dataclass
class DatabaseStats:
    table_counts: dict[str, int] = field(default_factory=dict)
    active_counts: dict[str, int] = field(default_factory=dict)  # NEW
    integrity_ok: bool = True
    # ... rest unchanged
```

Add corresponding properties after `total_recordings` (line ~375):

```python
@property
def active_songs(self) -> int:
    return self.active_counts.get("songs", 0)

@property
def active_recordings(self) -> int:
    return self.active_counts.get("recordings", 0)
```

### File: `src/stream_of_worship/admin/db/client.py`

In `get_stats()` (line ~370), after the existing `ROW_COUNT_QUERY` execution, add:

```python
cursor.execute(ACTIVE_ROW_COUNT_QUERY)
active_counts = {row[0]: row[1] for row in cursor.fetchall()}
```

Update the `return DatabaseStats(...)` call to include `active_counts=active_counts`.

Import `ACTIVE_ROW_COUNT_QUERY` from `schema` at the top of the file.

### File: `src/stream_of_worship/admin/commands/db.py`

Update the display rows at lines 242-243:

```python
# Before
stats_table.add_row("Songs", f"{stats.total_songs:,}")
stats_table.add_row("Recordings", f"{stats.total_recordings:,}")

# After
stats_table.add_row("Songs", f"{stats.active_songs:,} active / {stats.total_songs:,} total")
stats_table.add_row("Recordings", f"{stats.active_recordings:,} active / {stats.total_recordings:,} total")
```

---

## Improvement 4: Harden `db init --force` with Safety Guards

**Problem:** `db init --force` (lines 77-116 of `db.py`) already resets the local DB and re-syncs from Turso when configured. But it has three safety gaps:
1. No backup before deleting the local DB — data loss if Turso is empty or sync fails
2. No `_verify_turso_health()` check — will delete local data even if Turso is unreachable or empty
3. No confirmation prompt — a single `--force` flag triggers an irreversible destructive operation

The v1 spec proposed adding `--from-turso` to `db sync`, but this duplicates what `db init --force` already does. Instead, we harden the existing command.

### Changes to v1 spec

**Dropped: 4a (`detect_local_only_rows()`)** — The proposed `created_at < first_sync_at` heuristic is broken. Rows pulled FROM Turso during the first sync also have `created_at` timestamps older than `first_sync_at` (that's most of the data). libsql doesn't tag row provenance, so there's no reliable local-only way to distinguish row origin.

**Dropped: 4b (`--from-turso` on `db sync`)** — Duplicates `db init --force`. One command for this operation, not two.

**Replaced with: Harden `db init --force`** — Add the safety measures the v1 spec correctly identified as necessary, but apply them to the existing command.

### File: `src/stream_of_worship/admin/commands/db.py`

Replace the `db init --force` Turso re-sync path (lines 77-116) with:

```python
if force and db_path.exists():
    turso_url = config.effective_turso_url
    turso_token = os.environ.get("SOW_TURSO_TOKEN")

    if turso_url and LIBSQL_AVAILABLE:
        # Resetting with Turso configured — add safety guards
        sync_service = SyncService(
            db_path=db_path,
            turso_url=turso_url,
            turso_token=turso_token,
        )

        # Safety: verify Turso is healthy before destroying local data
        if not sync_service._verify_turso_health():
            console.print(
                "[red]Cannot reset: Turso remote appears unhealthy or empty.[/red]"
            )
            console.print(
                "[dim]If you want a local-only reset, disconnect Turso first.[/dim]"
            )
            raise typer.Exit(1)

        # Safety: compare counts to warn about potential data loss
        local_client = DatabaseClient(db_path)
        local_stats = local_client.get_stats()
        local_client.close()

        turso_songs, turso_recordings = _get_turso_counts(turso_url, turso_token)
        if local_stats.total_songs > turso_songs or local_stats.total_recordings > turso_recordings:
            console.print(
                f"[yellow]Warning: Local DB has more rows than Turso "
                f"(songs: {local_stats.total_songs} local vs {turso_songs} remote, "
                f"recordings: {local_stats.total_recordings} local vs {turso_recordings} remote). "
                f"Local-only rows will be lost.[/yellow]"
            )

        # Safety: confirmation prompt
        if not typer.confirm("This will delete the local DB and re-sync from Turso. Continue?"):
            raise typer.Exit(0)

        # Safety: backup before deleting
        backup_dir = sync_service._backup_local_db()
        console.print(f"[dim]Backed up to {backup_dir}[/dim]")

        sync_service._delete_local_db()
        console.print(f"[red]Resetting database at {db_path}...[/red]")

        client = DatabaseClient(
            db_path,
            turso_url=turso_url,
            turso_token=turso_token,
        )
        try:
            client.sync()
            console.print("[green]Database reset and synced from Turso![/green]")
        except SyncError as e:
            console.print(f"[red]Turso sync failed: {e}[/red]")
            console.print(f"[yellow]Your backup is at {backup_dir}[/yellow]")
            raise typer.Exit(1)
    else:
        # No Turso — simple local reset (original behavior, no backup needed)
        console.print(f"[red]Resetting database at {db_path}...[/red]")
        db_path.unlink()
        for f in db_path.parent.glob(f"{db_path.name}-*"):
            if f.is_dir():
                import shutil
                shutil.rmtree(f)
            else:
                f.unlink(missing_ok=True)

        client = DatabaseClient(db_path)
        client.reset_database()
        console.print("[green]Database reset and re-initialized successfully![/green]")
```

Key behavioral changes from the current code:
- **With Turso:** backup → health check → count comparison → confirmation → delete → sync. On sync failure, points user to the backup instead of silently falling back to local-only.
- **Without Turso:** unchanged (simple local reset, no backup needed since there's no remote to lose parity with).

### Helper: `_get_turso_counts()`

Add a module-level helper (near other helpers in `db.py`):

```python
def _get_turso_counts(turso_url: str, turso_token: Optional[str]) -> tuple[int, int]:
    """Query Turso for song and recording counts via in-memory sync."""
    import libsql

    conn = libsql.connect(
        ":memory:",
        sync_url=turso_url,
        auth_token=turso_token or os.environ.get("SOW_TURSO_TOKEN", ""),
    )
    conn.sync()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM songs")
    songs = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM recordings")
    recordings = cursor.fetchone()[0]
    conn.close()
    return songs, recordings
```

### Import additions in `db.py`

```python
from stream_of_worship.admin.services.sync import SyncService
```

(Already imported: `DatabaseClient`, `SyncError`, `LIBSQL_AVAILABLE`, `os`, `typer`)

---

## Verification

```bash
# 1. Filename truncation — visually confirm no wrapping in audio list
sow_admin audio list

# 2. --format count — should output raw integers
sow_admin catalog list --format count
sow_admin audio list --format count

# 3. --format count with filters
sow_admin catalog list --album "敬拜讚美15" --format count
sow_admin audio list --status completed --format count

# 4. db status — should show "N active / M total"
sow_admin db status

# 5. Cross-check: count format should match ids format
test $(sow_admin catalog list --format count) -eq $(sow_admin catalog list --format ids | wc -l | tr -d ' ')
test $(sow_admin audio list --format count) -eq $(sow_admin audio list --format ids | wc -l | tr -d ' ')

# 6. db init --force — should prompt, backup, health-check, then re-sync
sow_admin db init --force  # answer "n" to verify prompt works, then "y" to test full flow

# 7. Run existing tests
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/ -v
```

## Files Changed

| File | Changes |
|------|---------|
| `src/stream_of_worship/admin/commands/audio.py` | Truncate filename to 30 chars with `None` guard; add `--format count` branch |
| `src/stream_of_worship/admin/commands/catalog.py` | Add `--format count` branch; update help text |
| `src/stream_of_worship/admin/db/schema.py` | Add `ACTIVE_ROW_COUNT_QUERY` |
| `src/stream_of_worship/admin/db/models.py` | Add `active_counts` field and `active_songs`/`active_recordings` properties |
| `src/stream_of_worship/admin/db/client.py` | Execute `ACTIVE_ROW_COUNT_QUERY` in `get_stats()` |
| `src/stream_of_worship/admin/commands/db.py` | Display `"N active / M total"` format; harden `db init --force` with backup, health check, count comparison, and confirmation |

## Changes from v1

| v1 Item | v2 Disposition | Rationale |
|---------|---------------|-----------|
| 1. Truncate filenames | **Kept** (+ `None` guard) | `rec.original_filename` is the only column without `or "-"` fallback |
| 2. `--format count` | **Kept as-is** | No concerns |
| 3. Active/total in `db status` | **Kept as-is** | No concerns |
| 4a. `detect_local_only_rows()` | **Dropped** | Heuristic is broken — `created_at < first_sync_at` flags Turso-originated rows too |
| 4b. `--from-turso` on `db sync` | **Dropped** | Duplicates `db init --force`; one command, not two |
| 4c. Operational caveat | **Replaced** | Automated count comparison + confirmation prompt instead of manual audit |
| — | **New: Harden `db init --force`** | Adds backup, health check, count comparison, confirmation prompt |
