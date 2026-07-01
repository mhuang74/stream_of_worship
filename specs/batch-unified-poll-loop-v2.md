# Batch Unified Poll Loop + Parallel Downloads (v2)

> Supersedes `specs/batch-unified-poll-loop-v1.md`
>
> Changes from v1 (all review-driven):
> 1. Phases 0 and the unified loop are merged into a single interleaved main loop — no
>    `as_completed` blocking window.
> 2. Per-thread `ConnectionProvider` via `ThreadPoolExecutor` `initializer` / `initargs`.
> 3. Phase 0 has its own `KeyboardInterrupt` handler with manifest dump and future
>    cancellation.
> 4. Handlers no longer receive `active_jobs`; they return `is_terminal` and let the loop
>    own the dict.
> 5. Adaptive poll interval (5 s → 30 s) instead of fixed 30 s.
> 6. `_advance_song` explicitly captures job IDs from submission helpers and marks
>    pipeline completion when the chain is exhausted.

---

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

---

## Design

### Core Concept: Interleaved Main Loop

There is no longer a distinct "Phase 0" that blocks the main thread. A single main
loop manages both pending downloads and active service jobs:

```
┌──────────────────────────────────────────────────────────────────────┐
│  Main Loop (single thread)                                            │
│                                                                       │
│  while pending_downloads or active_jobs:                             │
│    # 1. Check for completed downloads                                │
│    done, pending_downloads = wait(pending_downloads, timeout=...,     │
│                                    return_when=FIRST_COMPLETED)       │
│    for each completed download:                                       │
│      print result, apply to results[]                                │
│      _advance_song(song_id, "download", ...)                         │
│                                                                       │
│    # 2. Poll all active service jobs (LRC / analyze / embedding)     │
│    for each (song_id, step) in active_jobs:                          │
│      fetch status via analysis_client.get_job(job_id)                │
│      if terminal:                                                    │
│        del active_jobs[(song_id, step)]                              │
│        _advance_song(song_id, step, ...)                             │
│                                                                       │
│    # 3. House-keeping                                                │
│    flush manifest                                                    │
│    print progress                                                    │
│    adaptive_sleep()                                                  │
└──────────────────────────────────────────────────────────────────────┘
```

This means:
- A song whose download finishes at T+1s is advanced (and may submit an LRC job)
  immediately.
- An LRC job submitted by that advance is polled in the **same** loop iteration on
  the next cycle — there is no "wait for all downloads first" blocking window.

### Parallel Downloads: Thread Pool with Per-Thread DB

Downloads still run in a `ThreadPoolExecutor`, but it is created once and reused
only for the download task. The main thread submits all download futures up front,
then enters the interleaved loop above.

```python
executor = ThreadPoolExecutor(
    max_workers=download_concurrency,
    initializer=_init_download_worker,
    initargs=(database_url,),
)
```

Each worker thread calls the initializer once, creating a **single**
`ConnectionProvider` + `DatabaseClient` that lives for the lifetime of the thread:

```python
def _init_download_worker(database_url: str):
    provider = ConnectionProvider(database_url)
    _worker_state.db = DatabaseClient(provider)
    _worker_state.provider = provider

def _download_worker(song_id, song, r2_client, ...):
    thread_db = _worker_state.db
    try:
        recording, error = _download_and_create_recording(
            song_id, song, thread_db, r2_client, Console(quiet=True)
        )
        ...
    except Exception as e:
        # Any unexpected failure is captured as a structured error so the main
        # loop can record it without crashing.
        return {"song_id": song_id, "status": "failed", "error": str(e)}
```

Thread-local storage (`_worker_state = threading.local()`) keeps the per-thread
objects isolated. The main thread never touches `_worker_state`.

**Thread-safety of shared resources**

| Resource | Thread-safe? | Mitigation |
|---|---|---|
| `results` dict | No — workers + poll loop | `threading.Lock` (`results_lock`) |
| `manifest_entries` list | No | `threading.Lock` (`manifest_lock`) |
| `active_jobs` dict | Written by main thread only | No lock needed (single writer) |
| `DatabaseClient` (per-thread) | Yes — one conn per thread | `initializer` creates one per thread |
| `R2Client` | Yes — HTTP-based boto3 client | No action needed |
| `AnalysisClient` | Yes — HTTP-based (requests/httpx) | No action needed |
| `Console` | Not guaranteed thread-safe | Workers use `Console(quiet=True)`; main thread prints all progress |

### Active Jobs Structure

Single dict keyed by `(song_id, step)`:

```python
active_jobs: dict[tuple[str, str], str] = {}
# (song_id, "lrc" | "analyze" | "embedding") -> job_id
```

A song has at most one active service job at any time, but different songs can be
on different steps. This eliminates head-of-line blocking.

### `_advance_song` Cascade Logic

When a step completes, `_advance_song` walks the step chain forward and submits the
next selected step that is ready. If a step is already completed (e.g., LRC
previously generated, analysis from prior run), it skips it and tries the next.

```python
chain = ["download", "lrc", "analyze", "embedding"]

def _advance_song(
    song_id: str,
    completed_step: str,
    selected_steps: list[str],
    db_client: DatabaseClient,
    analysis_client: AnalysisClient,
    r2_client: R2Client,
    force: bool,
    analysis_tier: str,
    stale_after_minutes: int,
    console: Console,
    results: dict,
    active_jobs: dict,
    _add_manifest_entry: Callable,
) -> None:
    next_idx = chain.index(completed_step) + 1
    for step in chain[next_idx:]:
        if step not in selected_steps:
            continue

        job_id, status = _submit_step(
            song_id, step, db_client, analysis_client, r2_client,
            force, analysis_tier, stale_after_minutes, console,
            results, _add_manifest_entry,
        )

        if status == "submitted":
            active_jobs[(song_id, step)] = job_id
            return
        elif status in (
            "skipped_r2", "skipped_completed", "skipped_no_lyrics",
            "skipped_no_recording", "skipped_up_to_date",
        ):
            continue  # try next step in chain
        else:  # failed
            results[song_id][step] = "failed"
            return

    # Chain exhausted — no further work for this song
    results[song_id]["_pipeline"] = "completed"
```

`_submit_step` is a thin dispatcher that routes to the extracted helpers
`_submit_lrc_for_song`, `_submit_analysis_for_song`, or `_submit_embedding_for_song`
and returns `(job_id, status)`.

---

## New / Refactored Functions

| # | Function | Purpose | Source |
|---|----------|---------|--------|
| 1 | `_submit_analysis_for_song(...)` | Submit/reuse analysis job for one song | Extract from Phase 3 submit loop (audio.py:5341–5473) |
| 2 | `_submit_embedding_for_song(...)` | Submit embedding for one song | Extract from Phase 4 submit loop (audio.py:5500–5571) |
| 3 | `_handle_lrc_completion(...)` | Process completed/failed/404 LRC job | Extract from `_poll_all_jobs` (audio.py:5640–5850) |
| 4 | `_handle_analysis_completion(...)` | Process completed/failed/404 analysis job | Extract from `_poll_analysis_jobs` (audio.py:6020–6275) |
| 5 | `_handle_embedding_completion(...)` | Process completed/failed/404 embedding job | Extract from `_poll_embedding_jobs` (audio.py:6357–6530) |
| 6 | `_advance_song(...)` | Dispatcher: submit next selected step | New |
| 7 | `_init_download_worker(database_url)` | Per-thread DB setup | New |
| 8 | `_download_worker(song_id, song, r2_client, ...)` | Per-thread download + eager LRC | New (wraps existing `_download_and_create_recording` + `_submit_lrc_for_song`) |
| 9 | `_unified_poll_loop(...)` | Single interleaved loop (downloads + service jobs) | New (replaces the three poll functions' while-loops and the blocking `as_completed` Phase 0) |

### Functions to Remove

After extraction, the three old poll functions are no longer called by either
`_process_batch` or `_resume_from_manifest`:

- `_poll_all_jobs` (audio.py:5601–5880) — ~280 lines
- `_poll_analysis_jobs` (audio.py:5985–6324) — ~340 lines
- `_poll_embedding_jobs` (audio.py:6326–6575) — ~250 lines

Total: ~870 lines removed. The completion handling logic lives on in the extracted
`_handle_*_completion` helpers.

---

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
    pending_futures: Set[Future] = set()

2. Start ThreadPoolExecutor(max_workers=download_concurrency,
                            initializer=_init_download_worker,
                            initargs=(database_url,))

3. Submit all download tasks:
    for each song_id:
        future = executor.submit(_download_worker, song_id, song, r2_client, ...)
        pending_futures.add(future)

4. Interleaved Main Loop
    while pending_futures or active_jobs:
        # A. Check downloads
        done, pending_futures = wait(pending_futures,
                                     timeout=adaptive_interval(),
                                     return_when=FIRST_COMPLETED)
        for f in done:
            result = f.result()
            with results_lock:
                results[result["song_id"]].update(result["updates"])
            if result.get("recording"):
                _advance_song(result["song_id"], "download", ...)

        # B. Poll active service jobs
        for key in list(active_jobs.keys()):
            song_id, step = key
            job_id = active_jobs[key]
            try:
                job = analysis_client.get_job(job_id)
                is_terminal = _handle_STEP_completion(...)
                if is_terminal:
                    del active_jobs[key]
                    _advance_song(song_id, step, ...)
            except AnalysisServiceError as e:
                ...
            except Exception as e:
                console.print(f"  [yellow]→ Error polling {song_id}/{step}: {e}[/yellow]")

        # C. Flush manifest + print progress
        _flush_manifest()
        _print_unified_progress(active_jobs, results, batch_start_time, console)

    executor.shutdown(wait=True)
    return results
```

### KeyboardInterrupt Handling

```python
try:
    while pending_futures or active_jobs:
        ...
except KeyboardInterrupt:
    # 1. Cancel any pending download futures
    for fut in pending_futures:
        fut.cancel()
    executor.shutdown(wait=False, cancel_futures=True)

    # 2. Reconcile active service jobs
    _reconcile_on_interrupt(active_jobs, results, db_client, r2_client, console)

    # 3. Flush manifest so --resume is possible
    _flush_manifest()
    raise
```

---

## `_unified_poll_loop` Implementation

In v2, the unified loop **is** the main loop described above; there is no separate
function called `_unified_poll_loop` that hides Phase 0. However, for testing and
code clarity, the body of the loop (steps A-C) can be extracted into a helper
`_poll_one_cycle(...)`:

```python
def _poll_one_cycle(
    pending_futures: Set[Future],
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
    results_lock: threading.Lock,
    manifest_lock: threading.Lock,
) -> Set[Future]:
    """Returns the updated pending_futures set."""
```

### 404 / Lost Job Handling by Step

| Step | 404 Recovery |
|------|--------------|
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
    completed = sum(1 for r in results.values() if r.get("_pipeline") == "completed")

    elapsed = time.time() - start_time
    console.print(
        f"⏳ pending(down/lrc/ana/emb)={len(pending_futures)}/{lrc_active}/"
        f"{analyze_active}/{embedding_active}  "
        f"✓(lrc/ana/emb)={lrc_done}/{analyze_done}/{embedding_done}  "
        f"pipeline={completed}  "
        f"✗={failed}  "
        f"(elapsed: {int(elapsed // 60)}m {int(elapsed % 60)}s)"
    )
```

---

## Adaptive Poll Interval

```python
_fast_interval = 5.0
_slow_interval = 30.0
_staleness_threshold = 180.0   # 3 minutes without a completion → slow mode

def adaptive_interval(
    last_completion_time: float,
    active_jobs: dict,
) -> float:
    if not active_jobs:
        return _fast_interval
    elapsed = time.time() - last_completion_time
    if elapsed > _staleness_threshold:
        return _slow_interval
    return _fast_interval
```

The `wait(..., timeout=adaptive_interval())` call in the main loop honours this.

---

## Resume Path: Unified Resume

`_resume_from_manifest` is rewritten to reconstruct the unified `active_jobs` dict
and enter the **same** interleaved loop that fresh batches use.

### Implementation

```python
def _resume_from_manifest(manifest_data, manifest_path, db_client,
                          r2_client, analysis_client, stale_after_minutes,
                          console, database_url, download_concurrency):
    songs = manifest_data.get("songs", [])
    results: Dict[str, dict] = {}
    manifest_entries: List[dict] = list(songs)
    active_jobs: Dict[tuple[str, str], str] = {}
    pending_futures: Set[Future] = set()

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

    # Enter the same interleaved loop used by fresh batches
    selected_steps = manifest_data.get("selected_steps", [])
    analysis_tier = manifest_data.get("analysis_tier", "fast")

    _process_batch_resume_loop(
        pending_futures=pending_futures,
        active_jobs=active_jobs,
        results=results,
        db_client=db_client,
        analysis_client=analysis_client,
        r2_client=r2_client,
        selected_steps=selected_steps,
        force=False,
        analysis_tier=analysis_tier,
        stale_after_minutes=stale_after_minutes,
        console=console,
        manifest_entries=manifest_entries,
        _add_manifest_entry=_add_manifest_entry,
        database_url=database_url,
        download_concurrency=download_concurrency,
    )

    return results
```

Because the resume path now uses the exact same loop, a manifest with LRC jobs in
"processing" alongside analysis jobs in "processing" will poll both concurrently,
and completions cascade immediately.

> **Known edge case (accepted):** If the process crashed between writing a manifest
> "completed" entry and advancing to submit the next step, the song will not be
> auto-advanced on `--resume`. The operator can work around this with
> `--force --step`. This is rare and not worth added complexity in v2.

---

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
`download_concurrency=download_concurrency`:

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

---

## Files to Modify

| File | Changes |
|------|---------|
| `ops/admin-cli/src/stream_of_worship/admin/commands/audio.py` | Add `--download-concurrency` flag to `batch()`; rewrite `_process_batch` with new signature + interleaved loop + parallel downloads; add helpers 1–9; rewrite `_resume_from_manifest` for unified resume; delete `_poll_all_jobs`, `_poll_analysis_jobs`, `_poll_embedding_jobs`; update `_print_progress` → `_print_unified_progress` |
| `ops/admin-cli/tests/admin/test_audio_batch_eager_lrc.py` | Update `_process_batch` calls for new `database_url` + `download_concurrency` params |
| `ops/admin-cli/tests/admin/test_audio_batch_v4.py` | Update if affected by signature changes |
| `ops/admin-cli/tests/admin/test_audio_batch_unified.py` | **New** — tests for `_advance_song`, `_poll_one_cycle`, `_download_worker`, adaptive interval, unified resume |

No changes needed to:
- `ops/admin-cli/src/stream_of_worship/db/connection.py` — `ConnectionProvider` already takes `database_url` in constructor
- `ops/admin-cli/src/stream_of_worship/admin/commands/catalog.py` — `get_db_client` already uses `config.get_connection_url()`
- `ops/admin-cli/src/stream_of_worship/admin/db/client.py` — `DatabaseClient` already accepts any `ConnectionProvider`

---

## Test Strategy

### 1. Unit Tests for Extracted Helpers

- `_submit_analysis_for_song`: verify submit, reuse-stale, skip-completed, skip-no-recording, failed paths
- `_submit_embedding_for_song`: verify submit, skip-up-to-date, skip-no-lyrics, failed paths
- `_handle_lrc_completion`: verify completed (R2 confirmed), failed, cancelled branches; returns `is_terminal=True`
- `_handle_analysis_completion`: verify completed (fast/full tier), failed, cancelled branches
- `_handle_embedding_completion`: verify completed (DB write), failed, cancelled branches

### 2. Integration Tests for `_advance_song`

- LRC completed → analysis submitted immediately
- LRC completed but analysis already completed → embedding submitted
- LRC completed but analysis not in selected_steps → no-op
- LRC completed but no recording → skip to next
- Analysis completed but embedding not selected → no-op
- Each step skipped → cascade continues to next step
- All steps skipped / chain exhausted → `results[sid]["_pipeline"] == "completed"`

### 3. Integration Tests for Interleaved Loop (`_poll_one_cycle`)

- Mock `analysis_client.get_job` to return mixed statuses across LRC/analysis/embedding
- Verify songs advance independently: song A completes LRC → analysis submitted, while song B's LRC still processing
- Verify no phase barriers: embedding submitted before all analysis jobs complete
- Verify 404 handling per step type
- Verify Ctrl+C reconciliation
- Verify adaptive interval drifts from 5 s to 30 s when staleness threshold crossed

### 4. Thread-Safety Tests for `_download_worker`

- Mock `_download_and_create_recording` + `_submit_lrc_for_song`
- Run N workers concurrently with mock DB
- Verify `results` dict has all songs, no corruption
- Verify `active_jobs` populated without duplicates
- Verify `_init_download_worker` is called exactly `max_workers` times, not `N` times

### 5. Existing Test Updates

- `test_audio_batch_eager_lrc.py`: add `database_url="postgresql://test"` and `download_concurrency=1` to all `_process_batch` calls
- `test_audio_batch_v4.py`: update if `_process_batch` signature changed

### 6. Unified Resume Test

- Build a manifest with mixed statuses: some "completed", some "processing" across LRC/analysis/embedding
- Verify `active_jobs` reconstructed as `{(song_id, step): job_id}`
- Verify interleaved loop entered directly
- Verify no phase-barrier ordering

### 7. KeyboardInterrupt Test

- Mock a slow download future and send `KeyboardInterrupt` (via `threading.Timer` raising into the main thread, or using `unittest.mock.side_effect` on `wait`)
- Verify futures are cancelled, manifest is flushed, and `_reconcile_on_interrupt` is called

---

## Risk Areas and Mitigations

| Risk | Mitigation |
|------|------------|
| Thread-safety of shared `results`/`manifest_entries` | `threading.Lock` around all writes; download workers use `Console(quiet=True)` |
| Per-thread DB connections exhausting pool | `download_concurrency` default=3, max 3 persistent connections — well within Postgres limits |
| `_download_and_create_recording` using module-level state | Verify stateless (it processes one song, creates a recording) |
| `--resume` manifest format changes | Manifest format is unchanged; only the consumer changes |
| Rich Console multi-thread output interleaving | Workers print nothing (quiet=True); all console output from main thread |
| YouTube rate-limiting on concurrent downloads | Default concurrency=3; operator can tune with `--download-concurrency` |
| Worker thread exceptions crashing the batch | `_download_worker` wraps body in `try/except Exception`, returns structured failure dict |
| Slow `yt-dlp` blocking executor shutdown on Ctrl+C | `executor.shutdown(wait=False, cancel_futures=True)` (Python 3.9+) |
| Adaptive interval logic bugs | Unit tests cover both fast and slow thresholds explicitly |

---

## Locked Decisions

1. **Complete rewrite** of `_process_batch` (not incremental).
2. **Unified resume** — `_resume_from_manifest` enters the same interleaved loop.
3. **Silent workers** — download workers use `Console(quiet=True)`, main thread prints all progress.
4. **`--download-concurrency` default 3** — safe for YouTube, configurable by operator.
5. **Per-thread DatabaseClient** via `ThreadPoolExecutor(initializer=...)` — one `ConnectionProvider` per worker thread.
6. **`active_jobs` keyed by `(song_id, step)` tuple** — one job per song at a time, different songs on different steps.
7. **Old poll functions deleted** — `_poll_all_jobs`, `_poll_analysis_jobs`, `_poll_embedding_jobs` removed after extraction.
8. **Manifest format unchanged** — only the consumer code changes.
9. **Interleaved main loop** — no separate blocking Phase 0; `wait(..., timeout=..., FIRST_COMPLETED)` runs alongside service-job polling.
10. **Adaptive poll interval** — 5 s when completions are frequent, 30 s after 3 min of staleness.
11. **Handlers do not mutate `active_jobs`** — they return `is_terminal: bool`; the loop owns insertion and deletion.

---

## Expected Throughput Improvement

| Scenario | Current (barrier) | New (streaming) |
|----------|-------------------|-----------------|
| 10 songs, 1 ASR fallback (30 min) | 36 min total, 0/10 done at 8 min | 36 min total, 9/10 done at 8 min |
| 30 songs, sequential downloads | 30 × download_time | ~10 × download_time (concurrency=3) |
| 50 songs, mixed LRC timings | max(all LRC) + max(all analysis) + max(all embedding) | max(per-song critical path) |
| Fast song finishes download in 1 min, slow in 10 min | LRC stalls 9 min waiting for all downloads | LRC polled as soon as download completes (interleaved loop) |

---

## Migration Notes for Implementer

- `wait` import: `from concurrent.futures import wait, FIRST_COMPLETED`
- `ThreadPoolExecutor` initializer requires Python 3.11+ for `initargs` support; our target is py311, so this is safe.
- When deleting `_poll_all_jobs`, `_poll_analysis_jobs`, `_poll_embedding_jobs`, double-check no other callers exist (grep for each name across the codebase).
- The `_submit_step` dispatcher should be typed to return `tuple[str | None, str]` where the first element is `job_id` (or `None` for non-submit statuses) and the second is the status string.
- Ensure `_advance_song` is called with `completed_step="download"` even for songs that never ran a download (R2-preexisting or `--skip-download`), because the step chain starts at "download" and the function skips already-done work.
