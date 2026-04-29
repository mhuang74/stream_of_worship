# Job Cancellation API Specification

**Service:** Analysis Service (`services/analysis/`)  
**Status:** Draft  
**Created:** 2026-04-29

## Overview

Add two admin-only endpoints to cancel/clear jobs, plus a new `CANCELLED` job status. This enables operators to stop problematic jobs (especially those crashing the system) by marking them as cancelled — preventing re-queueing after service restart — and to clear the entire queued backlog.

## 1. New `CANCELLED` Job Status

**Files:**
- `services/analysis/src/sow_analysis/models.py`
- `services/analysis/src/sow_analysis/storage/db.py`

Add `CANCELLED = "cancelled"` to the `JobStatus` enum.

Update the SQLite `CHECK` constraint in `db.py` to include `cancelled`:

```sql
CHECK (status IN ('queued', 'processing', 'completed', 'failed', 'cancelled'))
```

Update `purge_old_jobs()` to also purge `CANCELLED` jobs older than 7 days (same as COMPLETED/FAILED).

Update `initialize()` recovery logic: jobs found with status=`cancelled` on startup are **not** re-queued (unlike `processing` jobs which are reset to `queued`). They remain `cancelled`.

## 2. Admin Authorization

**New env var:** `SOW_ADMIN_API_KEY`

**New dependency:** `verify_admin_api_key` in `services/analysis/src/sow_analysis/routes.py` (or a new `auth.py` module).

```python
async def verify_admin_api_key(authorization: str = Header(...)):
    """
    Same pattern as verify_api_key, but checks against SOW_ADMIN_API_KEY.
    Returns 401 if missing/invalid.
    """
```

The existing `verify_api_key` is unchanged for read endpoints. The new cancel/clear endpoints require `verify_admin_api_key` instead.

If `SOW_ADMIN_API_KEY` is not set, admin endpoints return `503 Service Unavailable` with a message indicating the admin key is not configured.

## 3. Endpoint: `POST /api/v1/jobs/{job_id}/cancel`

**Auth:** `verify_admin_api_key`

### Behavior by current job status:

| Current Status | Action | Response |
|---|---|---|
| `QUEUED` | Set status → `CANCELLED`, persist to SQLite | `200` with updated `JobResponse` |
| `PROCESSING` | Set status → `CANCELLED`, persist to SQLite | `200` with `warning` field |
| `COMPLETED` | No-op | `200` with existing `JobResponse` |
| `FAILED` | No-op | `200` with existing `JobResponse` |
| `CANCELLED` | No-op | `200` with existing `JobResponse` |
| Not found | — | `404` |

### Request

No body.

### Response (200)

```json
{
  "job_id": "job_abc123def456",
  "status": "cancelled",
  "job_type": "analyze",
  "created_at": "2026-04-29T10:00:00Z",
  "updated_at": "2026-04-29T10:05:00Z",
  "progress": 0.5,
  "stage": "analysing",
  "error_message": null,
  "warning": "Job was PROCESSING. The running task continues until service restart.",
  "result": null
}
```

The `warning` field is **only present** when the job was in `PROCESSING` state at the time of cancellation.

### Removing QUEUED jobs from asyncio.Queue

Since `asyncio.Queue` doesn't support targeted removal, the job remains in the queue. When the processing loop picks it up, it checks the job's status before executing — if `CANCELLED`, skip processing and discard the queue entry. This is a "lazy removal" approach.

### Processing loop guard

Update `_process_job_with_semaphore` in `queue.py`:

```python
# After acquiring semaphore, before actual processing:
job = self._jobs.get(job_id)
if job and job.status == JobStatus.CANCELLED:
    logger.info(f"Skipping cancelled job {job_id}")
    return
```

## 4. Endpoint: `POST /api/v1/jobs/clear-queue`

**Auth:** `verify_admin_api_key`

**Behavior:** Sets all jobs with status `QUEUED` to `CANCELLED`. Persists each to SQLite.

**Request:** No body, no query params.

**Response (200):**

```json
{
  "cancelled_count": 5,
  "cancelled_job_ids": [
    "job_abc123def456",
    "job_xyz789abc012",
    "..."
  ]
}
```

### Implementation notes

- Iterate over `self._jobs` dict to find all `QUEUED` jobs.
- Also query SQLite for `QUEUED` jobs not in memory (edge case: if a job was persisted but not yet loaded).
- Set each to `CANCELLED` and persist.
- The `asyncio.Queue` entries for these jobs will be discarded lazily by the processing loop guard.

## 5. Startup Recovery Logic Update

In `JobQueue.initialize()`, update the recovery logic:

**Current:**
- Finds `PROCESSING` jobs → resets to `QUEUED` → re-queues them
- Finds `QUEUED` jobs → re-queues them

**Updated:**
- Find `PROCESSING` jobs → reset to `QUEUED` → re-queue (unchanged)
- Find `QUEUED` jobs → re-queue (unchanged)
- Find `CANCELLED` jobs → load into `_jobs` dict for queryability, but **do not** re-queue
- Update `_cleanup_finished_job()` to also handle `CANCELLED` jobs (evict from memory after 5-min delay, same as COMPLETED/FAILED)

## 6. JobResponse Model Update

Add optional `warning: Optional[str] = None` field to `JobResponse` in `models.py`. Only populated when a cancellation response needs to warn about side effects (e.g., PROCESSING job continued running).

## 7. Files to Modify

| File | Changes |
|---|---|
| `models.py` | Add `CANCELLED` to `JobStatus`; add `warning` to `JobResponse` |
| `storage/db.py` | Update `CHECK` constraint; include `cancelled` in `purge_old_jobs` |
| `queue.py` | Add `cancel_job()` method; add `clear_queue()` method; add processing loop guard for cancelled jobs; update `_cleanup_finished_job()` for `CANCELLED` state; update recovery logic in `initialize()` |
| `routes.py` | Add `POST /jobs/{job_id}/cancel` endpoint; add `POST /jobs/clear-queue` endpoint |
| `auth.py` (new) or `routes.py` | Add `verify_admin_api_key` dependency |
| `config.py` | Add `admin_api_key` setting from `SOW_ADMIN_API_KEY` env var |

## 8. Out of Scope

The following are explicitly **not** part of this spec:

- **Child job cascade cancellation:** User cancels parent and child manually using job IDs from logs.
- **DELETE endpoint (permanent DB removal):** Not included. Existing 7-day purge handles cleanup.
- **Running task abort:** Flag-only approach. Restart the service to actually stop a running task.
- **Job timeout:** Not addressed.
- **Partial result cleanup on cancel:** Temp files are handled by `TemporaryDirectory`; R2 uploads are not cleaned up on cancellation.

## 9. Decision Rationale

| Decision | Rationale |
|---|---|
| Add `CANCELLED` status vs delete | Preserves audit trail. Clients can query cancelled jobs like any other terminal state. |
| Flag-only for PROCESSING jobs | The job may be crashing the system. Restarting the service and re-submission logic for non-cancelled jobs is the safest and simplest way to stop a running job. |
| Separate admin API key | Allows operators to rotate admin credentials independently from regular API keys. |
| No-op on COMPLETED/FAILED | Terminal states represent immutable truth. Cancelling them would be semantically confusing. |
| Clear-queue clears ALL queued | Simplest mental model for operators. No filtering needed. |
| Lazy queue removal | `asyncio.Queue` doesn't support targeted removal. Checking status at dequeue time is simpler and race-condition-free. |
