# Admin CLI Maintenance Commands Improvements (Revised Plan)

## Overview

Improve the `sow-admin maintenance` commands with better output formatting (MB units, truncated timestamps), sensible default limits, and a no-args listing mode for `repair-songsets`.

## Decisions (original + clarifications)

| Topic | Decision |
|---|---|
| MB unit | Decimal MB (1 MB = 1,000,000 bytes), integer (e.g. `12`) |
| MB in JSON | Yes — replace `total_bytes` with `total_mb` (integer) and `r2_bytes` with `r2_mb` (integer) in both table and JSON |
| last_modified / created_at / deleted_at format | Space-separated datetime truncated to seconds: `YYYY-MM-DD HH:MM:SS` |
| Default limit scope | All list/diagnose commands: `list-soft-deletes`, `list-r2-waste`, `diagnose-render-failures`. Destructive purge commands keep current behavior. |
| `list-r2-waste` ordering | `last_modified DESC` (most recent first); `None` values sort last |
| `repair-songsets` no-args | One row per songset (summary): `songset_id`, `name`, `created_at`, `song_count` (total songs in songset), `user_email` |
| `repair-songsets` listing limit | Default 20, `--all` to list all |
| `repair-songsets --confirm` without target | Error and exit; do not silently list |
| `--format ids` limit | Respect the default 20 limit (use `--all` for all IDs) |
| `"user"` table missing | Let the query fail; admin DB is expected to have webapp tables |

## Files to Modify

1. `src/stream_of_worship/admin/commands/maintenance.py` — helpers, output formatting, command signatures, mode logic
2. `src/stream_of_worship/admin/db/client.py` — new `find_songsets_needing_repair()` method
3. `tests/admin/test_audio_soft_delete_maintenance.py` — update existing tests, add new tests

---

## Change A: Output Formatting Helpers (`maintenance.py`)

### A.1 New helper: `_bytes_to_mb`

```python
def _bytes_to_mb(total_bytes: int) -> int:
    """Convert bytes to decimal MB (1 MB = 1,000,000 bytes), rounded to integer."""
    return round(total_bytes / 1_000_000)
```

### A.2 New helper: `_format_datetime`

```python
def _format_datetime(ts: Optional[str]) -> str:
    """Truncate timestamp to seconds and format as 'YYYY-MM-DD HH:MM:SS'."""
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts)
        return dt.strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return str(ts)
```

### A.3 New helper: `_transform_rows`

```python
_BYTE_FIELDS = {"total_bytes": "total_mb", "r2_bytes": "r2_mb"}
_DATETIME_FIELDS = {"last_modified", "created_at", "deleted_at"}

def _transform_rows(rows: list[dict]) -> list[dict]:
    """Apply display transformations: bytes→MB, truncate datetimes to seconds."""
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
```

### A.4 Modify `_print_manifest`

```python
def _print_manifest(rows: list[dict], format_: str) -> None:
    rows = _transform_rows(rows)
    if format_ == "json":
        _print_json(rows)
        return
    # ... rest unchanged
```

**Impact:** All commands that output `total_bytes`, `r2_bytes`, `last_modified`, `created_at`, or `deleted_at` fields get automatic formatting. This includes `list-soft-deletes`, `list-r2-waste`, `purge-r2-waste`, `list-soft-deletes --with-r2`, and the new `repair-songsets` listing.

**Note:** The `--format ids` path in `list-soft-deletes` returns before `_print_manifest`, so it is unaffected by formatting.

---

## Change B: Default Limit 20 + `--all` Flag

Applies to: `list-soft-deletes`, `list-r2-waste`, `diagnose-render-failures`.

### B.1 `list-soft-deletes`

- `limit: Optional[int] = typer.Option(None, ...)` → `typer.Option(20, ...)`
- Add `all_: bool = typer.Option(False, "--all", help="List all results without limit")`
- Body: `effective_limit = None if all_ else limit`

### B.2 `list-r2-waste`

- Default `limit` to `20`; add `--all`
- Call `_orphan_r2_prefixes` without limit, then `_sort_by_last_modified_desc`, then apply `effective_limit`

### B.3 `diagnose-render-failures`

- Default `limit` to `20`; add `--all`
- Pass `effective_limit = None if all_ else limit` to `find_failed_render_jobs`

### B.4 Commands NOT changed

- `purge-soft-deletes` — keeps existing `--limit` default `None`; `--all` still processes all matching
- `restore-soft-deletes` — no `--limit` flag
- `purge-r2-waste` — no `--limit` flag

---

## Change C: `list-r2-waste` Ordering by `last_modified DESC`

### C.1 New helper: `_sort_by_last_modified_desc`

```python
def _sort_by_last_modified_desc(rows: list[dict]) -> list[dict]:
    """Sort rows by last_modified DESC. None/empty values sort last."""
    def sort_key(row):
        ts = row.get("last_modified")
        if not ts:
            return datetime.min
        try:
            return datetime.fromisoformat(ts)
        except (ValueError, TypeError):
            return datetime.min
    return sorted(rows, key=sort_key, reverse=True)
```

### C.2 Modify `_orphan_r2_prefixes`

Remove the `limit` parameter. The function now always returns all orphan prefixes. Sorting and limiting are handled by the caller.

**Before:**

```python
def _orphan_r2_prefixes(db_client, r2_client, blacklist, limit):
    rows = []
    for summary in r2_client.scan_recording_prefixes(blacklist=blacklist):
        if db_client.recording_row_exists(summary.prefix):
            continue
        references = db_client.count_recording_songset_references(summary.prefix)
        rows.append({...})
        if limit is not None and len(rows) >= limit:
            break
    return rows
```

**After:**

```python
def _orphan_r2_prefixes(db_client, r2_client, blacklist):
    rows = []
    for summary in r2_client.scan_recording_prefixes(blacklist=blacklist):
        if db_client.recording_row_exists(summary.prefix):
            continue
        references = db_client.count_recording_songset_references(summary.prefix)
        rows.append({...})
    return rows
```

### C.3 Update callers

- `list_r2_waste`: no limit arg, sort and limit in command body
- `purge_r2_waste`: remove `None` limit arg

**Performance note:** Removing the early-break means all orphan prefixes are collected before sorting. Since `scan_recording_prefixes` already scans the entire bucket, the only additional cost is DB existence checks for all prefixes (not just until limit is reached). This is unavoidable when sorting by `last_modified DESC`.

---

## Change D: `repair-songsets` No-Args Listing Mode

### D.1 New DB method: `find_songsets_needing_repair` (in `client.py`)

Add to `DatabaseClient`:

```python
def find_songsets_needing_repair(self, limit: Optional[int] = 20) -> list[dict]:
    """Find songsets that have at least one stale songset item.

    Returns one row per songset with: songset_id, name, created_at,
    song_count (total items in songset), user_email.
    """
    cursor = self.connection.cursor()
    sql = """
        SELECT s.id, s.name, s.created_at,
               (SELECT COUNT(*) FROM songset_items si2
                WHERE si2.songset_id = s.id) AS song_count,
                u.email
        FROM songsets s
        LEFT JOIN "user" u ON u.id = s.user_id
        WHERE EXISTS (
            SELECT 1 FROM songset_items si
            LEFT JOIN recordings r ON r.hash_prefix = si.recording_hash_prefix
            WHERE si.songset_id = s.id
              AND si.recording_hash_prefix IS NOT NULL
              AND (r.hash_prefix IS NULL OR r.deleted_at IS NOT NULL)
        )
        ORDER BY s.created_at DESC
    """
    if limit:
        sql += f" LIMIT {int(limit)}"
    cursor.execute(sql)
    return [
        {
            "songset_id": row[0],
            "name": row[1],
            "created_at": to_str(row[2]),
            "song_count": row[3],
            "user_email": row[4] or "",
        }
        for row in cursor.fetchall()
    ]
```

**SQL notes:**

- The `EXISTS` subquery uses the same stale condition as `find_stale_songset_items`: `recording_hash_prefix IS NOT NULL AND (r.hash_prefix IS NULL OR r.deleted_at IS NOT NULL)`.
- `song_count` is a correlated subquery counting ALL `songset_items` for the songset (not just stale ones), per user's choice "Total songs in songset".
- JOIN to `"user"` table (quoted, since `user` is a SQL reserved word) for email.
- `LEFT JOIN "user"` so songsets are still returned even if the user row is missing (email defaults to `""`).
- **Do not catch `UndefinedTable`:** if the `"user"` table does not exist, the command should fail.

### D.2 Modify `repair-songsets` command

**Signature changes:**

- Add `limit: Optional[int] = typer.Option(20, "--limit", min=1)`
- Update `--all` help text: `help="List all songsets needing repair (without --confirm); repair all stale items (with --confirm)"`

**Mode logic:**

```python
@app.command("repair-songsets")
def repair_songsets(
    songset_id: Optional[str] = typer.Option(None, "--songset-id"),
    hash_prefix: Optional[str] = typer.Option(None, "--hash-prefix"),
    all_: bool = typer.Option(False, "--all", help="List all (no --confirm) or repair all (with --confirm)"),
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
    r2_client = _load_r2(config)

    is_repair_mode = songset_id is not None or hash_prefix is not None or (all_ and confirm)

    if not is_repair_mode:
        effective_limit = None if all_ else limit
        rows = db_client.find_songsets_needing_repair(limit=effective_limit)
        _print_manifest(rows, format_)
        return

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
```

**Mode dispatch table:**

| Invocation | Mode | Behavior |
|---|---|---|
| `repair-songsets` | List | List 20 songsets needing repair |
| `repair-songsets --all` | List | List all songsets needing repair |
| `repair-songsets --limit 50` | List | List 50 songsets needing repair |
| `repair-songsets --confirm` | **Error** | Exit with message |
| `repair-songsets --songset-id X` | Repair | Dry-run repair for songset X (existing behavior) |
| `repair-songsets --songset-id X --confirm` | Repair | Repair songset X (existing behavior) |
| `repair-songsets --hash-prefix Y` | Repair | Dry-run repair for prefix Y (existing behavior) |
| `repair-songsets --hash-prefix Y --confirm` | Repair | Repair prefix Y (existing behavior) |
| `repair-songsets --all --confirm` | Repair | Repair all stale items (existing behavior) |

---

## Change E: Imports

Add `datetime` import to `maintenance.py`:

```python
from datetime import datetime
```

---

## Test Plan

### Update existing tests (`tests/admin/test_audio_soft_delete_maintenance.py`)

1. **`test_orphan_r2_prefixes_applies_limit_after_filtering_db_rows`** (line 362-376):
   - Update: `_orphan_r2_prefixes` no longer takes `limit` parameter. Test that it returns ALL orphans (not just 1). The limit is now applied in the caller.
   - Rename to `test_orphan_r2_prefixes_filters_db_rows` or similar.

2. **`test_maintenance_list_soft_deletes_ids`** (line 190-203):
   - `FakeMaintenanceDb.list_soft_deleted_recordings_with_counts` returns 1 recording. Default limit 20 is fine (1 < 20). Test should still pass.

3. **`FakeMaintenanceDb`** (line 163-187):
   - Add `find_songsets_needing_repair` method for repair-songsets tests.

### New tests

4. **`test_print_manifest_converts_total_bytes_to_mb`**:
   - Verify `total_bytes` field is renamed to `total_mb` and value is `round(bytes / 1_000_000)` in both table and JSON output.

5. **`test_print_manifest_formats_last_modified`**:
   - Verify `last_modified` with microseconds is truncated to seconds and formatted as `YYYY-MM-DD HH:MM:SS`.

6. **`test_print_manifest_formats_deleted_at`**:
   - Verify `deleted_at` is truncated to seconds and formatted as `YYYY-MM-DD HH:MM:SS`.

7. **`test_list_soft_deletes_defaults_to_limit_20`**:
   - Mock DB to return 25 rows. Verify only 20 are shown. Verify `--all` shows all 25.

8. **`test_list_r2_waste_defaults_to_limit_20`**:
   - Mock R2 to return 25 orphan prefixes. Verify only 20 are shown. Verify `--all` shows all 25.

9. **`test_list_r2_waste_orders_by_last_modified_desc`**:
   - Mock R2 to return prefixes with different `last_modified` values. Verify output is sorted most-recent-first. Verify `None` values sort last.

10. **`test_diagnose_render_failures_defaults_to_limit_20`**:
    - Mock DB to return 25 failed jobs. Verify only 20 are shown. Verify `--all` shows all 25.

11. **`test_repair_songsets_no_args_lists_songsets_needing_repair`**:
    - Mock `find_songsets_needing_repair` to return sample rows. Verify output has columns: `songset_id`, `name`, `created_at`, `song_count`, `user_email`. Verify default limit 20 is passed.

12. **`test_repair_songsets_all_lists_all_songsets`**:
    - Verify `--all` passes `limit=None` to `find_songsets_needing_repair`.

13. **`test_repair_songsets_all_confirm_repairs_all`**:
    - Verify `--all --confirm` triggers repair mode (calls `_repair_manifest` with `all_=True`), not list mode.

14. **`test_repair_songsets_songset_id_triggers_repair_mode`**:
    - Verify `--songset-id X` triggers repair mode, not list mode.

15. **`test_repair_songsets_confirm_without_target_errors`**:
    - Verify `repair-songsets --confirm` exits non-zero with an error message.

16. **`test_find_songsets_needing_repair_query`**:
    - Use `FakeCursor`/`FakeConnection` pattern (like `test_find_failed_render_jobs_formats_datetimes`). Verify SQL contains the EXISTS subquery and JOIN to `"user"`. Verify `created_at` is converted via `to_str`. Verify `song_count` is the total count (not just stale items).

---

## Edge Cases and Considerations

1. **`--format ids` in `list-soft-deletes`**: Returns before `_print_manifest`, so MB/datetime transformations don't apply. Default limit 20 still applies (only 20 IDs printed). Use `--all` for all IDs.

2. **`r2_bytes` → `r2_mb` in `list-soft-deletes --with-r2`**: This field is not `total_bytes` but represents bytes. Converted for consistency. Column name changes from `r2_bytes` to `r2_mb`.

3. **`deleted_object_count` in purge commands**: This is an object count, not bytes. NOT converted.

4. **`object_count` / `r2_object_count`**: Object counts, not bytes. NOT converted.

5. **`created_at` in `find_failed_render_jobs`**: Currently formatted as ISO string via `to_str`. The `_transform_rows` function will reformat it to `YYYY-MM-DD HH:MM:SS`. This changes existing output but is consistent with the new datetime formatting.

6. **`deleted_at` in `list-soft-deletes`**: Now also reformatted to `YYYY-MM-DD HH:MM:SS` for consistency.

7. **`"user"` table access**: The admin DB client connects to the same Postgres as the webapp. The `"user"` table is created by Better Auth (Drizzle). If the table does not exist, `find_songsets_needing_repair` will fail. **Decision: let it fail** (do not catch `UndefinedTable`).

8. **`repair-songsets --confirm` (no target)**: Now errors with a clear message and exits non-zero.

9. **Column ordering in table output**: `_print_manifest` sorts columns alphabetically. For repair-songsets listing, columns will appear as: `created_at`, `name`, `song_count`, `songset_id`, `user_email`.

10. **Performance of `list-r2-waste` with sorting**: The entire bucket is scanned and all orphan prefixes are collected before sorting and limiting. For large buckets this is slower than the previous early-break behavior, but is required for correct `last_modified DESC` ordering.
