import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// `after()` only exists inside a Next request scope; capture the enqueued
// callback so local REST mode can be driven deterministically.
const { afterCallbacks, sendGlueMessage, createGlueClient } = vi.hoisted(() => ({
  afterCallbacks: [] as Array<() => Promise<void>>,
  sendGlueMessage: vi.fn(async (_message: unknown) => "message-id"),
  createGlueClient: vi.fn(),
}));

vi.mock("next/server", () => ({
  after: (callback: () => Promise<void>) => {
    afterCallbacks.push(callback);
  },
}));

vi.mock("@/lib/sqs/client", () => ({
  createGlueSQSClientFromEnv: () => {
    createGlueClient();
    return { sendMessage: sendGlueMessage };
  },
}));

import { dispatchGlueMessage, getGlueWorkerMode } from "@/lib/glue/dispatch";

const MESSAGE = {
  kind: "glue.generate" as const,
  jobId: "job-1",
  songsetId: "songset-1",
  userId: 7,
};

async function runAfterCallbacks(): Promise<void> {
  while (afterCallbacks.length > 0) {
    const callback = afterCallbacks.shift();
    if (callback) await callback();
  }
}

describe("Glue dispatch", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    afterCallbacks.length = 0;
    vi.unstubAllEnvs();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("defaults to SQS and awaits a kind-tagged send", async () => {
    vi.stubEnv("SOW_RENDER_WORKER_MODE", "");
    expect(getGlueWorkerMode()).toBe("sqs");

    await dispatchGlueMessage(MESSAGE);

    expect(sendGlueMessage).toHaveBeenCalledWith({
      kind: "glue.generate",
      jobId: "job-1",
      songsetId: "songset-1",
      userId: 7,
    });
    // SQS has no asynchronous failure callback.
    expect(afterCallbacks).toHaveLength(0);
  });

  it("propagates an SQS send failure so the caller can mark dispatch-failed", async () => {
    vi.stubEnv("SOW_RENDER_WORKER_MODE", "sqs");
    sendGlueMessage.mockRejectedValueOnce(new Error("sqs down"));
    await expect(dispatchGlueMessage(MESSAGE)).rejects.toThrow("sqs down");
  });

  it("carries mode:staging only for staging recovery", async () => {
    vi.stubEnv("SOW_RENDER_WORKER_MODE", "sqs");
    await dispatchGlueMessage({ ...MESSAGE, mode: "staging" });
    expect(sendGlueMessage).toHaveBeenCalledWith({
      kind: "glue.generate",
      jobId: "job-1",
      songsetId: "songset-1",
      userId: 7,
      mode: "staging",
    });
  });

  it("returns before the worker answers in local REST mode", async () => {
    vi.stubEnv("SOW_RENDER_WORKER_MODE", "rest");
    vi.stubEnv("SOW_RENDER_WORKER_REST_URL", "http://worker.test/invoke");
    const fetchMock = vi.fn(async () => ({ ok: true, status: 200, statusText: "OK" }));
    vi.stubGlobal("fetch", fetchMock);

    await dispatchGlueMessage(MESSAGE);

    expect(getGlueWorkerMode()).toBe("rest");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(afterCallbacks).toHaveLength(1);
    expect(sendGlueMessage).not.toHaveBeenCalled();

    await runAfterCallbacks();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      { body: string },
    ];
    expect(url).toBe("http://worker.test/invoke");
    const envelope = JSON.parse(init.body) as {
      Records: Array<{ messageId: string; body: string }>;
    };
    expect(envelope.Records).toHaveLength(1);
    expect(JSON.parse(envelope.Records[0].body)).toEqual({
      kind: "glue.generate",
      jobId: "job-1",
      songsetId: "songset-1",
      userId: 7,
    });
  });

  it("reports a local REST failure asynchronously and releases the job", async () => {
    vi.stubEnv("SOW_RENDER_WORKER_MODE", "rest");
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
    }));
    vi.stubGlobal("fetch", fetchMock);
    const onAsyncFailure = vi.fn(async () => undefined);

    await dispatchGlueMessage(MESSAGE, { onAsyncFailure });
    await runAfterCallbacks();

    expect(onAsyncFailure).toHaveBeenCalledTimes(1);
    expect((onAsyncFailure.mock.calls[0][0] as Error).message).toContain("500");
  });
});
