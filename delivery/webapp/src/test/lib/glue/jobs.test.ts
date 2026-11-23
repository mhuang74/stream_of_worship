import { describe, it, expect, vi, beforeEach } from "vitest";

// The repository seam is replaced below; the real DB clients must never load in
// this suite (no SOW_DATABASE_URL).
vi.mock("@/db", () => ({ db: {} }));
vi.mock("@/db/mutation-db", () => ({ mutationDb: {} }));
vi.mock("@/lib/glue/dispatch", () => ({ dispatchGlueMessage: vi.fn() }));
vi.mock("@/lib/db/songsets", () => ({ selectGlueTake: vi.fn() }));

import {
  buildActiveGlueJobsPayload,
  cancelGlueJob,
  classifyCreateConstraintError,
  createGlueJob,
  createGlueJobDeps,
  evaluateGlueResume,
  evaluateQuotaClaim,
  getGlueJobStatus,
  glueElapsedSeconds,
  listGlueJobsForSongset,
  nextTakeOrdinal,
  randomGlueSeed,
  redispatchGlueJob,
  resumeGlueJob,
  retryGlueProcessing,
  toGlueJobStatus,
  type CreateGlueJobInput,
  type CreateJobAtomicInput,
  type GlueDispatch,
  type GlueJobRecord,
  type GlueJobRepository,
} from "@/lib/glue/jobs";
import { GlueJobRequestError } from "@/lib/glue/errors";
import { deriveGlueJunction, type GlueResolverItem } from "@/lib/glue/quote";
import type { ResolvedGlueJunction } from "@/lib/glue/resolve";
import type { GlueQuota } from "@/lib/glue/quota";

// These tests drive the orchestration above the transactional repository seam
// with an in-memory implementation: every invariant that needs a database
// (partial active index, row locks, CAS updates) stays in the repository, while
// the order of decisions — idempotency before free resolution, config before
// reservation, dispatch failure freeing the reservation — is asserted here.

const ISO = "2026-11-22T10:00:00.000Z";
const ISO_TO = "2026-11-22T10:05:00.000Z";
const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);
const QUOTA: GlueQuota = { limit: 5, used: 1, remaining: 4, resetAt: null };

function item(overrides: Partial<GlueResolverItem> = {}): GlueResolverItem {
  return {
    itemId: "item-a",
    position: 0,
    userId: 1,
    glueEnabled: true,
    glueBeats: 4,
    glueActiveTakeId: null,
    updatedAt: ISO,
    arrangementSteps: null,
    recording: {
      contentHash: HASH_A,
      durationSeconds: 200,
      tempoBpm: 120,
      sections: [{ label: "Verse", start: 0, end: 200 }],
      beats: [0, 1, 2, 3],
    },
    ...overrides,
  };
}

function successor(overrides: Partial<GlueResolverItem> = {}): GlueResolverItem {
  return item({
    itemId: "item-b",
    position: 1,
    updatedAt: ISO_TO,
    recording: {
      contentHash: HASH_B,
      durationSeconds: 180,
      tempoBpm: 120,
      sections: [{ label: "Chorus", start: 0, end: 180 }],
      beats: [0, 1, 2, 3],
    },
    ...overrides,
  });
}

const DESCRIPTOR_KEY = (() => {
  const derived = deriveGlueJunction(item(), successor());
  if (!derived.ok) throw new Error("test fixture must derive a descriptor");
  return derived.descriptorKey;
})();

function makeRecord(overrides: Partial<GlueJobRecord> = {}): GlueJobRecord {
  return {
    id: "job-1",
    userId: 1,
    songsetId: "songset-1",
    fromItemId: "item-a",
    toItemId: "item-b",
    clientRequestId: "11111111-1111-4111-8111-111111111111",
    takeMode: "initial",
    descriptorKey: "d".repeat(64),
    descriptorJson: "{}",
    expectedFromUpdatedAt: new Date(ISO),
    expectedToUpdatedAt: new Date(ISO_TO),
    phase: "queued",
    active: true,
    dispatchState: "pending",
    dispatchAttempt: 0,
    attemptId: "attempt-1",
    pendingTakeId: "22222222-2222-4222-8222-222222222222",
    takeOrdinal: 1,
    seed: 12345,
    paidReservationId: "33333333-3333-4333-8333-333333333333",
    paidReservationState: "reserved",
    providerContacted: false,
    providerPhase: null,
    providerStatus: null,
    retryKind: null,
    resultingTakeId: null,
    activeTakeId: null,
    rawResponseR2Key: null,
    rawResponseSha256: null,
    rawResponseBytes: null,
    errorCode: null,
    errorDetail: null,
    heartbeatAt: null,
    createdAt: new Date("2026-11-22T10:00:00Z"),
    startedAt: null,
    updatedAt: new Date("2026-11-22T10:00:00Z"),
    finishedAt: null,
    ...overrides,
  };
}

interface FakeRepo extends GlueJobRepository {
  createCalls: CreateJobAtomicInput[];
  markDispatchFailedCalls: Array<{ jobId: string; detail: string | undefined }>;
  markRetryFailures: Array<{ jobId: string; detail: string | undefined }>;
}

function createFakeRepository(seedJobs: GlueJobRecord[] = []): FakeRepo {
  const jobs = [...seedJobs];
  const createCalls: CreateJobAtomicInput[] = [];
  const markDispatchFailedCalls: Array<{ jobId: string; detail: string | undefined }> = [];
  const markRetryFailures: Array<{ jobId: string; detail: string | undefined }> = [];

  const repo: FakeRepo = {
    createCalls,
    markDispatchFailedCalls,
    markRetryFailures,
    findJobByClientRequestId: vi.fn(async (userId: number, clientRequestId: string) => {
      return (
        jobs.find((job) => job.userId === userId && job.clientRequestId === clientRequestId) ??
        null
      );
    }),
    findJob: vi.fn(async (jobId: string, userId: number) => {
      return jobs.find((job) => job.id === jobId && job.userId === userId) ?? null;
    }),
    listJobsForSongset: vi.fn(async (songsetId: string, userId: number) => {
      return jobs.filter((job) => job.songsetId === songsetId && job.userId === userId);
    }),
    selectNewestReadyTakeId: vi.fn(async () => null),
    createJobAtomically: vi.fn(async (input: CreateJobAtomicInput) => {
      createCalls.push(input);
      const record = makeRecord({
        id: input.jobId,
        userId: input.userId,
        songsetId: input.songsetId,
        fromItemId: input.fromItemId,
        toItemId: input.toItemId,
        clientRequestId: input.clientRequestId,
        takeMode: input.takeMode,
        descriptorKey: input.descriptorKey,
        descriptorJson: input.descriptorJson,
        expectedFromUpdatedAt: input.expectedFromUpdatedAt,
        expectedToUpdatedAt: input.expectedToUpdatedAt,
        attemptId: input.attemptId,
        pendingTakeId: input.pendingTakeId,
        seed: input.seed,
        createdAt: input.now,
        updatedAt: input.now,
      });
      jobs.push(record);
      return { kind: "created" as const, job: record };
    }),
    markDispatchFailed: vi.fn(async (jobId: string, _userId: number, detail: string | undefined) => {
      markDispatchFailedCalls.push({ jobId, detail });
      const index = jobs.findIndex((job) => job.id === jobId);
      if (index < 0) return null;
      jobs[index] = {
        ...jobs[index],
        phase: "dispatch-failed",
        active: false,
        dispatchState: "failed",
        paidReservationState: "released",
        finishedAt: new Date(),
      };
      return jobs[index];
    }),
    cancelJob: vi.fn(async () => ({ kind: "not-found" as const })),
    prepareRedispatch: vi.fn(async (jobId: string) => {
      const index = jobs.findIndex((job) => job.id === jobId);
      if (index < 0) return { kind: "not-found" as const };
      jobs[index] = {
        ...jobs[index],
        phase: "queued",
        active: true,
        dispatchState: "pending",
        finishedAt: null,
      };
      return { kind: "prepared" as const, job: jobs[index] };
    }),
    prepareRetryProcessing: vi.fn(async () => ({ kind: "not-found" as const })),
    prepareResume: vi.fn(async () => ({ kind: "not-found" as const })),
    markRetryDispatchFailed: vi.fn(
      async (jobId: string, _userId: number, detail: string | undefined) => {
        markRetryFailures.push({ jobId, detail });
      }
    ),
  };
  return repo;
}

function buildDeps(
  overrides: {
    repository?: FakeRepo;
    items?: GlueResolverItem[];
    junctions?: ResolvedGlueJunction[];
    configured?: boolean;
    dispatch?: GlueDispatch;
    quota?: GlueQuota;
  } = {}
) {
  const repository = overrides.repository ?? createFakeRepository();
  const items = overrides.items ?? [item(), successor()];
  const dispatch = overrides.dispatch ?? vi.fn(async () => undefined);
  let counter = 0;
  const deps = createGlueJobDeps({
    repository,
    loadItems: vi.fn(async () => new Map([["songset-1", items]])),
    loadJunctions: vi.fn(async () => overrides.junctions ?? []),
    selectTake: vi.fn(async () => null),
    isConfigured: vi.fn(() => overrides.configured ?? true),
    dispatch,
    readQuota: vi.fn(async () => overrides.quota ?? QUOTA),
    now: () => new Date("2026-11-22T10:10:00Z"),
    randomId: () => `00000000-0000-4000-8000-00000000000${counter++}`,
    randomSeed: () => 777,
  });
  return { deps, repository, dispatch };
}

function input(overrides: Partial<CreateGlueJobInput> = {}): CreateGlueJobInput {
  return {
    songsetId: "songset-1",
    userId: 1,
    fromItemId: "item-a",
    clientRequestId: "11111111-1111-4111-8111-111111111111",
    expectedFromUpdatedAt: ISO,
    expectedToUpdatedAt: ISO_TO,
    takeMode: "initial" as const,
    ...overrides,
  };
}

async function expectGlueError(promise: Promise<unknown>): Promise<GlueJobRequestError> {
  try {
    await promise;
  } catch (error) {
    expect(error).toBeInstanceOf(GlueJobRequestError);
    return error as GlueJobRequestError;
  }
  throw new Error("expected a GlueJobRequestError");
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("eligible take ordinal and seed", () => {
  it("allocates max(takes, jobs) + 1", () => {
    expect(nextTakeOrdinal(null, null)).toBe(1);
    expect(nextTakeOrdinal(2, null)).toBe(3);
    expect(nextTakeOrdinal(null, 4)).toBe(5);
    expect(nextTakeOrdinal(3, 3)).toBe(4);
  });

  it("generates a 32-bit seed", () => {
    for (let i = 0; i < 20; i += 1) {
      const seed = randomGlueSeed();
      expect(Number.isInteger(seed)).toBe(true);
      expect(seed).toBeGreaterThanOrEqual(0);
      expect(seed).toBeLessThanOrEqual(0xffffffff);
    }
  });
});

describe("quota claim", () => {
  const now = new Date("2026-11-22T12:00:00Z");

  it("allows the fifth consumed attempt and rejects the sixth with a reset time", () => {
    const four = [1, 2, 3, 4].map((hours) => new Date(now.getTime() - hours * 3600_000));
    expect(evaluateQuotaClaim(four, now)).toEqual({ allowed: true });

    const five = [1, 2, 3, 4, 5].map((hours) => new Date(now.getTime() - hours * 3600_000));
    const claim = evaluateQuotaClaim(five, now);
    expect(claim.allowed).toBe(false);
    if (!claim.allowed) {
      // Oldest consumed attempt + 24 h.
      expect(claim.resetAt).toBe(new Date(five[4].getTime() + 24 * 3600_000).toISOString());
    }
  });

  it("ignores attempts older than the rolling window", () => {
    const stale = [25, 26, 27, 28, 29].map(
      (hours) => new Date(now.getTime() - hours * 3600_000)
    );
    expect(evaluateQuotaClaim(stale, now)).toEqual({ allowed: true });
  });

  it("allows an exempt account past a full window", () => {
    const five = [1, 2, 3, 4, 5].map((hours) => new Date(now.getTime() - hours * 3600_000));
    expect(evaluateQuotaClaim(five, now, true)).toEqual({ allowed: true });
    expect(evaluateQuotaClaim(five, now).allowed).toBe(false);
  });
});

describe("constraint classification", () => {
  it("recognizes the active-job partial index by name", () => {
    expect(
      classifyCreateConstraintError(
        new Error('duplicate key value violates unique constraint "uq_glue_jobs_active_per_user"')
      )
    ).toBe("active-job");
  });

  it("recognizes the client-request unique index by name", () => {
    expect(
      classifyCreateConstraintError({
        code: "23505",
        constraint: "glue_jobs_user_id_client_request_id_unique",
      })
    ).toBe("duplicate-request");
  });

  it("returns null for unrelated failures", () => {
    expect(classifyCreateConstraintError(new Error("connection reset"))).toBeNull();
  });
});

describe("createGlueJob", () => {
  it("returns the original job for a duplicate clientRequestId without any side effect", async () => {
    const existing = makeRecord({ phase: "composing", active: true });
    const { deps, repository } = buildDeps({ repository: createFakeRepository([existing]) });
    const outcome = await createGlueJob(input(), deps);
    expect(outcome.kind).toBe("job");
    if (outcome.kind === "job") expect(outcome.job.id).toBe("job-1");
    expect(repository.createCalls).toHaveLength(0);
    expect(deps.dispatch).not.toHaveBeenCalled();
    expect(deps.selectTake).not.toHaveBeenCalled();
    expect(deps.isConfigured).not.toHaveBeenCalled();
  });

  it("redispatches the SAME job on a dispatch-failed replay", async () => {
    const failed = makeRecord({ phase: "dispatch-failed", active: false });
    const { deps, repository } = buildDeps({ repository: createFakeRepository([failed]) });
    const outcome = await createGlueJob(input(), deps);
    expect(outcome.kind).toBe("job");
    expect(repository.createCalls).toHaveLength(0);
    expect(repository.prepareRedispatch).toHaveBeenCalledTimes(1);
    expect(deps.dispatch).toHaveBeenCalledWith(
      { kind: "glue.generate", jobId: "job-1", songsetId: "songset-1", userId: 1 },
      expect.anything()
    );
  });

  it("resolves a current effective selection for free with no job or quota row", async () => {
    const junction = {
      fromItemId: "item-a",
      toItemId: "item-b",
      state: "ready",
      selectedTake: { id: "take-existing" },
    } as unknown as ResolvedGlueJunction;
    const { deps, repository, dispatch } = buildDeps({ junctions: [junction] });
    const outcome = await createGlueJob(input(), deps);
    expect(outcome).toEqual({ kind: "ready", takeId: "take-existing", cached: true });
    expect(repository.createCalls).toHaveLength(0);
    expect(dispatch).not.toHaveBeenCalled();
  });

  it("selects the newest ready take for a new initial request without a job", async () => {
    const { deps, repository } = buildDeps();
    vi.mocked(repository.selectNewestReadyTakeId).mockResolvedValue("take-1");
    const selectTake = vi.fn(async () => ({ item: {}, glue: {} }) as never);
    const withSelect = { ...deps, selectTake };
    const outcome = await createGlueJob(input(), withSelect);
    expect(outcome).toEqual({ kind: "ready", takeId: "take-1", cached: true });
    expect(selectTake).toHaveBeenCalledWith("item-a", "songset-1", 1, "take-1");
    expect(repository.createCalls).toHaveLength(0);
  });

  it("rejects a stale snapshot before any reservation", async () => {
    const { deps, repository } = buildDeps();
    const error = await expectGlueError(
      createGlueJob(input({ expectedFromUpdatedAt: "2026-01-01T00:00:00.000Z" }), deps)
    );
    expect(error.code).toBe("GLUE_STALE_SNAPSHOT");
    expect(error.status).toBe(409);
    expect(repository.createCalls).toHaveLength(0);
    expect(deps.isConfigured).not.toHaveBeenCalled();
  });

  it("rejects an unconfigured server before resering quota", async () => {
    const { deps, repository } = buildDeps({ configured: false });
    const error = await expectGlueError(createGlueJob(input({ takeMode: "new-take" }), deps));
    expect(error.code).toBe("GLUE_NOT_CONFIGURED");
    expect(error.status).toBe(503);
    expect(repository.createCalls).toHaveLength(0);
  });

  it("rejects a disabled Junction", async () => {
    const { deps } = buildDeps({ items: [item({ glueEnabled: false }), successor()] });
    const error = await expectGlueError(createGlueJob(input({ takeMode: "new-take" }), deps));
    expect(error.code).toBe("GLUE_NOT_READY");
  });

  it("rejects a side shorter than one second with 422", async () => {
    const { deps } = buildDeps({
      items: [
        item({
          recording: {
            contentHash: HASH_A,
            durationSeconds: 0.5,
            tempoBpm: 120,
            sections: [{ label: "Verse", start: 0, end: 0.5 }],
            beats: [0, 1],
          },
        }),
        successor(),
      ],
    });
    const error = await expectGlueError(createGlueJob(input({ takeMode: "new-take" }), deps));
    expect(error.code).toBe("GLUE_EXCERPT_TOO_SHORT");
    expect(error.status).toBe(422);
  });

  it("rejects a transition longer than 120 seconds with 422", async () => {
    const { deps } = buildDeps({
      items: [
        item({ glueBeats: 16 }),
        successor({
          recording: {
            contentHash: HASH_B,
            durationSeconds: 180,
            tempoBpm: 7,
            sections: [{ label: "Chorus", start: 0, end: 180 }],
            beats: [0, 1, 2, 3],
          },
        }),
      ],
    });
    const error = await expectGlueError(createGlueJob(input({ takeMode: "new-take" }), deps));
    expect(error.code).toBe("GLUE_DURATION_TOO_LONG");
    expect(error.status).toBe(422);
  });

  it("claims the quota slot and dispatches exactly one paid job", async () => {
    const { deps, repository } = buildDeps();
    const outcome = await createGlueJob(input({ takeMode: "new-take" }), deps);
    expect(outcome.kind).toBe("job");
    expect(repository.createCalls).toHaveLength(1);
    const created = repository.createCalls[0];
    expect(created.takeMode).toBe("new-take");
    expect(created.fromItemId).toBe("item-a");
    expect(created.toItemId).toBe("item-b");
    expect(created.seed).toBe(777);
    expect(deps.dispatch).toHaveBeenCalledTimes(1);
    expect(deps.dispatch).toHaveBeenCalledWith(
      {
        kind: "glue.generate",
        jobId: expect.any(String),
        songsetId: "songset-1",
        userId: 1,
      },
      expect.anything()
    );
    if (outcome.kind === "job") {
      expect(outcome.job).not.toHaveProperty("percent");
      expect(outcome.job.elapsedSeconds).toBe(0);
    }
  });

  it("maps a sixth consumed attempt to 429 with the reset time in detail", async () => {
    const repository = createFakeRepository();
    vi.mocked(repository.createJobAtomically).mockResolvedValue({
      kind: "quota",
      resetAt: "2026-11-23T09:15:00.000Z",
    });
    const { deps } = buildDeps({ repository });
    const error = await expectGlueError(createGlueJob(input({ takeMode: "new-take" }), deps));
    expect(error.code).toBe("GLUE_DAILY_LIMIT");
    expect(error.status).toBe(429);
    expect(error.detail).toContain("2026-11-23T09:15:00.000Z");
    expect(deps.dispatch).not.toHaveBeenCalled();
  });

  it("maps the active partial index to 409 naming the active Junction and job", async () => {
    const repository = createFakeRepository();
    vi.mocked(repository.createJobAtomically).mockResolvedValue({
      kind: "active-job",
      jobId: "job-active",
      fromItemId: "item-z",
    });
    const { deps } = buildDeps({ repository });
    const error = await expectGlueError(createGlueJob(input({ takeMode: "new-take" }), deps));
    expect(error.code).toBe("GLUE_ACTIVE_JOB");
    expect(error.status).toBe(409);
    expect(error.jobId).toBe("job-active");
    expect(error.detail).toContain("item-z");
    expect(deps.dispatch).not.toHaveBeenCalled();
  });

  it("marks dispatch-failed and frees the reservation when dispatch throws", async () => {
    const { deps, repository } = buildDeps({
      dispatch: vi.fn(async () => {
        throw new Error("sqs unavailable");
      }),
    });
    const error = await expectGlueError(createGlueJob(input({ takeMode: "new-take" }), deps));
    expect(error.code).toBe("GLUE_DISPATCH_FAILED");
    expect(error.status).toBe(503);
    expect(repository.markDispatchFailedCalls).toHaveLength(1);
    expect(repository.markDispatchFailedCalls[0].detail).toBe("sqs unavailable");
  });

  it("replays a dispatch-failed request for free instead of creating a second job", async () => {
    let shouldFail = true;
    const dispatch = vi.fn(async () => {
      if (shouldFail) throw new Error("worker down");
    });
    const { deps, repository } = buildDeps({ dispatch });

    const first = await expectGlueError(createGlueJob(input({ takeMode: "new-take" }), deps));
    expect(first.code).toBe("GLUE_DISPATCH_FAILED");
    expect(repository.createCalls).toHaveLength(1);

    const afterFailure = await repository.findJobByClientRequestId(
      1,
      "11111111-1111-4111-8111-111111111111"
    );
    expect(afterFailure?.phase).toBe("dispatch-failed");

    shouldFail = false;
    const replay = await createGlueJob(input({ takeMode: "new-take" }), deps);
    expect(replay.kind).toBe("job");
    expect(repository.createCalls).toHaveLength(1);
    expect(repository.prepareRedispatch).toHaveBeenCalledTimes(1);
    expect(dispatch).toHaveBeenCalledTimes(2);
  });

  it("returns the original job when the insert races the same clientRequestId", async () => {
    const repository = createFakeRepository();
    const duplicate = makeRecord({ phase: "queued" });
    vi.mocked(repository.createJobAtomically).mockResolvedValue({
      kind: "duplicate",
      job: duplicate,
    });
    const { deps } = buildDeps({ repository });
    const outcome = await createGlueJob(input({ takeMode: "new-take" }), deps);
    expect(outcome.kind).toBe("job");
    if (outcome.kind === "job") expect(outcome.job.id).toBe("job-1");
    expect(deps.dispatch).not.toHaveBeenCalled();
  });

  it("maps a mid-transaction input change to 409", async () => {
    const repository = createFakeRepository();
    vi.mocked(repository.createJobAtomically).mockResolvedValue({ kind: "inputs-changed" });
    const { deps } = buildDeps({ repository });
    const error = await expectGlueError(createGlueJob(input({ takeMode: "new-take" }), deps));
    expect(error.code).toBe("GLUE_INPUTS_CHANGED");
    expect(error.status).toBe(409);
  });
});

describe("status serialization", () => {
  it("emits the frozen field set with integer elapsed seconds and no percent", () => {
    const now = new Date("2026-11-22T10:00:12Z");
    const status = toGlueJobStatus(
      makeRecord({
        phase: "composing",
        startedAt: new Date("2026-11-22T10:00:00Z"),
        activeTakeId: "take-selected",
        resultingTakeId: "take-result",
        retryKind: "paid",
        errorCode: "GLUE_PROVIDER_UNKNOWN",
        errorDetail: "The provider took too long.",
      }),
      QUOTA,
      now
    );
    expect(Object.keys(status).sort()).toEqual(
      [
        "id",
        "songsetId",
        "fromItemId",
        "toItemId",
        "phase",
        "active",
        "takeMode",
        "createdAt",
        "startedAt",
        "finishedAt",
        "elapsedSeconds",
        "attemptId",
        "providerContacted",
        "selectedTakeId",
        "resultingTakeId",
        "errorCode",
        "errorDetail",
        "retryKind",
        "quota",
      ].sort()
    );
    expect(status.elapsedSeconds).toBe(12);
    expect(status.selectedTakeId).toBe("take-selected");
    expect(status.resultingTakeId).toBe("take-result");
    expect(status.retryKind).toBe("paid");
    expect(status.quota).toEqual(QUOTA);
  });

  it("uses the finish time once terminal and never goes negative", () => {
    expect(
      glueElapsedSeconds(
        {
          createdAt: new Date("2026-11-22T10:00:00Z"),
          startedAt: new Date("2026-11-22T10:00:00Z"),
          finishedAt: new Date("2026-11-22T10:00:05Z"),
        },
        new Date("2026-11-22T11:00:00Z")
      )
    ).toBe(5);
    expect(
      glueElapsedSeconds(
        {
          createdAt: new Date("2026-11-22T10:00:00Z"),
          startedAt: null,
          finishedAt: null,
        },
        new Date("2026-11-22T09:00:00Z")
      )
    ).toBe(0);
  });

  it("builds discovery payloads with the latest job per Junction", () => {
    const payload = buildActiveGlueJobsPayload(
      [
        makeRecord({ id: "job-new", fromItemId: "item-a", phase: "composing" }),
        makeRecord({ id: "job-old", fromItemId: "item-a", phase: "ready", active: false }),
        makeRecord({ id: "job-b", fromItemId: "item-b", phase: "queued" }),
      ],
      QUOTA,
      new Date("2026-11-22T10:00:00Z")
    );
    expect(payload.active.map((job) => job.id)).toEqual(["job-new", "job-b"]);
    expect(payload.latestByFromItemId["item-a"].id).toBe("job-new");
    expect(payload.latestByFromItemId["item-b"].id).toBe("job-b");
  });

  it("reads a single status and lists by Songset", async () => {
    const { deps } = buildDeps({ repository: createFakeRepository([makeRecord()]) });
    expect((await getGlueJobStatus("job-1", 1, deps)).id).toBe("job-1");
    const payload = await listGlueJobsForSongset("songset-1", 1, deps);
    expect(payload.active).toHaveLength(1);
    await expectGlueError(getGlueJobStatus("missing", 1, deps));
  });
});

describe("cancel / redispatch / recovery orchestration", () => {
  it("cancels when the CAS wins", async () => {
    const repository = createFakeRepository();
    vi.mocked(repository.cancelJob).mockResolvedValue({
      kind: "cancelled",
      job: makeRecord({ phase: "cancelled", active: false }),
    });
    const { deps } = buildDeps({ repository });
    await expect(cancelGlueJob({ jobId: "job-1", userId: 1 }, deps)).resolves.toEqual({
      jobId: "job-1",
      status: "cancelled",
    });
  });

  it("loses the cancel-vs-start race to provider contact with 409 and no retry of the mutation", async () => {
    const repository = createFakeRepository();
    vi.mocked(repository.cancelJob).mockResolvedValue({ kind: "provider-contacted" });
    const { deps } = buildDeps({ repository });
    const error = await expectGlueError(cancelGlueJob({ jobId: "job-1", userId: 1 }, deps));
    expect(error.code).toBe("GLUE_PAID_NOT_CANCELLABLE");
    expect(error.status).toBe(409);
    expect(repository.cancelJob).toHaveBeenCalledTimes(1);
  });

  it("treats an already-cancelled job as idempotent", async () => {
    const repository = createFakeRepository();
    vi.mocked(repository.cancelJob).mockResolvedValue({ kind: "already-cancelled" });
    const { deps } = buildDeps({ repository });
    await expect(cancelGlueJob({ jobId: "job-1", userId: 1 }, deps)).resolves.toEqual({
      jobId: "job-1",
      status: "cancelled",
    });
  });

  it("refuses to cancel a terminal job", async () => {
    const repository = createFakeRepository();
    vi.mocked(repository.cancelJob).mockResolvedValue({ kind: "terminal", phase: "ready" });
    const { deps } = buildDeps({ repository });
    const error = await expectGlueError(cancelGlueJob({ jobId: "job-1", userId: 1 }, deps));
    expect(error.code).toBe("GLUE_NOT_READY");
  });

  it("redispatches a dispatch-failed job and re-marks it when dispatch fails again", async () => {
    const repository = createFakeRepository([
      makeRecord({ phase: "dispatch-failed", active: false }),
    ]);
    const { deps } = buildDeps({
      repository,
      dispatch: vi.fn(async () => {
        throw new Error("still down");
      }),
    });
    const error = await expectGlueError(redispatchGlueJob({ jobId: "job-1", userId: 1 }, deps));
    expect(error.code).toBe("GLUE_DISPATCH_FAILED");
    expect(repository.markDispatchFailedCalls).toHaveLength(1);
  });

  it("refuses redispatch once the provider was contacted", async () => {
    const repository = createFakeRepository();
    vi.mocked(repository.prepareRedispatch).mockResolvedValue({ kind: "provider-contacted" });
    const { deps } = buildDeps({ repository });
    const error = await expectGlueError(redispatchGlueJob({ jobId: "job-1", userId: 1 }, deps));
    expect(error.code).toBe("GLUE_RETRY_PAID");
    expect(deps.dispatch).not.toHaveBeenCalled();
  });

  it("refuses retry-processing without verified raw-response metadata", async () => {
    const repository = createFakeRepository([makeRecord({ phase: "failed", active: false })]);
    vi.mocked(repository.prepareRetryProcessing).mockResolvedValue({ kind: "no-raw-response" });
    const { deps } = buildDeps({ repository });
    const error = await expectGlueError(retryGlueProcessing({ jobId: "job-1", userId: 1 }, deps));
    expect(error.code).toBe("GLUE_NOT_READY");
    expect(error.status).toBe(409);
    expect(deps.dispatch).not.toHaveBeenCalled();
  });

  it("is idempotent when retry-processing sees an already-ready job", async () => {
    const repository = createFakeRepository([makeRecord({ phase: "ready", active: false })]);
    vi.mocked(repository.prepareRetryProcessing).mockResolvedValue({
      kind: "already-ready",
      job: makeRecord({ phase: "ready", active: false }),
    });
    const { deps } = buildDeps({ repository });
    const status = await retryGlueProcessing({ jobId: "job-1", userId: 1 }, deps);
    expect(status.phase).toBe("ready");
    expect(deps.dispatch).not.toHaveBeenCalled();
  });

  it("dispatches staging recovery with mode:staging and no new reservation", async () => {
    const repository = createFakeRepository();
    vi.mocked(repository.prepareRetryProcessing).mockResolvedValue({
      kind: "prepared",
      job: makeRecord({ phase: "staging", active: true }),
    });
    const { deps } = buildDeps({ repository });
    const status = await retryGlueProcessing({ jobId: "job-1", userId: 1 }, deps);
    expect(status.phase).toBe("staging");
    expect(deps.dispatch).toHaveBeenCalledWith(
      {
        kind: "glue.generate",
        jobId: "job-1",
        songsetId: "songset-1",
        userId: 1,
        mode: "staging",
      },
      expect.anything()
    );
  });

  it("keeps raw-response metadata when staging dispatch fails", async () => {
    const repository = createFakeRepository();
    vi.mocked(repository.prepareRetryProcessing).mockResolvedValue({
      kind: "prepared",
      job: makeRecord({
        phase: "staging",
        active: true,
        rawResponseR2Key: "glues/1/jobs/1/response.pcm",
        rawResponseSha256: "e".repeat(64),
        rawResponseBytes: 1024,
      }),
    });
    const { deps } = buildDeps({
      repository,
      dispatch: vi.fn(async () => {
        throw new Error("down");
      }),
    });
    const error = await expectGlueError(retryGlueProcessing({ jobId: "job-1", userId: 1 }, deps));
    expect(error.code).toBe("GLUE_DISPATCH_FAILED");
    expect(repository.markRetryFailures).toHaveLength(1);
  });

  it("refuses resume while the provider phase may be in flight", async () => {
    const repository = createFakeRepository([
      makeRecord({ phase: "composing", active: true, retryKind: "free", providerContacted: true }),
    ]);
    const { deps } = buildDeps({ repository });
    const error = await expectGlueError(resumeGlueJob({ jobId: "job-1", userId: 1 }, deps));
    expect(error.code).toBe("GLUE_RETRY_PAID");
    expect(repository.prepareResume).not.toHaveBeenCalled();
  });

  it("requires a free retry classification for resume", async () => {
    const repository = createFakeRepository([
      makeRecord({ phase: "failed", active: false, providerContacted: true, retryKind: "paid" }),
    ]);
    vi.mocked(repository.prepareResume).mockResolvedValue({ kind: "requires-paid-retry" });
    const { deps } = buildDeps({ repository });
    const error = await expectGlueError(resumeGlueJob({ jobId: "job-1", userId: 1 }, deps));
    expect(error.code).toBe("GLUE_RETRY_PAID");
  });

  it("revalidates the snapshot before resuming a pre-contact failure", async () => {
    const repository = createFakeRepository([
      makeRecord({
        phase: "failed",
        active: false,
        retryKind: "free",
        providerContacted: false,
        expectedFromUpdatedAt: new Date("2026-01-01T00:00:00Z"),
      }),
    ]);
    const { deps } = buildDeps({ repository });
    const error = await expectGlueError(resumeGlueJob({ jobId: "job-1", userId: 1 }, deps));
    expect(error.code).toBe("GLUE_INPUTS_CHANGED");
    expect(repository.prepareResume).not.toHaveBeenCalled();
  });

  it("resumes a free pre-contact failure into queued", async () => {
    const repository = createFakeRepository([
      makeRecord({
        phase: "failed",
        active: false,
        retryKind: "free",
        providerContacted: false,
        descriptorKey: DESCRIPTOR_KEY,
      }),
    ]);
    vi.mocked(repository.prepareResume).mockResolvedValue({
      kind: "prepared",
      job: makeRecord({ phase: "queued", active: true }),
      phase: "queued",
    });
    const { deps } = buildDeps({ repository });
    const status = await resumeGlueJob({ jobId: "job-1", userId: 1 }, deps);
    expect(status.phase).toBe("queued");
    expect(deps.dispatch).toHaveBeenCalledTimes(1);
  });

  it("resumes a durable raw response into staging recovery", async () => {
    const repository = createFakeRepository([
      makeRecord({
        phase: "failed",
        active: false,
        retryKind: "free",
        providerContacted: true,
        descriptorKey: DESCRIPTOR_KEY,
        rawResponseR2Key: "glues/1/jobs/1/response.pcm",
        rawResponseSha256: "e".repeat(64),
        rawResponseBytes: 2048,
      }),
    ]);
    vi.mocked(repository.prepareResume).mockResolvedValue({
      kind: "prepared",
      job: makeRecord({ phase: "staging", active: true }),
      phase: "staging",
    });
    const { deps } = buildDeps({ repository });
    const status = await resumeGlueJob({ jobId: "job-1", userId: 1 }, deps);
    expect(status.phase).toBe("staging");
    expect(deps.dispatch).toHaveBeenCalledTimes(1);
    expect(vi.mocked(deps.dispatch).mock.calls[0][0]).toMatchObject({
      kind: "glue.generate",
      jobId: "job-1",
    });
  });

  it("resumes a definitely-unsent post-contact free failure into generation", async () => {
    // #317: an upload/compose call refused on the Lambda deadline before send,
    // or exhausted ProviderNotSent retries, persists retry_kind="free" with
    // provider_contacted=true and no raw response. It must remain resumable.
    const repository = createFakeRepository([
      makeRecord({
        phase: "failed",
        active: false,
        retryKind: "free",
        providerContacted: true,
        descriptorKey: DESCRIPTOR_KEY,
      }),
    ]);
    vi.mocked(repository.prepareResume).mockResolvedValue({
      kind: "prepared",
      job: makeRecord({ phase: "queued", active: true }),
      phase: "queued",
    });
    const { deps } = buildDeps({ repository });
    const status = await resumeGlueJob({ jobId: "job-1", userId: 1 }, deps);
    expect(status.phase).toBe("queued");
    expect(status.active).toBe(true);
    expect(repository.prepareResume).toHaveBeenCalledTimes(1);
    expect(deps.dispatch).toHaveBeenCalledTimes(1);
  });

  it("revalidates the snapshot before resuming a post-contact free failure", async () => {
    const repository = createFakeRepository([
      makeRecord({
        phase: "failed",
        active: false,
        retryKind: "free",
        providerContacted: true,
        expectedFromUpdatedAt: new Date("2026-01-01T00:00:00Z"),
      }),
    ]);
    const { deps } = buildDeps({ repository });
    const error = await expectGlueError(resumeGlueJob({ jobId: "job-1", userId: 1 }, deps));
    expect(error.code).toBe("GLUE_INPUTS_CHANGED");
    expect(repository.prepareResume).not.toHaveBeenCalled();
  });
});

describe("resume eligibility", () => {
  const base = {
    phase: "failed",
    rawResponseR2Key: null,
    rawResponseSha256: null,
    rawResponseBytes: null,
  };

  it("allows a free post-contact failure without a raw response", () => {
    expect(evaluateGlueResume({ ...base, retryKind: "free" })).toEqual({
      kind: "resumable",
      phase: "queued",
    });
  });

  it("routes a free failure with a durable raw response to staging", () => {
    expect(
      evaluateGlueResume({
        ...base,
        retryKind: "free",
        rawResponseR2Key: "glues/1/response.pcm",
        rawResponseSha256: "e".repeat(64),
        rawResponseBytes: 2048,
      })
    ).toEqual({ kind: "resumable", phase: "staging" });
  });

  it("refuses paid and unclassified attempts even with a raw response", () => {
    for (const retryKind of ["paid", "none", null] as const) {
      expect(
        evaluateGlueResume({
          ...base,
          retryKind,
          rawResponseR2Key: "glues/1/response.pcm",
          rawResponseSha256: "e".repeat(64),
          rawResponseBytes: 2048,
        })
      ).toEqual({ kind: "requires-paid-retry" });
    }
  });

  it("refuses a job that is not in a failed terminal phase", () => {
    expect(evaluateGlueResume({ ...base, phase: "composing", retryKind: "free" })).toEqual({
      kind: "not-resumable",
      phase: "composing",
    });
  });
});
