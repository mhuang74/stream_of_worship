# Fix: `audio status --reconcile` Shows YouTube URL in Title Column

## Problem Summary

`sow-admin audio status --reconcile` (and `sow-admin audio status` without arguments) renders the **Song Title** column as a YouTube URL (`https://www.youtube.com/watch?v=...`) instead of the actual song title.

## Root Cause

The `audio status` command uses a raw SQL query to fetch pending recordings along with their associated song titles:

```sql
SELECT r.*, s.title as song_title
FROM recordings r
LEFT JOIN songs s ON r.song_id = s.id
WHERE r.analysis_status != 'completed' OR r.lrc_status != 'completed'
ORDER BY r.imported_at DESC
```

results of this query is accessed by **fragile, hardcoded numeric indices** in the `for row in rows:` loop:

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

### How Column Drift Happened

The `recordings` table has had **four new columns** added since the original numeric indices were written:

| Column Added | Migration Entry | When |
|---|---|---|
| `youtube_url` | `("recordings", "youtube_url", "TEXT")` | After LRC reconciliation feature |
| `visibility_status` | `("recordings", "visibility_status", "TEXT")` | Visibility control feature |
| `deleted_at` | `("recordings", "deleted_at", "TIMESTAMP")` | Soft-delete support |
| `download_status` | `("recordings", "download_status", "TEXT DEFAULT 'pending'")` | Download state tracking |

`SQLite ALTER TABLE ADD COLUMN` **appends** columns physically. Each addition shifted all trailing columns to the right by one position.

**Before additions** (old schema):
- `song_title` (the joined column from `s.title`) was at index **25** after the 24 recording columns.

**Current schema** (`recordings` has 29 columns; actual count per `RECORDING_COLUMN_COUNT` in `schema.py`):
- `song_title` is now at index **29** (0-indexed position after 29 recording columns).
- Index 25 is now `youtube_url`.

Consequently, `row[25]` returns the recording's `youtube_url` instead of the joined song title.

### Why Existing Model Code Survived

`Recording.from_row()` and `Song.from_row()` in `models.py` already use **name-based mapping** via `cursor.description`, making them robust to schema drift:

```python
@classmethod
def from_row(cls, row: tuple, description: tuple) -> "Recording":
    col_names = [desc[0] for desc in description]
    values = dict(zip(col_names, row))
    ...
```

Only the inline numeric indexing in the `audio status` display loop is broken.

## Safety & Risk Assessment

- **Data Integrity**: No data is corrupted. Only the CLI display is wrong.
- **Write Surface**: The `audio status` command is **read-only** except for the `--reconcile`, `--sync`, and `--force-status` sub-modes. The display loop is read-only. No risk of DB mutation bugs.
- **Blast Radius**: The fix is localized to the display loop inside `check_status()` in `audio.py`. No other command uses these indices.
- **Backward Compatibility**: The fix is transparent to users; only broken output is corrected.

## Implementation Plan

### 1. Fix `src/stream_of_worship/admin/commands/audio.py`

**Location**: Inside `check_status()`, lines ~2166–2209 (the `for row in rows:` loop after the pending recordings query).

**Current code (broken)**:
```python
    for row in rows:
        song_id = row[2] if row[2] else "-"
        hash_prefix = row[1]
        song_title = row[25] if row[25] else "-"
        analysis_status = row[19]
        analysis_job_id = row[20] if row[20] else "-"
        lrc_status = row[21]
        lrc_job_id = row[22] if row[22] else "-"
```

**Desired code (robust)**:
Build a name-to-value mapping using `cursor.description`—the same technique already used in `Recording.from_row()`:

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

Alternatives considered and **rejected**:
- **Use `Recording.from_row(row, desc)` + extract `song_title`**: `from_row` expects exactly recording columns and depends on a model dataclass, but here we have a JOIN result with an extra `song_title` column at the end. Mapping by name is simpler and directly transparent.
- **Recalculate indices from `RECORDING_COLUMN_COUNT`**: Would fix it today but breaks again on the next column addition.

### 2. Add Regression Test

**File**: `tests/admin/test_audio_commands.py`

**New test**: `test_status_no_args_pending_with_youtube_url` inside `TestStatusCommand`.

**Purpose**: Insert a recording with a populated `youtube_url` and a pending status. Run `audio status --config ...`. Assert that:
1. The command output contains the **actual song title** (e.g., `"測試歌曲"`).
2. The command output **does NOT contain** the YouTube URL (`https://www.youtube.com/watch?v=cyo4B6MsK3g`).

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

### 3. Verification Steps

Run targeted tests for the `status` command:
```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/test_audio_commands.py::TestStatusCommand -v
```

Run broader admin tests to ensure no collateral damage:
```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/ -v
```

Then run the full backend-excluding-services test suite:
```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ \
  --ignore=tests/services/analysis \
  --ignore=services/qwen3/tests \
  --ignore=services/analysis/tests -v
```

### 4. Graphify Update

After code changes are committed, run to keep the knowledge graph current:
```bash
graphify update .
```

## Files Changed

| File | Action | Lines |
|---|---|---|
| `src/stream_of_worship/admin/commands/audio.py` | Modify | ~2166–2209: replace numeric index access with name-based mapping |
| `tests/admin/test_audio_commands.py` | Add | New test `test_status_no_args_pending_with_youtube_url` inside `TestStatusCommand` |

## Rollback Plan

Single-file revert.

```bash
git checkout src/stream_of_worship/admin/commands/audio.py
git checkout tests/admin/test_audio_commands.py
```

## References

- `admin/db/schema.py`: Canonical column order and `RECORDING_COLUMN_COUNT = 29`
- `admin/db/models.py`: `Recording.from_row()` demonstrates the correct name-based mapping pattern already in use
- `AGENTS.md`: Database Column Addition Checklist (explains why `ALTER TABLE ADD COLUMN` appends and order must never be reordered)
