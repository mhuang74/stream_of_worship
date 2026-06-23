import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  RenderWorkerRestClient,
  createRestClientFromEnv,
} from "@/lib/render/rest-client";

describe("RenderWorkerRestClient", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it("sends POST with correct SQS-wrapped payload", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: "OK",
    });
    global.fetch = mockFetch;

    const client = new RenderWorkerRestClient({
      url: "http://localhost:9000/2015-03-31/functions/function/invocations",
    });

    await client.invoke({
      jobId: "job-123",
      songsetId: "songset-456",
      userId: 1,
    });

    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:9000/2015-03-31/functions/function/invocations",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          Records: [
            {
              messageId: "rest-job-123",
              body: JSON.stringify({
                jobId: "job-123",
                songsetId: "songset-456",
                userId: 1,
              }),
            },
          ],
        }),
      }
    );
  });

  it("uses configured URL", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: "OK",
    });
    global.fetch = mockFetch;

    const customUrl = "http://custom-host:8080/invoke";
    const client = new RenderWorkerRestClient({ url: customUrl });

    await client.invoke({
      jobId: "job-123",
      songsetId: "songset-456",
      userId: 1,
    });

    expect(mockFetch).toHaveBeenCalledWith(
      customUrl,
      expect.objectContaining({ method: "POST" })
    );
  });

  it("throws on connection refused", async () => {
    const mockFetch = vi.fn().mockRejectedValue(new Error("fetch failed"));
    global.fetch = mockFetch;

    const client = new RenderWorkerRestClient({
      url: "http://localhost:9000/2015-03-31/functions/function/invocations",
    });

    await expect(
      client.invoke({
        jobId: "job-123",
        songsetId: "songset-456",
        userId: 1,
      })
    ).rejects.toThrow("fetch failed");
  });

  it("throws on non-2xx response status", async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
    });
    global.fetch = mockFetch;

    const client = new RenderWorkerRestClient({
      url: "http://localhost:9000/2015-03-31/functions/function/invocations",
    });

    await expect(
      client.invoke({
        jobId: "job-123",
        songsetId: "songset-456",
        userId: 1,
      })
    ).rejects.toThrow(
      "Render worker REST invocation failed: 500 Internal Server Error"
    );
  });

  it("awaits the full response (no abort/timeout)", async () => {
    let resolveFetch: () => void;
    const fetchPromise = new Promise<Response>((resolve) => {
      resolveFetch = () =>
        resolve({
          ok: true,
          status: 200,
          statusText: "OK",
        } as Response);
    });

    const mockFetch = vi.fn().mockReturnValue(fetchPromise);
    global.fetch = mockFetch;

    const client = new RenderWorkerRestClient({
      url: "http://localhost:9000/2015-03-31/functions/function/invocations",
    });

    const invokePromise = client.invoke({
      jobId: "job-123",
      songsetId: "songset-456",
      userId: 1,
    });

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(mockFetch).toHaveBeenCalledTimes(1);

    resolveFetch!();
    await expect(invokePromise).resolves.toBeUndefined();
  });
});

describe("createRestClientFromEnv", () => {
  const originalEnv = process.env;

  beforeEach(() => {
    vi.resetModules();
    process.env = { ...originalEnv };
  });

  afterEach(() => {
    process.env = originalEnv;
  });

  it("reads SOW_RENDER_WORKER_REST_URL from env", () => {
    process.env.SOW_RENDER_WORKER_REST_URL =
      "http://custom-host:8080/custom-invoke";

    const client = createRestClientFromEnv();
    expect(client).toBeInstanceOf(RenderWorkerRestClient);
  });

  it("uses default URL when env var not set", () => {
    delete process.env.SOW_RENDER_WORKER_REST_URL;

    const client = createRestClientFromEnv();
    expect(client).toBeInstanceOf(RenderWorkerRestClient);
  });
});
