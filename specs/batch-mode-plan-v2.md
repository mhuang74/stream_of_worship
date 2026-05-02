# Batch Mode: End-to-End Song Processing (v2)

## Problem Summary

Processing a song from catalog to usable state requires multiple manual steps: download audio, submit LRC job, wait for completion, check status, retry failures. For a catalog of 50+ songs, this is tedious and error-prone. There is no coordination layer that submits jobs, monitors progress, and reconciles status automatically across Analysis Service restarts/crashes/hangs.

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
sow-admin audio batch --album "敬拜精选"

# Or resubmit all failed LRC jobs:
sow-admin audio list --lrc-status failed --format ids | sow-admin audio batch --stdin
```

## Design

### Core Concept

Batch mode is a **submit-all-then-poll** orchestrator that:

1. Selects songs via filters (album, song name, status, etc.)
2. Downloads audio for all songs that need it (sequential, R2-first check)
3. Submits all LRC jobs at once (no waiting between submissions)
4. Polls all jobs in round-robin using **hybrid polling** (service-first, R2-fallback)
5. Reconciles on Ctrl+C: check R2, mark completed or failed
6. Prints summary stats when all songs reach terminal state

### Key Design Decisions (v2 Changes)

1. **Submit-all-then-poll**: No per-song sequential wait. Submit all jobs, then poll all in a loop. Jobs queue on the service and are processed according to service concurrency limits.
2. **Hybrid polling**: Poll Analysis Service first (detects `failed` with reasons, detects `completed` quickly). Fall back to R2 poll on connection errors. Resubmit on 404 (job purged or lost).
3. **No per-song timeout**: Use batch-level staleness timer instead (warns after 2hr of no completions, resets on any completion).
4. **Staleness timeout for `processing`**: Songs stuck in `lrc_status='processing'` for >2hr are treated as lost and resubmitted.
5. **`download_status` column**: New DB column to track download state separately from LRC state. Prevents conflating download failures with LRC failures.
6. **Ctrl+C reconciliation**: On interrupt, check R2 for each in-progress song. Mark completed if R2 has the file, failed otherwise.
7. **R2 confirmation check**: After service reports `completed`, verify R2 has the file (3 retries at 5s intervals) to handle eventual consistency.
8. **No schema changes for LRC failure tracking**: Failure details logged to stdout only. `download_status` is the only new column.

### Why Submit-All-Then-Poll (vs. per-song sequential)

The Analysis Service uses a SQLite-backed `JobQueue` with:
- `max_concurrent_lrc=2` (only 2 LRC jobs process simultaneously)
- Job persistence across restarts (same job ID, stage="requeued")
- 7-day purge window for completed/failed jobs

Submitting all jobs upfront means the service can start processing the first jobs immediately while the admin CLI is still submitting the rest. Per-song sequential waiting would leave the service idle between submissions.

### Why Hybrid Polling (vs. Option A or B from v1)

| Approach | Detects `failed`? | Survives service restart? | Detects completion? |
|----------|--------------------|---------------------------|---------------------|
| Poll service only (v1 Option A) | Yes, with reason | No (404 on restart) | Fast |
| Poll R2 only (v1 Option B) | No (silent timeout) | Yes | Slow (poll interval) |
| **Hybrid (v2)** | **Yes, with reason** | **Yes (R2 fallback + resubmit on 404)** | **Fast** |

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
| `--download-status TEXT` | Filter by download status (`pending`, `processing`, `failed`) |
| `--analysis-status TEXT` | Filter by analysis status |
| `--stdin` | Read song IDs from stdin (pipe-friendly) |
| `--limit INT` | Maximum number of songs to process |

### Processing Options

| Option | Description |
|--------|-------------|
| `--skip-download` | Skip download step (assume audio already on R2) |
| `--skip-lrc` | Skip LRC step |
| `--stale-after INT` | Minutes after which a `processing` song is treated as lost (default: 120) |
| `--dry-run` | Show what would be processed without executing |

### Output Options

| Option | Description |
|--------|-------------|
| `--format TEXT` | Output format: `rich` (default), `json` |

## File Changes

| File | Change |
|------|--------|
| `src/stream_of_worship/admin/commands/audio.py` | Add `batch()` command |
| `src/stream_of_worship/admin/db/client.py` | Add `download_status` column, `update_recording_download()` method |
| `src/stream_of_worship/admin/db/models.py` | Add `download_status` field to `Recording` model, update `from_row()` indices |
| `src/stream_of_worship/admin/services/r2.py` | No changes (uses `lrc_exists()`, `audio_exists()` from reconciliation v2) |

## Detailed Design

### Step Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 1: Download (sequential per-song)                            │
│                                                                     │
│  For each song:                                                     │
│    Check R2: audio_exists? ──yes──▶ skip                            │
│                    │                                                │
│                   no                                                 │
│                    ▼                                                │
│    download_audio() → upload to R2 → set download_status=completed │
│    (on failure: set download_status=failed, skip LRC for this song)│
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 2: Submit All LRC Jobs                                      │
│                                                                     │
│  For each song needing LRC:                                        │
│    Check R2: lrc_exists? ──yes──▶ update_recording_lrc(), skip     │
│                    │                                                │
│                   no                                                 │
│                    ▼                                                │
│    lrc_status=processing + not stale? ──yes──▶ reuse existing job_id│
│                    │                                                │
│                   no (or stale/lost)                                │
│                    ▼                                                │
│    analysis_client.submit_lrc() → set lrc_status=processing        │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 3: Poll All Jobs (hybrid polling)                           │
│                                                                     │
│  while has_incomplete_jobs():                                       │
│    for each (song_id, job_id) in incomplete_jobs:                   │
│      try:                                                           │
│        job = analysis_client.get_job(job_id)                        │
│        if job.status == "completed":                                │
│          confirm R2 has LRC (3x retry @ 5s)                        │
│          update_recording_lrc()                                     │
│          mark song done                                             │
│        elif job.status == "failed":                                 │
│          set lrc_status="failed", log error_message                │
│          mark song done                                             │
│        else: (queued/processing)                                    │
│          continue polling                                           │
│      except 404:                                                    │
│        Job lost — check R2: if found → completed, else resubmit   │
│      except ConnectionError:                                        │
│        Fall back to R2 check for this cycle                        │
│                                                                     │
│    Staleness check:                                                 │
│      if no completions for 2hr → print WARNING, reset timer       │
│                                                                     │
│    sleep(poll_interval)  # 30s                                      │
│                                                                     │
│  On Ctrl+C → reconcile all in-progress via R2                     │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Phase 4: Print Stats                                              │
└─────────────────────────────────────────────────────────────────────┘
```

### Processing Loop

```python
def batch(
    filters, skip_download, skip_lrc, stale_after_minutes, dry_run, format
):
    songs = resolve_song_ids(filters)
    if dry_run:
        print_dry_run(songs)
        return

    results = {}

    # Phase 1: Download
    if not skip_download:
        for song_id in songs:
            recording = get_or_create_recording(song_id)
            result = download_if_needed(song_id, recording)
            results[song_id] = result  # may be skipped, completed, or failed

    # Phase 2: Submit LRC jobs
    active_jobs = {}  # song_id -> job_id
    if not skip_lrc:
        songs_needing_lrc = [
            sid for sid in songs
            if results.get(sid, {}).get("download") != "failed"
            and results.get(sid, {}).get("lrc") != "completed"
        ]
        for song_id in songs_needing_lrc:
            recording = get_recording(song_id)
            job_id = submit_or_attach_lrc(song_id, recording, stale_after_minutes)
            if job_id:
                active_jobs[song_id] = job_id

    # Phase 3: Poll all jobs
    poll_all_jobs(active_jobs, results, stale_after_minutes)

    # Phase 4: Print stats
    print_stats(results)


def download_if_needed(song_id, recording):
    """Download audio if not on R2. Sets download_status in DB."""
    hash_prefix = recording.hash_prefix

    if r2_client.audio_exists(hash_prefix):
        db_client.update_recording_download(hash_prefix, "completed")
        return {"download": "skipped_r2"}

    db_client.update_recording_download(hash_prefix, "processing")
    try:
        download_audio(song_id)  # includes R2 upload
        db_client.update_recording_download(hash_prefix, "completed")
        return {"download": "completed"}
    except Exception as e:
        db_client.update_recording_download(hash_prefix, "failed")
        return {"download": "failed", "error": str(e)}


def submit_or_attach_lrc(song_id, recording, stale_after_minutes):
    """Submit LRC job or attach to existing one. Returns job_id or None."""
    hash_prefix = recording.hash_prefix

    # R2 already has LRC
    lrc_url = r2_client.lrc_exists(hash_prefix)
    if lrc_url:
        db_client.update_recording_lrc(hash_prefix, lrc_url)
        return None  # nothing to poll

    # Existing job — check if stale
    if recording.lrc_status == "processing" and recording.lrc_job_id:
        staleness = datetime.now(timezone.utc) - recording.updated_at
        if staleness < timedelta(minutes=stale_after_minutes):
            return recording.lrc_job_id  # attach to existing
        else:
            log(f"Job {recording.lrc_job_id} is stale ({staleness}), resubmitting")

    # Submit new job
    job = analysis_client.submit_lrc(...)
    db_client.update_recording_status(
        hash_prefix=hash_prefix,
        lrc_status="processing",
        lrc_job_id=job.job_id,
    )
    return job.job_id


def poll_all_jobs(active_jobs, results, stale_after_minutes):
    """Poll all active jobs until terminal state or Ctrl+C."""
    poll_interval = 30.0
    last_completion_time = time.time()
    STALE_WARNING_SECONDS = stale_after_minutes * 60

    try:
        while active_jobs:
            any_completed_this_cycle = False

            for song_id in list(active_jobs.keys()):
                job_id = active_jobs[song_id]
                try:
                    job = analysis_client.get_job(job_id)

                    if job.status == "completed":
                        # R2 confirmation check (eventual consistency)
                        recording = get_recording(song_id)
                        lrc_url = confirm_r2_lrc(recording.hash_prefix)
                        if lrc_url:
                            db_client.update_recording_lrc(
                                recording.hash_prefix, lrc_url
                            )
                            results[song_id]["lrc"] = "completed"
                        else:
                            # Rare: service says done but R2 doesn't have it yet
                            log(f"WARNING: Job {job_id} completed but LRC not on R2 yet")
                            continue  # will retry R2 check next cycle
                        del active_jobs[song_id]
                        any_completed_this_cycle = True

                    elif job.status == "failed":
                        db_client.update_recording_status(
                            hash_prefix=recording.hash_prefix,
                            lrc_status="failed",
                        )
                        results[song_id]["lrc"] = "failed"
                        results[song_id]["lrc_error"] = job.error_message
                        del active_jobs[song_id]
                        any_completed_this_cycle = True

                    # else: queued/processing — continue polling

                except AnalysisServiceError as e:
                    if e.status_code == 404:
                        # Job lost — check R2, resubmit if needed
                        recording = get_recording(song_id)
                        lrc_url = r2_client.lrc_exists(recording.hash_prefix)
                        if lrc_url:
                            db_client.update_recording_lrc(
                                recording.hash_prefix, lrc_url
                            )
                            results[song_id]["lrc"] = "completed"
                            del active_jobs[song_id]
                            any_completed_this_cycle = True
                        else:
                            log(f"Job {job_id} lost (404), resubmitting...")
                            new_job_id = submit_or_attach_lrc(
                                song_id, recording, stale_after_minutes
                            )
                            if new_job_id:
                                active_jobs[song_id] = new_job_id
                            else:
                                del active_jobs[song_id]
                    else:
                        log(f"Service error polling {job_id}: {e}")
                        # Fall back to R2 check
                        recording = get_recording(song_id)
                        lrc_url = r2_client.lrc_exists(recording.hash_prefix)
                        if lrc_url:
                            db_client.update_recording_lrc(
                                recording.hash_prefix, lrc_url
                            )
                            results[song_id]["lrc"] = "completed"
                            del active_jobs[song_id]
                            any_completed_this_cycle = True

                except (ConnectionError, RequestException):
                    # Service unreachable — fall back to R2 check
                    recording = get_recording(song_id)
                    lrc_url = r2_client.lrc_exists(recording.hash_prefix)
                    if lrc_url:
                        db_client.update_recording_lrc(
                            recording.hash_prefix, lrc_url
                        )
                        results[song_id]["lrc"] = "completed"
                        del active_jobs[song_id]
                        any_completed_this_cycle = True

            if any_completed_this_cycle:
                last_completion_time = time.time()

            # Staleness warning
            elapsed_since_completion = time.time() - last_completion_time
            if elapsed_since_completion > STALE_WARNING_SECONDS:
                console.print(
                    f"[yellow]WARNING: No jobs completed in "
                    f"{int(elapsed_since_completion // 3600)}h "
                    f"{int((elapsed_since_completion % 3600) // 60)}m. "
                    f"Analysis Service may be hung or restarting.[/yellow]"
                )
                console.print(
                    "[yellow]Press Ctrl+C to stop and reconcile, "
                    "or wait for service recovery.[/yellow]"
                )
                last_completion_time = time.time()  # reset to avoid spam

            print_progress(active_jobs, results)

            if active_jobs:
                time.sleep(poll_interval)

    except KeyboardInterrupt:
        reconcile_on_interrupt(active_jobs, results)


def confirm_r2_lrc(hash_prefix, max_retries=3, retry_delay=5.0):
    """Confirm LRC file exists on R2 after service reports completion.

    Handles R2 eventual consistency by retrying.
    Returns lrc_url if found, None if not confirmed.
    """
    for attempt in range(max_retries):
        lrc_url = r2_client.lrc_exists(hash_prefix)
        if lrc_url:
            return lrc_url
        if attempt < max_retries - 1:
            time.sleep(retry_delay)
    return None


def reconcile_on_interrupt(active_jobs, results):
    """Reconcile status for in-progress jobs on Ctrl+C."""
    console.print("\n[yellow]Batch interrupted. Reconciling status...[/yellow]")

    for song_id, job_id in active_jobs.items():
        recording = get_recording(song_id)
        hash_prefix = recording.hash_prefix

        # Check R2 for LRC
        lrc_url = r2_client.lrc_exists(hash_prefix)
        if lrc_url:
            db_client.update_recording_lrc(hash_prefix, lrc_url)
            results[song_id]["lrc"] = "completed"
            console.print(f"  {song_id}: LRC found on R2 (completed)")
        else:
            db_client.update_recording_status(
                hash_prefix=hash_prefix, lrc_status="failed"
            )
            results[song_id]["lrc"] = "failed"
            results[song_id]["lrc_error"] = "Batch interrupted, LRC not on R2"
            console.print(f"  {song_id}: LRC not on R2 (marked failed)")

    console.print(
        "[dim]Tip: Run 'sow-admin audio status --reconcile' later to catch "
        "late completions after the service finishes processing.[/dim]"
    )
```

### Polling Progress Display

Each polling cycle prints a concise status line:

```
⏳ Polling 38 jobs... 5 completed, 2 failed, 31 in progress (elapsed: 12m 30s)
```

When a job transitions to terminal state:

```
✅ 敬拜之歌1 — LRC completed (45.2s)
❌ 敬拜之歌2 — LRC failed: Audio too short for transcription
```

### R2 Confirmation Check

After the Analysis Service reports a job as `completed`, the LRC file may not be immediately visible on R2 due to eventual consistency. The confirmation check retries:

```
attempt 1: lrc_exists() → None
wait 5s
attempt 2: lrc_exists() → None
wait 5s
attempt 3: lrc_exists() → s3://bucket/abc123/lyrics.lrc ✓
```

If all 3 retries fail, the job stays in `active_jobs` and will be re-checked on the next polling cycle (30s later). This avoids falsely marking a completed job as failed.

### Ctrl+C Reconciliation

On `KeyboardInterrupt`, the batch loop:

1. Stops polling immediately
2. For each song still in `active_jobs` (in-progress):
   - Checks R2 for LRC file
   - If found → `update_recording_lrc()` (completed + auto-publish)
   - If not found → `update_recording_status(lrc_status="failed")`
3. Prints partial stats
4. Prints tip about running `--reconcile` later for late completions

**Important**: A job may complete on the service moments after Ctrl+C. The admin should know `--reconcile` can catch these.

### Retry Policy (simplified from v1)

v1 had per-step exponential backoff retries. v2 simplifies:

- **Download**: No automatic retry (single attempt per song). If download fails, set `download_status="failed"` and skip LRC for that song. Admin can re-run batch after fixing the issue.
- **LRC**: No per-song retry. Submit once, poll until terminal. On service 404 (job lost), resubmit once. On service hang, staleness timer warns admin who decides whether to Ctrl+C.
- **Rationale**: The Analysis Service has its own internal retry/requeue on restart. The batch orchestrator doesn't need to duplicate this. Per-song retries with exponential backoff added complexity without improving resilience — the staleness timer + Ctrl+C reconciliation covers the real failure modes.

### Stats Output

When all songs reach terminal state (or after Ctrl+C reconciliation), print:

```
╭─ Batch Summary ──────────────────────────╮
│ Songs processed:    42                    │
│                                          │
│ Downloads:                               │
│   Completed:          30                  │
│   Skipped (R2):       10                  │
│   Failed:             2                   │
│                                          │
│ LRC:                                     │
│   Completed:          38                  │
│   Failed:             2                   │
│   Skipped (R2):       8                   │
│   Skipped (dl failed):2                   │
│                                          │
│ LRC source:                              │
│   R2 pre-existing:    8                   │
│   ASR (generated):   30                   │
│                                          │
│ LRC timing (ASR jobs only):              │
│   Average:           45.2s                │
│   Median:            38.1s                │
│   Min/Max:           12.3s / 180.5s       │
│                                          │
│ Failed downloads:                        │
│   - 敬拜之歌 1: YouTube download error   │
│   - 敬拜之歌 3: No YouTube URL           │
│                                          │
│ Failed LRC:                              │
│   - 敬拜之歌 2: Audio too short           │
│   - 敬拜之歌 4: Whisper transcription err │
╰──────────────────────────────────────────╯
```

### LRC Source Tracking

Stats distinguish "LRC already existed on R2" vs "LRC generated by ASR":

- If `lrc_exists()` returned a URL before any job was submitted → "R2 pre-existing"
- If `lrc_exists()` returned a URL after job submission + completion → "ASR (generated)"

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

## Database Schema Change

### New Column: `download_status`

```sql
ALTER TABLE recordings ADD COLUMN download_status TEXT DEFAULT 'pending';
```

Values: `pending | processing | completed | failed`

### New Method: `update_recording_download()`

```python
def update_recording_download(
    self,
    hash_prefix: str,
    download_status: str,
) -> None:
    """Update download status for a recording."""
    # Validates download_status against allowed values
    # Sets updated_at = datetime('now')
```

### Model Update: `Recording`

Add `download_status: str = "pending"` field and update `from_row()` column index.

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| Song has no recording yet | Create recording (import step), then proceed |
| Song has no YouTube URL | Skip download with warning, set `download_status="failed"`, skip LRC |
| Download succeeds but upload to R2 fails | Set `download_status="failed"`, skip LRC. R2 upload is part of download step. |
| LRC job submitted, service crashes, then restarts | Service re-queues the job (same job_id). Batch polls using same job_id — will see `queued/processing` again. |
| LRC job submitted, service DB is wiped (catastrophic) | `get_job()` returns 404. Batch checks R2 → no file → resubmits. |
| Job completed but R2 eventual consistency | `confirm_r2_lrc()` retries 3x at 5s. If still not found, re-check next polling cycle (30s). Never falsely marks as failed. |
| Song stuck in `lrc_status="processing"` for hours | Staleness timeout (default 2hr). If `updated_at` is older than threshold, resubmit. |
| Multiple batch runs on same songs | R2 pre-check makes this idempotent — skips steps with existing R2 output. Existing `processing` jobs are reused (not duplicated). |
| Batch interrupted (Ctrl+C) | Reconcile via R2: mark completed if file exists, failed if not. Print partial stats. |
| `--skip-download` but audio not on R2 | Skip download step, LRC step will likely fail (no audio to transcribe). Service will report job as `failed` with error. |
| Service 7-day purge deletes completed job | `get_job()` returns 404. Batch checks R2 first → if LRC file exists, marks completed. Only problematic if purge happens AND R2 file is deleted. |
| Service connection timeout during poll | Fall back to R2 check for that cycle. Next cycle retries service. |
| Two batch runs simultaneously on overlapping songs | Allowed (idempotent). R2 pre-check + processing status check prevent duplicate work. First to write R2 wins. |

## Relationship to `--reconcile`

Batch mode and `--reconcile` are complementary:

| Feature | `--reconcile` | `batch` |
|---------|--------------|---------|
| Purpose | Check if jobs are done | Process songs end-to-end |
| Submits new jobs | No | Yes |
| Updates DB | Yes (status only) | Yes (status + download_status) |
| Handles `failed` state | No (only → completed) | Yes (sets `lrc_status="failed"`) |
| Resilience | R2-only check | Hybrid (service + R2) |
| Ctrl+C handling | N/A | Reconciles via R2 |
| Use case | Cron, manual status check | Bulk processing |
| Typical trigger | Automated (cron) | Manual (admin action) |

After a batch run (especially after Ctrl+C), `--reconcile` can be used as a cron job to pick up any completions that happened after the batch run ended.

## Design Walkthrough: Failure Scenarios

### Scenario: Analysis Service hangs mid-job

```
1. Batch submits 50 LRC jobs
2. Service processes 10, then hangs on job #11
3. Batch polls every 30s — sees jobs #1-10 completed, #11 still processing, #12-50 queued
4. After 2hr with no completions → WARNING printed
5. Admin presses Ctrl+C
6. Reconcile: R2 check for jobs #11-50
   - Jobs #12-20 actually completed while admin was reading the warning → found on R2 → marked completed
   - Job #11 and #21-50 not on R2 → marked failed
7. Admin restarts Analysis Service
8. Admin runs: sow-admin audio list --lrc-status failed --format ids | sow-admin audio batch --stdin
9. Batch resubmits failed jobs, service processes them
```

### Scenario: Analysis Service crashes and restarts

```
1. Batch submits 50 LRC jobs, sets lrc_status="processing" in DB
2. Service crashes while processing job #5
3. Batch polls — ConnectionError on all jobs
4. Batch falls back to R2 check — no new completions
5. Service restarts — JobQueue.initialize() recovers all jobs:
   - Job #5 was processing → re-queued (same job_id, stage="requeued")
   - Jobs #6-50 were queued → still queued (same job_ids)
6. Batch's next poll cycle — get_job() succeeds again
7. Job #5 completes (re-processed from scratch)
8. All jobs eventually complete normally
```

### Scenario: Ctrl+C then late completion

```
1. Batch has 10 jobs in-progress
2. Admin presses Ctrl+C
3. Reconcile: R2 check for all 10 — none found yet → all marked "failed"
4. 5 minutes later, service finishes processing 5 of those jobs
5. Admin runs: sow-admin audio status --reconcile
6. --reconcile finds LRC files on R2 for 5 songs → marks them "completed"
7. Remaining 5 songs stay "failed" → admin can re-batch them
```

## Future Enhancements (Out of Scope)

- **Parallel downloads**: Download multiple songs concurrently with `ThreadPoolExecutor`
- **Parallel LRC processing**: Process multiple songs concurrently on the service side (requires increasing `max_concurrent_lrc`)
- **Analysis step**: Add `analyze` step to the pipeline (requires Docker + ML dependencies)
- **Resume interrupted batch**: Save batch state to file, allow resuming from last checkpoint
- **Progress bar**: Rich progress bar for long-running batches
- **Notification**: Send email/webhook when batch completes
- **Failure details in DB**: Add `lrc_failed_at` / `lrc_failure_reason` columns for persistent failure tracking (requires schema change — deferred)
- **Clear stale `lrc_job_id`**: Method to clear stale job IDs from DB (cosmetic — poll logic handles it)
