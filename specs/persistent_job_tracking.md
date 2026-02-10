# Persistent Job Tracking for Analysis Service

## Problem Statement

The Analysis Service stores all job state in memory (`Dict[str, Job]` in `JobQueue`). When the service crashes or restarts:

- All in-flight and queued jobs are silently lost
- Job IDs returned to callers become invalid
- The admin CLI's local database retains `analysis_status = "processing"` or `lrc_status = "processing"` with no way to detect that the job is gone
- Manual intervention is required to resubmit stuck entries

With job volumes in the low thousands, a lightweight persistence layer is sufficient.

## Solution Overview

Add an SQLite database (`/cache/jobs.db`) to persist job state across service restarts. On startup, jobs that were QUEUED or PROCESSING when the service died are automatically re-queued for processing. Jobs older than 7 days are purged on startup.

## Technology Choice

**SQLite via `aiosqlite`** — async wrapper around Python's built-in `sqlite3`.

Rationale:
- Zero infrastructure: single file on the existing `/cache` Docker volume
- Async-compatible with the existing `asyncio` event loop
- Transactions ensure consistent state even during crashes
- Easy to query for status, filtering, and cleanup
- Lightweight dependency (~100KB)

## Database Schema

File: `services/analysis/src/sow_analysis/storage/db.py`

```sql
CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,           -- job_xxxxxxxxxxxx
    type            TEXT NOT NULL,              -- "analyze" | "lrc"
    status          TEXT NOT NULL DEFAULT 'queued',  -- queued | processing | completed | failed
    progress        REAL NOT NULL DEFAULT 0.0,
    stage           TEXT NOT NULL DEFAULT '',
    error_message   TEXT,

    -- Serialized request/result as JSON
    request_json    TEXT NOT NULL,              -- Full AnalyzeJobRequest or LrcJobRequest
    result_json     TEXT,                       -- Full JobResult (NULL until completed)

    -- Timestamps (ISO 8601 strings, UTC)
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,

    -- For cache key lookups and deduplication
    content_hash    TEXT NOT NULL,              -- Audio content hash from request

    -- Index for common queries
    CHECK (status IN ('queued', 'processing', 'completed', 'failed')),
    CHECK (type IN ('analyze', 'lrc'))
);

CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_content_hash ON jobs(content_hash);
CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON jobs(created_at);
```

## New Module: `JobStore`

File: `services/analysis/src/sow_analysis/storage/db.py`

```python
class JobStore:
    """SQLite-backed persistent job store."""

    def __init__(self, db_path: Path):
        """Initialize with path to SQLite database file."""

    async def initialize(self) -> None:
        """Create tables if not exist. Called once at startup."""

    async def insert_job(self, job: Job) -> None:
        """Insert a new job record."""

    async def update_job(self, job_id: str, **fields) -> None:
        """Update specific fields on a job (status, progress, stage, result, error)."""

    async def get_job(self, job_id: str) -> Optional[Job]:
        """Retrieve a single job by ID."""

    async def get_interrupted_jobs(self) -> list[Job]:
        """Return jobs with status QUEUED or PROCESSING (for restart recovery)."""

    async def purge_old_jobs(self, max_age_days: int = 7) -> int:
        """Delete completed/failed jobs older than max_age_days. Returns count deleted."""

    async def close(self) -> None:
        """Close the database connection."""
```

Serialization: `request_json` and `result_json` use Pydantic's `.model_dump_json()` for writes and `model_validate_json()` for reads, preserving full type fidelity.

## Changes to Existing Code

### Phase 1: Add `JobStore` and dependency

**1.1 Add `aiosqlite` dependency**

File: `services/analysis/pyproject.toml`

Add `"aiosqlite>=0.19.0"` to the `dependencies` list.

**1.2 Create `JobStore` class**

File: `services/analysis/src/sow_analysis/storage/db.py` (new file)

Implement the `JobStore` class as described above. Key details:
- Use `aiosqlite.connect()` with WAL mode for concurrent reads
- All writes use explicit transactions
- `get_interrupted_jobs()` returns jobs WHERE `status IN ('queued', 'processing')` — these are jobs that survived a crash
- `purge_old_jobs()` deletes WHERE `status IN ('completed', 'failed') AND created_at < (now - max_age_days)`
- Job reconstruction: deserialize `request_json` back to `AnalyzeJobRequest` or `LrcJobRequest` based on `type` field

### Phase 2: Integrate `JobStore` into `JobQueue`

**2.1 Update `JobQueue.__init__`**

File: `services/analysis/src/sow_analysis/workers/queue.py`

- Accept `db_path: Path` parameter (default: `cache_dir / "jobs.db"`)
- Create `self.job_store = JobStore(db_path)`
- Keep `self._jobs: Dict[str, Job]` as the in-memory hot cache for active jobs (fast progress updates)

**2.2 Add `async def initialize(self)` method**

New method on `JobQueue`, called from `main.py` lifespan before `process_jobs()`:

```python
async def initialize(self) -> None:
    """Initialize persistent store and recover interrupted jobs."""
    await self.job_store.initialize()

    # Purge old completed/failed jobs
    purged = await self.job_store.purge_old_jobs(max_age_days=7)
    if purged:
        logger.info(f"Purged {purged} old jobs from database")

    # Recover interrupted jobs (were QUEUED or PROCESSING when service died)
    interrupted = await self.job_store.get_interrupted_jobs()
    for job in interrupted:
        logger.info(f"Recovering interrupted job {job.id} (was {job.status})")
        job.status = JobStatus.QUEUED
        job.progress = 0.0
        job.stage = "requeued"
        job.updated_at = datetime.now(timezone.utc)

        self._jobs[job.id] = job
        await self._queue.put(job.id)
        await self.job_store.update_job(
            job.id, status="queued", progress=0.0, stage="requeued"
        )

    if interrupted:
        logger.info(f"Recovered {len(interrupted)} interrupted jobs")
```

**2.3 Update `submit()` method**

After creating the `Job` object and adding to `self._jobs`, persist it:

```python
await self.job_store.insert_job(job)
```

**2.4 Update `get_job()` method**

Try in-memory first (for active jobs with live progress), fall back to DB:

```python
async def get_job(self, job_id: str) -> Optional[Job]:
    job = self._jobs.get(job_id)
    if job:
        return job
    # Fall back to DB for completed/failed jobs that may have been evicted from memory
    return await self.job_store.get_job(job_id)
```

**2.5 Update `_process_analysis_job()` and `_process_lrc_job()`**

At each major state transition, persist to DB:

- When status changes to PROCESSING: `await self.job_store.update_job(job.id, status="processing", stage=..., progress=...)`
- When status changes to COMPLETED: `await self.job_store.update_job(job.id, status="completed", progress=1.0, stage="complete", result_json=job.result.model_dump_json())`
- When status changes to FAILED: `await self.job_store.update_job(job.id, status="failed", stage="error", error_message=str(e))`

These DB writes happen alongside the existing in-memory updates. They should be **non-blocking on the critical path** — a failed DB write should log an error but not fail the job itself.

**2.6 Memory management for `_jobs` dict**

After a job reaches a terminal state (COMPLETED or FAILED), it can be removed from `_jobs` after a short delay (e.g., 5 minutes) since `get_job()` falls back to the DB. This prevents unbounded memory growth.

Add a cleanup in `_process_job_with_semaphore()`:

```python
async def _cleanup_finished_job(self, job_id: str, delay: float = 300.0):
    """Remove finished job from in-memory cache after delay."""
    await asyncio.sleep(delay)
    self._jobs.pop(job_id, None)
```

Call this after job completion: `asyncio.create_task(self._cleanup_finished_job(job.id))`

**2.7 Update `stop()` method**

Close the DB connection:

```python
async def stop(self) -> None:
    self._running = False
    await self.stop_periodic_logging()
    await self.job_store.close()
```

### Phase 3: Update `main.py` lifespan

File: `services/analysis/src/sow_analysis/main.py`

Add `await job_queue.initialize()` between queue creation and `process_jobs()`:

```python
job_queue = JobQueue(
    max_concurrent_analysis=settings.SOW_MAX_CONCURRENT_ANALYSIS_JOBS,
    max_concurrent_lrc=settings.SOW_MAX_CONCURRENT_LRC_JOBS,
    cache_dir=settings.CACHE_DIR,
)

# Initialize persistent store and recover interrupted jobs
await job_queue.initialize()

# Initialize R2 if configured
if settings.SOW_R2_ENDPOINT_URL:
    job_queue.initialize_r2(settings.SOW_R2_BUCKET, settings.SOW_R2_ENDPOINT_URL)

set_job_queue(job_queue)
task = asyncio.create_task(job_queue.process_jobs())
```

### Phase 4: Add new API endpoints

File: `services/analysis/src/sow_analysis/routes/jobs.py`

**4.1 List jobs endpoint**

```python
@router.get("/jobs", response_model=list[JobResponse])
async def list_jobs(
    status: Optional[JobStatus] = None,
    job_type: Optional[JobType] = None,
    api_key: str = Depends(verify_api_key),
) -> list[JobResponse]:
    """List jobs with optional status/type filtering."""
```

This queries the DB directly (not in-memory dict) via a new `JobStore.list_jobs(status, job_type, limit)` method.

**4.2 Update `models.py`**

No schema changes needed — existing `JobResponse` already has all required fields. The `JobStatus` enum already covers `queued`, `processing`, `completed`, `failed`.

### Phase 5: Tests

File: `services/analysis/tests/test_job_store.py` (new file)

Test cases:
- `test_initialize_creates_tables` — verify DB and tables are created
- `test_insert_and_get_job` — round-trip for both analyze and LRC jobs
- `test_update_job_status` — verify status transitions persist
- `test_get_interrupted_jobs` — verify only QUEUED/PROCESSING jobs returned
- `test_purge_old_jobs` — verify old completed/failed jobs deleted, recent ones kept
- `test_purge_preserves_active_jobs` — verify QUEUED/PROCESSING jobs never purged regardless of age

File: `services/analysis/tests/test_queue_persistence.py` (new file)

Integration tests:
- `test_job_survives_queue_restart` — submit job, create new queue instance, verify job recovered
- `test_completed_job_queryable_after_memory_eviction` — verify `get_job()` falls back to DB

## File Summary

| File | Action |
|------|--------|
| `services/analysis/pyproject.toml` | Add `aiosqlite` dependency |
| `services/analysis/src/sow_analysis/storage/db.py` | **New** — `JobStore` class |
| `services/analysis/src/sow_analysis/workers/queue.py` | Integrate `JobStore`, add `initialize()`, update submit/process/get methods |
| `services/analysis/src/sow_analysis/main.py` | Call `job_queue.initialize()` in lifespan |
| `services/analysis/src/sow_analysis/routes/jobs.py` | Add `GET /jobs` list endpoint |
| `services/analysis/tests/test_job_store.py` | **New** — unit tests for `JobStore` |
| `services/analysis/tests/test_queue_persistence.py` | **New** — integration tests for persistence |

## Implementation Order

1. **Phase 1** — `JobStore` class + dependency (self-contained, testable independently)
2. **Phase 2** — Integrate into `JobQueue` (the core change)
3. **Phase 3** — Update `main.py` lifespan (wire it together)
4. **Phase 4** — Add list endpoint (optional but useful)
5. **Phase 5** — Tests

## Risk Considerations

- **DB writes on hot path**: Job progress updates happen frequently during processing. To avoid performance impact, only persist at major state transitions (queued → processing → completed/failed), not on every progress increment. In-memory `_jobs` dict handles fine-grained progress for live polling.
- **Concurrent access**: SQLite with WAL mode supports concurrent reads + single writer. Since job state updates are serialized per-job (each job processed by one task), write contention is minimal.
- **Crash during DB write**: If the service crashes mid-write, SQLite's transaction guarantees ensure the DB is not corrupted. The job will be recovered as QUEUED or PROCESSING on next startup.
- **Migration path**: No existing data to migrate — this is purely additive. The DB file is created fresh on first run.
