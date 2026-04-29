# Job Cancellation API - Design & Flow

**Purpose:** Enable operators to stop problematic jobs (especially those crashing the system) and clear the queued backlog.

---

## Core Design Decisions

| Decision | Rationale |
|----------|-----------|
| **Add `CANCELLED` status** vs delete | Preserves audit trail; clients can query cancelled jobs like any terminal state |
| **Flag-only for PROCESSING jobs** | Job may be crashing the system; restart service to stop running task safely |
| **Separate admin API key** | Rotate admin credentials independently from regular API keys |
| **No-op on terminal states** | COMPLETED/FAILED represent immutable truth |
| **Lazy queue removal** | `asyncio.Queue` doesn't support targeted removal; skip cancelled jobs at dequeue time |
| **Clear-queue clears ALL queued+processing** | Simplest mental model; also handles jobs that already transitioned to PROCESSING |
| **Startup processing delay** | 30s configurable window to cancel/clear before jobs start processing |

---

## State Machine

```
QUEUED ──cancel──> CANCELLED
  │                      │
  │ start              terminal
  │                      │
  ▼                      │
PROCESSING ──cancel──> CANCELLED (with warning)
  │                      │
  │ complete/fail      terminal
  │                      │
  ▼                      ▼
COMPLETED/FAILED    queryable, purged after 7 days
```

**Key:** CANCELLED is terminal (like COMPLETED/FAILED). Warning only shown if job was PROCESSING at cancellation time.

---

## API Endpoints

### `POST /api/v1/jobs/{job_id}/cancel`
**Auth:** `SOW_ADMIN_API_KEY` (Bearer token)

**Behavior:**
| Current Status | Action | Response |
|---------------|--------|----------|
| QUEUED | Set → CANCELLED | 200 + JobResponse |
| PROCESSING | Set → CANCELLED | 200 + warning field |
| COMPLETED/FAILED/CANCELLED | No-op | 200 + existing JobResponse |
| Not found | — | 404 |

**Warning for PROCESSING:**
```json
{
  "warning": "Job was PROCESSING. The running task continues until service restart."
}
```

### `POST /api/v1/jobs/clear-queue`
**Auth:** `SOW_ADMIN_API_KEY` (Bearer token)

**Behavior:** Sets all QUEUED and PROCESSING jobs to CANCELLED.

**Response:**
```json
{
  "cancelled_count": 5,
  "cancelled_job_ids": ["job_abc123def456", "job_xyz789abc012", "..."]
}
```

---

## Processing Flow

### 1. Cancel Single Job
```
Client → POST /jobs/{id}/cancel + admin key
    ↓
routes.jobs.verify_admin_api_key()
    ↓ (if valid)
job_queue.cancel_job(job_id)
    ↓
Update job.status = CANCELLED in memory
    ↓
Persist to SQLite (update_job with status="cancelled")
    ↓
Return (job, warning) tuple
    ↓
Build JobResponse with warning field (only for PROCESSING → CANCELLED)
```

### 2. Clear Queue
```
Client → POST /jobs/clear-queue + admin key
    ↓
routes.jobs.verify_admin_api_key()
    ↓ (if valid)
job_queue.clear_queue()
    ↓
Iterate _jobs dict → find all QUEUED and PROCESSING
    ↓
Set each to CANCELLED + persist to SQLite
    ↓
Query SQLite for QUEUED/PROCESSING jobs not in memory
    ↓
Set each to CANCELLED + persist + add to _jobs
    ↓
Return list[Job]
    ↓
Build ClearQueueResponse
```

### 3. Processing Loop Guard
```
worker processes queue item
    ↓
acquire semaphore lock
    ↓
CHECK: current_job.status == CANCELLED?
    ↓ YES → log "Skipping cancelled job", return early
    ↓ NO  → continue processing
    ↓
execute job (analyze/lrc/stem_separation)
```

### 4. Startup Recovery
```
JobQueue.initialize()
    ↓
purge_old_jobs()  # removes COMPLETED/FAILED/CANCELLED > 7 days
    ↓
get_interrupted_jobs()  # PROCESSING → reset to QUEUED, re-queue
    ↓
get_queued_jobs()  # load QUEUED → add to _jobs + _queue
    ↓
processing begins
```

### 5. Startup Processing Delay
```
process_jobs() called
    ↓
SOW_QUEUE_START_DELAY_SECONDS > 0?
    ↓ YES: log "Queue processing paused for {delay}s — use this window to cancel/clear jobs"
    ↓
sleep 1s at a time (responsive to stop())
    ↓
log "Queue processing starting"
    ↓
begin dequeuing jobs
```

---

## Lazy Queue Removal Detail

**Problem:** `asyncio.Queue` doesn't support removing specific items.

**Solution:** Jobs remain in queue until dequeued. When processing loop picks up a job:

```python
# After acquiring semaphore, before actual processing:
job = self._jobs.get(job_id)
if job and job.status == JobStatus.CANCELLED:
    logger.info(f"Skipping cancelled job {job_id}")
    return  # Discard without processing
```

This is race-condition-free because:
- Status check happens under semaphore (serialized per job type)
- Memory state (_jobs dict) is source of truth
- SQLite persistence ensures state survives restarts

---

## Environment Configuration

```bash
# Required for regular operations
SOW_ANALYSIS_API_KEY="your-regular-api-key"

# Required for admin operations (cancel, clear-queue)
SOW_ADMIN_API_KEY="your-admin-api-key"

# Startup delay before processing begins (window to cancel/clear jobs)
SOW_QUEUE_START_DELAY_SECONDS=30
```

**Admin endpoints behavior:**
- No `SOW_ADMIN_API_KEY` set → 503 Service Unavailable
- Wrong key → 401 Unauthorized

---

## Data Persistence

| Aspect | Behavior |
|--------|----------|
| **Status stored in** | SQLite (`status` column with CHECK constraint) |
| **Memory cache** | `_jobs` dict for active jobs (cancelled not loaded — queryable via DB fallback) |
| **Terminal state cleanup** | 5-minute delay before evicting from memory |
| **Database purge** | 7 days for COMPLETED/FAILED/CANCELLED |
| **Periodic log** | Excludes cancelled (terminal state, like completed/failed) |
| **Migration** | Auto-detect old schema, recreate table with new constraint |

---

## Testing Coverage

| Component | Tests |
|-----------|-------|
| CANCELLED enum | `test_models.py::TestJobStatus` |
| Warning field | `test_models.py::TestJobResponse::test_response_with_warning` |
| Cancel endpoint | `test_api.py::TestAdminEndpoints::test_cancel_job_*` (5 tests) |
| Clear-queue endpoint | `test_api.py::TestAdminEndpoints::test_clear_queue` |
| Clear-queue cancels PROCESSING | `test_queue_persistence.py::test_clear_queue_cancels_processing_jobs` |
| Clear-queue skips terminal | `test_queue_persistence.py::test_clear_queue_skips_completed_failed_cancelled` |
| get_cancelled_jobs() | `test_job_store.py::test_get_cancelled_jobs` |
| Purge cancelled | `test_job_store.py::test_purge_includes_cancelled_jobs` |

**Total:** 10 new tests, all passing.

---

## Files Modified

```
services/analysis/src/sow_analysis/
├── models.py           # CANCELLED enum, warning field
├── config.py           # SOW_ADMIN_API_KEY
├── storage/db.py       # CHECK constraint, get_cancelled_jobs(), purge, migration
├── workers/queue.py    # cancel_job(), clear_queue(), loop guard, startup recovery
└── routes/jobs.py      # verify_admin_api_key, cancel endpoint, clear-queue endpoint

tests/services/analysis/
├── test_models.py      # +2 tests
├── test_api.py         # +8 tests
└── test_job_store.py   # +2 tests

reports/
└── cancel_jobs_api_impl_summary.md
```

---

## Out of Scope

Not implemented (per spec):
- Child job cascade cancellation (manual using job IDs from logs)
- DELETE endpoint for permanent DB removal (7-day purge handles cleanup)
- Running task abort (flag-only; restart service to stop)
- Job timeout (not addressed)
- Partial result cleanup on cancel (temp files handled by TemporaryDirectory)

---

*Implementation complete. See `reports/cancel_jobs_api_impl_summary.md` for full details.*
