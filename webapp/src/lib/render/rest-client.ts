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
