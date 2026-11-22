import "server-only";

import { after } from "next/server";

import {
  createGlueSQSClientFromEnv,
  type GlueSQSClient,
  type GlueSQSMessage,
} from "@/lib/sqs/client";
import { logGlueError } from "@/lib/glue/log";

// Glue worker dispatch (spec: specs/inpaint-transition-elevenlabs-v2.md §2.3,
// §6.3). Two transports, one message shape:
//
//   { kind: "glue.generate", jobId, songsetId, userId, mode?: "staging" }
//
// `mode: "staging"` is only sent by the free retry-processing route; the worker
// reads it as STAGING RECOVERY ONLY (decode the persisted raw response and
// select a take — never upload or compose again). Ordinary generation omits
// `mode`. The queue URL is chosen by deployment mode, never by message content:
// render messages keep `SOW_SQS_QUEUE_URL`; Glue messages use
// `SOW_GLUE_SQS_QUEUE_URL`.

export type GlueWorkerMode = "sqs" | "rest";

export function getGlueWorkerMode(): GlueWorkerMode {
  return process.env.SOW_RENDER_WORKER_MODE === "rest" ? "rest" : "sqs";
}

export interface GlueDispatchMessage {
  kind: "glue.generate";
  jobId: string;
  songsetId: string;
  userId: number;
  /** Present only for free staging recovery. */
  mode?: "staging";
}

export interface GlueDispatchOptions {
  /**
   * Local REST mode returns before the worker answers, so a failure can only be
   * reported asynchronously. The caller uses this to persist `dispatch-failed`
   * and release the still-unconsumed reservation (free redispatch path).
   */
  onAsyncFailure?: (error: unknown) => Promise<void> | void;
}

const LOCAL_REST_DEFAULT_URL =
  "http://localhost:9000/2015-03-31/functions/function/invocations";

let cachedGlueSqsClient: GlueSQSClient | null = null;

/**
 * The local REST shim expects a Lambda-style event, and the one local function
 * routes on the body's `kind` exactly like the SQS path. Building the envelope
 * here (instead of reusing the render REST client, whose fixed payload predates
 * Glue) keeps render dispatch untouched while local Glue messages keep their
 * tag and staging mode.
 */
async function invokeLocalRest(message: GlueDispatchMessage): Promise<void> {
  const response = await fetch(
    process.env.SOW_RENDER_WORKER_REST_URL || LOCAL_REST_DEFAULT_URL,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        Records: [
          {
            messageId: `rest-glue-${message.jobId}`,
            body: JSON.stringify(message),
          },
        ],
      }),
    }
  );
  if (!response.ok) {
    throw new Error(
      `Glue worker REST invocation failed: ${response.status} ${response.statusText}`
    );
  }
}

/**
 * Sends one Glue job message. SQS mode awaits the send and propagates failure
 * so the caller can take the `dispatch-failed` path. Local REST mode enqueues
 * the invocation with `after()` and returns immediately: the browser always
 * gets its `202`, and a failure lands on `onAsyncFailure`.
 */
export async function dispatchGlueMessage(
  message: GlueDispatchMessage,
  options: GlueDispatchOptions = {}
): Promise<void> {
  if (getGlueWorkerMode() === "rest") {
    after(async () => {
      try {
        await invokeLocalRest(message);
      } catch (error) {
        logGlueError("glue.dispatch.failed", error, {
          glueJobId: message.jobId,
          mode: message.mode ?? "generate",
          transport: "rest",
        });
        if (options.onAsyncFailure) {
          await options.onAsyncFailure(error);
        }
      }
    });
    return;
  }

  const client = cachedGlueSqsClient ?? createGlueSQSClientFromEnv();
  cachedGlueSqsClient = client;
  const payload: GlueSQSMessage = {
    kind: "glue.generate",
    jobId: message.jobId,
    songsetId: message.songsetId,
    userId: message.userId,
    ...(message.mode === undefined ? {} : { mode: message.mode }),
  };
  await client.sendMessage(payload);
}
