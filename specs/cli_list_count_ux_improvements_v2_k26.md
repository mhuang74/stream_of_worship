# CLI List & Count UX Improvements (v2)

## Context

` sow_admin catalog list | wc -l ` and ` sow_admin audio list | wc -l ` produce misleading counts because:
1. Rich Table output adds ~5 non-data lines (borders, header, title)
2. `audio list` has 9 columns — long filenames cause cell wrapping, further inflating `wc -l`
3. `db status` shows total row counts (including soft-deleted), while `catalog list` and `audio list` filter `deleted_at IS NULL` — the numbers don't match and there's no way to see why

These four improvements address the confusion.

---

## Improvement 1: Truncate Filenames in `audio list`

**Problem:** The 9-column Rich table wraps long filenames across multiple terminal lines, making `wc -l` unreliable and the table hard to scan.

**Change:** Truncate `original_filename` to 30 characters (27 + `...`) in table display.

### File: `src/stream_of_worship/admin/commands/audio.py`

At line ~1127, where `rec.original_filename` is passed to `table.add_row()`:

```python
# Before
table.add_row(
    album_name or "-",
    song_title or "-",
    visibility_text,
    size_str,
    lrc_text,
    duration_str,
    song_id,
    rec.original_filename,
    rec.hash_prefix,
)

# After
filename_display = rec.original_filename
if filename_display and len(filename_display) > 30:
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
       console.print(str(len(songs)))
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
       console.print(str(len(enriched)))
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

Add a new field to `DatabaseStats` (line ~351):

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
cursor.execute(ROW_COUNT_QUERY)
table_counts = {row[0]: row[1] for row in cursor.fetchall()}

# Add active counts
cursor.execute(ACTIVE_ROW_COUNT_QUERY)
active_counts = {row[0]: row[1] for row in cursor.fetchall()}
```

Update the `return DatabaseStats(...)` call (line ~393) to include `active_counts=active_counts`.

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

## Improvement 4: Explicit Warning on First Sync from Existing Local DB (v2)

**Problem:** libsql's bidirectional sync pushes local changes to Turso and pulls remote changes. If a local DB was created (via `db init` or populated manually) before sync was configured, the user may not realize that the first `db sync` will push their local data to Turso.

A heuristic to detect "local-only rows" after sync is unreliable: any row created before the first sync will have `created_at < first_sync_at`, triggering false positives. This creates alert fatigue and misleads users.

**Solution (v2):** Warn explicitly at the first sync (when `last_sync_at` is `None` but a local DB exists). Explain the behavior and offer a clear path to reset from Turso if desired.

### 4a: Enhanced Onboarding Warning

Replace the existing note in `db.py` (lines ~447-449) with a more informative warning:

```python
if config.db_path.exists() and not sync_status.last_sync_at:
    console.print("\n[yellow]⚠ Note: Local database exists but has no sync history.[/yellow]")
    console.print(
        "[yellow]  Bidirectional sync will push local rows to Turso and pull remote rows.[/yellow]"
    )
    console.print(
        "[dim]  If this local DB contains test data or you want to discard it in favor of Turso,[/dim]"
    )
    console.print(
        "[dim]  run: sow_admin db sync --reset-from-turso[/dim]"
    )
    console.print(
        "[dim]  (backs up local DB, then re-downloads clean from Turso).[/dim]"
    )
    console.print()
```

### 4b: Add `--reset-from-turso` Flag for Clean Re-Sync

A safe, explicit way to drop the local DB and re-sync clean from Turso — making Turso the single source of truth. Flag renamed from `--from-turso` to `--reset-from-turso` to emphasize destructiveness.

#### File: `src/stream_of_worship/admin/commands/db.py`

Add a new option to `sync_db()` (line ~386):

```python
reset_from_turso: bool = typer.Option(
    False,
    "--reset-from-turso",
    help="Drop local DB and re-sync clean from Turso (Turso becomes single source of truth)",
),
```

When `--reset-from-turso` is set, before syncing:

```python
if reset_from_turso:
    if db_path.exists():
        console.print(f"[yellow]Backing up local DB before reset...[/yellow]")
        sync_service = SyncService(
            db_path=db_path,
            turso_url=effective_url,
            turso_token=config.effective_turso_token,
        )
        backup_dir = sync_service.backup_local_db()
        sync_service.delete_local_db()
        console.print("[green]Local DB cleared. Syncing from Turso...[/green]")
    else:
        console.print("[yellow]Local DB does not exist. Performing normal sync.[/yellow]")
```

Add confirmation prompt (before the backup logic):

```python
if reset_from_turso:
    typer.confirm(
        "This will delete the local database and re-download from Turso.\n"
        "Any additions or modifications not yet synced will be lost.\n"
        "A backup will be created automatically. Continue?",
        abort=True,
    )
```

After successful sync with `--reset-from-turso`, print the backup directory:

```python
if reset_from_turso and backup_dir:
    console.print(f"\n[green]Local DB backed up to: {backup_dir}[/green]")
```

#### File: `src/stream_of_worship/admin/services/sync.py`

Make existing private methods public (or add wrapper):

```python
def backup_local_db(self) -> Path:
    """Create a timestamped backup of the local DB and sidecar files.

    Returns:
        Path to the backup directory.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")  # Added microseconds
    backup_dir = self.db_path.parent / f"{self.db_path.name}.bak-{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=False)

    if self.db_path.exists():
        shutil.copy2(self.db_path, backup_dir / self.db_path.name)
    for sibling in self.db_path.parent.glob(f"{self.db_path.name}-*"):
        if sibling.is_file():
            shutil.copy2(sibling, backup_dir / sibling.name)

    return backup_dir


def delete_local_db(self) -> None:
    """Delete local DB file and known sidecar files only."""
    if self.db_path.exists():
        self.db_path.unlink()

    sidecar_patterns = ["-wal", "-shm", "-journal", "-info"]
    for pattern in sidecar_patterns:
        sidecar = self.db_path.parent / f"{self.db_path.name}{pattern}"
        if sidecar.exists():
            if sidecar.is_dir():
                shutil.rmtree(sidecar)
            else:
                sidecar.unlink(missing_ok=True)


def reset_from_remote(self) -> SyncResult:
    """Drop local DB, backup, and re-sync clean from Turso.

    Raises:
        SyncNetworkError: If Turso is unhealthy or empty
    """
    if not self._verify_turso_health():
        raise SyncNetworkError(
            "Cannot reset: Turso remote appears unhealthy or empty."
        )
    self.backup_local_db()
    self.delete_local_db()
    return self.execute_sync()
```

**Note:** Changed backup timestamp to include microseconds (`%Y%m%dT%H%M%S%f`) to avoid collisions on rapid successive invocations.

**Note:** Changed `delete_local_db()` to only delete known sidecar patterns (`-wal`, `-shm`, `-journal`, `-info`) instead of a broad `db_path.name + "-*"` glob, to avoid accidentally deleting non-DB files.

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

# 6. First sync warning (when local DB exists but no sync history)
#    Create a new local DB first:
sow-admin db init
sow-admin db sync  # Should show enhanced warning

# 7. --reset-from-turso clean re-sync
sow_admin db sync --reset-from-turso  # Should prompt for confirmation, backup, then re-sync

# 8. Run existing tests
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/ -v
```

---

## Files Changed

| File | Changes |
|------|---------|
| `src/stream_of_worship/admin/commands/audio.py` | Truncate filename to 30 chars; add `--format count` branch |
| `src/stream_of_worship/admin/commands/catalog.py` | Add `--format count` branch; update help text |
| `src/stream_of_worship/admin/db/schema.py` | Add `ACTIVE_ROW_COUNT_QUERY` |
| `src/stream_of_worship/admin/db/models.py` | Add `active_counts` field and `active_songs`/`active_recordings` properties |
| `src/stream_of_worship/admin/db/client.py` | Execute `ACTIVE_ROW_COUNT_QUERY` in `get_stats()` |
| `src/stream_of_worship/admin/commands/db.py` | Display `"N active / M total"` format; add `--reset-from-turso` flag; enhanced onboarding warning; add confirmation prompt; print backup path |
| `src/stream_of_worship/admin/services/sync.py` | Add public `backup_local_db()`, `delete_local_db()`, `reset_from_remote()` methods; use microsecond timestamp; restrict sidecar deletion to known patterns |

---

## Changes from v1

| Aspect | v1 | v2 |
|--------|----|----|
| Local-only row detection | `detect_local_only_rows()` heuristic using `created_at < first_sync` | Removed — heuristic was unreliable and caused false positives |
| `--from-turso` flag | `--from-turso` (v1) | `--reset-from-turso` (v2) — renamed for clarity |
| Confirmation prompt | Mentioned in caveat, not in code | Explicit `typer.confirm()` before destructive action |
| First sync warning | Simple note | 5-line explanation with guidance |
| Backup timestamp | `%Y%m%dT%H%M%S` (1-second resolution) | `%Y%m%dT%H%M%S%f` (microsecond resolution) to avoid collisions |
| Sidecar deletion | Glob `db_name-*` (broad) | Specific patterns only (`-wal`, `-shm`, `-journal`, `-info`) |
| Backup path output | Not printed | Printed after successful `--reset-from-turso` |