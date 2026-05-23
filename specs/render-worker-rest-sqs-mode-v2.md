# Render Worker Mode: REST vs SQS (v2)

## Summary

Add `SOW_RENDER_WORKER_MODE` env var to the webapp Next.js server to determine whether to trigger the render worker via **REST** (AWS Lambda RIE) or **SQS** (current behavior). This enables local development without an SQS queue by invoking the render-worker Docker container directly via its Runtime Interface Emulator (RIE) endpoint.

## Current Architecture

```
Browser --POST /api/render-jobs--> Next.js
  1. createRenderJob() -> INSERT render_jobs (status: "queued")
  2. SQSClient.sendMessage({ jobId, songsetId, userId }) -> AWS SQS
  3. Return 201

AWS SQS --event-source-mapping--> Lambda (sow-render-worker)
  1. handler(event) -> parse Records[].body JSON
  2. execute_render_pipeline(jobId, userId, conn)
  3. Updates DB status (running -> completed/failed)
```

**Key files:**
- `webapp/src/app/api/render-jobs/route.ts` — API route that creates job + enqueues to SQS
- `webapp/src/lib/sqs/client.ts` — SQS client wrapper (`SQSClient`, `createSQSClientFromEnv`)
- `webapp/src/lib/render/job-manager.ts` — DB job CRUD (`createRenderJob`, `failRenderJob`, etc.)
- `services/render-worker/src/sow_render_worker/lambda_handler.py` — Lambda handler expecting SQS `Records` format

## New Architecture

```
SOW_RENDER_WORKER_MODE=sqs (default, production):
  Same as current: Next.js -> SQS -> Lambda

SOW_RENDER_WORKER_MODE=rest (local dev):
  Next.js -> POST http://localhost:9000/2015-03-31/functions/function/invocations
  Payload wrapped in SQS Records format (Lambda handler unchanged)
  Awaits full response (Lambda runs to completion before API returns)
```

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| REST invocation style | **Await response** | The RIE endpoint does not return response headers until the Lambda completes. A fire-and-forget with timeout would always abort (video renders take 4+ min). Awaiting the full response is acceptable in local dev (long-running Node.js, not Vercel serverless). The API route blocks until the render completes, then returns 201. |
| REST payload format | **Wrap in SQS `Records` format** | Lambda handler works unchanged. No new handler endpoint needed. |
| RIE URL | **Configurable via `SOW_RENDER_WORKER_REST_URL`** | Defaults to `http://localhost:9000/2015-03-31/functions/function/invocations`. Flexible for different Docker setups. |
| REST failure handling | **Mark job as failed** | If `fetch()` throws (connection refused, DNS failure, etc.), the API route catches the error and marks the job as failed — same path as SQS dispatch failure. If the Lambda itself fails, the handler already marks the job as failed in DB. |
| Concurrency control | **None (local dev)** | REST mode sends all jobs to a single Docker container with no concurrency limit. Local dev typically has 1 user submitting 1 job at a time. If the container OOMs, orphan recovery (30 min) catches it. |
| Batch semantics | **1 record per invocation** | REST mode always sends exactly 1 SQS Record per invocation. The Lambda handler's batch failure protocol (`batchItemFailures` return value) and inter-record `conn.rollback()` are no-ops in this mode. |

### Why not fire-and-forget?

The v1 spec proposed fire-and-forget with a 5s `AbortController` timeout. This is broken because:

1. The RIE endpoint buffers the entire Lambda response before sending HTTP headers
2. Video renders take 4+ minutes, so the 5s timeout **always** fires
3. This causes `AbortError` → API route marks job as "failed"
4. But the Lambda is still running → later marks job as "completed"
5. Final DB status becomes non-deterministic (race condition)

Awaiting the full response eliminates this race condition. The trade-off is that `POST /api/render-jobs` blocks for the full render duration, which is acceptable because:

- REST mode is only for local development (not Vercel serverless)
- The browser already polls DB for progress regardless of when the 201 returns
- The user sees the job transition from "queued" → "running" → "completed" via SSE/polling

## Environment Variables

### New Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SOW_RENDER_WORKER_MODE` | No | `sqs` | `sqs` or `rest`. Determines how the webapp triggers the render worker. |
| `SOW_RENDER_WORKER_REST_URL` | No | `http://localhost:9000/2015-03-31/functions/function/invocations` | RIE invocation endpoint URL. Only used when mode is `rest`. |

### Existing Variables (unchanged)

When `SOW_RENDER_WORKER_MODE=sqs` (default), all existing SQS env vars are still required:
- `AWS_REGION`
- `SQS_QUEUE_URL`
- `AWS_ACCESS_KEY_ID` (optional)
- `AWS_SECRET_ACCESS_KEY` (optional)
- `SQS_ENDPOINT_URL` (optional)

When `SOW_RENDER_WORKER_MODE=rest`, SQS env vars are **not required**.

## Implementation Plan

### Step 1: Create REST client module

**New file:** `webapp/src/lib/render/rest-client.ts`

```typescript
export interface RenderWorkerRestConfig {
  url: string;
}

export interface RenderWorkerMessage {
  jobId: string;
  songsetId: string;
  userId: number;
}

export class RenderWorkerRestClient {
  private url: string;

  constructor(config: RenderWorkerRestConfig) {
    this.url = config.url;
  }

  async invoke(message: RenderWorkerMessage): Promise<void> {
    const payload = {
      Records: [
        {
          messageId: `rest-${message.jobId}`,
          body: JSON.stringify({
            jobId: message.jobId,
            songsetId: message.songsetId,
            userId: message.userId,
          }),
        },
      ],
    };

    const response = await fetch(this.url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      throw new Error(
        `Render worker REST invocation failed: ${response.status} ${response.statusText}`
      );
    }
  }
}

export function createRestClientFromEnv(): RenderWorkerRestClient {
  const url =
    process.env.SOW_RENDER_WORKER_REST_URL ||
    "http://localhost:9000/2015-03-31/functions/function/invocations";

  return new RenderWorkerRestClient({ url });
}
```

**Key design points:**
- Payload wraps the message in SQS `Records` format with a synthetic `messageId` (`rest-{jobId}`)
- Awaits the full response — the `fetch()` call blocks until the Lambda completes (4+ min for video renders). This is intentional and acceptable for local dev.
- Checks `response.ok` — if the RIE returns a non-2xx status (e.g., container error), throws an error that the API route can catch.
- No `AbortController` or timeout — avoids the race condition described in "Why not fire-and-forget?"
- `createRestClientFromEnv()` reads `SOW_RENDER_WORKER_REST_URL` with a sensible default

### Step 2: Create dispatcher module

**New file:** `webapp/src/lib/render/dispatcher.ts`

This module abstracts the SQS vs REST dispatch logic, keeping the API route clean.

```typescript
import { createSQSClientFromEnv, SQSClient, SQSMessage } from "@/lib/sqs/client";
import {
  createRestClientFromEnv,
  RenderWorkerRestClient,
  RenderWorkerMessage,
} from "@/lib/render/rest-client";

export type RenderWorkerMode = "sqs" | "rest";

export function getRenderWorkerMode(): RenderWorkerMode {
  const mode = process.env.SOW_RENDER_WORKER_MODE;
  if (mode === "rest") return "rest";
  return "sqs";
}

export interface DispatchMessage {
  jobId: string;
  songsetId: string;
  userId: number;
}

let cachedSqsClient: SQSClient | null = null;
let cachedRestClient: RenderWorkerRestClient | null = null;

export async function dispatchToRenderWorker(message: DispatchMessage): Promise<void> {
  const mode = getRenderWorkerMode();

  if (mode === "rest") {
    if (!cachedRestClient) {
      cachedRestClient = createRestClientFromEnv();
    }
    await cachedRestClient.invoke(message as RenderWorkerMessage);
  } else {
    if (!cachedSqsClient) {
      cachedSqsClient = createSQSClientFromEnv();
    }
    await cachedSqsClient.sendMessage(message as SQSMessage);
  }
}
```

**Key design points:**
- Client instances (`SQSClient`, `RenderWorkerRestClient`) are cached at module level to avoid creating new instances on every dispatch call. This matches the current pattern where the SQS client is created once in the API route.
- `message as SQSMessage` / `message as RenderWorkerMessage` type assertions are safe because `DispatchMessage` shares the same shape (`{ jobId, songsetId, userId }`). If the interfaces diverge in the future, add explicit field mapping.

### Step 3: Update API route

**Modify:** `webapp/src/app/api/render-jobs/route.ts`

Replace the direct SQS call with the dispatcher:

```typescript
// Before:
import { createSQSClientFromEnv } from "@/lib/sqs/client";
// ...
const sqsClient = createSQSClientFromEnv();
await sqsClient.sendMessage({
  jobId: job.id,
  songsetId: job.songsetId,
  userId: Number(session.user.id),
});

// After:
import { dispatchToRenderWorker } from "@/lib/render/dispatcher";
// ...
await dispatchToRenderWorker({
  jobId: job.id,
  songsetId: job.songsetId,
  userId: Number(session.user.id),
});
```

The error handling block stays the same — on failure, mark the job as failed and return 500. Update the error message to be mode-agnostic:

```typescript
// Before:
await failRenderJob(job.id, Number(session.user.id), "Failed to enqueue render job to SQS");
// After:
await failRenderJob(job.id, Number(session.user.id), "Failed to dispatch render job to worker");
```

**Behavioral difference in REST mode:** In SQS mode, the API route returns 201 almost instantly (SQS enqueue is fast). In REST mode, the API route blocks until the Lambda completes. The browser's `POST /api/render-jobs` request will be in-flight for the full render duration. This is acceptable because:
- The browser already polls DB for progress via SSE
- Local dev only, not Vercel serverless (no function timeout)
- The user sees the job progress through the existing polling UI

### Step 4: Update environment variable files

**Modify:** `webapp/.env.example` — Add new variables:

```env
# Render worker dispatch mode: "sqs" (default, production) or "rest" (local dev with Docker).
# When "rest", the webapp invokes the render worker via its Lambda RIE endpoint
# instead of enqueuing to SQS. SQS env vars are not required in "rest" mode.
# NOTE: In "rest" mode, the API route blocks until the render completes (4+ min for video).
SOW_RENDER_WORKER_MODE=sqs

# Render worker REST URL (only used when SOW_RENDER_WORKER_MODE=rest).
# Default: http://localhost:9000/2015-03-31/functions/function/invocations
SOW_RENDER_WORKER_REST_URL=http://localhost:9000/2015-03-31/functions/function/invocations
```

**Modify:** `webapp/.env.production.example` — Add documentation for the new variables in the AWS SQS section.

### Step 5: Add tests

**New file:** `webapp/src/test/lib/render/rest-client.test.ts`

Test cases:
1. `RenderWorkerRestClient.invoke()` sends POST with correct SQS-wrapped payload
2. `RenderWorkerRestClient.invoke()` uses configured URL
3. `RenderWorkerRestClient.invoke()` throws on connection refused
4. `RenderWorkerRestClient.invoke()` throws on non-2xx response status
5. `RenderWorkerRestClient.invoke()` awaits the full response (no abort/timeout)
6. `createRestClientFromEnv()` reads `SOW_RENDER_WORKER_REST_URL` from env
7. `createRestClientFromEnv()` uses default URL when env var not set

**New file:** `webapp/src/test/lib/render/dispatcher.test.ts`

Test cases:
1. `getRenderWorkerMode()` returns `"sqs"` by default (no env var)
2. `getRenderWorkerMode()` returns `"rest"` when `SOW_RENDER_WORKER_MODE=rest`
3. `getRenderWorkerMode()` returns `"sqs"` when `SOW_RENDER_WORKER_MODE=sqs`
4. `getRenderWorkerMode()` returns `"sqs"` for unknown values
5. `dispatchToRenderWorker()` calls SQS client when mode is `sqs`
6. `dispatchToRenderWorker()` calls REST client when mode is `rest`
7. `dispatchToRenderWorker()` passes correct message fields
8. `dispatchToRenderWorker()` reuses cached client instances across calls

**Modify:** `webapp/src/test/api/render-jobs/route.test.ts`

Update existing tests:
- Mock `dispatchToRenderWorker` instead of `createSQSClientFromEnv`
- Update the "SQS enqueue fails" test to test "dispatch fails" generically
- Add a test for REST mode dispatch success
- Add a test for REST mode dispatch failure

### Step 6: Update docker-compose documentation

**Modify:** `services/render-worker/README.md` — Add a section on using `SOW_RENDER_WORKER_MODE=rest` for local development, showing the complete flow:

```bash
# Terminal 1: Start render worker
cd services/render-worker && docker compose up --build

# Terminal 2: Start webapp with REST mode
cd webapp
SOW_RENDER_WORKER_MODE=rest pnpm dev
```

Add a note about concurrency: "REST mode sends all jobs to a single Docker container with no concurrency limit. For local dev with a single user this is fine. If the container crashes (OOM), orphan recovery will mark the job as failed after 30 minutes."

## Files Changed Summary

| File | Action | Description |
|------|--------|-------------|
| `webapp/src/lib/render/rest-client.ts` | **New** | REST client for invoking Lambda RIE (awaits full response, no timeout) |
| `webapp/src/lib/render/dispatcher.ts` | **New** | Dispatcher that selects SQS vs REST based on env var (caches client instances) |
| `webapp/src/app/api/render-jobs/route.ts` | **Modify** | Replace direct SQS call with `dispatchToRenderWorker()` |
| `webapp/.env.example` | **Modify** | Add `SOW_RENDER_WORKER_MODE` and `SOW_RENDER_WORKER_REST_URL` |
| `webapp/.env.production.example` | **Modify** | Document new env vars |
| `webapp/src/test/lib/render/rest-client.test.ts` | **New** | REST client unit tests |
| `webapp/src/test/lib/render/dispatcher.test.ts` | **New** | Dispatcher unit tests |
| `webapp/src/test/api/render-jobs/route.test.ts` | **Modify** | Update mocks to use dispatcher |
| `services/render-worker/README.md` | **Modify** | Add local dev section with REST mode + concurrency note |

## No Changes Required

- **`services/render-worker/`** — Lambda handler works unchanged because REST payload is wrapped in SQS `Records` format. Batch failure protocol (`batchItemFailures`) is a no-op since REST mode always sends exactly 1 record.
- **`webapp/src/lib/sqs/client.ts`** — SQS client stays as-is; it's still used when mode is `sqs`
- **`webapp/src/lib/render/job-manager.ts`** — Job manager stays as-is; same DB operations regardless of dispatch mode
- **SSE progress endpoint** — Still polls DB; no changes needed

## Changes from v1

| Item | v1 | v2 | Reason |
|------|----|----|--------|
| REST invocation style | Fire-and-forget with 5s timeout | Await full response | RIE doesn't return headers until Lambda completes; 5s timeout always fires, causing race condition between API route (marks failed) and Lambda (marks completed) |
| AbortController | Yes (5s timeout) | Removed | No longer needed without fire-and-forget |
| Response status check | No | Yes (`response.ok`) | Detects container-level errors (non-2xx) that `fetch` doesn't throw on |
| Client instance caching | No (created per call) | Yes (module-level cache) | Avoids creating new `SQSClient`/`RenderWorkerRestClient` on every dispatch |
| Concurrency note | Not mentioned | Documented | Single Docker container, no SQS concurrency control; orphan recovery as safety net |
| Batch semantics note | Not mentioned | Documented | REST mode always sends 1 record; `batchItemFailures` return value is ignored |
