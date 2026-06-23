import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

vi.mock("@/lib/sqs/client", () => ({
  createSQSClientFromEnv: vi.fn(),
  SQSClient: vi.fn(),
}));

vi.mock("@/lib/render/rest-client", () => ({
  createRestClientFromEnv: vi.fn(),
  RenderWorkerRestClient: vi.fn(),
}));

import { createSQSClientFromEnv, SQSClient } from "@/lib/sqs/client";
import {
  createRestClientFromEnv,
  RenderWorkerRestClient,
} from "@/lib/render/rest-client";

const mockSqsSendMessage = vi.fn().mockResolvedValue("msg-123");
const mockRestInvoke = vi.fn().mockResolvedValue(undefined);

vi.mocked(createSQSClientFromEnv).mockReturnValue({
  sendMessage: mockSqsSendMessage,
} as unknown as SQSClient);

vi.mocked(createRestClientFromEnv).mockReturnValue({
  invoke: mockRestInvoke,
} as unknown as RenderWorkerRestClient);

describe("getRenderWorkerMode", () => {
  const originalEnv = process.env;

  beforeEach(() => {
    process.env = { ...originalEnv };
  });

  afterEach(() => {
    process.env = originalEnv;
  });

  it('returns "sqs" by default (no env var)', async () => {
    delete process.env.SOW_RENDER_WORKER_MODE;
    const { getRenderWorkerMode } = await import("@/lib/render/dispatcher");
    expect(getRenderWorkerMode()).toBe("sqs");
  });

  it('returns "rest" when SOW_RENDER_WORKER_MODE=rest', async () => {
    process.env.SOW_RENDER_WORKER_MODE = "rest";
    const { getRenderWorkerMode } = await import("@/lib/render/dispatcher");
    expect(getRenderWorkerMode()).toBe("rest");
  });

  it('returns "sqs" when SOW_RENDER_WORKER_MODE=sqs', async () => {
    process.env.SOW_RENDER_WORKER_MODE = "sqs";
    const { getRenderWorkerMode } = await import("@/lib/render/dispatcher");
    expect(getRenderWorkerMode()).toBe("sqs");
  });

  it('returns "sqs" for unknown values', async () => {
    process.env.SOW_RENDER_WORKER_MODE = "unknown";
    const { getRenderWorkerMode } = await import("@/lib/render/dispatcher");
    expect(getRenderWorkerMode()).toBe("sqs");
  });
});

describe("dispatchToRenderWorker", () => {
  const originalEnv = process.env;

  beforeEach(() => {
    vi.clearAllMocks();
    process.env = { ...originalEnv };
  });

  afterEach(() => {
    process.env = originalEnv;
  });

  it("calls SQS client when mode is sqs", async () => {
    process.env.SOW_RENDER_WORKER_MODE = "sqs";
    vi.resetModules();

    const { dispatchToRenderWorker } = await import("@/lib/render/dispatcher");

    await dispatchToRenderWorker({
      jobId: "job-123",
      songsetId: "songset-456",
      userId: 1,
    });

    expect(mockSqsSendMessage).toHaveBeenCalledWith({
      jobId: "job-123",
      songsetId: "songset-456",
      userId: 1,
    });
    expect(mockRestInvoke).not.toHaveBeenCalled();
  });

  it("calls REST client when mode is rest", async () => {
    process.env.SOW_RENDER_WORKER_MODE = "rest";
    vi.resetModules();

    const { dispatchToRenderWorker } = await import("@/lib/render/dispatcher");

    await dispatchToRenderWorker({
      jobId: "job-123",
      songsetId: "songset-456",
      userId: 1,
    });

    expect(mockRestInvoke).toHaveBeenCalledWith({
      jobId: "job-123",
      songsetId: "songset-456",
      userId: 1,
    });
    expect(mockSqsSendMessage).not.toHaveBeenCalled();
  });

  it("passes correct message fields", async () => {
    process.env.SOW_RENDER_WORKER_MODE = "sqs";
    vi.resetModules();

    const { dispatchToRenderWorker } = await import("@/lib/render/dispatcher");

    await dispatchToRenderWorker({
      jobId: "test-job-id",
      songsetId: "test-songset-id",
      userId: 42,
    });

    expect(mockSqsSendMessage).toHaveBeenCalledWith({
      jobId: "test-job-id",
      songsetId: "test-songset-id",
      userId: 42,
    });
  });

  it("reuses cached client instances across calls", async () => {
    process.env.SOW_RENDER_WORKER_MODE = "sqs";
    vi.resetModules();

    const { dispatchToRenderWorker } = await import("@/lib/render/dispatcher");

    await dispatchToRenderWorker({
      jobId: "job-1",
      songsetId: "songset-1",
      userId: 1,
    });

    await dispatchToRenderWorker({
      jobId: "job-2",
      songsetId: "songset-2",
      userId: 2,
    });

    expect(createSQSClientFromEnv).toHaveBeenCalledTimes(1);
    expect(mockSqsSendMessage).toHaveBeenCalledTimes(2);
  });
});
