# Admin CLI: `backup-r2` Progress Indicator + MB Units

## Overview

Improve the `sow-admin maintenance backup-r2` command with:
1. A **Rich progress bar** during the backup writing phase (downloading objects to tar chunks)
2. **MB units** instead of raw bytes in all backup/verify/restore CLI output (manifest.json on disk stays in bytes)

## Decisions

| Topic | Decision |
|---|---|
| Progress scope | Backup writing phase only (inventory building already prints start/end messages) |
| Progress style | Rich `Progress` bar with: spinner, bar, %, `{completed}/{total} objects`, bytes transferred, transfer speed, ETA |
| Progress console | Same as existing pattern: stderr when `--format json`, stdout otherwise |
| MB unit | Decimal MB (1 MB = 1,000,000 bytes), integer (e.g. `12894`) — matches existing `_bytes_to_mb()` |
| MB in JSON | Yes — replace `total_bytes` with `total_mb` (integer) in JSON stdout output |
| Manifest.json on disk | Keep raw bytes for precision; only CLI display converts to MB |
| Per-object sizes in restore plan | Convert to MB for consistency. Small objects (e.g. 5 bytes) will display as `0 MB` |

## Files to Modify

1. `src/stream_of_worship/admin/services/r2_backup.py` — add optional `on_progress` callback to `write_backup()`
2. `src/stream_of_worship/admin/commands/maintenance.py` — wire up Rich Progress, convert bytes→MB in output
3. `tests/admin/test_r2_backup_commands.py` — update JSON field assertions (`total_bytes` → `total_mb`)

---

## Change A: Progress Callback in `write_backup()` (`r2_backup.py`)

### A.1 Add `on_progress` parameter

```python
from typing import Callable, Optional

# ... existing imports ...

def write_backup(
    r2_client: R2Client,
    output_dir: Path,
    inventory: Inventory,
    chunk_size_bytes: int = DEFAULT_CHUNK_SIZE_BYTES,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> BackupResult:
    """Write a full backup to the output directory.

    ... existing docstring ...

    Args:
        ...
        on_progress: Optional callback invoked after each object is written.
            Receives (objects_completed, bytes_completed).
    """
```

### A.2 Invoke callback in the download loop

In the `for idx, inv_obj in enumerate(inventory.objects):` loop (line 413), after `current_chunk_bytes += inv_obj.size` (line 429), add:

```python
            if on_progress is not None:
                on_progress(idx + 1, sum(o["size"] for o in manifest_objects))
```

**Note:** The bytes_completed is computed from the manifest objects already written, not `current_chunk_bytes` (which is per-chunk). This gives the true cumulative total.

### A.3 No-op when callback is None

When `on_progress` is `None` (default), behavior is identical to current code. All existing tests pass without modification.

---

## Change B: Rich Progress Bar in `backup_r2` Command (`maintenance.py`)

### B.1 Imports

Add to existing imports:

```python
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
```

### B.2 Wrap `write_backup()` call in Progress context

Replace the current `write_backup()` call (lines 644-653) with:

```python
    try:
        with Progress(
            SpinnerColumn(),
            BarColumn(),
            TaskProgressColumn(),
            TextColumn("[progress.description]{task.description}"),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=progress_console,
        ) as progress:
            task = progress.add_task(
                f"Backing up {inventory.object_count} objects...",
                total=inventory.total_bytes,
            )

            def _on_progress(objects_done: int, bytes_done: int) -> None:
                progress.update(
                    task,
                    completed=bytes_done,
                    description=f"Backing up {objects_done}/{inventory.object_count} objects...",
                )

            result = write_backup(
                r2_client=r2_client,
                output_dir=output,
                inventory=inventory,
                chunk_size_bytes=chunk_size_bytes,
                on_progress=_on_progress,
            )
    except BackupError as e:
        console.print(f"[red]Backup failed: {e}[/red]")
        raise typer.Exit(1)
```

**Design notes:**
- `total=inventory.total_bytes` on the progress task so `DownloadColumn` and `TransferSpeedColumn` show meaningful byte values.
- The description updates with object count (`objects_done/total`) while the bar tracks bytes.
- `progress_console` is already set up (lines 632-635) to route to stderr when `--format json`.

---

## Change C: Bytes → MB in CLI Output

### C.1 `backup-r2` summary table

Modify `_print_backup_summary_table` (line 593):

```python
def _print_backup_summary_table(result, output_dir: Path) -> None:
    table = Table(title="R2 Backup Complete")
    table.add_column("Field")
    table.add_column("Value")
    table.add_row("Output directory", str(output_dir))
    table.add_row("Object count", str(result.object_count))
    table.add_row("Total MB", str(_bytes_to_mb(result.total_bytes)))
    table.add_row("Chunk count", str(result.chunk_count))
    console.print(table)
```

### C.2 `backup-r2` JSON output

Modify the JSON dict (line 656):

```python
        _print_json_to_stdout(
            {
                "output_dir": str(result.output_dir),
                "object_count": result.object_count,
                "total_mb": _bytes_to_mb(result.total_bytes),
                "chunk_count": result.chunk_count,
            }
        )
```

### C.3 `verify-r2-backup` success message

Modify the success message (line 695):

```python
            console.print(
                f"[green]Verification OK: {result.object_count} objects, "
                f"{_bytes_to_mb(result.total_bytes)} MB, {result.chunk_count} chunks[/green]"
            )
```

### C.4 `verify-r2-backup` JSON output

Modify the JSON dict (line 688):

```python
        _print_json_to_stdout(
            {
                "ok": result.ok,
                "errors": result.errors,
                "object_count": result.object_count,
                "total_mb": _bytes_to_mb(result.total_bytes),
                "chunk_count": result.chunk_count,
            }
        )
```

### C.5 `restore-r2` plan table

Modify the plan table (line 793-799):

```python
        table = Table(title="Restore Plan" + (" (DRY RUN)" if not confirm else ""))
        table.add_column("Key")
        table.add_column("Action")
        table.add_column("Size (MB)")
        for row in rows_data:
            table.add_row(row["key"], row["action"], str(_bytes_to_mb(row["size"])))
        console.print(table)
```

### C.6 `restore-r2` JSON output (dry-run plan)

Modify the plan rows_data (line 774-782):

```python
    rows_data = [
        {
            "key": r.key,
            "action": r.action,
            "size_mb": _bytes_to_mb(r.size),
            "chunk_index": r.chunk_index,
        }
        for r in plan.rows
    ]
```

---

## Change D: Test Updates

### D.1 `test_r2_backup_commands.py`

**`test_backup_json_output_is_parseable`** (line 176-203):

```python
        assert data["object_count"] == 1
        assert data["total_mb"] == 0   # 5 bytes rounds to 0 MB
        assert data["chunk_count"] == 1
        assert data["output_dir"] == str(output)
        # total_bytes no longer present in JSON output
        assert "total_bytes" not in data
```

**`test_verify_json_output`** (line 236-250):

Add assertion:

```python
        assert data["ok"] is True
        assert data["object_count"] == 1
        assert "total_mb" in data
        assert "total_bytes" not in data
```

**`test_restore_json_output`** (line 396-422):

Update assertion:

```python
        assert data["plan"][0]["action"] == "create"
        assert "size_mb" in data["plan"][0]
        assert "size" not in data["plan"][0]
```

### D.2 `test_r2_backup.py`

No changes needed. `write_backup()` defaults `on_progress=None`, so all existing tests pass.

### D.3 New test: progress callback is invoked

Add to `test_r2_backup.py`:

```python
class TestWriteBackupProgress:
    def test_on_progress_called_with_correct_counts(self, tmp_path):
        """write_backup calls on_progress with correct object and byte counts."""
        objects = [
            {"key": "a/file1", "size": 100, "etag": "etag1", "data": b"x" * 100},
            {"key": "a/file2", "size": 200, "etag": "etag2", "data": b"y" * 200},
        ]
        r2 = _make_r2_mock(objects)
        inventory = build_inventory(r2)
        output = tmp_path / "backup"

        calls = []
        def _on_progress(objects_done: int, bytes_done: int) -> None:
            calls.append((objects_done, bytes_done))

        result = write_backup(r2, output, inventory, on_progress=_on_progress)

        assert len(calls) == 2
        assert calls[0] == (1, 100)
        assert calls[1] == (2, 300)
        assert result.object_count == 2
        assert result.total_bytes == 300
```

---

## Verification

```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/test_r2_backup.py tests/admin/test_r2_backup_commands.py -v
```

---

## Edge Cases and Considerations

1. **Empty bucket**: `inventory.object_count == 0`. Progress bar shows `0/0 objects` and completes immediately. No division by zero issues since `total=0` is valid in Rich Progress.

2. **Single object**: Progress bar shows `1/1 objects` and completes at 100%.

3. **`--format json`**: Progress bar renders to stderr (via `progress_console`). JSON output goes to stdout. This matches the existing pattern at lines 632-635.

4. **Small objects in restore plan**: A 5-byte object displays as `0 MB` in the plan table. This is the trade-off of using MB for all display values. The user explicitly requested MB conversion for restore-r2 output.

5. **Manifest.json on disk**: The `manifest.json` file inside the backup directory keeps `total_bytes` (raw bytes) and per-object `size` (raw bytes). Only the CLI's stdout/table output converts to MB.

6. **Existing `_bytes_to_mb`**: Reuses the existing helper (maintenance.py:74) which uses `round(total_bytes / 1_000_000)`. No new helper needed.

7. **Progress bar and chunk rotation**: The progress callback is invoked after each object is fully written to the tar, so the byte count is accurate even when chunk rotation happens mid-loop.

8. **Error during backup**: If `write_backup()` raises `BackupError`, the `Progress` context manager exits cleanly and the error is caught by the existing `except BackupError` block.
