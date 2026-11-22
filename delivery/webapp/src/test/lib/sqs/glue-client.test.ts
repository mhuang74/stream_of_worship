import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Assert the wire body and queue URL without touching AWS: the SDK classes are
// replaced, and SendMessageCommand keeps its input for inspection.
const sentCommands = vi.hoisted(() => [] as Array<{ QueueUrl: string; MessageBody: string }>);
const sendMock = vi.hoisted(() =>
  vi.fn(async (): Promise<{ MessageId?: string }> => ({ MessageId: "message-1" }))
);

vi.mock("@aws-sdk/client-sqs", () => ({
  SQSClient: class {
    async send(command: unknown): Promise<unknown> {
      return sendMock(command);
    }
  },
  SendMessageCommand: class {
    input: { QueueUrl: string; MessageBody: string };
    constructor(input: { QueueUrl: string; MessageBody: string }) {
      this.input = input;
      sentCommands.push(input);
    }
  },
}));

import {
  GlueSQSClient,
  createGlueSQSClientFromEnv,
  createSQSClientFromEnv,
} from "@/lib/sqs/client";

describe("Glue SQS client", () => {
  beforeEach(() => {
    sentCommands.length = 0;
    sendMock.mockClear();
    vi.unstubAllEnvs();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("requires SOW_GLUE_SQS_QUEUE_URL and region", () => {
    vi.stubEnv("SOW_AWS_REGION", "us-east-1");
    vi.stubEnv("SOW_GLUE_SQS_QUEUE_URL", "");
    expect(() => createGlueSQSClientFromEnv()).toThrow(
      "SOW_GLUE_SQS_QUEUE_URL environment variable is required for Glue SQS client"
    );

    vi.stubEnv("SOW_AWS_REGION", "");
    vi.stubEnv("SOW_GLUE_SQS_QUEUE_URL", "https://sqs.example/glue");
    expect(() => createGlueSQSClientFromEnv()).toThrow(
      "SOW_AWS_REGION environment variable is required for Glue SQS client"
    );
  });

  it("uses the Glue queue URL, never the render queue URL", async () => {
    vi.stubEnv("SOW_AWS_REGION", "us-east-1");
    vi.stubEnv("SOW_GLUE_SQS_QUEUE_URL", "https://sqs.example/glue");
    vi.stubEnv("SOW_SQS_QUEUE_URL", "https://sqs.example/render");
    vi.stubEnv("SOW_AWS_ACCESS_KEY_ID", "key");
    vi.stubEnv("SOW_AWS_SECRET_ACCESS_KEY", "secret");

    const client = createGlueSQSClientFromEnv();
    await client.sendMessage({
      kind: "glue.generate",
      jobId: "job-1",
      songsetId: "songset-1",
      userId: 7,
    });

    expect(sentCommands).toHaveLength(1);
    expect(sentCommands[0].QueueUrl).toBe("https://sqs.example/glue");
    expect(JSON.parse(sentCommands[0].MessageBody)).toEqual({
      kind: "glue.generate",
      jobId: "job-1",
      songsetId: "songset-1",
      userId: 7,
    });
  });

  it("adds mode only for staging recovery", async () => {
    const client = new GlueSQSClient({
      region: "us-east-1",
      queueUrl: "https://sqs.example/glue",
    });
    await client.sendMessage({
      kind: "glue.generate",
      jobId: "job-1",
      songsetId: "songset-1",
      userId: 7,
      mode: "staging",
    });
    expect(JSON.parse(sentCommands[0].MessageBody)).toMatchObject({
      kind: "glue.generate",
      mode: "staging",
    });
  });

  it("keeps the render message untagged", async () => {
    vi.stubEnv("SOW_AWS_REGION", "us-east-1");
    vi.stubEnv("SOW_SQS_QUEUE_URL", "https://sqs.example/render");
    const render = createSQSClientFromEnv();
    await render.sendMessage({
      jobId: "render-1",
      songsetId: "songset-1",
      userId: 7,
    });
    expect(sentCommands[0].QueueUrl).toBe("https://sqs.example/render");
    expect(JSON.parse(sentCommands[0].MessageBody)).toEqual({
      jobId: "render-1",
      songsetId: "songset-1",
      userId: 7,
    });
  });

  it("throws when SQS returns no MessageId", async () => {
    sendMock.mockResolvedValueOnce({ MessageId: undefined });
    const client = new GlueSQSClient({
      region: "us-east-1",
      queueUrl: "https://sqs.example/glue",
    });
    await expect(
      client.sendMessage({
        kind: "glue.generate",
        jobId: "job-1",
        songsetId: "songset-1",
        userId: 7,
      })
    ).rejects.toThrow("Failed to send Glue SQS message");
  });
});
