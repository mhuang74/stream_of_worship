# Fix: `audio status` Title Column Mismatch & `from_row()` Missing Argument

## Problem Summary

Two bugs exist in `src/stream_of_worship/admin/commands/audio.py` related to column drift in the `recordings` table:

1. **Bug 1 (already fixed, needs regression test):** `sow-admin audio status --reconcile` rendered the Song Title column as a YouTube URL instead of the actual song title. The fix (name-based column mapping) is already applied, but no regression test exists to prevent reversion.

2. **Bug 2 (unfixed, runtime crash):** `_force_sync_all_pending()` calls `Recording.from_row(row)` without the required `description` argument, causing a `TypeError` whenever `audio status --force-status --sync` is invoked with pending recordings.

---

## Bug 1: Title Column Shows YouTube URL (Fixed, Needs Test)

### Root Cause

The `check_status()` function used a raw SQL query with a JOIN:

```sql
SELECT r.*, s.title as song_title
FROM recordings r
LEFT JOIN songs s ON r.song_id = s.id
WHERE r.analysis_status != 'completed' OR r.lrc_status != 'completed'
ORDER BY r.imported_at DESC
```

Results were accessed by **fragile, hardcoded numeric indices**:

```python
song_title = row[25] if row[25] else "-"  # BROKEN
```

Four `ALTER TABLE ADD COLUMN` migrations appended new columns to `recordings`, shifting `song_title` (the joined column) from index 25 to index 29:

| Column Added | Migration Entry |
|---|---|
| `youtube_url` | `("recordings", "youtube_url", "TEXT")` |
| `visibility_status` | `("recordings", "visibility_status", "TEXT")` |
| `deleted_at` | `("recordings", "deleted_at", "TIMESTAMP")` |
| `download_status` | `("recordings", "download_status", "TEXT DEFAULT 'pending'")` |

Index 25 now maps to `youtube_url`. The fix — using `cursor.description` for name-based mapping — was already applied at `audio.py:2190-2201`:

```python
description = cursor.description
col_names = [desc[0] for desc in description]

for row in rows:
    row_dict = dict(zip(col_names, row))
    song_title = row_dict.get("song_title") or "-"
    # ... other fields by name
```

**But no regression test exists.** A future change could silently reintroduce numeric indexing.

### Fix (Already Applied)

**Location**: `audio.py:2190-2211` — the `for row in rows:` loop inside `check_status()`.

No code change needed. The fix is already in place.

### Regression Test (Missing)

**File**: `tests/admin/test_audio_commands.py`
**Class**: `TestStatusCommand`
**New test**: `test_status_no_args_pending_with_youtube_url`

Insert a recording with a populated `youtube_url` and a pending status. Assert:
1. Output contains the **actual song title** (`"測試歌曲"`)
2. Output **does NOT contain** the YouTube URL (`cyo4B6MsK3g`)

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
    assert "測試歌曲" in result.output
    assert "cyo4B6MsK3g" not in result.output
```

---

## Bug 2: `_force_sync_all_pending()` Crashes with Missing `description` Argument

### Root Cause

At `audio.py:2299`, `_force_sync_all_pending()` calls `Recording.from_row()` with only one argument:

```python
rec = Recording.from_row(row)
```

But `Recording.from_row()` requires **two** positional arguments (`models.py:195`):

```python
@classmethod
def from_row(cls, row: tuple, description: tuple) -> "Recording":
```

This is the **same class of bug** as Bug 1. When `from_row()` was refactored from numeric indexing to name-based mapping (requiring `description`), this call site was not updated.

### Impact

- **Trigger**: `sow-admin audio status --force-status <status> --sync` when pending recordings exist
- **Error**: `TypeError: Recording.from_row() missing 1 required positional argument: 'description'`
- **Blast radius**: Only the `--force-status --sync` code path. No data corruption risk — the function crashes before any writes.

### Fix

**Location**: `audio.py:2299`

**Current (broken)**:
```python
        rec = Recording.from_row(row)
```

**Fixed**:
```python
        rec = Recording.from_row(row, cursor.description)
```

The `cursor` variable is already in scope — it was created at `audio.py:2283` and the query executed at lines 2284-2288. `cursor.description` is populated after `cursor.execute()`, so it is valid.

### Regression Test (Missing)

**File**: `tests/admin/test_audio_commands.py`
**Class**: `TestStatusCommand`
**New test**: `test_status_force_status_sync`

Create a recording with a pending status, then invoke `audio status --force-status completed --sync`. Assert:
1. Exit code is 0
2. Output confirms the force update was applied

```python
def test_status_force_status_sync(self, setup):
    """Regression test: --force-status --sync must not crash (from_row missing description)."""
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
        ["audio", "status", "--force-status", "completed", "--sync",
         "--config", str(setup["config_path"])],
    )

    assert result.exit_code == 0
    assert "Force updated" in result.output
```

---

## Safety & Risk Assessment

| Aspect | Bug 1 | Bug 2 |
|---|---|---|
| Data integrity | No data corrupted (display only) | No data corrupted (crashes before writes) |
| Write surface | Read-only display loop | `--force-status --sync` path only |
| Blast radius | `check_status()` display loop | `_force_sync_all_pending()` |
| Backward compatibility | Transparent — only broken output corrected | Transparent — fixes a crash |

---

## Implementation Plan

### 1. Fix `_force_sync_all_pending()` in `audio.py`

**Line 2299**: Change `Recording.from_row(row)` → `Recording.from_row(row, cursor.description)`

### 2. Add regression test for Bug 1

**File**: `tests/admin/test_audio_commands.py` → `TestStatusCommand`
**Test**: `test_status_no_args_pending_with_youtube_url`

### 3. Add regression test for Bug 2

**File**: `tests/admin/test_audio_commands.py` → `TestStatusCommand`
**Test**: `test_status_force_status_sync`

### 4. Verify

```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/test_audio_commands.py::TestStatusCommand -v
```

Broader regression check:

```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/admin/ -v
```

Full suite (excluding services):

```bash
PYTHONPATH=src uv run --python 3.11 --extra app --extra test pytest tests/ \
  --ignore=tests/services/analysis \
  --ignore=services/qwen3/tests \
  --ignore=services/analysis/tests -v
```

### 5. Graphify update

```bash
graphify update .
```

---

## Files Changed

| File | Action | Lines |
|---|---|---|
| `src/stream_of_worship/admin/commands/audio.py` | Modify | 2299: add `cursor.description` arg to `Recording.from_row()` |
| `tests/admin/test_audio_commands.py` | Add | `test_status_no_args_pending_with_youtube_url` in `TestStatusCommand` |
| `tests/admin/test_audio_commands.py` | Add | `test_status_force_status_sync` in `TestStatusCommand` |

## Rollback Plan

```bash
git checkout src/stream_of_worship/admin/commands/audio.py
git checkout tests/admin/test_audio_commands.py
```

## References

- `admin/db/schema.py`: `RECORDING_COLUMN_COUNT = 29` (line 252), column order in DDL (lines 35-72)
- `admin/db/models.py`: `Recording.from_row(row, description)` signature (line 195)
- `AGENTS.md`: Database Column Addition Checklist — explains why `ALTER TABLE ADD COLUMN` appends and order must never be reordered
