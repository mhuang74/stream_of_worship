# WAITING Status for Fire-and-Forget Jobs

## Summary

Add a `WAITING` job status to the Analysis Service that distinguishes "dequeued but blocked on a semaphore" from "actively doing work." This improves queue visibility and restart recovery accuracy while preserving the existing fire-and-forget architecture and the already-implemented YouTube transcript rate limiter.

## Problem

### Current Behavior

`process_jobs()` (queue.py:366-374) dequeues jobs from `asyncio.Queue` and immediately creates fire-and-forget tasks:

```python
job_id = await asyncio.wait_for(self._queue.get(), timeout=1.0)
job = self._jobs.get(job_id)
if job:
    asyncio.create_task(self._process_job_with_semaphore(job))
```

Every `_process_*_job` method sets `job.status = JobStatus.PROCESSING` as its **first action**, before any semaphore acquisition. This means:

- The `asyncio.Queue` is always empty — all jobs have been dequeued into coroutines.
- All dequeued jobs show as `PROCESSING` even if they're blocked on a semaphore.
- Production logs show `LRC[queued:0,processing:57,completed:1,failed:0]` — 57 "processing" jobs, but only 1 is actively doing work. The other 56 are blocked on the YouTube rate limiter semaphore (`_YouTubeRateLimiter._semaphore`, default `max_concurrent=1`).

### YouTube Rate Limiter (Already Implemented)

The `_YouTubeRateLimiter` class in `youtube_transcript.py` already provides:
1. **Concurrency semaphore** (`SOW_YOUTUBE_TRANSCRIPT_MAX_CONCURRENT=1`) — serializes YouTube API calls
2. **Min-interval throttle** (`SOW_YOUTUBE_TRANSCRIPT_MIN_INTERVAL_SECONDS=3.0`) — spaces requests
3. **Retry with exponential backoff** on HTTP 429 (`SOW_YOUTUBE_TRANSCRIPT_MAX_RETRIES=3`)
4. **Circuit breaker** with auto-recovery (`SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_THRESHOLD=5`, `SOW_YOUTUBE_TRANSCRIPT_CIRCUIT_BREAKER_COOLDOWN=120`)

This satisfies the core requirement: YouTube transcript API calls are serialized, and slow ASR-based transcription does not block YouTube-capable LRC jobs (the YouTube path does not acquire `_local_model_semaphore`).

### What's Missing

The remaining problem is **queue visibility and restart recovery**:

1. **Visibility**: 56 jobs blocked on the YouTube rate limiter show as `PROCESSING`, not `WAITING`. Operators can't distinguish "actively running" from "blocked on a semaphore."
2. **Restart recovery**: On restart, `get_interrupted_jobs()` (db.py:550) returns all `PROCESSING` jobs. 56 jobs that were merely waiting on the YouTube rate limiter are treated identically to 1 job that was mid-ML-execution. All go through the interrupted-job recovery path (status reset, stage="requeued", progress=0). This is heavier than needed — jobs that hadn't started real work could be treated more simply.

## Design

### New `WAITING` Status

Add `WAITING` to `JobStatus` enum. A job is `WAITING` when:
- It has been dequeued from `asyncio.Queue` and assigned to a fire-and-forget task
- It has NOT yet started real work (I/O, ML, API calls)
- It is blocked on a semaphore or about to start

### Status Lifecycle

```
QUEUED → WAITING → PROCESSING → COMPLETED/FAILED/CANCELLED
         ^                    ^
         |                    |
    dequeued +          acquired semaphore /
    fire-and-forget     started real work
```

`WAITING` is set in `_process_job_with_semaphore()` immediately after dequeue, before any semaphore acquisition or processor call. Each `_process_*_job()` method transitions to `PROCESSING` only when it begins real work.

### Which Jobs Benefit

| Job Type | Semaphore Location | WAITING Duration | Notes |
|---|---|---|---|
| ANALYZE | JobQueue (`_local_model_semaphore` at dispatch) | Meaningful — blocked while other analysis runs | Set WAITING before `async with self._local_model_semaphore` |
| FAST_ANALYZE | JobQueue (`_fast_analyze_semaphore` at dispatch) | Meaningful | Set WAITING before `async with self._fast_analyze_semaphore` |
| EMBEDDING | JobQueue (`_embedding_semaphore` at dispatch) | Meaningful | Set WAITING before `async with self._embedding_semaphore` |
| LRC | Module-level (`_YouTubeRateLimiter._semaphore` inside `youtube_transcript.py`, `_local_model_semaphore` inside `lrc.py`) | Brief — no JobQueue-level semaphore to block on | WAITING set in `_process_job_with_semaphore`, immediately transitioned to PROCESSING in `_process_lrc_job` |
| STEM_SEPARATION | Inside `process_stem_separation()` | Brief — no JobQueue-level semaphore at dispatch | Same pattern as LRC |
| FORCED_ALIGNMENT | Inside `_process_forced_alignment_job()` around `align()` | Brief — no JobQueue-level semaphore at dispatch | Same pattern as LRC |

For ANALYZE/FAST_ANALYZE/EMBEDDING: `WAITING` is the primary state while blocked on the semaphore. The queue state log will show `ANALYZE[queued:0,waiting:4,processing:1]` instead of `ANALYZE[queued:0,processing:5]`.

For LRC/STEM_SEPARATION/FORCED_ALIGNMENT: `WAITING` is a brief transition state (set in `_process_job_with_semaphore`, immediately overwritten to `PROCESSING` by the processor). The primary visibility for these jobs comes from the **stage** field (`trying_youtube_transcript`, `transcribing`, `aligning`, etc.) combined with the `PROCESSING` status. The WAITING state still improves restart recovery by distinguishing "hadn't started real work" from "was mid-execution."

### Accepted Trade-off (Option A)

This approach keeps fire-and-forget. The YouTube rate limiter's semaphore is internal to `youtube_transcript.py` — jobs blocked on it show as `PROCESSING` with stage `trying_youtube_transcript`, not as `WAITING`. This is accepted because:
- Eliminating fire-and-forget would serialize YouTube transcript and ASR jobs (rejected by user)
- Wrapping LRC in a JobQueue-level YouTube semaphore would block ASR fallback behind YouTube waiters (rejected by user)
- The YouTube rate limiter already serializes API calls, which is the main priority

## Files to Modify

### 1. `src/sow_analysis/models.py`

Add `WAITING` to `JobStatus` enum (line 11-18):

```python
class JobStatus(str, Enum):
    """Job status values."""

    QUEUED = "queued"
    WAITING = "waiting"        # ← new
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
```

### 2. `src/sow_analysis/storage/db.py`

#### 2a. Update `CREATE TABLE` CHECK constraint (line 63-89)

Add `'waiting'` to the status CHECK constraint in the `CREATE TABLE IF NOT EXISTS jobs` statement:

```python
CHECK (status IN ('queued', 'waiting', 'processing', 'completed', 'failed', 'cancelled')),
```

No migration code is needed. Since the existing `jobs.db` has no data to preserve (all jobs have completed or been cancelled), simply delete `jobs.db` before next startup. The table will be recreated with the new schema automatically.

#### 2b. Add `get_waiting_jobs()` method

Add after `get_queued_jobs()` (line 591):

```python
async def get_waiting_jobs(self) -> list[Job]:
    """Return jobs with status WAITING (for restart recovery).

    WAITING jobs were dequeued but hadn't started real work.
    On restart, they are re-queued like QUEUED jobs.
    """
    if not self._db:
        raise RuntimeError("JobStore not initialized")

    async with self._db.execute(
        "SELECT * FROM jobs WHERE status = 'waiting'"
    ) as cursor:
        rows = await cursor.fetchall()

    jobs = [self._row_to_job(row) for row in rows]
    logger.info(f"Found {len(jobs)} waiting jobs in database")
    return jobs
```

### 3. `src/sow_analysis/workers/queue.py`

#### 3a. Set `WAITING` in `_process_job_with_semaphore()`

After the cancellation check (line 388), before the job type dispatch (line 390), add:

```python
# Transition from QUEUED to WAITING — job is now dequeued and assigned to a task
# but hasn't started real work yet
if job.status == JobStatus.QUEUED:
    job.status = JobStatus.WAITING
    job.stage = "waiting"
    job.updated_at = datetime.now(timezone.utc)
    try:
        await self.job_store.update_job(job.id, status="waiting", stage="waiting")
    except Exception as e:
        logger.error(f"Failed to update job {job.id} to WAITING in database: {e}")
```

#### 3b. Update `initialize()` recovery (line 217-264)

Add recovery for WAITING jobs. After the existing QUEUED jobs recovery block (line 250-264), add:

```python
# Recover WAITING jobs (dequeued but hadn't started real work)
waiting = await self.job_store.get_waiting_jobs()
for job in waiting:
    job.status = JobStatus.QUEUED
    job.stage = "requeued"
    job.progress = 0.0
    job.updated_at = datetime.now(timezone.utc)

    self._jobs[job.id] = job
    await self._queue.put(job.id)
    try:
        await self.job_store.update_job(
            job.id, status="queued", progress=0.0, stage="requeued"
        )
    except Exception as e:
        logger.error(f"Failed to update WAITING job {job.id} during recovery: {e}")

if waiting:
    logger.info(f"Recovered {len(waiting)} waiting jobs from database")
```

#### 3c. Move `PROCESSING` assignment in each `_process_*_job` method

Each processor currently sets `PROCESSING` as its **first action**. This should remain — but now it represents "starting real work" (not just "dequeued and assigned to a task"). The WAITING state was already set by `_process_job_with_semaphore()` above.

No changes needed to the `_process_*_job` methods themselves — they already set `PROCESSING` at the right time. The WAITING state was set earlier in the pipeline, so the transition is `QUEUED → WAITING → PROCESSING`.

However, for **ANALYZE, FAST_ANALYZE, EMBEDDING** (jobs that acquire a JobQueue-level semaphore at dispatch), the WAITING state persists between `_process_job_with_semaphore` setting it and the semaphore being acquired. The processor sets `PROCESSING` after acquiring the semaphore. This is correct.

For **LRC, STEM_SEPARATION, FORCED_ALIGNMENT** (no JobQueue-level semaphore at dispatch), WAITING is set in `_process_job_with_semaphore` and immediately overwritten to `PROCESSING` by the processor. This is also correct — the brief WAITING state still helps restart recovery.

#### 3d. Update `_log_queue_state()` (line 1959-2014)

Add WAITING to the stats output. After line 2000, add `waiting` count:

```python
parts.append(
    f"{jt.name}[queued:{s[JobStatus.QUEUED]},"
    f"waiting:{s[JobStatus.WAITING]},"  # ← new
    f"processing:{s[JobStatus.PROCESSING]},"
    f"completed:{s[JobStatus.COMPLETED]},"
    f"failed:{s[JobStatus.FAILED]}]"
)
```

Add WAITING wait times tracking. After the `queued_wait_times` block (line 1972-1976), add:

```python
elif job.status == JobStatus.WAITING:
    queued_wait_times[job.type].append(
        (now - job.created_at).total_seconds()
    )
    has_reportable_jobs = True
```

This groups WAITING with QUEUED for wait time reporting, since both represent jobs that haven't started.

#### 3e. Update `cancel_job()` (line 1823-1873)

Update the warning logic (line 1870):

```python
# Warning if job was processing (running task may continue)
if previous_status == JobStatus.PROCESSING:
    warning = "Job was PROCESSING. The running task continues until service restart."
elif previous_status == JobStatus.WAITING:
    # WAITING jobs have a running task but no side effects yet — safe to cancel
    pass
```

No warning for WAITING — the task will check the cancelled status and exit without side effects.

#### 3f. Update `clear_queue()` (line 1875-1949)

Add WAITING to the in-memory cancellation filter (line 1885):

```python
if job.status in (JobStatus.QUEUED, JobStatus.WAITING, JobStatus.PROCESSING):
```

Add a DB query for WAITING jobs not in memory, after the QUEUED and PROCESSING DB queries:

```python
# Also query DB for WAITING jobs not in memory
try:
    db_waiting_jobs = await self.job_store.list_jobs(status=JobStatus.WAITING, limit=1000)
    for job in db_waiting_jobs:
        if job.id not in self._jobs:
            job.status = JobStatus.CANCELLED
            job.updated_at = datetime.now(timezone.utc)
            job.stage = "cancelled"
            try:
                await self.job_store.update_job(job.id, status="cancelled", stage="cancelled")
            except Exception as e:
                logger.error(f"Failed to update cancelled job {job.id} in database: {e}")
            self._jobs[job.id] = job
            cancelled_jobs.append(job)
except Exception as e:
    logger.error(f"Failed to list waiting jobs from database: {e}")
```

### 4. `src/sow_analysis/routes/jobs.py`

`JobResponse` (models.py:201) already uses `status: JobStatus`, which serializes via the enum. No changes needed — the new `WAITING` value will automatically be exposed in API responses.

No changes to `job_to_response()` (jobs.py:92) needed.

## Restart Recovery Behavior

| Status at crash | Recovery action | Reason |
|---|---|---|
| QUEUED | Re-queue via `get_queued_jobs()` | Never started — no recovery needed |
| WAITING | Re-queue via `get_waiting_jobs()` → set to QUEUED | Dequeued but hadn't started real work — no side effects to handle |
| PROCESSING | Re-queue via `get_interrupted_jobs()` → set to QUEUED, reset progress | Was mid-execution — may have partial side effects (temp files, R2 uploads) |
| COMPLETED | No action | Terminal state |
| FAILED | No action | Terminal state |
| CANCELLED | No action | Terminal state |

This gives operators insight during restart recovery logs:

```
Found 3 interrupted jobs in database
Found 56 waiting jobs in database
Found 0 queued jobs in database
Recovered 3 interrupted jobs (e.g., job_abc123, job_def456, job_ghi789)
Recovered 56 waiting jobs from database
```

## Expected Log Output After Implementation

### Before (current):
```
Queue state: LRC[queued:0,processing:57,completed:1,failed:0] STEM_SEPARATION[queued:0,processing:3,completed:0,failed:0]
```

### After:
```
Queue state: LRC[queued:0,waiting:0,processing:57,completed:1,failed:0] STEM_SEPARATION[queued:0,waiting:0,processing:3,completed:0,failed:0]
```

For LRC, `waiting:0` because jobs transition through WAITING almost instantly (no JobQueue-level semaphore). The YouTube rate limiter's semaphore is internal and not reflected in job status.

For ANALYZE with `max_concurrent_local_model=1`:
```
Queue state: ANALYZE[queued:0,waiting:4,processing:1,completed:0,failed:0]
```

Here `waiting:4` shows real backlog — 4 jobs blocked on `_local_model_semaphore`.

## Testing

### Unit Tests

| Test | File | Description |
|---|---|---|
| `test_waiting_status_in_enum` | `test_job_store.py` | `JobStatus.WAITING` exists and equals `"waiting"` |
| `test_job_set_to_waiting_on_dequeue` | `test_queue_persistence.py` | After `process_jobs()` dequeues but before semaphore, job is `WAITING` in DB |
| `test_waiting_job_recovered_as_queued` | `test_queue_persistence.py` | WAITING job in DB on restart → recovered to QUEUED, re-queued |
| `test_waiting_job_not_treated_as_interrupted` | `test_job_store.py` | `get_interrupted_jobs()` does not return WAITING jobs |
| `test_get_waiting_jobs` | `test_job_store.py` | `get_waiting_jobs()` returns only WAITING-status jobs |
| `test_cancel_waiting_job_no_warning` | `test_queue_persistence.py` | Cancelling a WAITING job returns no warning (unlike PROCESSING) |
| `test_clear_queue_cancels_waiting` | `test_queue_persistence.py` | `clear_queue()` cancels WAITING jobs |
| `test_log_queue_state_shows_waiting` | `test_queue_persistence.py` | `_log_queue_state()` output includes `waiting:N` count |

### Integration Test

Submit 5 ANALYZE jobs with `max_concurrent_local_model=1`. Verify:
1. 1 job shows `PROCESSING` in API response
2. 4 jobs show `WAITING` in API response
3. Queue state log shows `ANALYZE[queued:0,waiting:4,processing:1]`

### Test Commands

```bash
cd ops/analysis-service && uv run --extra dev pytest tests/test_job_store.py tests/test_queue_persistence.py -v
cd ops/analysis-service && uv run --extra dev pytest tests/integration/test_queue.py -v
```

## What NOT to Change

- **Do NOT eliminate fire-and-forget.** LRC needs it because semaphore acquisition is conditional (YouTube path skips the local model semaphore entirely). Serial processing would serialize YouTube transcript jobs unnecessarily.
- **Do NOT add a JobQueue-level YouTube semaphore for LRC.** The existing `_YouTubeRateLimiter` in `youtube_transcript.py` already serializes YouTube API calls. A JobQueue-level semaphore would either wrap the entire LRC job (blocking ASR fallback behind YouTube waiters) or duplicate the rate limiter.
- **Do NOT change `_YouTubeRateLimiter`.** It's already fully implemented and working correctly.
- **Do NOT change semaphore parameters.** This is a status/visibility fix, not a concurrency tuning fix.

## Related Specs

- `specs/youtube-transcript-rate-limiting.md` — YouTube rate limiter implementation (already deployed)
- `specs/cancel-jobs-api.md` — Cancel/clear queue API endpoints
