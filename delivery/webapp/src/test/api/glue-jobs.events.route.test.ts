import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import type * as GlueJobsModule from "@/lib/glue/jobs";

vi.mock("@/db", () => ({ db: {} }));
vi.mock("@/db/mutation-db", () => ({ mutationDb: {} }));
vi.mock("@/lib/glue/dispatch", () => ({ dispatchGlueMessage: vi.fn() }));
vi.mock("@/lib/db/songsets", () => ({ selectGlueTake: vi.fn() }));

vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: vi.fn() } },
}));

vi.mock("@/lib/glue/jobs", async (importOriginal) => {
  const actual = await importOriginal<typeof GlueJobsModule>();
  return { ...actual, glueJobStreamSnapshot: vi.fn() };
});

import { GET } from "@/app/api/glue-jobs/[id]/events/route";
import { auth } from "@/lib/auth";
import { glueJobStreamSnapshot, type GlueJobStatus } from "@/lib/glue/jobs";

const params = { params: Promise.resolve({ id: "job-1" }) };
const session = { user: { id: 1 } } as never;

function request(signal?: AbortSignal): NextRequest {
  return new Request("http://localhost/api/glue-jobs/job-1/events", {
    signal,
  }) as unknown as NextRequest;
}

function status(phase: string, elapsedSeconds = 0): GlueJobStatus {
  return {
    id: "job-1",
    songsetId: "songset-1",
    fromItemId: "item-a",
    toItemId: "item-b",
    descriptorKey: "descriptor-1",
    phase,
    active: phase !== "ready",
    takeMode: "new-take",
    createdAt: "2026-11-22T10:00:00.000Z",
    startedAt: null,
    finishedAt: null,
    elapsedSeconds,
    attemptId: "attempt-1",
    providerContacted: true,
    selectedTakeId: null,
    resultingTakeId: null,
    errorCode: null,
    errorDetail: null,
    retryKind: null,
    quota: { limit: 5, used: 1, remaining: 4, resetAt: null },
  };
}

async function readSnapshot(
  reader: ReadableStreamDefaultReader<Uint8Array>
): Promise<{ done: boolean; value?: Record<string, unknown> }> {
  const chunk = await reader.read();
  if (chunk.done) return { done: true };
  const text = new TextDecoder().decode(chunk.value);
  expect(text.startsWith("data: ")).toBe(true);
  expect(text.endsWith("\n\n")).toBe(true);
  return { done: false, value: JSON.parse(text.slice("data: ".length)) as Record<string, unknown> };
}

describe("GET /api/glue-jobs/[id]/events", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useRealTimers();
    vi.mocked(auth.api.getSession).mockResolvedValue(session);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("requires a session", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    expect((await GET(request(), params)).status).toBe(401);
  });

  it("returns 404 for an unknown or foreign job before streaming", async () => {
    vi.mocked(glueJobStreamSnapshot).mockResolvedValue(null);
    const response = await GET(request(), params);
    expect(response.status).toBe(404);
    expect((await response.json()).code).toBe("GLUE_NOT_FOUND");
  });

  it("sets text/event-stream and closes immediately on a terminal phase", async () => {
    vi.mocked(glueJobStreamSnapshot).mockResolvedValue(status("ready", 8));
    const response = await GET(request(), params);
    expect(response.headers.get("Content-Type")).toContain("text/event-stream");
    expect(response.headers.get("Cache-Control")).toContain("no-cache");
    expect(response.headers.get("X-Accel-Buffering")).toBe("no");

    const reader = response.body!.getReader();
    const first = await readSnapshot(reader);
    expect(first.done).toBe(false);
    expect(first.value?.phase).toBe("ready");
    expect(first.value).not.toHaveProperty("percent");
    // Terminal: the stream ends after the terminal snapshot.
    expect((await reader.read()).done).toBe(true);
  });

  it("emits one snapshot per second and closes on the terminal phase", async () => {
    vi.useFakeTimers();
    vi.mocked(glueJobStreamSnapshot)
      .mockResolvedValueOnce(status("queued", 0))
      .mockResolvedValueOnce(status("composing", 1))
      .mockResolvedValueOnce(status("ready", 2));

    const response = await GET(request(), params);
    const reader = response.body!.getReader();

    const first = await readSnapshot(reader);
    expect(first.value?.phase).toBe("queued");

    await vi.advanceTimersByTimeAsync(1000);
    const second = await readSnapshot(reader);
    expect(second.value?.phase).toBe("composing");

    await vi.advanceTimersByTimeAsync(1000);
    const third = await readSnapshot(reader);
    expect(third.value?.phase).toBe("ready");

    expect((await reader.read()).done).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("ends at 50 seconds from handler entry while the job stays active past 60 seconds", async () => {
    vi.useFakeTimers();
    vi.mocked(auth.api.getSession).mockImplementationOnce(
      () => new Promise((resolve) => setTimeout(() => resolve(session), 2_000))
    );
    vi.mocked(glueJobStreamSnapshot).mockResolvedValue(status("composing"));

    const response = GET(request(), params);
    await vi.advanceTimersByTimeAsync(2_000);
    const reader = (await response).body!.getReader();

    for (let second = 2; second < 50; second += 1) {
      expect((await readSnapshot(reader)).value?.phase).toBe("composing");
      if (second < 49) await vi.advanceTimersByTimeAsync(1_000);
    }
    const onRead = vi.fn();
    void reader.read().then(onRead);
    await vi.advanceTimersByTimeAsync(999);
    expect(onRead).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    expect(onRead).toHaveBeenCalledWith({ done: true, value: undefined });

    await vi.advanceTimersByTimeAsync(11_000);
    expect(glueJobStreamSnapshot).toHaveBeenCalledTimes(48);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("closes while a poll is stalled and discards the late result", async () => {
    vi.useFakeTimers();
    const late = Promise.withResolvers<GlueJobStatus>();
    const clientRequest = request();
    const removeAbortListener = vi.spyOn(clientRequest.signal, "removeEventListener");
    vi.mocked(glueJobStreamSnapshot)
      .mockResolvedValueOnce(status("composing"))
      .mockReturnValueOnce(late.promise);

    const response = await GET(clientRequest, params);
    const reader = response.body!.getReader();
    await readSnapshot(reader);
    const onRead = vi.fn();
    void reader.read().then(onRead);
    await vi.advanceTimersByTimeAsync(50_000);
    expect(glueJobStreamSnapshot).toHaveBeenCalledTimes(2);
    expect(onRead).toHaveBeenCalledWith({ done: true, value: undefined });
    expect(removeAbortListener).toHaveBeenCalledWith("abort", expect.any(Function));
    expect(vi.getTimerCount()).toBe(0);

    late.resolve(status("ready"));
    await vi.advanceTimersByTimeAsync(1_000);
    expect((await reader.read()).done).toBe(true);
    expect(glueJobStreamSnapshot).toHaveBeenCalledTimes(2);
  });

  it("ends the request on budget when the initial read stalls", async () => {
    vi.useFakeTimers();
    const late = Promise.withResolvers<GlueJobStatus>();
    vi.mocked(glueJobStreamSnapshot).mockReturnValueOnce(late.promise);

    const onResponse = vi.fn();
    const response = GET(request(), params);
    void response.then(onResponse);
    await vi.advanceTimersByTimeAsync(50_000);
    expect(onResponse).toHaveBeenCalledOnce();
    const expired = await response;
    expect(expired.headers.get("Content-Type")).toContain("text/event-stream");
    expect(await expired.text()).toBe("");
    late.resolve(status("ready"));
    await vi.advanceTimersByTimeAsync(1_000);
    expect(glueJobStreamSnapshot).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("stops when the client disconnects", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const clientRequest = request(controller.signal);
    const removeAbortListener = vi.spyOn(clientRequest.signal, "removeEventListener");
    vi.mocked(glueJobStreamSnapshot).mockResolvedValue(status("queued"));

    const response = await GET(clientRequest, params);
    const reader = response.body!.getReader();
    await readSnapshot(reader);

    controller.abort();
    expect((await reader.read()).done).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
    expect(removeAbortListener).toHaveBeenCalledWith("abort", expect.any(Function));
    await vi.advanceTimersByTimeAsync(50_000);
    expect(glueJobStreamSnapshot).toHaveBeenCalledOnce();
  });

  it("stops polling when the runtime cancels the response body", async () => {
    vi.useFakeTimers();
    const clientRequest = request();
    const removeAbortListener = vi.spyOn(clientRequest.signal, "removeEventListener");
    vi.mocked(glueJobStreamSnapshot).mockResolvedValue(status("queued"));

    const response = await GET(clientRequest, params);
    await response.body!.cancel();

    expect(vi.getTimerCount()).toBe(0);
    expect(removeAbortListener).toHaveBeenCalledWith("abort", expect.any(Function));
    await vi.advanceTimersByTimeAsync(50_000);
    expect(glueJobStreamSnapshot).toHaveBeenCalledOnce();
  });
});
