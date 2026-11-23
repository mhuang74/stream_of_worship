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
  return { ...actual, getGlueJobStatus: vi.fn() };
});

import { GET } from "@/app/api/glue-jobs/[id]/route";
import { auth } from "@/lib/auth";
import { getGlueJobStatus } from "@/lib/glue/jobs";
import { GlueJobRequestError } from "@/lib/glue/errors";

const params = { params: Promise.resolve({ id: "job-1" }) };

const STATUS = {
  id: "job-1",
  songsetId: "songset-1",
  fromItemId: "item-a",
  toItemId: "item-b",
  phase: "composing",
  active: true,
  takeMode: "new-take",
  createdAt: "2026-11-22T10:00:00.000Z",
  startedAt: "2026-11-22T10:00:05.000Z",
  finishedAt: null,
  elapsedSeconds: 7,
  attemptId: "attempt-1",
  providerContacted: true,
  selectedTakeId: "take-old",
  resultingTakeId: null,
  errorCode: null,
  errorDetail: null,
  retryKind: null,
  quota: { limit: 5, used: 2, remaining: 3, resetAt: null },
};

function request(): NextRequest {
  return new Request("http://localhost/api/glue-jobs/job-1") as unknown as NextRequest;
}

describe("GET /api/glue-jobs/[id]", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as never);
  });

  it("requires a session", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    expect((await GET(request(), params)).status).toBe(401);
  });

  it("returns the identical GlueJobStatus contract with no percent", async () => {
    vi.mocked(getGlueJobStatus).mockResolvedValue(STATUS as never);
    const response = await GET(request(), params);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body).toEqual(STATUS);
    expect(body).not.toHaveProperty("percent");
    expect(getGlueJobStatus).toHaveBeenCalledWith("job-1", 1);
  });

  it("maps a missing or foreign job to 404", async () => {
    vi.mocked(getGlueJobStatus).mockRejectedValue(
      new GlueJobRequestError("GLUE_NOT_FOUND", "AI transition not found.", { jobId: "job-1" })
    );
    const response = await GET(request(), params);
    expect(response.status).toBe(404);
    expect(await response.json()).toMatchObject({
      code: "GLUE_NOT_FOUND",
      jobId: "job-1",
    });
  });
});
