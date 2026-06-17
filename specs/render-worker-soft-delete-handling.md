# Render Worker Soft-Deleted Recording Handling Plan

## Problem Statement

When a recording is soft-deleted (`recordings.deleted_at IS NOT NULL`), the render worker pipeline still attempts to download its audio file from R2. This results in a confusing HTTP 404 error that obscures the real root cause — the recording has been soft-deleted and should not be used for rendering.

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
2. **Distinguish soft-deleted recordings from genuinely missing R2 objects** to aid debugging
3. **Preserve existing behavior** for valid recordings (no performance regression)

## Proposed Changes

### 1. Validate Recordings in `fetch_songset_items()`

**File:** `services/render-worker/src/sow_render_worker/pipeline.py`

Modify the SQL query in `fetch_songset_items()` to include `r.deleted_at`:

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
    "  r.deleted_at, "          # <-- ADD THIS
    "  s.title AS song_title "
    "FROM songset_items si "
    "LEFT JOIN recordings r ON si.recording_hash_prefix = r.hash_prefix "
    "LEFT JOIN songs s ON si.song_id = s.id "
    "WHERE si.songset_id = %s "
    "ORDER BY si.position",
    (songset_id,),
)
```

After fetching items, validate before returning:

```python
deleted_items = [
    item for item in items
    if item.recording_hash_prefix and item.deleted_at is not None
]

if deleted_items:
    hashes = ", ".join(item.recording_hash_prefix for item in deleted_items)
    raise ValueError(
        f"Songset contains {len(deleted_items)} soft-deleted recording(s): { hashes }. "
        f"Run 'sow-admin maintenance repair-songsets' to fix stale references."
    )
```

**Rationale:** This catches the problem at the earliest possible point — during data fetching, before any audio mixing begins. The error message is actionable and references the admin repair command.

### 2. Add `deleted_at` to `SongsetItem` Dataclass

**File:** `services/render-worker/src/sow_render_worker/audio_engine.py`

Add `deleted_at` field to `SongsetItem`:

```python
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
    deleted_at: datetime | None = None  # <-- ADD THIS
```

**Note:** `datetime` import required.

### 3. Update Pipeline to Pass `deleted_at`

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
        deleted_at=row["deleted_at"],  # <-- ADD THIS
    )
    for row in rows
]
```

### 4. Alternative: Validate in `AssetFetcher.download_audio()`

**Rejected:** Checking at the asset fetcher level is too late — the pipeline has already started, progress has been logged, and the error context is lost. The `AssetFetcher` has no database access and should remain a simple download utility.

**Preferred:** The validation belongs in `fetch_songset_items()` where we already have the DB connection and can inspect the full songset state.

## Error Message Format

The new error message should be:

```
Songset contains 1 soft-deleted recording(s): b1979244c818. Run 'sow-admin maintenance repair-songsets' to fix stale references.
```

For multiple soft-deleted recordings:
```
Songset contains 3 soft-deleted recording(s): b1979244c818, abc123def456, xyz789uvw012. Run 'sow-admin maintenance repair-songsets' to fix stale references.
```

## Test Plan

### Unit Tests

**File:** `services/render-worker/tests/test_pipeline.py`

1. **`test_fetch_songset_items_rejects_soft_deleted_recordings`**
   - Mock DB returning 2 items, one with `deleted_at = <timestamp>`
   - Assert `ValueError` is raised with expected message containing hash prefix

2. **`test_fetch_songset_items_allows_active_recordings`**
   - Mock DB returning 2 items, both with `deleted_at = None`
   - Assert no exception, items returned normally

3. **`test_fetch_songset_items_allows_null_recording_hash`**
   - Mock DB returning item with `recording_hash_prefix = None`
   - Assert no exception (no recording to validate)

4. **`test_pipeline_fails_fast_on_soft_deleted_recording`**
   - Mock full pipeline with songset containing soft-deleted recording
   - Assert job status = `failed`, error_message contains "soft-deleted"
   - Assert `fail_render_job` called with clear message
   - Assert no audio mixing attempted (fail fast)

### Integration Test

5. **`test_end_to_end_soft_deleted_recording`**
   - Insert songset with item referencing soft-deleted recording
   - Execute pipeline
   - Assert job fails with message referencing the hash and repair command

## Backward Compatibility

- **No schema changes** — `deleted_at` already exists on `recordings`
- **No API changes** — `SongsetItem` gains an optional field; existing code that doesn't set it defaults to `None`
- **No behavior change for valid songsets** — active recordings proceed exactly as before
- **No performance impact** — single additional column in existing query

## Files to Modify

| File | Change |
|------|--------|
| `services/render-worker/src/sow_render_worker/audio_engine.py` | Add `deleted_at` field to `SongsetItem` |
| `services/render-worker/src/sow_render_worker/pipeline.py` | Add `deleted_at` to query, validate in `fetch_songset_items()`, pass to `SongsetItem` |
| `services/render-worker/tests/test_pipeline.py` | Add tests for soft-deleted rejection and active recording acceptance |

## Future Considerations (Out of Scope)

- **Auto-repair on failure:** Automatically trigger `repair-songsets` logic before failing. Rejected — the render worker should not mutate songsets; that's an admin operation.
- **Skip instead of fail:** Skip soft-deleted items and render with remaining songs. Rejected — this would produce incomplete renders silently; failing fast is safer.
- **Pre-flight check in webapp:** Validate recordings before enqueueing. This is a separate feature that could complement this change.
