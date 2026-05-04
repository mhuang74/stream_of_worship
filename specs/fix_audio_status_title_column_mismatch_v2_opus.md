# Fix: `audio status` Column-Index Bugs (v2)

## Problem Summary

Two bugs in `src/stream_of_worship/admin/commands/audio.py` caused by fragile positional access on SQL result rows:

1. **Title column shows YouTube URL** — `check_status()` display loop used hardcoded numeric indices on a `SELECT r.*, s.title as song_title` JOIN query. Column drift from 4 added columns shifted `song_title` from index 25 to index 29; index 25 now points to `youtube_url`.

2. **`_force_sync_all_pending()` crashes** — calls `Recording.from_row(row)` without the required `description` argument. Every other call site in the codebase passes `cursor.description`; this one was missed.

## Root Cause

### Bug 1: Numeric Index Drift in `check_status()` Display Loop

The `check_status()` function (line ~2168) runs:

```sql
SELECT r.*, s.title as song_title
FROM recordings r
LEFT JOIN songs s ON r.song_id = s.id
WHERE r.analysis_status != 'completed' OR r.lrc_status != 'completed'
ORDER BY r.imported_at DESC
```

The results were accessed by hardcoded numeric indices:

```python
for row in rows:
    song_id = row[2] if row[2] else "-"
    hash_prefix = row[1]
    song_title = row[25] if row[25] else "-"            # <-- BROKEN
    analysis_status = row[19]
    analysis_job_id = row[20] if row[20] else "-"
    lrc_status = row[21]
    lrc_job_id = row[22] if row[22] else "-"
```

Four columns were appended to the `recordings` table via `ALTER TABLE ADD COLUMN` migrations (which always append physically):

| Column | Migration |
|---|---|
| `youtube_url` | `("recordings", "youtube_url", "TEXT")` |
| `visibility_status` | `("recordings", "visibility_status", "TEXT")` |
| `deleted_at` | `("recordings", "deleted_at", "TIMESTAMP")` |
| `download_status` | `("recordings", "download_status", "TEXT DEFAULT 'pending'")` |

This shifted the joined `song_title` column from index 25 to index 29. Index 25 now returns `youtube_url`.

### Bug 2: Missing `description` Argument in `_force_sync_all_pending()`

At line 2299:

```python
rec = Recording.from_row(row)
```

`Recording.from_row()` signature requires two arguments:

```python
@classmethod
def from_row(cls, row: tuple, description: tuple) -> "Recording":
```

The `cursor` from line 2283 has `.description` available, but it was never passed. This causes a `TypeError` at runtime when `audio status --force-status` is invoked. Every other call site in the codebase (22 total across `client.py`, `read_client.py`, `catalog.py`) correctly passes `cursor.description`.

## Safety & Risk Assessment

- **Data Integrity**: No data corruption. Bug 1 is display-only; Bug 2 crashes before any writes.
- **Blast Radius**: Both fixes are localized — one display loop and one function call in `audio.py`.
- **Backward Compatibility**: Transparent to users; only broken output is corrected and a crash is prevented.

## Implementation Plan

### 1. Fix `check_status()` Display Loop (`audio.py:~2190`)

**Status**: Already applied (uncommitted on `reconcile_fixes` branch).

Replace numeric indices with name-based mapping via `cursor.description` — the same pattern used by `Recording.from_row()`:

```python
    description = cursor.description
    col_names = [desc[0] for desc in description]

    for row in rows:
        row_dict = dict(zip(col_names, row))
        song_id = row_dict.get("song_id") or "-"
        hash_prefix = row_dict.get("hash_prefix", "")
        song_title = row_dict.get("song_title") or "-"
        analysis_status = row_dict.get("analysis_status", "pending")
        analysis_job_id = row_dict.get("analysis_job_id") or "-"
        lrc_status = row_dict.get("lrc_status", "pending")
        lrc_job_id = row_dict.get("lrc_job_id") or "-"
```

### 2. Fix `_force_sync_all_pending()` (`audio.py:2299`)

**Status**: Not yet applied.

**Current code (broken)**:
```python
rec = Recording.from_row(row)
```

**Fix**:
```python
rec = Recording.from_row(row, cursor.description)
```

The `cursor` variable from line 2283 is in scope at line 2299.

### 3. Add Regression Test

**File**: `tests/admin/test_audio_commands.py`

**New test**: `test_status_no_args_pending_with_youtube_url` inside `TestStatusCommand` class (after `test_status_no_args_pending` at line 1534).

**Purpose**: Insert a recording with a populated `youtube_url` and pending status. Run `audio status`. Assert:
1. The song title (`"測試歌曲"`) appears in the output.
2. The YouTube URL (`cyo4B6MsK3g`) does NOT appear in the output.

```python
def test_status_no_args_pending_with_youtube_url(self, setup):
    """Regression: youtube_url must not leak into Title column."""
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
    assert "測試歌曲" in result.output
    assert "cyo4B6MsK3g" not in result.output
```

The existing `setup` fixture (line 1398) already inserts a `Song` with `id="song_001"` and `title="測試歌曲"`, so the JOIN will resolve the title correctly.

### 4. Verification

```bash
# Targeted status command tests
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/test_audio_commands.py::TestStatusCommand -v

# Broader admin tests
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/ -v

# Full suite excluding Docker services
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ \
  --ignore=tests/services/analysis \
  --ignore=services/qwen3/tests \
  --ignore=services/analysis/tests -v
```

### 5. Graphify Update

```bash
graphify update .
```

## Files Changed

| File | Action | Detail |
|---|---|---|
| `src/stream_of_worship/admin/commands/audio.py` | Modify | Line ~2190: replace numeric indices with `row_dict` (already done) |
| `src/stream_of_worship/admin/commands/audio.py` | Modify | Line 2299: add missing `cursor.description` arg to `Recording.from_row()` |
| `tests/admin/test_audio_commands.py` | Add | New `test_status_no_args_pending_with_youtube_url` in `TestStatusCommand` |

## Alternatives Considered and Rejected

- **Use `Recording.from_row(row, desc)` in the display loop**: The JOIN result has an extra `song_title` column that doesn't belong to the `Recording` dataclass. Building a plain `row_dict` is simpler and more transparent.
- **Recalculate indices from `RECORDING_COLUMN_COUNT`**: Would fix it today but breaks again on the next column addition.

## Rollback Plan

Single-file reverts:

```bash
git checkout src/stream_of_worship/admin/commands/audio.py
git checkout tests/admin/test_audio_commands.py
```

## References

- `admin/db/schema.py`: `RECORDING_COLUMN_COUNT = 29`, `COLUMN_MIGRATIONS` list
- `admin/db/models.py`: `Recording.from_row()` — correct name-based mapping pattern
- `CLAUDE.md`: Database Column Addition Checklist
