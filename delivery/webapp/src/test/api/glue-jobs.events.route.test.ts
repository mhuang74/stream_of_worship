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
import { glueJobStreamSnapshot } from "@/lib/glue/jobs";

const params = { params: Promise.resolve({ id: "job-1" }) };

function request(signal?: AbortSignal): NextRequest {
  return new Request("http://localhost/api/glue-jobs/job-1/events", {
    signal,
  }) as unknown as NextRequest;
}

function status(phase: string, elapsedSeconds = 0) {
  return {
    id: "job-1",
    songsetId: "songset-1",
    fromItemId: "item-a",
    toItemId: "item-b",
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
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as never);
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
    vi.mocked(glueJobStreamSnapshot).mockResolvedValue(status("ready", 8) as never);
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
      .mockResolvedValueOnce(status("queued", 0) as never)
      .mockResolvedValueOnce(status("composing", 1) as never)
      .mockResolvedValueOnce(status("ready", 2) as never);

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
  });

  it("stops when the client disconnects", async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    vi.mocked(glueJobStreamSnapshot).mockResolvedValue(status("queued", 0) as never);

    const response = await GET(request(controller.signal), params);
    const reader = response.body!.getReader();
    await readSnapshot(reader);

    controller.abort();
    await vi.advanceTimersByTimeAsync(1000);
    expect((await reader.read()).done).toBe(true);
  });
});
