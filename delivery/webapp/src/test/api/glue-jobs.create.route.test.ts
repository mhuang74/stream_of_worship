import { beforeEach, describe, expect, it, vi } from "vitest";
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
  return {
    ...actual,
    createGlueJob: vi.fn(),
    listGlueJobsForSongset: vi.fn(),
  };
});

import { GET, POST } from "@/app/api/songsets/[id]/glue-jobs/route";
import { auth } from "@/lib/auth";
import { createGlueJob, listGlueJobsForSongset } from "@/lib/glue/jobs";
import { GlueJobRequestError } from "@/lib/glue/errors";

const params = { params: Promise.resolve({ id: "songset-1" }) };

function makeRequest(
  url: string,
  body?: unknown,
  headers: Record<string, string> = {}
): NextRequest {
  const init: RequestInit = { headers: new Headers(headers) };
  if (body !== undefined) {
    init.method = "POST";
    init.body = JSON.stringify(body);
  }
  const request = new Request(url, init) as unknown as NextRequest;
  Object.defineProperty(request, "nextUrl", { value: new URL(url), writable: false });
  Object.defineProperty(request, "json", { value: async () => body, writable: false });
  return request;
}

const BODY = {
  fromItemId: "item-a",
  clientRequestId: "11111111-1111-4111-8111-111111111111",
  expectedFromUpdatedAt: "2026-11-22T10:00:00.000Z",
  expectedToUpdatedAt: "2026-11-22T10:05:00.000Z",
  takeMode: "new-take" as const,
};

const STATUS = {
  id: "job-1",
  songsetId: "songset-1",
  fromItemId: "item-a",
  toItemId: "item-b",
  phase: "queued",
  active: true,
  takeMode: "new-take",
  createdAt: "2026-11-22T10:00:00.000Z",
  startedAt: null,
  finishedAt: null,
  elapsedSeconds: 0,
  attemptId: "attempt-1",
  providerContacted: false,
  selectedTakeId: null,
  resultingTakeId: null,
  errorCode: null,
  errorDetail: null,
  retryKind: null,
  quota: { limit: 5, used: 1, remaining: 4, resetAt: null },
};

describe("POST /api/songsets/[id]/glue-jobs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as never);
  });

  it("requires a session", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const response = await POST(
      makeRequest("http://localhost/api/songsets/songset-1/glue-jobs", BODY),
      params
    );
    expect(response.status).toBe(401);
    expect(createGlueJob).not.toHaveBeenCalled();
  });

  it("rejects a client-supplied descriptor or other extra field", async () => {
    const response = await POST(
      makeRequest("http://localhost/api/songsets/songset-1/glue-jobs", {
        ...BODY,
        descriptorJson: "{}",
      }),
      params
    );
    expect(response.status).toBe(400);
    expect((await response.json()).code).toBe("GLUE_INVALID_REQUEST");
    expect(createGlueJob).not.toHaveBeenCalled();
  });

  it("rejects a malformed clientRequestId", async () => {
    const response = await POST(
      makeRequest("http://localhost/api/songsets/songset-1/glue-jobs", {
        ...BODY,
        clientRequestId: "not-a-uuid",
      }),
      params
    );
    expect(response.status).toBe(400);
  });

  it("passes the owner and the five intent fields to the service", async () => {
    vi.mocked(createGlueJob).mockResolvedValue({
      kind: "job",
      job: STATUS as never,
    });
    await POST(
      makeRequest("http://localhost/api/songsets/songset-1/glue-jobs", BODY),
      params
    );
    expect(createGlueJob).toHaveBeenCalledWith({
      songsetId: "songset-1",
      userId: 1,
      ...BODY,
    });
  });

  it("returns the free selection as 200 ready/cached", async () => {
    vi.mocked(createGlueJob).mockResolvedValue({
      kind: "ready",
      takeId: "take-1",
      cached: true,
    });
    const response = await POST(
      makeRequest("http://localhost/api/songsets/songset-1/glue-jobs", {
        ...BODY,
        takeMode: "initial",
      }),
      params
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      status: "ready",
      takeId: "take-1",
      cached: true,
    });
  });

  it("returns a paid job as 202 with its real phase and no percent", async () => {
    vi.mocked(createGlueJob).mockResolvedValue({
      kind: "job",
      job: STATUS as never,
    });
    const response = await POST(
      makeRequest("http://localhost/api/songsets/songset-1/glue-jobs", BODY),
      params
    );
    expect(response.status).toBe(202);
    const body = await response.json();
    expect(body).toEqual({ jobId: "job-1", status: "queued" });
    expect(body).not.toHaveProperty("percent");
  });

  it("maps a stale snapshot to 409 GLUE_STALE_SNAPSHOT", async () => {
    vi.mocked(createGlueJob).mockRejectedValue(
      new GlueJobRequestError("GLUE_STALE_SNAPSHOT", "changed")
    );
    const response = await POST(
      makeRequest("http://localhost/api/songsets/songset-1/glue-jobs", BODY),
      params
    );
    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({
      code: "GLUE_STALE_SNAPSHOT",
      message: "changed",
    });
  });

  it("maps quota exhaustion to 429 with the reset detail", async () => {
    vi.mocked(createGlueJob).mockRejectedValue(
      new GlueJobRequestError("GLUE_DAILY_LIMIT", "No paid AI transitions are left today.", {
        detail: "resets at 2026-11-23T09:15:00.000Z",
      })
    );
    const response = await POST(
      makeRequest("http://localhost/api/songsets/songset-1/glue-jobs", BODY),
      params
    );
    expect(response.status).toBe(429);
    const body = await response.json();
    expect(body.code).toBe("GLUE_DAILY_LIMIT");
    expect(body.detail).toContain("2026-11-23T09:15:00.000Z");
  });

  it("sanitizes an unexpected failure instead of leaking it", async () => {
    vi.mocked(createGlueJob).mockRejectedValue(
      new Error("boom at https://api.elevenlabs.io/v1/x")
    );
    const response = await POST(
      makeRequest("http://localhost/api/songsets/songset-1/glue-jobs", BODY),
      params
    );
    expect(response.status).toBe(500);
    const body = await response.json();
    expect(body.code).toBe("GLUE_PROVIDER_UNKNOWN");
    expect(JSON.stringify(body)).not.toContain("elevenlabs.io");
  });
});

describe("GET /api/songsets/[id]/glue-jobs?active=1", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as never);
  });

  it("returns the discovery payload", async () => {
    vi.mocked(listGlueJobsForSongset).mockResolvedValue({
      active: [STATUS as never],
      latestByFromItemId: { "item-a": STATUS as never },
    });
    const response = await GET(
      makeRequest("http://localhost/api/songsets/songset-1/glue-jobs?active=1"),
      params
    );
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.active).toHaveLength(1);
    expect(body.latestByFromItemId["item-a"].id).toBe("job-1");
    expect(listGlueJobsForSongset).toHaveBeenCalledWith("songset-1", 1);
  });

  it("rejects a discovery request without active=1", async () => {
    const response = await GET(
      makeRequest("http://localhost/api/songsets/songset-1/glue-jobs"),
      params
    );
    expect(response.status).toBe(400);
    expect(listGlueJobsForSongset).not.toHaveBeenCalled();
  });
});
