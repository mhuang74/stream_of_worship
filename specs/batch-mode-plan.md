# Batch Mode: End-to-End Song Processing

## Problem Summary

Processing a song from catalog to usable state requires multiple manual steps: download audio, submit LRC job, wait for completion, check status, retry failures. For a catalog of 50+ songs, this is tedious and error-prone. There is no coordination layer that submits jobs, monitors progress, and retries failures automatically.

### Current Workflow (Manual)

```bash
# For each song:
sow-admin audio download <song_id>     # download MP3 from YouTube
sow-admin audio lrc <song_id>          # submit LRC job
sow-admin audio status <job_id> --wait # wait (single-song only)
# If failed:
sow-admin audio lrc <song_id> --force  # retry
```

### Desired Workflow (Batch)

```bash
# Process all songs in an album that need LRC:
sow-admin audio batch --album "敬拜精选" --max-retries 3

# Or resubmit all failed LRC jobs:
sow-admin audio list --lrc-status failed --format ids | sow-admin audio batch --stdin
```

## Design

### Core Concept

Batch mode is an **orchestration loop** that:

1. Selects songs via filters (album, song name, status, etc.)
2. For each song, runs steps in order: download → LRC
3. Checks R2 before each step (skip if output already exists)
4. Submits jobs and polls for completion
5. Retries failed steps up to N times
6. Marks final failures as `lrc_status='failed'` in DB
7. Prints summary stats when all songs reach terminal state

### Key Design Decisions

1. **No schema changes**: Uses existing `lrc_status`, `update_recording_status()` for failure marking. Failure details are logged to stdout only.
2. **R2-first checks**: Before running any step, check if output already exists on R2 (avoid duplicate work)
3. **Sequential per-song, parallel across songs**: Each song's steps run sequentially, but multiple songs can be processed concurrently (out of scope for v1 — future enhancement)
4. **Terminal states only**: Batch loop continues until every song reaches `completed` or `failed`
5. **Download + LRC only**: Analysis is out of scope for v1 (different dependencies, timing)

## Command Interface

```bash
sow-admin audio batch [OPTIONS]
```

### Filter Options

| Option | Description |
|--------|-------------|
| `--album TEXT` | Filter by album name (exact match) |
| `--song TEXT` | Filter by song name (partial match, case-insensitive) |
| `--lrc-status TEXT` | Filter by LRC status (`pending`, `processing`, `failed`, `incomplete`) |
| `--analysis-status TEXT` | Filter by analysis status |
| `--stdin` | Read song IDs from stdin (pipe-friendly) |
| `--limit INT` | Maximum number of songs to process |

### Processing Options

| Option | Description |
|--------|-------------|
| `--max-retries INT` | Max retry attempts per step (default: 3) |
| `--skip-download` | Skip download step (assume audio already on R2) |
| `--skip-lrc` | Skip LRC step |
| `--dry-run` | Show what would be processed without executing |

### Output Options

| Option | Description |
|--------|-------------|
| `--format TEXT` | Output format: `rich` (default), `json` |

## File Changes

| File | Change |
|------|--------|
| `src/stream_of_worship/admin/commands/audio.py` | Add `batch()` command |
| `src/stream_of_worship/admin/services/r2.py` | No changes (uses `lrc_exists()`, `audio_exists()` from reconciliation v2) |
| `src/stream_of_worship/admin/db/client.py` | No changes |

## Detailed Design

### Step Pipeline

```
┌─────────────┐     ┌─────────────┐
│  Download   │────▶│     LRC     │
│  (YouTube)  │     │  (ASR API)  │┌──────────┐
└──────┬──────┘     └──────┬──────┘│  FAILED  │
       │                  │      └──────────┘
       ▼                  ▼
  Check R2:          Check R2:
  audio.mp3?         lyrics.lrc?
  (skip if exists)   (skip if exists)
```

### Processing Loop

```python
def batch(...):
    songs = resolve_song_ids(filters)
    results = {}

    for song_id in songs:
        result = process_song(song_id, max_retries)
        results[song_id] = result

    print_stats(results)

def process_song(song_id, max_retries) -> SongResult:
    recording = get_or_create_recording(song_id)
    hash_prefix = recording.hash_prefix

    # Step 1: Download
    if not skip_download:
        if r2_client.audio_exists(hash_prefix):
            log(f"Audio already on R2, skipping download")
        else:
            for attempt in range(1, max_retries + 1):
                try:
                    download_audio(song_id)
                    break
                except Exception as e:
                    if attempt == max_retries:
                        mark_failed(recording, "download", e)
                        return SongResult(status="failed", step="download", error=str(e))
                    log(f"Download attempt {attempt} failed: {e}, retrying...")

    # Step 2: LRC
    if not skip_lrc:
        if r2_client.lrc_exists(hash_prefix):
            log(f"LRC already on R2, skipping")
            update_recording_lrc(hash_prefix, lrc_url)  # reconcile in case DB is stale
        else:
            for attempt in range(1, max_retries + 1):
                try:
                    submit_lrc_job(song_id)
                    wait_for_lrc_completion(song_id, timeout=...)
                    break
                except Exception as e:
                    if attempt == max_retries:
                        db_client.update_recording_status(
                            hash_prefix=hash_prefix, lrc_status="failed"
                        )
                        return SongResult(status="failed", step="lrc", error=str(e))
                    log(f"LRC attempt {attempt} failed: {e}, retrying...")

    return SongResult(status="completed")
```

### Job Completion Waiting

After submitting an LRC job, batch mode needs to detect when it completes. Two approaches:

#### Option A: Poll Analysis Service (current `--wait` pattern)

```python
while not timed_out:
    job = analysis_client.get_job(job_id)
    if job.status in ("completed", "failed"):
        break
    time.sleep(poll_interval)
```

**Pro**: Fast detection (job status updates immediately on completion)
**Con**: Fragile if Analysis Service restarts and loses job state

#### Option B: Poll R2 (reconcile pattern)

```python
while not timed_out:
    if r2_client.lrc_exists(hash_prefix):
        break
    time.sleep(poll_interval)
```

**Pro**: Robust against Analysis Service restarts
**Con**: Slightly higher latency (poll interval + R2 eventual consistency)

**Recommended**: Use Option B (poll R2) for consistency with `--reconcile`. After R2 confirms the file, call `update_recording_lrc()` to update DB. Poll interval: 30s for first 5 min, 60s thereafter.

### Retry Policy

- **Per-step retries**: Each step (download, LRC) retries independently up to `max_retries`
- **Backoff**: Exponential — 10s, 30s, 90s between retries
- **Final failure**: Set `lrc_status='failed'` via `update_recording_status()`, log reason to stdout
- **Continue on failure**: Batch mode does NOT stop when one song fails — it marks it failed and moves on

### Stats Output

When all songs reach terminal state, print:

```
╭─ Batch Summary ──────────────────────────╮
│ Songs processed:    42                    │
│ Completed:          38 (90%)              │
│ Failed:             4 (10%)               │
│ Skipped (R2):       12                    │
│                                          │
│ LRC timing:                              │
│   Average:           45.2s                │
│   Median:            38.1s                │
│   Min/Max:           12.3s / 180.5s       │
│                                          │
│ LRC source:                              │
│   YouTube (existing): 8                   │
│   ASR (generated):   30                   │
│                                          │
│ Failed songs:                            │
│   - 敬拜之歌 1: download failed (3 retries)
│   - 敬拜之歌 2: LRC failed (3 retries)
│   - ...                                  │
╰──────────────────────────────────────────╯
```

### LRC Source Tracking

Stats need to distinguish "LRC already existed on R2" vs "LRC generated by ASR." This is derivable from batch processing:

- If `lrc_exists()` returned a URL before any job was submitted → "YouTube (existing)" or "R2 pre-existing"
- If `lrc_exists()` returned a URL after job submission → "ASR (generated)"

No new DB column needed — tracked in-memory during the batch run.

## `--stdin` Pipe Integration

Enables flexible song selection:

```bash
# Resubmit all failed LRC jobs
sow-admin audio list --lrc-status failed --format ids | sow-admin audio batch --stdin

# Process specific songs
echo -e "song1\nsong2\nsong3" | sow-admin audio batch --stdin

# Combine with grep
sow-admin audio list --format csv | grep "敬拜" | cut -d, -f1 | sow-admin audio batch --stdin
```

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| Song has no recording yet | Create recording (import step), then proceed |
| Song has no YouTube URL | Skip with warning, mark as failed |
| Download succeeds but upload to R2 fails | Retry download step (R2 upload is part of download step) |
| LRC job submitted but Analysis Service crashes | Poll R2 — when service restarts and reprocesses, R2 check succeeds |
| Multiple batch runs on same songs | R2 pre-check makes this idempotent — skips steps with existing R2 output |
| Batch interrupted (Ctrl+C) | Print partial stats, mark in-progress jobs as `processing` (they'll be picked up by `--reconcile` or next batch run) |
| `--skip-download` but audio not on R2 | Skip download step, LRC step will likely fail (no audio to transcribe) |

## Relationship to `--reconcile`

Batch mode and `--reconcile` are complementary:

| Feature | `--reconcile` | `batch` |
|---------|--------------|---------|
| Purpose | Check if jobs are done | Process songs end-to-end |
| Submits new jobs | No | Yes |
| Updates DB | Yes (status only) | Yes (status + retries) |
| Use case | Cron, manual status check | Bulk processing |
| Typical trigger | Automated (cron) | Manual (admin action) |

After a batch run, `--reconcile` can be used to pick up any completions that happened after the batch run ended.

## Future Enhancements (Out of Scope)

- **Parallel processing**: Process multiple songs concurrently with `ThreadPoolExecutor`
- **Analysis step**: Add `analyze` step to the pipeline (requires Docker + ML dependencies)
- **Resume interrupted batch**: Save batch state to file, allow resuming from last checkpoint
- **Progress bar**: Rich progress bar for long-running batches
- **Notification**: Send email/webhook when batch completes
- **Failure details in DB**: Add `lrc_failed_at` / `lrc_failure_reason` columns for persistent failure tracking (requires schema change — deferred)
