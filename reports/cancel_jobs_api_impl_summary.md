# Job Cancellation API Implementation Summary

**Specification:** @specs/cancel-jobs-api.md  
**Date:** 2026-04-29  
**Service:** Analysis Service (`services/analysis/`)

---

## Overview

This implementation adds two admin-only endpoints to the Analysis Service for job management:

1. **`POST /api/v1/jobs/{job_id}/cancel`** - Cancel a specific job
2. **`POST /api/v1/jobs/clear-queue`** - Cancel all queued jobs

A new `CANCELLED` job status was introduced, allowing operators to stop problematic jobs (especially those crashing the system) by marking them as cancelled — preventing re-queueing after service restart — and clearing the entire queued backlog.

---

## Changes Implemented

### 1. Models (`services/analysis/src/sow_analysis/models.py`)

**Added `CANCELLED` to `JobStatus` enum:**
```python
class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"  # NEW
```

**Added optional `warning` field to `JobResponse`:**
```python
class JobResponse(BaseModel):
    # ... existing fields ...
    error_message: Optional[str] = None
    warning: Optional[str] = None  # NEW - only populated when cancellation warns about side effects
    result: Optional[JobResult] = None
```

---

### 2. Database Layer (`services/analysis/src/sow_analysis/storage/db.py`)

**Updated SQLite CHECK constraint:**
```sql
CHECK (status IN ('queued', 'processing', 'completed', 'failed', 'cancelled'))
```

**Updated `purge_old_jobs()` to include CANCELLED:**
```python
async def purge_old_jobs(self, max_age_days: int = 7) -> int:
    """Delete completed/failed/cancelled jobs older than max_age_days."""
    # Now includes 'cancelled' in the status filter
    WHERE status IN ('completed', 'failed', 'cancelled')
```

**Added `get_cancelled_jobs()` method:**
```python
async def get_cancelled_jobs(self) -> list[Job]:
    """Return jobs with status CANCELLED for startup recovery.
    
    These jobs are loaded into memory on startup for queryability,
    but are not re-queued for processing.
    """
```

---

### 3. Configuration (`services/analysis/src/sow_analysis/config.py`)

**Added `SOW_ADMIN_API_KEY` setting:**
```python
class Settings(BaseSettings):
    # API Security
    SOW_ANALYSIS_API_KEY: str = ""
    SOW_ADMIN_API_KEY: str = ""  # Admin API key for privileged operations
```

If `SOW_ADMIN_API_KEY` is not set, admin endpoints return `503 Service Unavailable`.

---

### 4. Queue Implementation (`services/analysis/src/sow_analysis/workers/queue.py`)

**Updated startup recovery (`initialize()`):**
- Load `CANCELLED` jobs into memory for queryability
- Do NOT re-queue cancelled jobs (unlike PROCESSING jobs which are reset to QUEUED)

**Added processing loop guard (`_process_job_with_semaphore()`):**
```python
async def _process_job_with_semaphore(self, job: Job) -> None:
    # Check if job was cancelled before processing
    job_id = job.id
    current_job = self._jobs.get(job_id)
    if current_job and current_job.status == JobStatus.CANCELLED:
        logger.info(f"Skipping cancelled job {job_id}")
        return
    # ... continue processing ...
```

**Updated `_cleanup_finished_job()` to handle CANCELLED:**
```python
if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
    asyncio.create_task(self._cleanup_finished_job(job.id))
```

**Added `cancel_job()` method:**
```python
async def cancel_job(self, job_id: str) -> tuple[Optional[Job], Optional[str]]:
    """Cancel a job by ID.
    
    Returns:
        Tuple of (job, warning_message):
        - job: The cancelled job (None if not found)
        - warning_message: Set if job was PROCESSING at time of cancellation
    
    Behavior by status:
    - QUEUED: Set to CANCELLED
    - PROCESSING: Set to CANCELLED with warning (task continues until restart)
    - COMPLETED/FAILED/CANCELLED: No-op, returns existing job
    """
```

**Added `clear_queue()` method:**
```python
async def clear_queue(self) -> list[Job]:
    """Cancel all queued jobs.
    
    Returns:
        List of jobs that were cancelled
    
    Note: Uses lazy removal from asyncio.Queue - cancelled jobs are
    skipped when dequeued by the processing loop.
    """
```

**Updated logging stats:**
```python
# Now includes cancelled count in queue state logging
analyze_stats = f"queued:{...},cancelled:{stats[JobType.ANALYZE][JobStatus.CANCELLED]}"
```

---

### 5. API Routes (`services/analysis/src/sow_analysis/routes/jobs.py`)

**Added `verify_admin_api_key()` dependency:**
```python
async def verify_admin_api_key(authorization: Optional[str] = Header(None)) -> str:
    """Verify Bearer token matches SOW_ADMIN_API_KEY.
    
    Returns 401 if token invalid/missing.
    Returns 503 if SOW_ADMIN_API_KEY not configured.
    """
```

**Added `ClearQueueResponse` model:**
```python
class ClearQueueResponse(BaseModel):
    cancelled_count: int
    cancelled_job_ids: list[str]
```

**Added `POST /api/v1/jobs/{job_id}/cancel` endpoint:**
```python
@router.post("/jobs/{job_id}/cancel", response_model=JobResponse)
async def cancel_job(
    job_id: str,
    admin_key: str = Depends(verify_admin_api_key),
) -> JobResponse:
    """Cancel a job by ID.
    
    Response includes warning field only if job was PROCESSING
    at time of cancellation.
    """
```

**Added `POST /api/v1/jobs/clear-queue` endpoint:**
```python
@router.post("/jobs/clear-queue", response_model=ClearQueueResponse)
async def clear_queue(
    admin_key: str = Depends(verify_admin_api_key),
) -> ClearQueueResponse:
    """Cancel all queued jobs."""
```

---

## API Usage Examples

### Cancel a Specific Job

```bash
# Cancel a queued job
curl -X POST http://localhost:8000/api/v1/jobs/job_abc123/cancel \
  -H "Authorization: Bearer $SOW_ADMIN_API_KEY"

# Response (QUEUED → CANCELLED)
{
  "job_id": "job_abc123",
  "status": "cancelled",
  "job_type": "analyze",
  "created_at": "2026-04-29T10:00:00Z",
  "updated_at": "2026-04-29T10:05:00Z",
  "progress": 0.0,
  "stage": "cancelled",
  "error_message": null,
  "warning": null,  # No warning for queued jobs
  "result": null
}

# Response (PROCESSING → CANCELLED with warning)
{
  "job_id": "job_processing456",
  "status": "cancelled",
  "job_type": "analyze",
  "created_at": "2026-04-29T10:00:00Z",
  "updated_at": "2026-04-29T10:05:00Z",
  "progress": 0.5,
  "stage": "cancelled",
  "error_message": null,
  "warning": "Job was PROCESSING. The running task continues until service restart.",
  "result": null
}
```

### Clear All Queued Jobs

```bash
# Cancel all queued jobs
curl -X POST http://localhost:8000/api/v1/jobs/clear-queue \
  -H "Authorization: Bearer $SOW_ADMIN_API_KEY"

# Response
{
  "cancelled_count": 5,
  "cancelled_job_ids": [
    "job_abc123def456",
    "job_xyz789abc012",
    "..."
  ]
}
```

### Error Responses

```bash
# Missing admin key configuration
HTTP/1.1 503 Service Unavailable
{"detail": "Admin API key not configured on server"}

# Invalid admin key
HTTP/1.1 401 Unauthorized
{"detail": "Invalid admin API key"}

# Job not found
HTTP/1.1 404 Not Found
{"detail": "Job not found"}
```

---

## Test Coverage

### New/Updated Tests

| Test File | Tests | Description |
|-----------|-------|-------------|
| `services/analysis/tests/test_job_store.py` | 2 new | `get_cancelled_jobs()`, cancelled purge |
| `tests/services/analysis/test_models.py` | 2 new | CANCELLED enum, warning field |
| `tests/services/analysis/test_api.py` | 8 new | Admin endpoints (cancel, clear-queue) |

**Test Summary:**
- All 12 job store tests pass ✓
- All 6 queue persistence tests pass ✓
- All 6 cache tests pass ✓
- All 13 model tests pass ✓
- All 17 API tests pass ✓

**Total:** 54 tests in core analysis service test suite, all passing.

---

## Implementation Notes

### Design Decisions

1. **Flag-only for PROCESSING jobs**: The job may be crashing the system. Restarting the service is the safest way to stop a running job. The CANCELLED flag prevents re-queueing on restart.

2. **Lazy queue removal**: `asyncio.Queue` doesn't support targeted removal. Cancelled jobs remain in the queue but are skipped when dequeued by the processing loop.

3. **Separate admin API key**: Allows operators to rotate admin credentials independently from regular API keys used by clients.

4. **No-op on terminal states**: Cancelling COMPLETED/FAILED jobs returns the existing state without modification. This preserves immutable truth of terminal states.

5. **Audit trail preservation**: CANCELLED is a terminal state like COMPLETED/FAILED. Jobs remain queryable for 7 days before purge.

### Out of Scope (Per Spec)

- Child job cascade cancellation (manual operation using job IDs from logs)
- DELETE endpoint for permanent DB removal (7-day purge handles cleanup)
- Running task abort (flag-only approach)
- Job timeout (not addressed)
- Partial result cleanup on cancel (handled by TemporaryDirectory)

---

## Files Modified

```
services/analysis/src/sow_analysis/models.py        # CANCELLED status, warning field
services/analysis/src/sow_analysis/storage/db.py      # CHECK constraint, get_cancelled_jobs(), purge
services/analysis/src/sow_analysis/config.py          # SOW_ADMIN_API_KEY
services/analysis/src/sow_analysis/workers/queue.py # cancel_job(), clear_queue(), loop guard
services/analysis/src/sow_analysis/routes/jobs.py     # verify_admin_api_key, endpoints

# Tests
tests/services/analysis/test_models.py                # CANCELLED enum test, warning field test
tests/services/analysis/test_api.py                   # Admin endpoints tests
services/analysis/tests/test_job_store.py             # get_cancelled_jobs(), purge tests
```

---

## Environment Configuration

Add to your `.env` file or environment:

```bash
# Regular API key for job submission/status queries
SOW_ANALYSIS_API_KEY="your-regular-api-key"

# Admin API key for privileged operations (cancel, clear-queue)
SOW_ADMIN_API_KEY="your-admin-api-key"
```

---

*Implementation complete and tested.*
