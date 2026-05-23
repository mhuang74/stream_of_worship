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
