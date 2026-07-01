# Batch Unified Poll Loop + Parallel Downloads (v1)

> Supersedes the phase-barrier design in `specs/scale-key-bpm-analysis-batch-v4.md`
> (§ "Current State" and `_process_batch` at `audio.py:5084`). v4's step flags,
> manifest, and `--resume` surface are preserved; the phase barriers and sequential
> downloads are replaced.

## Problem

The current `_process_batch` (`commands/audio.py:5084–5598`) has **three phase
barriers**:

```
Download ALL → Poll LRC ALL (BARRIER) → Poll Analysis ALL (BARRIER) → Poll Embedding ALL (BARRIER)
```

If one song's LRC job falls back to local ASR (20–40 min), every other song's
analysis and embedding work waits — even if their LRC finished 20 minutes ago. The
same head-of-line blocking repeats at the analysis→embedding barrier.

Additionally, downloads are sequential. For a batch of 30 songs, the download phase
alone can take 30+ minutes of wall-time where only one song downloads at a time.

### Concrete Example

10 songs, one has ASR fallback (30 min LRC), others take ~2 min LRC. Each analysis
takes ~5 min, embedding ~1 min.

**Current (barrier design):**
- LRC phase: 30 min (blocked by ASR song)
- Analysis phase: 5 min (all at once, then wait)
- Embedding phase: 1 min
- Total: ~36 min, **0/10 songs fully complete at 8 min**

**New (streaming design):**
- 9 songs finish LRC at ~2 min → immediately start analysis (5 min) → embedding (1 min)
  = 8 min for 9 songs
- ASR song finishes LRC at 30 min → analysis (5 min) → embedding (1 min) = 36 min
- Total: 36 min, but **9/10 songs fully complete at 8 min**

## Design

### Core Concept: Per-Song Streaming Pipeline with Unified Poll Loop

Replace the serial phase-barrier architecture with a per-song state machine driven
by a single unified poll loop. Each song flows through the pipeline independently:
as soon as song A's LRC completes, its analysis job is submitted immediately — even
while song B's LRC is still running. Downloads run concurrently via a thread pool.

```
┌─────────────────────────────────────────────────────────────┐
│  Phase 0: Parallel Download (ThreadPoolExecutor)              │
│                                                               │
│  Submit N songs to pool (max_workers = --download-concurrency)│
│  Each worker:                                                 │
│    1. Download audio (yt-dlp) + upload to R2                  │
│    2. Create recording in DB (per-thread connection)         │
│    3. Eagerly submit LRC job (if both download + lrc steps)  │
│  As each future completes → main thread prints result        │
│  active_jobs[(song_id, "lrc")] populated by workers           │
└───────────────────────────┬─────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  Initial Readiness Sweep                                      │
│                                                               │
│  For each song not already in active_jobs, walk the step     │
│  chain and submit the first step that is ready:              │
│    download → lrc → analyze → embedding                       │
│  This also seeds active_jobs with songs that skipped download │
│  (R2 preexisting) or already have LRC/analysis completed.     │
└───────────────────────────┬─────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  Unified Poll Loop (single loop, all job types)              │
│                                                               │
│  while active_jobs:                                          │
│    for each (song_id, step) in active_jobs:                  │
│      poll job status                                         │
│      if terminal (completed/failed):                         │
│        handle completion (update DB, results, manifest)      │
│        del active_jobs[(song_id, step)]                      │
│        _advance_song(song_id, step) → may submit next step   │
│    flush manifest                                            │
│    print progress                                            │
│    sleep(poll_interval)                                      │
└─────────────────────────────────────────────────────────────┘
```

### Active Jobs Structure

Single dict keyed by `(song_id, step)`:

```python
active_jobs: dict[tuple[str, str], str] = {}
# (song_id, "lrc" | "analyze" | "embedding") -> job_id
```

A song has at most one active job at any time (can't be doing LRC and analysis
simultaneously), but different songs can be on different steps. This is what
eliminates head-of-line blocking.

### `_advance_song` Cascade Logic

When a job completes, `_advance_song` walks the step chain forward and submits the
next selected step that is ready. If a step is already completed (e.g., LRC
previously generated, analysis from prior run), it skips it and tries the next.

```
chain = ["download", "lrc", "analyze", "embedding"]

def _advance_song(song_id, completed_step, selected_steps, ...):
    next_idx = chain.index(completed_step) + 1
    for step in chain[next_idx:]:
        if step not in selected_steps:
            continue
        status = _submit_step(song_id, step, ...)
        if status == "submitted":
            active_jobs[(song_id, step)] = job_id
            return  # job is now active, will be polled
        elif status in ("skipped_r2", "skipped_completed", "skipped_no_lyrics",
                        "skipped_no_recording", "skipped_up_to_date"):
            continue  # try next step in chain
        else:  # failed
            results[song_id][step] = "failed"
            return
```

This enables cascading: if LRC is already done (R2 preexisting), `_advance_song`
skips it and tries analysis. If analysis is already done, tries embedding — all in
one call, no phase barrier.

## New / Refactored Functions

| # | Function | Purpose | Source |
|---|---|---|---|
| 1 | `_submit_analysis_for_song(...)` | Submit/reuse analysis job for one song | Extract from Phase 3 submit loop (audio.py:5341–5473) |
| 2 | `_submit_embedding_for_song(...)` | Submit embedding for one song | Extract from Phase 4 submit loop (audio.py:5500–5571) |
| 3 | `_handle_lrc_completion(...)` | Process completed/failed/404 LRC job | Extract from `_poll_all_jobs` (audio.py:5640–5850) |
| 4 | `_handle_analysis_completion(...)` | Process completed/failed/404 analysis job | Extract from `_poll_analysis_jobs` (audio.py:6020–6275) |
| 5 | `_handle_embedding_completion(...)` | Process completed/failed/404 embedding job | Extract from `_poll_embedding_jobs` (audio.py:6357–6530) |
| 6 | `_advance_song(song_id, completed_step, ...)` | Dispatcher: submit next selected step | New |
| 7 | `_download_worker(song_id, song, database_url, r2_client, ...)` | Per-thread download + eager LRC | New (wraps existing `_download_and_create_recording` + `_submit_lrc_for_song`) |
| 8 | `_unified_poll_loop(active_jobs, results, ...)` | Single loop polling all job types, submits next on completion | New (replaces the three poll functions' while-loops) |

### Functions to Remove

After extraction, the three old poll functions are no longer called by either
`_process_batch` or `_resume_from_manifest`:

- `_poll_all_jobs` (audio.py:5601–5880) — ~280 lines
- `_poll_analysis_jobs` (audio.py:5985–6324) — ~340 lines
- `_poll_embedding_jobs` (audio.py:6326–6575) — ~250 lines

Total: ~870 lines removed. The completion handling logic lives on in the extracted
`_handle_*_completion` helpers.

## Thread-Safety: Parallel Downloads

### Problem

`DatabaseClient` uses a **single shared psycopg3 connection** (`autocommit=True`,
no pool). `ConnectionProvider` (`db/connection.py:17–88`) guards connection
lifecycle with a `threading.Lock` but does **not** serialize query execution.
Concurrent threads sharing one connection will corrupt cursor result streams and
race on `transaction()` / `rollback()`.

### Solution: Per-Thread DatabaseClient

Each download worker creates its own `ConnectionProvider` + `DatabaseClient` with
the same `database_url`:

```python
def _download_worker(song_id, song, database_url, r2_client, analysis_client,
                    force, stale_after_minutes, selected_steps,
                    results, results_lock, manifest_lock,
                    _add_manifest_entry_fn):
    # Per-thread DB connection (psycopg3 single-conn is NOT thread-safe)
    provider = ConnectionProvider(database_url)
    thread_db = DatabaseClient(provider)
    try:
        recording, error = _download_and_create_recording(
            song_id, song, thread_db, r2_client, Console(quiet=True)
        )
        if not recording:
            with results_lock:
                results[song_id]["download"] = "failed"
                results[song_id]["error"] = error
            return song_id, None, error

        with results_lock:
            results[song_id]["download"] = "completed"

        # Eager LRC submission (uses thread_db, analysis_client — both thread-safe)
        if "lrc" in selected_steps:
            with results_lock, manifest_lock:
                status = _submit_lrc_for_song(
                    song_id, thread_db, analysis_client, r2_client,
                    force, stale_after_minutes, Console(quiet=True),
                    results, active_jobs, lrc_attempted, _add_manifest_entry_fn
                )
        return song_id, recording, None
    finally:
        provider.close()
```

### Thread-Safety of Shared Resources

| Resource | Thread-safe? | Mitigation |
|---|---|---|
| `results` dict | No — multiple workers + poll loop | `threading.Lock` (`results_lock`) |
| `manifest_entries` list | No | `threading.Lock` (`manifest_lock`) |
| `active_jobs` dict | Written by workers under `results_lock`; read-only by poll loop (single-threaded) | Protected during download phase |
| `DatabaseClient` | No — single shared connection | Per-thread `ConnectionProvider` |
| `R2Client` | Yes — HTTP-based boto3 client | No action needed |
| `AnalysisClient` | Yes — HTTP-based (requests/httpx) | No action needed |
| `Console` | Not guaranteed thread-safe | Workers use `Console(quiet=True)`; main thread prints results |

### Concurrency Limit

`--download-concurrency` CLI flag (default: 3). YouTube rate-limits aggressive
concurrent downloads. 3 is a safe default that cuts download wall-time by ~3x
without triggering throttling.

## New `_process_batch` Implementation

### Signature

```python
def _process_batch(
    db_client: DatabaseClient,
    r2_client: R2Client,
    analysis_client: AnalysisClient,
    song_ids: list[str],
    selected_steps: List[str],
    force: bool,
    analysis_tier: str,
    stale_after_minutes: int,
    console: Console,
    database_url: str,           # NEW: for per-thread connections
    download_concurrency: int,   # NEW: max parallel downloads
) -> dict:
```

### Flow

```
1. Initialize shared state:
   results: Dict[str, dict] = {sid: {} for sid in song_ids}
   active_jobs: Dict[tuple[str, str], str] = {}
   lrc_attempted: set = set()
   results_lock = threading.Lock()
   manifest_lock = threading.Lock()

2. Phase 0: Parallel Downloads (if "download" in selected_steps)
   - Build work list: [(sid, db_client.get_song(sid)) for sid in song_ids]
   - ThreadPoolExecutor(max_workers=download_concurrency)
   - Submit _download_worker for each song
   - as_completed(): main thread prints result for each
   - Workers eagerly submit LRC (populating active_jobs under lock)
   - After all futures complete: flush manifest

3. Initial Readiness Sweep (for all songs)
   - For each song_id:
     - If (song_id, "lrc") already in active_jobs → skip (worker submitted it)
     - Else: _advance_song(song_id, "download", ...) → submits first ready step
   - This handles:
     - Songs that skipped download (R2 preexisting)
     - Songs where download step was not selected
     - Songs whose LRC/analysis is already completed from prior runs
   - Flush manifest

4. Unified Poll Loop
   _unified_poll_loop(active_jobs, results, db_client, analysis_client, r2_client,
                      selected_steps, force, analysis_tier, stale_after_minutes,
                      console, manifest_entries, _add_manifest_entry)
   - Polls all active jobs (LRC + analysis + embedding) in a single pass
   - On completion: handle_step_completion → _advance_song → may add new job
   - Flush manifest each cycle
   - sleep(poll_interval=30s)

5. Return results
```

## `_unified_poll_loop` Implementation

```python
def _unified_poll_loop(
    active_jobs: Dict[tuple[str, str], str],
    results: dict,
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    r2_client: R2Client,
    selected_steps: List[str],
    force: bool,
    analysis_tier: str,
    stale_after_minutes: int,
    console: Console,
    manifest_entries: List[dict],
    _add_manifest_entry: Any,
) -> None:
    poll_interval = 30.0
    batch_start_time = time.time()
    last_completion_time = time.time()
    stale_warning_seconds = stale_after_minutes * 60
    retried: set = set()
    resubmit_counts: Dict[tuple[str, str], int] = {}
    max_resubmits = 3

    try:
        while active_jobs:
            any_completed_this_cycle = False

            for key in list(active_jobs.keys()):
                song_id, step = key
                job_id = active_jobs[key]

                try:
                    job = analysis_client.get_job(job_id)
                    terminal = False

                    if step == "lrc":
                        terminal = _handle_lrc_completion(
                            song_id, job, job_id, db_client, analysis_client,
                            r2_client, results, _add_manifest_entry, console,
                            force, stale_after_minutes, resubmit_counts,
                            max_resubmits, active_jobs, key
                        )
                    elif step == "analyze":
                        terminal = _handle_analysis_completion(
                            song_id, job, job_id, db_client, analysis_client,
                            results, _add_manifest_entry, console,
                            analysis_tier
                        )
                    elif step == "embedding":
                        terminal = _handle_embedding_completion(
                            song_id, job, job_id, db_client, analysis_client,
                            results, _add_manifest_entry, console
                        )

                    if terminal:
                        del active_jobs[key]
                        any_completed_this_cycle = True
                        # Cascade: submit next step immediately
                        _advance_song(
                            song_id, step, selected_steps, db_client,
                            analysis_client, r2_client, force,
                            analysis_tier, stale_after_minutes, console,
                            results, active_jobs, _add_manifest_entry
                        )

                except AnalysisServiceError as e:
                    if e.status_code == 404:
                        # Job lost — step-specific recovery
                        _handle_lost_job(song_id, step, job_id, ...)
                    elif key not in retried and _is_retryable_poll_error(e):
                        retried.add(key)
                        console.print(f"  [yellow]→ {song_id}/{step}: transient error...[/yellow]")
                    else:
                        _mark_failed(song_id, step, str(e), ...)
                        del active_jobs[key]
                        any_completed_this_cycle = True
                except Exception as e:
                    console.print(f"  [yellow]→ Error polling {song_id}/{step}: {e}[/yellow]")

            if any_completed_this_cycle:
                last_completion_time = time.time()

            # Staleness warning
            elapsed_since_completion = time.time() - last_completion_time
            if elapsed_since_completion > stale_warning_seconds and active_jobs:
                console.print(f"[yellow]⚠ No jobs completed in "
                              f"{int(elapsed_since_completion // 60)}m.[/yellow]")
                last_completion_time = time.time()

            # Progress
            if active_jobs:
                _print_unified_progress(active_jobs, results, batch_start_time, console)
                time.sleep(poll_interval)

    except KeyboardInterrupt:
        _reconcile_on_interrupt(active_jobs, results, db_client, r2_client, console)
```

### 404 / Lost Job Handling by Step

| Step | 404 Recovery |
|---|---|
| `lrc` | Check R2 for existing LRC → mark completed. If not found, resubmit (up to `max_resubmits=3`). After max, mark failed. |
| `analyze` | No R2 fallback. Mark failed. Operator can `--force --analyze` later. |
| `embedding` | No R2 fallback. Mark failed. Operator can `--force --embedding` later. |

### `_print_unified_progress`

```python
def _print_unified_progress(active_jobs, results, start_time, console):
    lrc_active = sum(1 for (_, s) in active_jobs if s == "lrc")
    analyze_active = sum(1 for (_, s) in active_jobs if s == "analyze")
    embedding_active = sum(1 for (_, s) in active_jobs if s == "embedding")

    lrc_done = sum(1 for r in results.values() if r.get("lrc") == "completed")
    analyze_done = sum(1 for r in results.values() if r.get("analyze") == "completed")
    embedding_done = sum(1 for r in results.values() if r.get("embedding") == "completed")
    failed = sum(1 for r in results.values()
                for v in r.values() if v == "failed")

    elapsed = time.time() - start_time
    console.print(
        f"⏳ lrc={lrc_active}({lrc_done}✓) "
        f"analyze={analyze_active}({analyze_done}✓) "
        f"embed={embedding_active}({embedding_done}✓) "
        f"{failed}✗ "
        f"(elapsed: {int(elapsed // 60)}m {int(elapsed % 60)}s)"
    )
```

## Resume Path: Unified Resume

`_resume_from_manifest` is rewritten to reconstruct the unified `active_jobs` dict
and enter `_unified_poll_loop` directly — no phase barriers.

### Implementation

```python
def _resume_from_manifest(manifest_data, manifest_path, db_client,
                          r2_client, analysis_client, stale_after_minutes,
                          console):
    songs = manifest_data.get("songs", [])
    results: Dict[str, dict] = {}
    manifest_entries: List[dict] = list(songs)
    active_jobs: Dict[tuple[str, str], str] = {}

    # ... _add_manifest_entry helper (same as current) ...

    for entry in songs:
        song_id = entry["song_id"]
        step = entry["step"]
        job_id = entry.get("job_id")
        status = entry.get("status", "")

        results.setdefault(song_id, {})

        if status in ("completed", "failed", "abandoned"):
            if status == "failed":
                results[song_id][step] = "failed"
                results[song_id][f"{step}_error"] = entry.get("error_message", "")
            if status == "completed":
                _apply_manifest_writeback(...)
            continue

        if not job_id:
            continue

        # Reconstruct active job
        active_jobs[(song_id, step)] = job_id

    # Enter unified poll loop directly
    selected_steps = manifest_data.get("selected_steps", [])
    analysis_tier = manifest_data.get("analysis_tier", "fast")

    _unified_poll_loop(
        active_jobs, results, db_client, analysis_client, r2_client,
        selected_steps, force=False, analysis_tier=analysis_tier,
        stale_after_minutes=stale_after_minutes, console=console,
        manifest_entries=manifest_entries, _add_manifest_entry=_add_manifest_entry,
    )

    return results
```

This means `--resume` benefits from the same no-barrier streaming as fresh batches.
If the manifest has LRC jobs in "processing" alongside analysis jobs in
"processing", both are polled concurrently, and completions cascade immediately.

## CLI Changes

### New `--download-concurrency` Flag

Added to `batch()` function signature (audio.py:4220):

```python
download_concurrency: int = typer.Option(
    3, "--download-concurrency",
    help="Max concurrent downloads (default: 3)"
),
```

### Passing `database_url` to `_process_batch`

The `batch()` function already loads `config` and calls `get_db_client(config)`.
It will additionally pass `database_url=config.get_connection_url()` and
`download_concurrency=download_concurrency` to the new `_process_batch`:

```python
results = _process_batch(
    db_client=db_client,
    r2_client=r2_client,
    analysis_client=analysis_client,
    song_ids=song_ids,
    selected_steps=selected_steps,
    force=force,
    analysis_tier=analysis_tier,
    stale_after_minutes=stale_after,
    console=console,
    database_url=config.get_connection_url(),       # NEW
    download_concurrency=download_concurrency,       # NEW
)
```

## Files to Modify

| File | Changes |
|---|---|
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | Add `--download-concurrency` flag to `batch()`; rewrite `_process_batch` with new signature + parallel downloads + unified loop; add helpers 1–8; rewrite `_resume_from_manifest` for unified resume; delete `_poll_all_jobs`, `_poll_analysis_jobs`, `_poll_embedding_jobs`; update `_print_progress` → `_print_unified_progress` |
| `ops/admin-cli/tests/admin/test_audio_batch_eager_lrc.py` | Update `_process_batch` calls for new `database_url` + `download_concurrency` params |
| `ops/admin-cli/tests/admin/test_audio_batch_v4.py` | Update if affected by signature changes |
| `ops/admin-cli/tests/admin/test_audio_batch_unified.py` | **New** — tests for `_advance_song`, `_unified_poll_loop`, `_download_worker`, unified resume |

No changes needed to:
- `ops/admin-cli/src/stream_of_worship/db/connection.py` — `ConnectionProvider` already takes `database_url` in constructor
- `ops/admin-cli/src/stream_of_worship/admin/commands/catalog.py` — `get_db_client` already uses `config.get_connection_url()`
- `ops/admin-cli/src/stream_of_worship/admin/db/client.py` — `DatabaseClient` already accepts any `ConnectionProvider`

## Test Strategy

### 1. Unit Tests for Extracted Helpers

- `_submit_analysis_for_song`: verify submit, reuse-stale, skip-completed, skip-no-recording, failed paths
- `_submit_embedding_for_song`: verify submit, skip-up-to-date, skip-no-lyrics, failed paths
- `_handle_lrc_completion`: verify completed (R2 confirmed), failed, cancelled branches
- `_handle_analysis_completion`: verify completed (fast/full tier), failed, cancelled branches
- `_handle_embedding_completion`: verify completed (DB write), failed, cancelled branches

### 2. Integration Tests for `_advance_song`

- LRC completed → analysis submitted immediately
- LRC completed but analysis already completed → embedding submitted
- LRC completed but analysis not in selected_steps → no-op
- LRC completed but no recording → skip to next
- Analysis completed but embedding not selected → no-op
- Each step skipped → cascade continues to next step

### 3. Integration Tests for `_unified_poll_loop`

- Mock `analysis_client.get_job` to return mixed statuses across LRC/analysis/embedding
- Verify songs advance independently: song A completes LRC → analysis submitted, while song B's LRC still processing
- Verify no phase barriers: embedding submitted before all analysis jobs complete
- Verify 404 handling per step type
- Verify Ctrl+C reconciliation

### 4. Thread-Safety Tests for `_download_worker`

- Mock `_download_and_create_recording` + `_submit_lrc_for_song`
- Run N workers concurrently with mock DB
- Verify `results` dict has all songs, no corruption
- Verify `active_jobs` populated without duplicates

### 5. Existing Test Updates

- `test_audio_batch_eager_lrc.py`: add `database_url="postgresql://test"` and `download_concurrency=1` to all `_process_batch` calls
- `test_audio_batch_v4.py`: update if `_process_batch` signature changed

### 6. Unified Resume Test

- Build a manifest with mixed statuses: some "completed", some "processing" across LRC/analysis/embedding
- Verify `active_jobs` reconstructed as `{(song_id, step): job_id}`
- Verify unified poll loop entered directly
- Verify no phase-barrier ordering

## Risk Areas and Mitigations

| Risk | Mitigation |
|---|---|
| Thread-safety of shared `results`/`manifest_entries` | `threading.Lock` around all writes; download workers use `Console(quiet=True)` |
| Per-thread DB connections exhausting pool | `download_concurrency` default=3, max 3 connections — well within Postgres limits |
| `_download_and_create_recording` using module-level state | Verify stateless (it processes one song, creates a recording — should be fine) |
| `--resume` manifest format changes | Manifest format is unchanged; only the consumer (`_resume_from_manifest`) changes |
| Rich Console multi-thread output interleaving | Workers print nothing (quiet=True); all console output from main thread |
| YouTube rate-limiting on concurrent downloads | Default concurrency=3; operator can tune with `--download-concurrency` |

## Locked Decisions

1. **Complete rewrite** of `_process_batch` (not incremental).
2. **Unified resume** — `_resume_from_manifest` enters unified poll loop directly.
3. **Silent workers** — download workers use `Console(quiet=True)`, main thread prints all progress.
4. **`--download-concurrency` default 3** — safe for YouTube, configurable by operator.
5. **Per-thread DatabaseClient** — each download worker creates+destroys its own `ConnectionProvider`.
6. **`active_jobs` keyed by `(song_id, step)` tuple** — one job per song at a time, different songs on different steps.
7. **Old poll functions deleted** — `_poll_all_jobs`, `_poll_analysis_jobs`, `_poll_embedding_jobs` removed after extraction.
8. **Manifest format unchanged** — only the consumer code changes.

## Expected Throughput Improvement

| Scenario | Current (barrier) | New (streaming) |
|---|---|---|
| 10 songs, 1 ASR fallback (30 min) | 36 min total, 0/10 done at 8 min | 36 min total, 9/10 done at 8 min |
| 30 songs, sequential downloads | 30 × download_time | 10 × download_time (concurrency=3) |
| 50 songs, mixed LRC timings | max(all LRC) + max(all analysis) + max(all embedding) | max(per-song critical path) |
