# Render Worker Soft-Deleted & Missing Recording Handling Plan

## Problem Statement

When a recording is soft-deleted (`recordings.deleted_at IS NOT NULL`), the render worker pipeline still attempts to download its audio file from R2. This results in a confusing HTTP 404 error that obscures the real root cause — the recording has been soft-deleted and should not be used for rendering.

A related but distinct problem exists for hard-deleted or missing recording rows: when `songset_items.recording_hash_prefix` references a `hash_prefix` that has no matching row in `recordings` at all, the LEFT JOIN returns NULLs for all `r.*` columns. The pipeline then proceeds to `download_audio()` and fails with the same confusing HTTP 404.

### Observed Failure Pattern

**CloudWatch Logs:**
```
[ERROR] Failed to download audio for b1979244c818
Traceback (most recent call last):
  File "asset_fetcher.py", line 63, in download_audio
    raise RuntimeError(f"Failed to download audio: HTTP {response.status}")
RuntimeError: Failed to download audio: HTTP 404
```

**Database:**
```
error_message: "Failed to download audio for b1979244c818: Failed to download audio: HTTP 404"
```

**Root Cause:** Recording `b1979244c818` was soft-deleted on `2026-06-08 22:33:22` but the songset item still referenced it. The pipeline attempted to download the audio and got HTTP 404 because the R2 object no longer exists.

## Goals

1. **Fail fast with a clear error message** when a songset item references a soft-deleted recording
2. **Fail fast with a distinct error message** when a songset item references a recording that has no database row at all (hard-deleted or data integrity issue)
3. **Distinguish soft-deleted recordings from genuinely missing R2 objects** to aid debugging
4. **Preserve existing behavior** for valid recordings (no performance regression)

## Proposed Changes

### 1. Validate Recordings in `fetch_songset_items()`

**File:** `services/render-worker/src/sow_render_worker/pipeline.py`

Modify the SQL query in `fetch_songset_items()` to include `r.deleted_at` and `r.id AS recording_id`:

```python
cur.execute(
    "SELECT "
    "  si.id, "
    "  si.songset_id, "
    "  si.song_id, "
    "  si.recording_hash_prefix, "
    "  si.position, "
    "  si.gap_beats, "
    "  si.crossfade_enabled, "
    "  si.crossfade_duration_seconds, "
    "  si.key_shift_semitones, "
    "  si.tempo_ratio, "
    "  r.tempo_bpm, "
    "  r.duration_seconds, "
    "  r.id AS recording_id, "
    "  r.deleted_at, "
    "  s.title AS song_title "
    "FROM songset_items si "
    "LEFT JOIN recordings r ON si.recording_hash_prefix = r.hash_prefix "
    "LEFT JOIN songs s ON si.song_id = s.id "
    "WHERE si.songset_id = %s "
    "ORDER BY si.position",
    (songset_id,),
)
```

After fetching items, validate before returning — check for both soft-deleted and missing recordings:

```python
missing_items = [
    item for item in items
    if item.recording_hash_prefix and item.recording_id is None
]

if missing_items:
    hashes = ", ".join(item.recording_hash_prefix for item in missing_items)
    raise ValueError(
        f"Songset contains {len(missing_items)} recording(s) not found in database: {hashes}. "
        f"Recording row may have been hard-deleted or never existed."
    )

deleted_items = [
    item for item in items
    if item.recording_hash_prefix and item.deleted_at is not None
]

if deleted_items:
    hashes = ", ".join(item.recording_hash_prefix for item in deleted_items)
    raise ValueError(
        f"Songset contains {len(deleted_items)} soft-deleted recording(s): {hashes}. "
        f"Run 'sow-admin maintenance repair-songsets' to fix stale references."
    )
```

**Rationale:**
- Missing-row check comes first because it's the more severe data integrity issue.
- Using `r.id AS recording_id` as the sentinel is explicit and self-documenting — a NULL `recording_id` unambiguously means the LEFT JOIN found no matching row.
- The soft-deleted check uses `deleted_at IS NOT NULL`, which only matches when the row exists but is soft-deleted.
- Both checks guard on `recording_hash_prefix` being set, so items with no recording reference are skipped.

### 2. Add `deleted_at` and `recording_id` to `SongsetItem` Dataclass

**File:** `services/render-worker/src/sow_render_worker/audio_engine.py`

Add `recording_id` and `deleted_at` fields to `SongsetItem`:

```python
from datetime import datetime

@dataclass(frozen=True)
class SongsetItem:
    id: str
    songset_id: str
    song_id: str
    song_title: str | None = None
    recording_hash_prefix: str | None = None
    position: int = 0
    gap_beats: float | None = None
    crossfade_enabled: int | None = None
    crossfade_duration_seconds: float | None = None
    key_shift_semitones: float | None = None
    tempo_ratio: float | None = None
    tempo_bpm: float | None = None
    duration_seconds: float | None = None
    recording_id: str | None = None
    deleted_at: datetime | None = None
```

**Note:** `datetime` import required at the top of the file.

### 3. Update Pipeline to Pass `recording_id` and `deleted_at`

**File:** `services/render-worker/src/sow_render_worker/pipeline.py`

Update the `SongsetItem` construction in `fetch_songset_items()`:

```python
items = [
    SongsetItem(
        id=row["id"],
        songset_id=row["songset_id"],
        song_id=row["song_id"],
        recording_hash_prefix=row["recording_hash_prefix"],
        position=row["position"],
        gap_beats=row["gap_beats"],
        crossfade_enabled=row["crossfade_enabled"],
        crossfade_duration_seconds=row["crossfade_duration_seconds"],
        key_shift_semitones=row["key_shift_semitones"],
        tempo_ratio=row["tempo_ratio"],
        tempo_bpm=row["tempo_bpm"],
        duration_seconds=row["duration_seconds"],
        song_title=row["song_title"],
        recording_id=row["recording_id"],
        deleted_at=row["deleted_at"],
    )
    for row in rows
]
```

### 4. Alternative: Validate in `AssetFetcher.download_audio()`

**Rejected:** Checking at the asset fetcher level is too late — the pipeline has already started, progress has been logged, and the error context is lost. The `AssetFetcher` has no database access and should remain a simple download utility.

**Preferred:** The validation belongs in `fetch_songset_items()` where we already have the DB connection and can inspect the full songset state.

## Error Message Format

### Soft-deleted recording

```
Songset contains 1 soft-deleted recording(s): b1979244c818. Run 'sow-admin maintenance repair-songsets' to fix stale references.
```

For multiple soft-deleted recordings:
```
Songset contains 3 soft-deleted recording(s): b1979244c818, abc123def456, xyz789uvw012. Run 'sow-admin maintenance repair-songsets' to fix stale references.
```

### Missing recording row

```
Songset contains 1 recording(s) not found in database: b1979244c818. Recording row may have been hard-deleted or never existed.
```

For multiple missing recordings:
```
Songset contains 2 recording(s) not found in database: b1979244c818, abc123def456. Recording row may have been hard-deleted or never existed.
```

## Test Plan

### Unit Tests

**File:** `services/render-worker/tests/test_pipeline.py`

1. **`test_fetch_songset_items_rejects_soft_deleted_recordings`**
   - Mock DB returning 2 items, one with `deleted_at = <timestamp>` and `recording_id` set
   - Assert `ValueError` is raised with message containing "soft-deleted" and the hash prefix

2. **`test_fetch_songset_items_rejects_missing_recording_rows`**
   - Mock DB returning 1 item with `recording_hash_prefix` set but `recording_id = None` (LEFT JOIN returned no row)
   - Assert `ValueError` is raised with message containing "not found in database"

3. **`test_fetch_songset_items_missing_checked_before_deleted`**
   - Mock DB returning 1 item with both `recording_id = None` and `deleted_at = None`
   - Assert the "not found in database" error is raised (missing-row check runs first)

4. **`test_fetch_songset_items_allows_active_recordings`**
   - Mock DB returning 2 items, both with `deleted_at = None` and `recording_id` set
   - Assert no exception, items returned normally

5. **`test_fetch_songset_items_allows_null_recording_hash`**
   - Mock DB returning item with `recording_hash_prefix = None`
   - Assert no exception (no recording to validate)

6. **`test_pipeline_fails_fast_on_soft_deleted_recording`**
   - Mock full pipeline with songset containing soft-deleted recording
   - Assert job status = `failed`, error_message contains "soft-deleted"
   - Assert `fail_render_job` called with clear message
   - Assert no audio mixing attempted (fail fast)

7. **`test_pipeline_fails_fast_on_missing_recording_row`**
   - Mock full pipeline with songset containing a recording_hash_prefix with no matching row
   - Assert job status = `failed`, error_message contains "not found in database"
   - Assert no audio mixing attempted (fail fast)

### Integration Test

8. **`test_end_to_end_soft_deleted_recording`**
   - Insert songset with item referencing soft-deleted recording
   - Execute pipeline
   - Assert job fails with message referencing the hash and repair command

9. **`test_end_to_end_missing_recording_row`**
   - Insert songset with item referencing a hash_prefix that has no recordings row
   - Execute pipeline
   - Assert job fails with message referencing the hash and "not found in database"

### Test Helper Update

Update `_make_songset_item()` in `test_pipeline.py` to include the new fields:

```python
def _make_songset_item(**overrides) -> SongsetItem:
    defaults = {
        "id": "item_1",
        "songset_id": "ss_001",
        "song_id": "song_1",
        "song_title": "Test Song",
        "recording_hash_prefix": "abc123",
        "position": 0,
        "gap_beats": 2.0,
        "crossfade_enabled": 0,
        "crossfade_duration_seconds": None,
        "key_shift_semitones": 0,
        "tempo_ratio": 1.0,
        "tempo_bpm": 120.0,
        "duration_seconds": 180.0,
        "recording_id": "rec_001",
        "deleted_at": None,
    }
    defaults.update(overrides)
    return SongsetItem(**defaults)
```

## Backward Compatibility

- **No schema changes** — `deleted_at` and `id` already exist on `recordings`
- **No API changes** — `SongsetItem` gains two optional fields; existing code that doesn't set them defaults to `None`
- **No behavior change for valid songsets** — active recordings proceed exactly as before
- **No performance impact** — two additional columns in existing query

## Files to Modify

| File | Change |
|------|--------|
| `services/render-worker/src/sow_render_worker/audio_engine.py` | Add `recording_id` and `deleted_at` fields to `SongsetItem`; add `datetime` import |
| `services/render-worker/src/sow_render_worker/pipeline.py` | Add `r.id AS recording_id` and `r.deleted_at` to query; add missing-row and soft-deleted validation in `fetch_songset_items()`; pass new fields to `SongsetItem` |
| `services/render-worker/tests/test_pipeline.py` | Update `_make_songset_item()` helper; add tests for soft-deleted rejection, missing-row rejection, validation order, and active recording acceptance |

## Follow-up Work (Out of Scope)

These webapp-side gaps were identified during review. They are tracked here but not implemented in this plan.

### 1. Validate recording existence at insert time

**File:** `webapp/src/lib/db/songsets.ts` — `addSongsetItem()` and `updateSongsetItem()`

Currently, these functions do not verify that `recordingHashPrefix` refers to a non-deleted, published recording. A soft-deleted or non-existent `hashPrefix` can be persisted in `songset_items`, resulting in a phantom item that is invisible in the editor (filtered out by `!item.recordingDeletedAt`) but still exists in the database and will cause render failures.

**Fix:** Before inserting/updating a songset item, query `recordings` to confirm the `hash_prefix` exists, is not soft-deleted, and has `visibility_status = 'published'`.

### 2. Check LRC availability at insert time

**File:** `webapp/src/lib/db/songsets.ts` — `addSongsetItem()` and `updateSongsetItem()`

A recording without an LRC file (`r2_lrc_url IS NULL`) can be added to a songset. The render pipeline will fail later when generating chapters, but there is no early guard.

**Fix:** Validate that the recording has `r2_lrc_url IS NOT NULL` before allowing it to be added to a songset.

### 3. Hard-deleted recording rows pass the webapp filter

**File:** `webapp/src/lib/db/songsets.ts` — `getSongsetEditorData()`, `getSongsetPublicView()`, `getRenderPageData()`

These functions filter out soft-deleted recordings with `!item.recordingDeletedAt`. However, when a recording row is completely absent (hard-deleted or never existed), the LEFT JOIN returns `null` for `recordingDeletedAt`, which passes the `!null` check. The item displays with null recording data instead of being filtered or flagged.

**Fix:** Also check for `item.recordingId` (or equivalent) being null to catch the hard-deleted case. This aligns with the render-worker approach of using `r.id AS recording_id` as a sentinel.

### 4. Pre-flight check before enqueueing render job

**File:** `webapp/src/app/api/render/route.ts` (or equivalent)

Validate that all songset items reference active, published recordings with LRC files before enqueueing a render job. This would prevent the render worker from ever encountering these issues, complementing the fail-fast validation in the worker itself.

## Future Considerations (Out of Scope)

- **Auto-repair on failure:** Automatically trigger `repair-songsets` logic before failing. Rejected — the render worker should not mutate songsets; that's an admin operation.
- **Skip instead of fail:** Skip soft-deleted items and render with remaining songs. Rejected — this would produce incomplete renders silently; failing fast is safer.
