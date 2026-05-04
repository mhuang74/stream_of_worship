# CLI List & Count UX Improvements

## Context

`sow_admin catalog list | wc -l` and `sow_admin audio list | wc -l` produce misleading counts because:
1. Rich Table output adds ~5 non-data lines (borders, header, title)
2. `audio list` has 9 columns — long filenames cause cell wrapping, further inflating `wc -l`
3. `db status` shows total row counts (including soft-deleted), while `catalog list` and `audio list` filter `deleted_at IS NULL` — the numbers don't match and there's no way to see why

Additionally, bidirectional libsql sync doesn't clean up local-only rows — if Turso is the source of truth, local-only recordings can silently inflate counts.

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

## Improvement 4: Detect and Surface Local-Only Rows After Sync

**Problem:** libsql's bidirectional sync pushes local changes to Turso and pulls remote changes, but it does **not** delete local rows that don't exist on the remote. If Turso is the single source of truth (e.g., recordings were deleted remotely, or the local DB was seeded with test data before syncing), those local-only rows persist silently — inflating counts and appearing in `audio list`.

**Current behavior** (from `client.py:144-231`):
- `conn.sync()` at line 212 is bidirectional — it will *push* local-only rows to Turso, not remove them
- The second `conn.sync()` at line 222 pushes any column migrations back
- There is no reconciliation step that compares local vs remote row sets

### 4a: Post-Sync Local-Only Row Detection

After sync completes, query for recordings/songs that exist locally but not on Turso. Surface a warning in the sync output.

#### File: `src/stream_of_worship/admin/db/client.py`

Add a method to `DatabaseClient` (after `sync()`, line ~231):

```python
def detect_local_only_rows(self) -> dict[str, int]:
    """Detect rows that may be local-only (not from Turso).

    Heuristic: rows where created_at == updated_at and created_at is before
    the first successful sync timestamp. These were likely created locally
    before sync was configured.

    Returns:
        Dict mapping table name to count of suspected local-only rows.
    """
    cursor = self.connection.cursor()
    cursor.execute("SELECT value FROM sync_metadata WHERE key = 'first_sync_at'")
    row = cursor.fetchone()
    if not row:
        return {}

    first_sync = row[0]
    counts = {}
    for table in ("songs", "recordings"):
        cursor.execute(
            f"SELECT COUNT(*) FROM {table} "
            f"WHERE created_at < ? AND deleted_at IS NULL",
            (first_sync,),
        )
        count = cursor.fetchone()[0]
        if count > 0:
            counts[table] = count
    return counts
```

This requires recording `first_sync_at` during the initial sync. In `sync()` (line ~231), after the first successful `conn.sync()`:

```python
cursor.execute("SELECT value FROM sync_metadata WHERE key = 'first_sync_at'")
if not cursor.fetchone():
    self.update_sync_metadata("first_sync_at", datetime.now().isoformat())
```

#### File: `src/stream_of_worship/admin/commands/db.py`

In the `sync_db` command, after a successful sync (line ~479), call `detect_local_only_rows()` and print a warning if any are found:

```python
local_only = client.detect_local_only_rows()
if local_only:
    console.print()
    console.print("[yellow]Warning: Detected rows that may be local-only (pre-sync):[/yellow]")
    for table, count in local_only.items():
        console.print(f"  {table}: {count} rows")
    console.print("[dim]Use 'db sync --from-turso' to reset local DB from Turso.[/dim]")
```

### 4b: Add `--from-turso` Flag for Clean Re-Sync

A safe, explicit way to drop the local DB and re-sync clean from Turso — making Turso the authoritative source.

#### File: `src/stream_of_worship/admin/commands/db.py`

Add a new option to `sync_db()` (line ~386):

```python
from_turso: bool = typer.Option(
    False,
    "--from-turso",
    help="Drop local DB and re-sync clean from Turso (Turso becomes single source of truth)",
),
```

When `--from-turso` is set, before syncing:

```python
if from_turso:
    if db_path.exists():
        console.print(f"[yellow]Backing up local DB before reset...[/yellow]")
        # Reuse existing _backup_local_db and _delete_local_db from SyncService
        sync_service = get_sync_service_from_config(config)
        sync_service._backup_local_db()
        sync_service._delete_local_db()
        console.print("[green]Local DB cleared. Syncing from Turso...[/green]")
```

#### File: `src/stream_of_worship/admin/services/sync.py`

The `_backup_local_db()` (already exists) and `_delete_local_db()` (already exists) methods handle backup and cleanup. They are currently private but already do exactly what's needed. Either:
- Make them public (`backup_local_db()`, `delete_local_db()`)
- Or add a public `reset_from_remote()` method that wraps both + re-sync

Preferred approach — add to `SyncService`:

```python
def reset_from_remote(self) -> SyncResult:
    """Drop local DB, backup, and re-sync clean from Turso.

    Raises:
        SyncNetworkError: If Turso is unhealthy or empty
    """
    if not self._verify_turso_health():
        raise SyncNetworkError(
            "Cannot reset: Turso remote appears unhealthy or empty."
        )
    self._backup_local_db()
    self._delete_local_db()
    return self.execute_sync()
```

Then in `db.py`, `--from-turso` simply calls `sync_service.reset_from_remote()`.

### 4c: Caveat — Pre-Migration Local Recordings Audit

If the local DB has recordings that were created before Turso sync was configured, a `--from-turso` reset would **lose** those recordings (they'd only exist in the backup). Before adding `--from-turso`, the user should audit whether any local recordings need to be pushed to Turso first.

This is an operational step, not a code change:

```bash
# Check for recordings that might only exist locally
sow_admin audio list --sort imported

# If any recordings pre-date Turso setup, ensure they're synced UP first:
sow_admin db sync  # bidirectional — pushes local rows to Turso

# Then verify on Turso
turso db shell sow-catalog "select count(1) from recordings;"

# Only after confirming Turso has all data, use --from-turso
sow_admin db sync --from-turso
```

The `--from-turso` command itself should print a confirmation prompt (using `typer.confirm()`) warning that local-only data will be lost.

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

# 6. Sync with local-only detection
sow_admin db sync  # should print warning if local-only rows detected

# 7. --from-turso clean re-sync (after confirming Turso has all data)
sow_admin db sync --from-turso  # should prompt for confirmation, backup, then re-sync

# 8. Run existing tests
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/ -v
```

## Files Changed

| File | Changes |
|------|---------|
| `src/stream_of_worship/admin/commands/audio.py` | Truncate filename to 30 chars; add `--format count` branch |
| `src/stream_of_worship/admin/commands/catalog.py` | Add `--format count` branch; update help text |
| `src/stream_of_worship/admin/db/schema.py` | Add `ACTIVE_ROW_COUNT_QUERY` |
| `src/stream_of_worship/admin/db/models.py` | Add `active_counts` field and `active_songs`/`active_recordings` properties |
| `src/stream_of_worship/admin/db/client.py` | Execute `ACTIVE_ROW_COUNT_QUERY` in `get_stats()`; add `detect_local_only_rows()`; record `first_sync_at` |
| `src/stream_of_worship/admin/commands/db.py` | Display `"N active / M total"` format; add `--from-turso` flag; surface local-only row warnings |
| `src/stream_of_worship/admin/services/sync.py` | Add `reset_from_remote()` public method |
