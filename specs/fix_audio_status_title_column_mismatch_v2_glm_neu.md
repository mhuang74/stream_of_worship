# Fix: `Recording.from_row(row)` Missing `description` Argument in `_force_sync_all_pending()`

## Problem Summary

`sow-admin audio status --force-status <STATUS> --sync` crashes with `TypeError: from_row() missing 1 required positional argument: 'description'` because `_force_sync_all_pending()` calls `Recording.from_row(row)` without supplying the required `cursor.description` parameter.

Additionally, the existing `test_status_no_args_pending` test does not verify that the **song title** (not `youtube_url`) appears in the output, leaving a gap where column-drift regressions could go undetected.

## History

### V1 Spec (Partially Stale)

The original spec (`fix_audio_status_title_column_mismatch.md`) described `row[25]` returning `youtube_url` instead of `song_title` in the `check_status()` display loop. **This specific bug has already been fixed** — the display loop at `audio.py:2190-2201` now uses `cursor.description` + `dict(zip(...))` name-based mapping:

```python
description = cursor.description
col_names = [desc[0] for desc in description]

for row in rows:
    row_dict = dict(zip(col_names, row))
    song_title = row_dict.get("song_title") or "-"
    ...
```

### Remaining Bug (This Spec)

The `_force_sync_all_pending()` function at `audio.py:2299` still calls `Recording.from_row(row)` **without** the `description` argument that was made mandatory in the `from_row()` signature:

```python
rec = Recording.from_row(row)   # BUG: missing `description` argument
```

`Recording.from_row()` signature (from `models.py`):

```python
@classmethod
def from_row(cls, row: tuple, description: tuple) -> "Recording":
```

This code path is triggered when a user runs:
```bash
sow-admin audio status --force-status completed --sync
```

It is **not covered by any existing test**, which is why the regression went unnoticed.

## Root Cause

When `Recording.from_row()` was refactored from positional indexing to name-based mapping (via `cursor.description`), the parameter was made mandatory. The call site in `_force_sync_all_pending()` was not updated to pass `cursor.description`, likely because it was not caught during the refactor.

This is the same class of bug as V1 (fragile coupling between row tuple positions and consumer code), but manifests as a missing required argument rather than a wrong numeric index.

## Safety & Risk Assessment

- **Data Integrity**: No data corruption risk. The bug causes a `TypeError` crash before any mutations occur.
- **Write Surface**: `_force_sync_all_pending()` does mutate the DB (updates `analysis_status` / `lrc_status`), but the crash occurs before any writes.
- **Blast Radius**: Single call site at `audio.py:2299`. No other code path is affected.
- **Backward Compatibility**: Fix is transparent — it makes a broken code path work correctly.

## Implementation Plan

### 1. Fix `src/stream_of_worship/admin/commands/audio.py` line 2299

**Current code (broken)**:
```python
    for row in rows:
        rec = Recording.from_row(row)
```

**Fixed code**:
```python
    for row in rows:
        rec = Recording.from_row(row, cursor.description)
```

The `cursor` variable is already in scope — it is used on line 2283 to execute the query.

### 2. Add Regression Test: YouTube URL Must Not Leak into Title Column

**File**: `tests/admin/test_audio_commands.py`
**Class**: `TestStatusCommand`

Insert a recording with a populated `youtube_url` and pending statuses. Assert that the output contains the actual song title and does NOT contain the YouTube URL.

This test would have caught the original V1 bug and guards against future column-drift regressions:

```python
def test_status_no_args_pending_with_youtube_url(self, setup):
    """Regression test: youtube_url must not leak into Title column."""
    db_client = DatabaseClient(setup["db_path"])
    recording = Recording(
        content_hash="a" * 64,
        hash_prefix="aaaaaaaaaaaa",
        song_id="song_001",
        original_filename="test.mp3",
        file_size_bytes=1000,
        imported_at=datetime.now().isoformat(),
        r2_audio_url="s3://sow-audio/test/audio.mp3",
        youtube_url="https://www.youtube.com/watch?v=cyo4B6MsK3g",
        analysis_status="pending",
        lrc_status="pending",
    )
    db_client.insert_recording(recording)

    result = runner.invoke(
        app,
        ["audio", "status", "--config", str(setup["config_path"])],
    )

    assert result.exit_code == 0
    assert "Pending Recordings" in result.output
    assert "測試歌曲" in result.output          # Song title must be present
    assert "cyo4B6MsK3g" not in result.output  # YouTube URL must NOT leak into Title
```

Note: The `setup` fixture already inserts a `Song(id="song_001", title="測試歌曲")`, so the JOIN will resolve `song_title` correctly.

### 3. Add Test: `_force_sync_all_pending` Works After Fix

**File**: `tests/admin/test_audio_commands.py`
**Class**: `TestStatusCommand`

Test that `--force-status` does not crash with `TypeError`:

```python
def test_status_force_status_updates_pending(self, setup):
    """--force-status must not crash (regression: missing description arg)."""
    db_client = DatabaseClient(setup["db_path"])
    recording = Recording(
        content_hash="a" * 64,
        hash_prefix="aaaaaaaaaaaa",
        song_id="song_001",
        original_filename="test.mp3",
        file_size_bytes=1000,
        imported_at=datetime.now().isoformat(),
        r2_audio_url="s3://sow-audio/test/audio.mp3",
        analysis_status="pending",
        lrc_status="pending",
    )
    db_client.insert_recording(recording)

    result = runner.invoke(
        app,
        [
            "audio", "status",
            "--force-status", "failed",
            "--config", str(setup["config_path"]),
        ],
    )

    assert result.exit_code == 0
    assert "Updated" in result.output
```

### 4. Verification Steps

Run targeted tests:
```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/test_audio_commands.py::TestStatusCommand -v
```

Run broader admin tests:
```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/ -v
```

Run full test suite (excluding backend services):
```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ \
  --ignore=tests/services/analysis \
  --ignore=services/qwen3/tests \
  --ignore=services/analysis/tests -v
```

### 5. Graphify Update

After committing, run:
```bash
graphify update .
```

## Files Changed

| File | Action | Description |
|---|---|---|
| `src/stream_of_worship/admin/commands/audio.py` | Modify | Line 2299: add `cursor.description` argument to `Recording.from_row()` |
| `tests/admin/test_audio_commands.py` | Add | `test_status_no_args_pending_with_youtube_url` — regression guard for column drift |
| `tests/admin/test_audio_commands.py` | Add | `test_status_force_status_updates_pending` — regression guard for missing `description` arg |

## Rollback Plan

Single-file revert:

```bash
git checkout src/stream_of_worship/admin/commands/audio.py
git checkout tests/admin/test_audio_commands.py
```

## References

- `admin/db/models.py`: `Recording.from_row(row, description)` — requires `description` parameter
- `admin/db/schema.py`: `RECORDING_COLUMN_COUNT = 29`, `COLUMN_MIGRATIONS` list
- `AGENTS.md`: Database Column Addition Checklist
- V1 spec: `specs/fix_audio_status_title_column_mismatch.md` (partially stale — display loop already fixed)
