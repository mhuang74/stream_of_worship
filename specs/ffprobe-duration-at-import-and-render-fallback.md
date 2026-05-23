# FFprobe Duration at Import Time + Render Worker Fallback

## Problem

The render worker Lambda fails with:

```
Songset items have no valid duration_seconds — cannot estimate render time
```

**Root cause:** All 41 recordings in the production Neon database have `duration_seconds = NULL` and `analysis_status = 'pending'`. The `duration_seconds` column is only populated by the analysis service (a heavy ML pipeline), which has never been run. The render worker's pipeline (`pipeline.py:208-212`) hard-fails when the sum of `duration_seconds` across songset items is zero.

**Why the JOIN works but duration is NULL:** The `LEFT JOIN recordings r ON si.recording_hash_prefix = r.hash_prefix` succeeds — every `songset_items.recording_hash_prefix` matches a valid `recordings.hash_prefix`. The problem is that `recordings.duration_seconds` is NULL because the analysis pipeline was never executed.

**The irony:** The render worker's `audio_engine.py:generate_songset_audio()` already probes actual audio files via FFmpeg (`get_audio_info()`) and returns an accurate `total_duration_seconds` at line 247 of `pipeline.py`. The early failure at line 208-212 only prevents the **initial progress estimate** from being computed — it doesn't affect the actual render.

## Solution Overview

Two-part fix:

1. **Render worker: Graceful fallback** — Replace the hard `ValueError` with a simple heuristic estimate when `duration_seconds` is missing. The accurate duration is corrected later from `audio_result.total_duration_seconds`.

2. **Admin CLI: FFprobe at download time** — Add ffprobe probing to `audio download` and `audio batch` commands so `duration_seconds` is populated immediately when audio is first downloaded, without waiting for the full analysis pipeline.

3. **Backfill: `audio probe` command** — New admin subcommand to backfill `duration_seconds` for existing recordings with NULL values.

---

## Part 1: Render Worker — Simple Estimate Fallback

### File: `services/render-worker/src/sow_render_worker/pipeline.py`

**Current code (lines 208-212):**
```python
total_duration_seconds = sum(item.duration_seconds or 0 for item in items)
if total_duration_seconds <= 0:
    raise ValueError(
        "Songset items have no valid duration_seconds — cannot estimate render time"
    )
```

**New code:**
```python
total_duration_seconds = sum(item.duration_seconds or 0 for item in items)
if total_duration_seconds <= 0:
    total_duration_seconds = 180.0 * len(items)
    logger.warning(
        "Songset items have no valid duration_seconds — "
        "using rough estimate of %.0fs (%d items × 180s/item)",
        total_duration_seconds,
        len(items),
    )
```

**Why 180s:** Average worship song is ~3-4 minutes. 180s is a reasonable conservative estimate. The initial estimate is only used for progress reporting — the accurate duration from `audio_result.total_duration_seconds` (line 247) corrects it after the audio mixing phase.

### Test changes: `services/render-worker/tests/test_pipeline.py`

**Update `test_pipeline_rejects_zero_duration_items` (lines 839-860):**

The test currently asserts `pytest.raises(ValueError, match="no valid duration_seconds")`. Change it to verify the fallback behavior:

```python
def test_pipeline_uses_fallback_estimate_when_duration_missing(self):
    job = _make_render_job()
    mock_conn = MagicMock()
    mock_fetcher = _make_mock_fetcher()
    mock_uploader = _make_mock_uploader()
    items = [_make_songset_item(duration_seconds=None)]

    with patch("sow_render_worker.pipeline.get_render_job", return_value=job), \
         patch("sow_render_worker.pipeline.start_render_job", return_value=job), \
         patch("sow_render_worker.pipeline.update_render_progress"), \
         patch("sow_render_worker.pipeline.fail_render_job") as mock_fail, \
         patch("sow_render_worker.pipeline.fetch_songset_items", return_value=items), \
         patch("sow_render_worker.pipeline.get_render_ratio", return_value=0.8), \
         patch("sow_render_worker.pipeline.generate_songset_audio") as mock_audio, \
         patch("sow_render_worker.pipeline.check_cancelled"), \
         patch("sow_render_worker.pipeline.generate_chapters_manifest"), \
         patch("sow_render_worker.pipeline.upload_artifacts"):

        mock_audio.return_value = ExportResult(
            output_path="/tmp/out.mp3",
            total_duration_seconds=180.0,
            segments=(),
        )

        execute_render_pipeline(
            "job_abc123", 42, mock_conn,
            asset_fetcher=mock_fetcher,
            uploader=mock_uploader,
        )

        mock_fail.assert_not_called()
```

**Add a test for multiple items with NULL duration:**

```python
def test_pipeline_fallback_estimate_multiplies_by_item_count(self):
    items = [_make_songset_item(duration_seconds=None) for _ in range(3)]
    # Should estimate 180 * 3 = 540s
    ...
```

---

## Part 2: Admin CLI — FFprobe at Download Time

### New module: `src/stream_of_worship/admin/services/ffprobe.py`

Create a dedicated ffprobe utility module mirroring the render worker's `get_audio_info()`:

```python
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


def probe_audio(file_path: Path) -> dict[str, Any] | None:
    """Probe an audio file with ffprobe and return metadata.

    Returns dict with keys: duration_seconds, duration_ms, channels,
    sample_rate, bitrate_kbps. Returns None on failure.
    """
    try:
        if not file_path.exists():
            return None
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                str(file_path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        metadata = json.loads(result.stdout)
        streams = metadata.get("streams", [])
        if not streams:
            return None
        audio_stream = None
        for s in streams:
            if s.get("codec_type") == "audio":
                audio_stream = s
                break
        if audio_stream is None:
            audio_stream = streams[0]
        fmt = metadata.get("format", {})
        duration_seconds = float(fmt.get("duration", 0))
        bitrate = int(fmt.get("bit_rate", "0") or "0")
        return {
            "duration_seconds": duration_seconds,
            "duration_ms": round(duration_seconds * 1000),
            "channels": int(audio_stream.get("channels", 2)),
            "sample_rate": int(audio_stream.get("sample_rate", 44100)),
            "bitrate_kbps": round(bitrate / 1000),
        }
    except Exception:
        return None


def probe_duration(file_path: Path) -> float | None:
    """Probe an audio file and return duration_seconds, or None on failure."""
    info = probe_audio(file_path)
    if info and info["duration_seconds"] > 0:
        return info["duration_seconds"]
    return None
```

**Design notes:**
- `probe_audio()` returns full metadata dict (mirrors render-worker's `get_audio_info()`)
- `probe_duration()` is a convenience wrapper that returns just the float
- Both accept `Path` objects (per project convention: "ALWAYS use `pathlib.Path` for file system operations")
- 30-second timeout matches the render-worker implementation
- Returns `None` on any failure (file not found, ffprobe not installed, corrupt file, etc.)

### Change 2a: `audio download` command

**File:** `src/stream_of_worship/admin/commands/audio.py`

**Location:** Lines 825-856 (after download, before `Recording()` construction)

**Current flow:**
```
download → hash → upload to R2 → Recording(duration_seconds=None) → insert_recording → cleanup
```

**New flow:**
```
download → hash → probe duration → upload to R2 → Recording(duration_seconds=<probed>) → insert_recording → cleanup
```

**Specific changes:**

1. Add import at top of file:
```python
from stream_of_worship.admin.services.ffprobe import probe_duration
```

2. After line 833 (`prefix = get_hash_prefix(content_hash)`), before line 835 (`# Upload to R2`), insert:
```python
    # Probe audio duration
    duration = probe_duration(audio_path)
    if duration:
        console.print(f"[dim]Duration: {duration:.1f}s[/dim]")
    else:
        console.print("[yellow]Could not probe audio duration[/yellow]")
```

3. In the `Recording()` constructor (line 845-855), add `duration_seconds=duration`:
```python
    recording = Recording(
        content_hash=content_hash,
        hash_prefix=prefix,
        song_id=song_id,
        original_filename=audio_path.name,
        file_size_bytes=file_size,
        imported_at=datetime.now().isoformat(),
        r2_audio_url=r2_url,
        download_status="completed",
        youtube_url=video_info.get("webpage_url"),
        duration_seconds=duration,  # NEW
    )
```

**Important:** The probe happens **before** `audio_path.unlink()` (line 860), so the temp file is still available. The probe is fast (~100ms for ffprobe) and adds negligible overhead.

### Change 2b: `audio batch` → `_download_and_create_recording()`

**File:** `src/stream_of_worship/admin/commands/audio.py`

**Location:** Lines 3570-3653

Same pattern as `audio download`:

1. After line 3617 (`prefix = get_hash_prefix(content_hash)`), insert:
```python
        duration = probe_duration(audio_path)
        if duration:
            console.print(f"  [dim]Duration: {duration:.1f}s[/dim]")
```

2. In the `Recording()` constructor (line 3632-3642), add `duration_seconds=duration`:
```python
        recording = Recording(
            content_hash=content_hash,
            hash_prefix=prefix,
            song_id=song_id,
            original_filename=audio_path.name,
            file_size_bytes=file_size,
            imported_at=datetime.now().isoformat(),
            r2_audio_url=r2_url,
            download_status="completed",
            youtube_url=youtube_url,
            duration_seconds=duration,  # NEW
        )
```

### Change 2c: `audio batch` → `_download_if_needed()`

**File:** `src/stream_of_worship/admin/commands/audio.py`

**Location:** Lines 3655-3735

This function handles the case where a recording already exists but the audio file needs to be (re-)downloaded. After a successful download, we should update `duration_seconds` on the existing recording.

1. After line 3717 (`r2_url = r2_client.upload_audio(audio_path, prefix)`), insert:
```python
        duration = probe_duration(audio_path)
```

2. After line 3726 (`db_client.update_recording_download(hash_prefix, "completed")`), insert:
```python
        if duration is not None:
            db_client.update_recording_duration(hash_prefix, duration)
```

3. Before `audio_path.unlink()` at line 3728 (so the file is still available for probing).

**Note:** This requires a new `update_recording_duration()` method on `DatabaseClient` (see Part 2d below).

### Change 2d: New DB method `update_recording_duration()`

**File:** `src/stream_of_worship/admin/db/client.py`

Add after `update_recording_youtube_url()` (line 934):

```python
    def update_recording_duration(
        self,
        hash_prefix: str,
        duration_seconds: float,
    ) -> None:
        """Update duration_seconds for a recording.

        Args:
            hash_prefix: The hash prefix of the recording.
            duration_seconds: The probed audio duration in seconds.
        """
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE recordings SET
                    duration_seconds = %s,
                    updated_at = NOW()
                WHERE hash_prefix = %s
                """,
                (duration_seconds, hash_prefix),
            )
```

**Why a dedicated method instead of extending `update_recording_status()`:**
- `update_recording_status()` is specifically for analysis/LRC job tracking fields
- `duration_seconds` is a physical audio property, not a job status
- A dedicated method keeps the API clean and avoids bloating the existing method with unrelated parameters

**Also fix the existing bug at line 3720-3723:** `_download_if_needed()` currently calls `db_client.update_recording_status(hash_prefix=hash_prefix, r2_audio_url=r2_url)`, but `update_recording_status()` does not accept `r2_audio_url`. This would raise `TypeError` at runtime. Replace with the correct call pattern using existing methods:
```python
        # Replace the broken call:
        #   db_client.update_recording_status(hash_prefix=hash_prefix, r2_audio_url=r2_url)
        # With:
        db_client.update_recording_download(hash_prefix, "completed")
```

The `r2_audio_url` is already set during `insert_recording()` (the upsert sets it via `EXCLUDED.r2_audio_url`). If the recording already existed with a NULL `r2_audio_url`, we need a separate update — but this is an edge case that can be addressed separately.

---

## Part 3: Backfill — `audio probe` Command

### New subcommand: `sow-admin audio probe`

**File:** `src/stream_of_worship/admin/commands/audio.py`

Add a new `@app.command("probe")` that downloads audio from R2, runs ffprobe, and updates `duration_seconds` in the database.

```python
@app.command("probe")
def probe(
    song_id: str = typer.Argument(..., help="Song ID to probe"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-probe even if duration_seconds is already set"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Probe audio duration via ffprobe and update the recording in the database.

    Downloads the audio file from R2 (using the local cache), runs ffprobe
    to determine duration, and updates recordings.duration_seconds.

    Use --force to re-probe recordings that already have a duration.
    """
```

**Flow:**
1. Load config, get DB client
2. Look up recording by `song_id` via `db_client.get_recording_by_song_id(song_id)`
3. If no recording found, error and exit
4. If `recording.duration_seconds is not None` and not `--force`, print current value and skip
5. Initialize R2 client and AssetCache
6. Download audio from R2 to local cache via `cache.download_audio(hash_prefix)`
7. Run `probe_duration(audio_path)`
8. If duration found, call `db_client.update_recording_duration(hash_prefix, duration)`
9. Print result

**Batch variant:** Add `--batch` option or a separate `audio probe-batch` that processes multiple songs:

```python
@app.command("probe-batch")
def probe_batch(
    album: Optional[str] = typer.Option(None, "--album", help="Filter by album name"),
    song: Optional[str] = typer.Option(None, "--song", help="Filter by song name (partial match)"),
    analysis_status: Optional[str] = typer.Option(None, "--analysis-status", help="Filter by analysis status"),
    force: bool = typer.Option(False, "--force", "-f", help="Re-probe even if duration_seconds is already set"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be probed without executing"),
    limit: Optional[int] = typer.Option(None, "--limit", help="Maximum number of songs to process"),
    config_path: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to config file"),
) -> None:
    """Batch probe audio durations for recordings missing duration_seconds.

    Downloads audio from R2, runs ffprobe, and updates duration_seconds
    for all recordings that have NULL duration_seconds (or all if --force).
    """
```

**Flow:**
1. Load config, get DB client
2. Query recordings with `duration_seconds IS NULL` (or all if `--force`)
3. Filter by album/song/status options
4. If `--dry-run`, print list and exit
5. For each recording:
   a. Download audio from R2 to local cache
   b. Run `probe_duration()`
   c. Update DB via `db_client.update_recording_duration()`
   d. Print progress
6. Print summary (probed, skipped, failed counts)

**Alternative: Extend `audio batch` with `--probe-only` flag.** This avoids adding a new command but makes `batch` more complex. A separate command is cleaner because `probe` is conceptually different from `batch` (which is download + LRC).

**Recommendation:** Add both `probe` (single song) and `probe-batch` (bulk) as separate commands. They're lightweight and focused.

---

## Implementation Order

1. **Part 1: Render worker fallback** — Immediate fix, unblocks renders right now
   - `services/render-worker/src/sow_render_worker/pipeline.py` (3 lines changed)
   - `services/render-worker/tests/test_pipeline.py` (update 1 test, add 1 test)

2. **Part 2a: New `ffprobe.py` module** — Foundation for Parts 2b-2d and Part 3
   - `src/stream_of_worship/admin/services/ffprobe.py` (new file, ~60 lines)

3. **Part 2b: `audio download` command** — Most common user flow
   - `src/stream_of_worship/admin/commands/audio.py` (import + ~5 lines)

4. **Part 2c: `audio batch` helpers** — Batch download flow
   - `src/stream_of_worship/admin/commands/audio.py` (~10 lines across 2 functions)

5. **Part 2d: `update_recording_duration()` DB method** — Needed by Parts 2c and 3
   - `src/stream_of_worship/admin/db/client.py` (~20 lines)

6. **Part 3: `audio probe` + `probe-batch` commands** — Backfill existing data
   - `src/stream_of_worship/admin/commands/audio.py` (~120 lines for both commands)

7. **Run backfill** — Execute `sow-admin audio probe-batch` against production to populate the 41 existing recordings

---

## Files Changed Summary

| File | Change | Lines |
|------|--------|-------|
| `services/render-worker/src/sow_render_worker/pipeline.py` | Replace hard ValueError with fallback estimate | ~5 |
| `services/render-worker/tests/test_pipeline.py` | Update test for new fallback behavior | ~30 |
| `src/stream_of_worship/admin/services/ffprobe.py` | **New file** — ffprobe utility functions | ~60 |
| `src/stream_of_worship/admin/commands/audio.py` | Add probe to `download`, `_download_and_create_recording`, `_download_if_needed`; add `probe` and `probe-batch` commands | ~140 |
| `src/stream_of_worship/admin/db/client.py` | Add `update_recording_duration()` method | ~20 |

---

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| ffprobe not available in admin CLI environment | `probe_duration()` returns `None` gracefully — recording is still created with `duration_seconds=None`, same as current behavior |
| ffprobe timeout on corrupt files | 30-second timeout per file; returns `None` on failure |
| `insert_recording()` upsert overwrites `duration_seconds` with NULL on conflict | The upsert at line 452 sets `duration_seconds = EXCLUDED.duration_seconds`. If the new Recording has `duration_seconds=<probed>`, it will overwrite. If the existing row already has a value from analysis, the probed value will replace it. This is acceptable since ffprobe duration is equally accurate for this purpose. |
| Render worker fallback estimate is inaccurate | Only affects initial progress estimate. Accurate duration from `audio_result.total_duration_seconds` corrects it after audio mixing phase (line 247-259). |
| `_download_if_needed()` bug with `update_recording_status(r2_audio_url=...)` | Fix as part of this change — replace with correct method calls |

---

## Verification

1. **Render worker:** Run `PYTHONPATH=src pytest tests/test_pipeline.py -v -k "duration"` from `services/render-worker/`
2. **Admin ffprobe module:** Add unit tests for `probe_audio()` and `probe_duration()` in `tests/admin/services/test_ffprobe.py` (mock `subprocess.run`)
3. **Admin commands:** Manual test with `sow-admin audio download <song_id>` and verify `duration_seconds` is populated in DB
4. **Backfill:** Run `sow-admin audio probe-batch --dry-run` to preview, then `sow-admin audio probe-batch` to execute
5. **End-to-end:** After backfill, trigger a render job and verify it no longer fails with "no valid duration_seconds"
