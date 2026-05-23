# Render Worker Mode: REST vs SQS

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
  Fire-and-forget (don't await response)
```

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| REST invocation style | **Fire-and-forget** | Matches current SQS async behavior. Webapp polls DB for progress as it does today. Avoids tying up a Vercel serverless function for minutes. |
| REST payload format | **Wrap in SQS `Records` format** | Lambda handler works unchanged. No new handler endpoint needed. |
| RIE URL | **Configurable via `SOW_RENDER_WORKER_REST_URL`** | Defaults to `http://localhost:9000/2015-03-31/functions/function/invocations`. Flexible for different Docker setups. |
| REST failure handling | **Mark job as failed** | Same as current SQS failure behavior. Simple and predictable. |

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
    // Wrap in SQS Records format so Lambda handler works unchanged
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

    // Fire-and-forget: use fetch with AbortController timeout
    // Don't await the response body — just ensure the request is sent
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);

    try {
      await fetch(this.url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeout);
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
- Fire-and-forget: `fetch()` is called but we only wait for the request to be **sent** (not for the Lambda to complete). The 5-second timeout is for the initial TCP connection / request acceptance, not for the full render pipeline.
- `createRestClientFromEnv()` reads `SOW_RENDER_WORKER_REST_URL` with a sensible default

### Step 2: Create dispatcher module

**New file:** `webapp/src/lib/render/dispatcher.ts`

This module abstracts the SQS vs REST dispatch logic, keeping the API route clean.

```typescript
import { createSQSClientFromEnv, SQSMessage } from "@/lib/sqs/client";
import { createRestClientFromEnv, RenderWorkerMessage } from "@/lib/render/rest-client";

export type RenderWorkerMode = "sqs" | "rest";

export function getRenderWorkerMode(): RenderWorkerMode {
  const mode = process.env.SOW_RENDER_WORKER_MODE;
  if (mode === "rest") return "rest";
  return "sqs"; // default
}

export interface DispatchMessage {
  jobId: string;
  songsetId: string;
  userId: number;
}

export async function dispatchToRenderWorker(message: DispatchMessage): Promise<void> {
  const mode = getRenderWorkerMode();

  if (mode === "rest") {
    const client = createRestClientFromEnv();
    await client.invoke(message);
  } else {
    const sqsClient = createSQSClientFromEnv();
    await sqsClient.sendMessage(message as SQSMessage);
  }
}
```

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

### Step 4: Update environment variable files

**Modify:** `webapp/.env.example` — Add new variables:

```env
# Render worker dispatch mode: "sqs" (default, production) or "rest" (local dev with Docker).
# When "rest", the webapp invokes the render worker via its Lambda RIE endpoint
# instead of enqueuing to SQS. SQS env vars are not required in "rest" mode.
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
4. `RenderWorkerRestClient.invoke()` throws on abort timeout
5. `createRestClientFromEnv()` reads `SOW_RENDER_WORKER_REST_URL` from env
6. `createRestClientFromEnv()` uses default URL when env var not set

**New file:** `webapp/src/test/lib/render/dispatcher.test.ts`

Test cases:
1. `getRenderWorkerMode()` returns `"sqs"` by default (no env var)
2. `getRenderWorkerMode()` returns `"rest"` when `SOW_RENDER_WORKER_MODE=rest`
3. `getRenderWorkerMode()` returns `"sqs"` when `SOW_RENDER_WORKER_MODE=sqs`
4. `getRenderWorkerMode()` returns `"sqs"` for unknown values
5. `dispatchToRenderWorker()` calls SQS client when mode is `sqs`
6. `dispatchToRenderWorker()` calls REST client when mode is `rest`
7. `dispatchToRenderWorker()` passes correct message fields

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

## Files Changed Summary

| File | Action | Description |
|------|--------|-------------|
| `webapp/src/lib/render/rest-client.ts` | **New** | REST client for invoking Lambda RIE |
| `webapp/src/lib/render/dispatcher.ts` | **New** | Dispatcher that selects SQS vs REST based on env var |
| `webapp/src/app/api/render-jobs/route.ts` | **Modify** | Replace direct SQS call with `dispatchToRenderWorker()` |
| `webapp/.env.example` | **Modify** | Add `SOW_RENDER_WORKER_MODE` and `SOW_RENDER_WORKER_REST_URL` |
| `webapp/.env.production.example` | **Modify** | Document new env vars |
| `webapp/src/test/lib/render/rest-client.test.ts` | **New** | REST client unit tests |
| `webapp/src/test/lib/render/dispatcher.test.ts` | **New** | Dispatcher unit tests |
| `webapp/src/test/api/render-jobs/route.test.ts` | **Modify** | Update mocks to use dispatcher |
| `services/render-worker/README.md` | **Modify** | Add local dev section with REST mode |

## No Changes Required

- **`services/render-worker/`** — Lambda handler works unchanged because REST payload is wrapped in SQS `Records` format
- **`webapp/src/lib/sqs/client.ts`** — SQS client stays as-is; it's still used when mode is `sqs`
- **`webapp/src/lib/render/job-manager.ts`** — Job manager stays as-is; same DB operations regardless of dispatch mode
- **SSE progress endpoint** — Still polls DB; no changes needed
