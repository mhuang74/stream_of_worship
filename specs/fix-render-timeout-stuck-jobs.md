# Fix Render Worker Timeout — Stuck Jobs Not Detected by Webapp (v2)

**Service:** Render Worker (`services/render-worker/`) + Webapp (`webapp/`)
**Status:** Draft
**Created:** 2026-05-23
**Updated:** 2026-05-23 (v2 — incorporates operational review)

## Changelog (v1 → v2)

| Change | Reason |
|--------|--------|
| Fix 2 downgraded from P0 to P1 | SIGTERM grace period for container-image Lambdas is ~300-500ms, not 5s. Insufficient for reliable DB write. |
| Fix 2 rewritten: flag-only SIGTERM | DB I/O in a Python signal handler is unsafe (psycopg2 C internals are not async-signal-safe; risk of deadlock if connection is mid-operation). Handler now sets a flag only; `check_lambda_timeout()` inspects it. |
| Fix 1 expanded: timeout check inside frame-rendering loop | Phase-boundary checks miss long-running inner loops. `rendering_frames` renders in a loop and can run for minutes; timeout must be checked per-frame. |
| Fix 3 expanded: `reclaim_stale_job` resets all progress fields | Without resetting `started_at`, `phase`, `phase_index`, `percent_complete`, a reclaimed job shows stale elapsed time and progress on retry. |
| Fix 4: documented SSE write side effect and 410-on-reconnect interaction | The events route changes from read-only observer to actively failing jobs. Multiple SSE connections may race (harmless due to DB guard). A job failed by the SSE endpoint will get HTTP 410 on next reconnect instead of a terminal SSE event. |
| Out of Scope updated: SIGTERM rationale corrected | Removed claim that "5-second window is enough for one DB write." |

## Problem Statement

When the render worker Lambda function times out without properly rendering artifacts, the webapp Next.js server does not pick up this error state and error out the render job. The job remains stuck in `running` status indefinitely, and the user sees "Rendering..." forever.

## Root Cause Analysis

When a Lambda timeout occurs, **five compounding failures** prevent proper error detection:

### 1. Lambda Timeout Kills Process Without Cleanup

When AWS terminates a timed-out Lambda:
- The process is **SIGKILL'd** — no `finally` blocks execute
- `fail_render_job()` is never called — the DB row stays `running`
- `conn.close()` never runs — DB connection leaks
- `asset_fetcher.cleanup_temp()` never runs — temp files orphaned
- The `context` parameter is **completely unused** (`lambda_handler.py:43`) — no `context.get_remaining_time_in_millis()` check exists
- No `SIGTERM` signal handler, no `atexit` handler, no Lambda Extension registered

### 2. SQS Retries Are Ineffective for Timed-Out Jobs

After Lambda timeout, the SQS message becomes visible again (after 15min visibility timeout). On retry:
- `start_render_job()` (`db.py:137-153`) uses `WHERE status = 'queued'` — but the job is already `running`
- Returns `None`, pipeline silently skips at `pipeline.py:182-188`
- The retry is **wasted** — the job stays `running`

### 3. Orphan Recovery Is Not Automated

`recoverOrphanedJobs()` exists in both codebases with a 30-minute threshold:
- **Webapp** (`job-manager.ts:346-384`): Only called when `createRenderJob()` is invoked — purely **opportunistic**, not periodic
- **Render worker** (`db.py:297-331`): Never called automatically by the Lambda handler
- **No cron job or scheduled function** exists to periodically sweep for orphans

### 4. SSE Stream Has No Staleness Detection

The SSE endpoint (`events/route.ts`) polls the DB every 1 second but:
- Only checks for terminal states (`completed`/`failed`/`cancelled`)
- **Never checks if `updated_at` is stale** — a job stuck in `running` with no progress updates for 30+ minutes still gets streamed as a normal `running` event
- The 30-minute `MAX_DURATION_MS` timeout just closes the SSE connection with a generic "Connection timed out" — it does **not** mark the job as failed
- **Vercel constraint:** `vercel.json` sets `maxDuration: 60` for this route, so the SSE function is killed at 60s. The 30-minute `MAX_DURATION_MS` is never reached in production. Staleness detection only fires on the first poll of each new SSE connection (after client reconnects).

### 5. Client-Side Has No Watchdog

`RenderProgress.tsx` retries SSE connections on error but has no mechanism to detect a job that's been `running` too long without progress changes.

### Failure Flow Summary

```
Lambda times out
  → Process killed (SIGKILL)
  → No fail_render_job() called
  → Job stays "running" in DB
  → SQS retry: start_render_job() sees "running" not "queued" → silently skips
  → No periodic orphan recovery runs
  → SSE polls DB every 1s → sees "running" → streams progress events forever
  → Client shows "Rendering..." indefinitely
  → After 60s Vercel kills SSE function → client reconnects → same cycle
  → After 3 SSE retries fail: "Lost connection" (but job still "running")
  → Job is permanently stuck in "running" until someone creates a new render
```

## Implementation Plan

### Fix 1: Proactive Timeout Detection in Lambda (P0)

**Goal:** Give the pipeline time to call `fail_render_job()` before Lambda timeout kills the process.

**File:** `services/render-worker/src/sow_render_worker/lambda_handler.py`

Pass the Lambda `context` object to the pipeline:

```python
def handler(event, context):
    # ... existing code ...
    execute_render_pipeline(job_id, user_id, conn, lambda_context=context)
```

**File:** `services/render-worker/src/sow_render_worker/pipeline.py`

Add `lambda_context` parameter and check remaining time before each phase **and inside long-running loops**:

```python
import signal

LAMBDA_TIMEOUT_SAFETY_MARGIN_SECONDS = 60

_shutdown_requested = False


def _sigterm_handler(signum: int, frame) -> None:
    global _shutdown_requested
    _shutdown_requested = True


signal.signal(signal.SIGTERM, _sigterm_handler)


def execute_render_pipeline(
    job_id: str,
    user_id: int,
    conn: psycopg2.extensions.connection,
    asset_fetcher: AssetFetcher | None = None,
    uploader: R2Uploader | None = None,
    lambda_context: Any | None = None,
) -> None:
    # ... existing code ...

    def check_lambda_timeout() -> None:
        """Raise TimeoutError if Lambda is about to timeout or SIGTERM was received."""
        global _shutdown_requested
        if _shutdown_requested:
            _shutdown_requested = False
            raise TimeoutError("Lambda received SIGTERM, shutting down gracefully")
        if lambda_context is None:
            return
        remaining_ms = lambda_context.get_remaining_time_in_millis()
        remaining_seconds = remaining_ms / 1000
        if remaining_seconds < LAMBDA_TIMEOUT_SAFETY_MARGIN_SECONDS:
            raise TimeoutError(
                f"Lambda timeout imminent ({remaining_seconds:.0f}s remaining, "
                f"need {LAMBDA_TIMEOUT_SAFETY_MARGIN_SECONDS}s safety margin)"
            )

    # Call check_lambda_timeout():
    # - After start_render_job() (before preparing phase)
    # - Before mixing_audio phase
    # - Before rendering_frames phase
    # - INSIDE the rendering_frames loop (per-frame or per-batch)
    # - Before encoding_video phase
    # - Before uploading phase
```

The existing `except Exception` handler at `pipeline.py:347-358` will catch `TimeoutError` and call `fail_render_job()`.

**Why per-frame check in rendering_frames:** This is the longest phase — it renders frames in a loop and can run for many minutes. A phase-boundary-only check would miss the case where remaining time drops below the safety margin mid-phase. The per-frame check adds negligible overhead (one function call + integer comparison per frame).

### Fix 2: SIGTERM Flag Handler for Graceful Shutdown (P1)

**Goal:** Catch SIGTERM (sent ~300-500ms before SIGKILL for container-image Lambdas) and set a flag that `check_lambda_timeout()` inspects.

**File:** `services/render-worker/src/sow_render_worker/pipeline.py`

The SIGTERM handler and flag are defined in Fix 1 above. The handler is minimal — it only sets `_shutdown_requested = True`. No DB I/O occurs in the signal handler.

**Why flag-only (no DB I/O in signal handler):** Python signal handlers run on the main thread between bytecode instructions. If SIGTERM fires while psycopg2 is mid-operation (holding the socket lock, inside a C extension call), attempting a DB write in the handler risks deadlock or connection corruption. Setting a flag is async-signal-safe; the next `check_lambda_timeout()` call (within the same frame or phase boundary) will detect it and raise `TimeoutError`, which the existing `except Exception` handler will catch and call `fail_render_job()`.

**Why P1 not P0:** The SIGTERM grace period for container-image Lambda functions is approximately 300-500ms, not the 5 seconds sometimes cited for zip-deployed functions. This window is too short to rely on as a primary mechanism. Fix 1 (proactive `get_remaining_time_in_millis()` check with 60s margin) is the primary P0 defense. The SIGTERM flag is a best-effort backup for the narrow case where the proactive check's timing doesn't catch the timeout.

### Fix 3: Fix SQS Retry Logic to Reclaim Stale Jobs (P1)

**Goal:** When an SQS retry happens and the job is already `running`, check if it's stale and reclaim it with a full progress reset.

**File:** `services/render-worker/src/sow_render_worker/db.py`

Add new function:

```python
STALE_JOB_THRESHOLD_SECONDS = 300  # 5 minutes


def reclaim_stale_job(
    conn: psycopg2.extensions.connection,
    job_id: str,
    user_id: int,
    stale_threshold_seconds: int = STALE_JOB_THRESHOLD_SECONDS,
) -> Optional[RenderJob]:
    """
    Reclaim a job that's stuck in 'running' state with no recent progress.

    Resets all progress fields (started_at, phase, phase_index, percent_complete)
    so the retry starts with a clean slate.

    Returns the job if successfully reclaimed (status set to 'queued'),
    or None if the job was not stale or didn't exist.
    """
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(seconds=stale_threshold_seconds)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            "SELECT * FROM render_jobs WHERE id = %s AND user_id = %s AND status = %s",
            (job_id, user_id, "running"),
        )
        row = cur.fetchone()
        if not row:
            return None

        job = _row_to_render_job(row)
        if job.updated_at is None:
            return None

        aware_updated = (
            job.updated_at.replace(tzinfo=timezone.utc)
            if job.updated_at.tzinfo is None
            else job.updated_at
        )
        if aware_updated >= threshold:
            return None

        # Reclaim: reset to queued with all progress fields cleared
        cur.execute(
            "UPDATE render_jobs "
            "SET status = %s, error_message = %s, updated_at = %s, "
            "    started_at = NULL, phase = %s, phase_index = %s, percent_complete = %s "
            "WHERE id = %s AND user_id = %s AND status = %s "
            "RETURNING *",
            ("queued", None, now, "preparing", 0, 0, job_id, user_id, "running"),
        )
        row = cur.fetchone()

    if not row:
        return None
    return _row_to_render_job(row)
```

**Why reset all progress fields:** Without resetting `started_at`, `phase`, `phase_index`, and `percent_complete`, a reclaimed job would display stale elapsed time (from the original start) and incorrect progress on retry. The `start_render_job()` function uses `COALESCE(started_at, now)` so a NULL `started_at` will be set to the current time on the next claim.

**File:** `services/render-worker/src/sow_render_worker/pipeline.py`

Update the `start_render_job()` check:

```python
started = start_render_job(conn, job_id, user_id)
if not started:
    reclaimed = reclaim_stale_job(conn, job_id, user_id)
    if reclaimed:
        logger.info(
            "Reclaimed stale job %s (was stuck in 'running' for too long), retrying",
            job_id,
        )
        started = start_render_job(conn, job_id, user_id)

    if not started:
        logger.info(
            "Render job %s was already claimed by another invocation, skipping",
            job_id,
        )
        return
```

### Fix 4: SSE Staleness Detection (P1)

**Goal:** The SSE endpoint should detect stale `running` jobs and mark them as failed.

**File:** `webapp/src/app/api/render-jobs/[id]/events/route.ts`

Add staleness check in the poll loop:

```typescript
import { failRenderJob } from "@/lib/render/job-manager";

const STALE_JOB_THRESHOLD_MINUTES = 15;

async function poll() {
    // ... existing code ...

    const updatedJob = await getRenderJob(id, Number(session!.user.id));

    // ... existing null check ...

    // Check for stale running job
    if (updatedJob.status === "running" && updatedJob.updatedAt) {
        const staleMinutes = (Date.now() - updatedJob.updatedAt.getTime()) / 60000;
        if (staleMinutes > STALE_JOB_THRESHOLD_MINUTES) {
            await failRenderJob(
                id,
                Number(session.user.id),
                `Job timed out (no progress for ${Math.round(staleMinutes)} minutes)`
            );
            const failedJob = await getRenderJob(id, Number(session!.user.id));
            if (failedJob) {
                const finalEvent: SSEEvent = {
                    phase: failedJob.phase ?? "preparing",
                    phaseIndex: failedJob.phaseIndex ?? 0,
                    totalPhases: failedJob.totalPhases ?? 5,
                    estimatedTotalSeconds: failedJob.estimatedTotalSeconds ?? 0,
                    elapsedSeconds: failedJob.startedAt
                        ? (Date.now() - failedJob.startedAt.getTime()) / 1000
                        : 0,
                    status: "failed",
                    errorMessage: failedJob.errorMessage ?? "Job timed out",
                };
                safeEnqueue(encoder.encode(`data: ${JSON.stringify(finalEvent)}\n\n`));
                safeClose();
            }
            return;
        }
    }

    // ... existing terminal state check and progress event ...
}
```

**Operational notes:**

1. **SSE endpoint is now a write endpoint.** Previously the events route was read-only (polling DB, streaming events). It now calls `failRenderJob()`, which mutates DB state. This changes its permission/security profile — ensure the route's auth check (`session`) is sufficient for write operations.

2. **Concurrent SSE connections may race.** If multiple browser tabs or clients open SSE streams for the same job, they may all detect staleness simultaneously and race to call `failRenderJob()`. This is harmless — `failRenderJob` guards with `WHERE status IN ('running', 'queued')`, so only the first call succeeds. Subsequent calls return `None` and the SSE stream will see the already-failed status on the next poll.

3. **410-on-reconnect interaction.** The SSE endpoint currently returns HTTP 410 (JSON, not SSE) for jobs already in a terminal state. After this fix fails a stale job, the client's `EventSource` will reconnect and receive a 410, triggering `onerror` and up to 3 retry attempts before the separate `fetch` fallback in `RenderProgress.tsx` catches the terminal state. This is not ideal but is an existing bug (not introduced by this fix). A future improvement should send a terminal SSE event instead of HTTP 410.

4. **Vercel maxDuration constraint.** The SSE route has `maxDuration: 60` in `vercel.json`. The staleness check only fires during the ~60s window each SSE connection is alive. For a job that's been stale for 15+ minutes, the check will fire on the first poll of the first SSE connection that opens after the job becomes stale. The client's reconnect cycle (Vercel kills at 60s → client reconnects with backoff) means detection latency is at most ~65s after the user opens the SSE stream.

### Fix 5: Reduce Orphan Threshold to 15 Minutes (P1)

**Goal:** Align orphan threshold with SQS visibility timeout and Lambda max timeout.

**File:** `services/render-worker/src/sow_render_worker/db.py`

```python
ORPHANED_JOB_THRESHOLD_MINUTES = 15  # Changed from 30
```

**File:** `webapp/src/lib/render/job-manager.ts`

```typescript
const ORPHANED_JOB_THRESHOLD_MINUTES = 15;  // Changed from 30
```

### Fix 6: Client-Side Staleness Warning (P2)

**Goal:** Show a warning to the user if the job has been running for a long time without progress changes.

**File:** `webapp/src/components/render/RenderProgress.tsx`

Add a watchdog that tracks `elapsedSeconds` changes:

```typescript
const STALE_PROGRESS_THRESHOLD_MINUTES = 10;

// In the component:
const lastElapsedRef = useRef<number>(0);
const lastChangeTimeRef = useRef<number>(Date.now());
const [staleWarning, setStaleWarning] = useState<string | null>(null);

// In the SSE onmessage handler:
if (data.elapsedSeconds !== lastElapsedRef.current) {
    lastElapsedRef.current = data.elapsedSeconds;
    lastChangeTimeRef.current = Date.now();
    setStaleWarning(null);
} else {
    const minutesSinceChange = (Date.now() - lastChangeTimeRef.current) / 60000;
    if (minutesSinceChange > STALE_PROGRESS_THRESHOLD_MINUTES) {
        setStaleWarning(
            `Progress hasn't updated in ${Math.round(minutesSinceChange)} minutes. ` +
            `The render may be stuck. You can cancel and try again.`
        );
    }
}
```

Add warning display in the JSX:

```tsx
{staleWarning && (
    <Alert variant="destructive">
        <AlertCircle className="size-4" />
        <AlertDescription>{staleWarning}</AlertDescription>
    </Alert>
)}
```

## File Change Summary

### Render Worker (`services/render-worker/`)

| File | Changes |
|------|---------|
| `src/sow_render_worker/lambda_handler.py` | Pass `context` to pipeline |
| `src/sow_render_worker/pipeline.py` | Accept `lambda_context` parameter; add `check_lambda_timeout()` before each phase and inside rendering_frames loop; add SIGTERM flag handler (signal handler sets `_shutdown_requested`, `check_lambda_timeout()` inspects it); update `start_render_job()` logic to call `reclaim_stale_job()` |
| `src/sow_render_worker/db.py` | Reduce `ORPHANED_JOB_THRESHOLD_MINUTES` to 15; add `STALE_JOB_THRESHOLD_SECONDS` constant; add `reclaim_stale_job()` function with full progress field reset |
| `README.md` | Update orphan threshold documentation from 30 min to 15 min |

### Webapp (`webapp/`)

| File | Changes |
|------|---------|
| `src/app/api/render-jobs/[id]/events/route.ts` | Add `STALE_JOB_THRESHOLD_MINUTES` constant; add staleness check in poll loop; import `failRenderJob` |
| `src/lib/render/job-manager.ts` | Reduce `ORPHANED_JOB_THRESHOLD_MINUTES` to 15 |
| `src/components/render/RenderProgress.tsx` | Add stale progress watchdog with refs; add `staleWarning` state; display warning Alert |

## Implementation Order

1. **Fix 1** (P0) — Proactive timeout detection with per-frame checks
   - This is the primary defense against Lambda timeout
   - Includes SIGTERM flag handler (Fix 2) since it's defined in the same code
   - Test by simulating long-running renders that approach Lambda timeout

2. **Fix 3** (P1) — SQS retry logic with full progress reset
   - Depends on Fix 1 being in place (otherwise retries will still fail)
   - Test by manually setting a job to `running` with old `updated_at`, then triggering SQS retry

3. **Fix 4 + Fix 5** (P1) — SSE staleness + orphan threshold
   - These are independent and can be implemented in parallel
   - Test by manually setting a job to `running` with old `updated_at`, then opening SSE stream

4. **Fix 6** (P2) — Client-side warning
   - Nice-to-have UX improvement
   - Test by simulating a job with frozen `elapsedSeconds`

## Testing Plan

### Unit Tests

**File:** `services/render-worker/tests/test_timeout_handling.py` (new)

- `test_check_lambda_timeout_raises_when_low` — verify `check_lambda_timeout()` raises when remaining time < margin
- `test_check_lambda_timeout_passes_when_sufficient` — verify no raise when sufficient time
- `test_check_lambda_timeout_raises_on_sigterm_flag` — verify `check_lambda_timeout()` raises when `_shutdown_requested` is True
- `test_sigterm_handler_sets_flag` — verify SIGTERM handler sets `_shutdown_requested = True`
- `test_reclaim_stale_job_succeeds` — verify stale job is reset to `queued` with all progress fields cleared
- `test_reclaim_stale_job_resets_progress_fields` — verify `started_at=NULL`, `phase='preparing'`, `phase_index=0`, `percent_complete=0` after reclaim
- `test_reclaim_stale_job_skips_recent_job` — verify recent job is not reclaimed
- `test_reclaim_stale_job_skips_non_running_job` — verify non-running jobs are skipped

**File:** `webapp/src/lib/render/__tests__/job-manager.test.ts` (update)

- Add test for 15-minute orphan threshold

### Integration Tests

**Manual test scenarios:**

1. **Lambda timeout simulation:**
   - Set Lambda timeout to 60 seconds (temporarily)
   - Submit a render job that takes > 60 seconds
   - Verify job is marked as `failed` with appropriate error message
   - Verify SSE stream receives `failed` status

2. **SQS retry simulation:**
   - Manually set a job to `running` with `updated_at` 10 minutes ago
   - Send a test SQS message for that job
   - Verify job is reclaimed with progress fields reset and processed

3. **SSE staleness simulation:**
   - Manually set a job to `running` with `updated_at` 20 minutes ago
   - Open SSE stream for that job
   - Verify job is marked as `failed` and SSE stream closes with `failed` status

4. **Client-side warning:**
   - Start a render job
   - Manually freeze `elapsedSeconds` in DB (or simulate stuck worker)
   - Verify warning appears after 10 minutes

## Out of Scope

The following are explicitly **not** part of this spec:

- **Lambda Extension for pre-SIGKILL cleanup** — The SIGTERM flag handler is a best-effort backup; the primary mechanism is the proactive `get_remaining_time_in_millis()` check. A Lambda Extension would add deployment complexity for marginal gain.
- **Cron-based periodic orphan recovery** — SSE staleness check provides self-healing without external infrastructure. Every SSE connection acts as a watchdog.
- **Webhook/callback from Lambda to webapp** — Pull-based polling is simpler and already implemented.
- **Automatic retry of failed jobs** — User can manually retry after failure.
- **Cleanup of orphaned temp files in Lambda** — Lambda reuses containers; `/tmp` is cleaned between invocations in most cases.
- **DB connection pooling** — Current single-connection-per-invocation is sufficient for batch-size 1.
- **Fix HTTP 410 for terminal-state SSE connections** — Existing bug where the SSE endpoint returns JSON 410 instead of a terminal SSE event. Interacts with Fix 4 but is a separate issue.

## Decision Rationale

| Decision | Rationale |
|----------|-----------|
| 15-minute staleness threshold | Aligns with SQS visibility timeout (15 min) and typical Lambda max timeout (15 min). A job with no progress for 15 min is almost certainly dead. |
| 60-second Lambda timeout safety margin | Gives enough time for `fail_render_job()` DB write plus cleanup. Most DB writes complete in < 1 second. |
| Flag-only SIGTERM handler (no DB I/O) | psycopg2's C internals are not async-signal-safe. DB I/O in a signal handler risks deadlock if the connection is mid-operation. Setting a flag is safe; `check_lambda_timeout()` inspects it on the next check. |
| SIGTERM handler as P1 (not P0) | Container-image Lambda functions receive SIGTERM ~300-500ms before SIGKILL, not 5 seconds. This window is too short for reliable DB writes. The proactive `get_remaining_time_in_millis()` check (Fix 1) is the primary P0 defense. |
| Per-frame timeout check in rendering_frames | This phase renders in a loop and can run for minutes. Phase-boundary-only checks would miss timeout mid-phase. Per-frame check adds negligible overhead. |
| 5-minute stale job threshold for SQS retry | If a job has been `running` for 5+ minutes with no progress, it's likely stuck. Shorter than 15-minute orphan threshold to enable faster recovery on retry. |
| Full progress field reset on reclaim | Without resetting `started_at`, `phase`, `phase_index`, `percent_complete`, a reclaimed job shows stale elapsed time and incorrect progress on retry. |
| SSE staleness check instead of cron | Self-healing without external infrastructure. Every SSE connection acts as a watchdog. |
| Client-side warning at 10 minutes | Earlier than 15-minute server-side threshold to give user a heads-up before automatic failure. |
