import { describe, it, expect, beforeEach, vi } from "vitest";

const mockSend = vi.fn();

vi.mock("@aws-sdk/client-sqs", () => {
  return {
    SQSClient: class MockSQSClient {
      send = mockSend;
      constructor() {}
    },
    SendMessageCommand: class MockSendMessageCommand {
      QueueUrl: string;
      MessageBody: string;
      constructor(input: { QueueUrl: string; MessageBody: string }) {
        this.QueueUrl = input.QueueUrl;
        this.MessageBody = input.MessageBody;
      }
    },
  };
});

import { SQSClient, createSQSClientFromEnv } from "@/lib/sqs/client";

describe("SQSClient", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("constructor", () => {
    it("creates client with explicit credentials", () => {
      const client = new SQSClient({
        region: "us-east-1",
        queueUrl: "https://sqs.us-east-1.amazonaws.com/123456789/render-jobs",
        accessKeyId: "AKIAIOSFODNN7EXAMPLE",
        secretAccessKey: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
      });
      expect(client).toBeInstanceOf(SQSClient);
    });

    it("creates client without explicit credentials (IAM role)", () => {
      const client = new SQSClient({
        region: "us-east-1",
        queueUrl: "https://sqs.us-east-1.amazonaws.com/123456789/render-jobs",
      });
      expect(client).toBeInstanceOf(SQSClient);
    });

    it("creates client with custom endpoint", () => {
      const client = new SQSClient({
        region: "us-east-1",
        queueUrl: "http://sqs.us-west-2.localhost.localstack.cloud:4566/000000000000/sow-render-jobs",
        endpoint: "http://localhost:4566",
      });
      expect(client).toBeInstanceOf(SQSClient);
    });

    it("creates client without endpoint (production AWS)", () => {
      const client = new SQSClient({
        region: "us-east-1",
        queueUrl: "https://sqs.us-east-1.amazonaws.com/123456789/render-jobs",
      });
      expect(client).toBeInstanceOf(SQSClient);
    });
  });

  describe("sendMessage", () => {
    it("sends message with correct body", async () => {
      mockSend.mockResolvedValue({ MessageId: "msg-123" });

      const client = new SQSClient({
        region: "us-east-1",
        queueUrl: "https://sqs.us-east-1.amazonaws.com/123456789/render-jobs",
        accessKeyId: "AKIAIOSFODNN7EXAMPLE",
        secretAccessKey: "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
      });

      const messageId = await client.sendMessage({
        jobId: "job-1",
        songsetId: "songset-1",
        userId: 42,
      });

      expect(messageId).toBe("msg-123");
      expect(mockSend).toHaveBeenCalledTimes(1);

      const commandInput = mockSend.mock.calls[0][0];
      expect(commandInput.QueueUrl).toBe(
        "https://sqs.us-east-1.amazonaws.com/123456789/render-jobs"
      );
      expect(JSON.parse(commandInput.MessageBody)).toEqual({
        jobId: "job-1",
        songsetId: "songset-1",
        userId: 42,
      });
    });

    it("constructs JSON body with all fields", async () => {
      mockSend.mockResolvedValue({ MessageId: "msg-456" });

      const client = new SQSClient({
        region: "ap-southeast-1",
        queueUrl: "https://sqs.ap-southeast-1.amazonaws.com/999/render",
      });

      await client.sendMessage({
        jobId: "abc-def",
        songsetId: "ss-xyz",
        userId: 100,
      });

      const commandInput = mockSend.mock.calls[0][0];
      const body = JSON.parse(commandInput.MessageBody);
      expect(body).toEqual({
        jobId: "abc-def",
        songsetId: "ss-xyz",
        userId: 100,
      });
    });

    it("throws when SQS returns no MessageId", async () => {
      mockSend.mockResolvedValue({});

      const client = new SQSClient({
        region: "us-east-1",
        queueUrl: "https://sqs.us-east-1.amazonaws.com/123456789/render-jobs",
      });

      await expect(
        client.sendMessage({
          jobId: "job-1",
          songsetId: "songset-1",
          userId: 1,
        })
      ).rejects.toThrow("Failed to send SQS message: no MessageId returned");
    });

    it("propagates SQS service errors", async () => {
      mockSend.mockRejectedValue(new Error("Access Denied"));

      const client = new SQSClient({
        region: "us-east-1",
        queueUrl: "https://sqs.us-east-1.amazonaws.com/123456789/render-jobs",
      });

      await expect(
        client.sendMessage({
          jobId: "job-1",
          songsetId: "songset-1",
          userId: 1,
        })
      ).rejects.toThrow("Access Denied");
    });

    it("uses the configured queue URL", async () => {
      mockSend.mockResolvedValue({ MessageId: "msg-789" });

      const customQueueUrl =
        "https://sqs.eu-west-1.amazonaws.com/111222333/my-custom-queue";
      const client = new SQSClient({
        region: "eu-west-1",
        queueUrl: customQueueUrl,
      });

      await client.sendMessage({
        jobId: "job-2",
        songsetId: "songset-2",
        userId: 5,
      });

      const commandInput = mockSend.mock.calls[0][0];
      expect(commandInput.QueueUrl).toBe(customQueueUrl);
    });
  });
});

describe("createSQSClientFromEnv", () => {
  const originalEnv = process.env;

  beforeEach(() => {
    vi.clearAllMocks();
    process.env = { ...originalEnv };
  });

  afterEach(() => {
    process.env = originalEnv;
  });

  it("creates client from environment variables", () => {
    process.env.SOW_AWS_REGION = "us-east-1";
    process.env.SOW_SQS_QUEUE_URL =
      "https://sqs.us-east-1.amazonaws.com/123456789/render-jobs";
    process.env.SOW_AWS_ACCESS_KEY_ID = "AKIAIOSFODNN7EXAMPLE";
    process.env.SOW_AWS_SECRET_ACCESS_KEY =
      "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY";

    const client = createSQSClientFromEnv();
    expect(client).toBeInstanceOf(SQSClient);
  });

  it("creates client without explicit credentials when not provided", () => {
    process.env.SOW_AWS_REGION = "us-east-1";
    process.env.SOW_SQS_QUEUE_URL =
      "https://sqs.us-east-1.amazonaws.com/123456789/render-jobs";
    delete process.env.SOW_AWS_ACCESS_KEY_ID;
    delete process.env.SOW_AWS_SECRET_ACCESS_KEY;

    const client = createSQSClientFromEnv();
    expect(client).toBeInstanceOf(SQSClient);
  });

  it("throws when SOW_AWS_REGION is missing", () => {
    delete process.env.SOW_AWS_REGION;
    process.env.SOW_SQS_QUEUE_URL =
      "https://sqs.us-east-1.amazonaws.com/123456789/render-jobs";

    expect(() => createSQSClientFromEnv()).toThrow(
      "SOW_AWS_REGION environment variable is required for SQS client"
    );
  });

  it("throws when SOW_SQS_QUEUE_URL is missing", () => {
    process.env.SOW_AWS_REGION = "us-east-1";
    delete process.env.SOW_SQS_QUEUE_URL;

    expect(() => createSQSClientFromEnv()).toThrow(
      "SOW_SQS_QUEUE_URL environment variable is required for SQS client"
    );
  });

  it("throws when both required env vars are missing", () => {
    delete process.env.SOW_AWS_REGION;
    delete process.env.SOW_SQS_QUEUE_URL;

    expect(() => createSQSClientFromEnv()).toThrow(
      "SOW_AWS_REGION environment variable is required for SQS client"
    );
  });

  it("reads SOW_SQS_ENDPOINT_URL from environment", () => {
    process.env.SOW_AWS_REGION = "us-east-1";
    process.env.SOW_SQS_QUEUE_URL =
      "http://sqs.us-west-2.localhost.localstack.cloud:4566/000000000000/sow-render-jobs";
    process.env.SOW_SQS_ENDPOINT_URL = "http://localhost:4566";

    const client = createSQSClientFromEnv();
    expect(client).toBeInstanceOf(SQSClient);
  });

  it("creates client without endpoint when SOW_SQS_ENDPOINT_URL is not set", () => {
    process.env.SOW_AWS_REGION = "us-east-1";
    process.env.SOW_SQS_QUEUE_URL =
      "https://sqs.us-east-1.amazonaws.com/123456789/render-jobs";
    delete process.env.SOW_SQS_ENDPOINT_URL;

    const client = createSQSClientFromEnv();
    expect(client).toBeInstanceOf(SQSClient);
  });
});
