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
    cancelGlueJob: vi.fn(),
    redispatchGlueJob: vi.fn(),
    retryGlueProcessing: vi.fn(),
    resumeGlueJob: vi.fn(),
  };
});

import { POST as cancel } from "@/app/api/glue-jobs/[id]/cancel/route";
import { POST as redispatch } from "@/app/api/glue-jobs/[id]/redispatch/route";
import { POST as retryProcessing } from "@/app/api/glue-jobs/[id]/retry-processing/route";
import { POST as resume } from "@/app/api/glue-jobs/[id]/resume/route";
import { auth } from "@/lib/auth";
import {
  cancelGlueJob,
  redispatchGlueJob,
  resumeGlueJob,
  retryGlueProcessing,
} from "@/lib/glue/jobs";
import { GlueJobRequestError } from "@/lib/glue/errors";

const params = { params: Promise.resolve({ id: "job-1" }) };

function request(): NextRequest {
  return new Request("http://localhost/api/glue-jobs/job-1/x", {
    method: "POST",
  }) as unknown as NextRequest;
}

function status(overrides: Record<string, unknown> = {}) {
  return {
    id: "job-1",
    songsetId: "songset-1",
    fromItemId: "item-a",
    toItemId: "item-b",
    phase: "staging",
    active: true,
    takeMode: "new-take",
    createdAt: "2026-11-22T10:00:00.000Z",
    startedAt: null,
    finishedAt: null,
    elapsedSeconds: 0,
    attemptId: "attempt-2",
    providerContacted: true,
    selectedTakeId: null,
    resultingTakeId: null,
    errorCode: null,
    errorDetail: null,
    retryKind: "free",
    quota: { limit: 5, used: 1, remaining: 4, resetAt: null },
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as never);
});

describe("POST /api/glue-jobs/[id]/cancel", () => {
  it("returns 200 cancelled when the CAS wins", async () => {
    vi.mocked(cancelGlueJob).mockResolvedValue({ jobId: "job-1", status: "cancelled" });
    const response = await cancel(request(), params);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ jobId: "job-1", status: "cancelled" });
  });

  it("returns 409 GLUE_PAID_NOT_CANCELLABLE after provider contact", async () => {
    vi.mocked(cancelGlueJob).mockRejectedValue(
      new GlueJobRequestError(
        "GLUE_PAID_NOT_CANCELLABLE",
        "This paid request can't be cancelled or refunded.",
        { jobId: "job-1" }
      )
    );
    const response = await cancel(request(), params);
    expect(response.status).toBe(409);
    const body = await response.json();
    expect(body.code).toBe("GLUE_PAID_NOT_CANCELLABLE");
    expect(body.message).not.toMatch(/glue|take|descriptor/i);
    // The route never retries the mutation: exactly one attempt.
    expect(cancelGlueJob).toHaveBeenCalledTimes(1);
  });
});

describe("POST /api/glue-jobs/[id]/redispatch", () => {
  it("returns 202 queued and charged:false", async () => {
    vi.mocked(redispatchGlueJob).mockResolvedValue(status({ phase: "queued" }) as never);
    const response = await redispatch(request(), params);
    expect(response.status).toBe(202);
    expect(await response.json()).toEqual({
      jobId: "job-1",
      status: "queued",
      charged: false,
    });
  });

  it("returns 503 when dispatch fails again", async () => {
    vi.mocked(redispatchGlueJob).mockRejectedValue(
      new GlueJobRequestError("GLUE_DISPATCH_FAILED", "Could not start.", { jobId: "job-1" })
    );
    const response = await redispatch(request(), params);
    expect(response.status).toBe(503);
    expect((await response.json()).code).toBe("GLUE_DISPATCH_FAILED");
  });
});

describe("POST /api/glue-jobs/[id]/retry-processing", () => {
  it("returns 202 staging from a verified raw response", async () => {
    vi.mocked(retryGlueProcessing).mockResolvedValue(status() as never);
    const response = await retryProcessing(request(), params);
    expect(response.status).toBe(202);
    expect(await response.json()).toEqual({
      jobId: "job-1",
      status: "staging",
      charged: false,
    });
  });

  it("is idempotent for an already-ready job", async () => {
    vi.mocked(retryGlueProcessing).mockResolvedValue(
      status({ phase: "ready", active: false }) as never
    );
    const response = await retryProcessing(request(), params);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      jobId: "job-1",
      status: "ready",
      charged: false,
    });
  });

  it("refuses without raw-response metadata", async () => {
    vi.mocked(retryGlueProcessing).mockRejectedValue(
      new GlueJobRequestError("GLUE_NOT_READY", "There is no saved AI response to finish.")
    );
    const response = await retryProcessing(request(), params);
    expect(response.status).toBe(409);
    expect((await response.json()).code).toBe("GLUE_NOT_READY");
  });
});

describe("POST /api/glue-jobs/[id]/resume", () => {
  it("returns 202 with the durable phase and charged:false", async () => {
    vi.mocked(resumeGlueJob).mockResolvedValue(status({ phase: "queued" }) as never);
    const response = await resume(request(), params);
    expect(response.status).toBe(202);
    expect(await response.json()).toEqual({
      jobId: "job-1",
      status: "queued",
      charged: false,
    });
  });

  it("refuses an ambiguous provider state with GLUE_RETRY_PAID", async () => {
    vi.mocked(resumeGlueJob).mockRejectedValue(
      new GlueJobRequestError("GLUE_RETRY_PAID", "This attempt needs a new paid request.")
    );
    const response = await resume(request(), params);
    expect(response.status).toBe(409);
    expect((await response.json()).code).toBe("GLUE_RETRY_PAID");
  });
});

describe("recovery route authentication", () => {
  it("rejects every recovery route without a session", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    for (const handler of [cancel, redispatch, retryProcessing, resume]) {
      expect((await handler(request(), params)).status).toBe(401);
    }
  });
});
