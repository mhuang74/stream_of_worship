import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/db", () => ({
  db: {
    query: {
      renderJobs: { findFirst: vi.fn(), findMany: vi.fn() },
    },
    insert: vi.fn(),
    update: vi.fn(),
    select: vi.fn(),
    delete: vi.fn(),
    transaction: vi.fn(),
  },
}));
vi.mock("@/db/mutation-db", () => ({ mutationDb: { transaction: vi.fn() } }));
vi.mock("nanoid", () => ({ nanoid: vi.fn(() => "mock-job-id") }));
vi.mock("@/lib/db/songsets", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/db/songsets")>();
  return { ...actual, getSongsetRenderMetrics: vi.fn() };
});
vi.mock("@/lib/glue/resolve", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/glue/resolve")>();
  return {
    ...actual,
    loadResolvedGlueJunctions: vi.fn(),
    resolveGlueJunctions: vi.fn(),
  };
});

import { db } from "@/db";
import { mutationDb } from "@/db/mutation-db";
import { createRenderJob } from "@/lib/render/job-manager";
import {
  SongsetRenderValidationError,
  getSongsetRenderMetrics,
} from "@/lib/db/songsets";
import { resolveGlueJunctions } from "@/lib/glue/resolve";
import type { ResolvedGlueJunction } from "@/lib/glue/resolve";

const TAKE_A = "11111111-1c2b-4c4d-8a3a-1b2c3d4e5f60";
const TAKE_B = "22222222-1c2b-4c4d-8a3a-1b2c3d4e5f60";
const DESCRIPTOR_KEY = "c".repeat(64);
const SHA_A = "d".repeat(64);
const SHA_B = "e".repeat(64);
const R2_A = "glues/1/desc/take-a.wav";
const R2_B = "glues/1/desc/take-b.wav";

function selectChain(rows: unknown[]) {
  const resolved = Promise.resolve(rows);
  const chain: any = {};
  for (const method of [
    "from",
    "innerJoin",
    "leftJoin",
    "where",
    "groupBy",
    "orderBy",
    "limit",
    "offset",
    "for",
  ]) {
    chain[method] = vi.fn().mockReturnValue(chain);
  }
  chain.then = (callback: any) => resolved.then(callback);
  return chain;
}

function itemRow(id: string, position: number, overrides: Record<string, unknown> = {}) {
  return {
    id,
    position,
    title: id === "item-a" ? "Song A" : "Song B",
    updatedAt: new Date(`2026-11-22T10:0${position}:00.000Z`),
    glueEnabled: true,
    glueBeats: 4,
    glueActiveTakeId: position === 0 ? TAKE_A : null,
    arrangementSteps: null,
    contentHash: position === 0 ? "a".repeat(64) : "b".repeat(64),
    durationSeconds: 100,
    tempoBpm: 120,
    sections: null,
    beats: null,
    recordingDeletedAt: null,
    ...overrides,
  };
}

function takeRow(overrides: Record<string, unknown> = {}) {
  return {
    id: TAKE_A,
    userId: 1,
    descriptorKey: DESCRIPTOR_KEY,
    descriptorJson: "{}",
    ordinal: 1,
    seed: 1,
    status: "ready",
    r2Key: R2_A,
    seconds: 2.786,
    rawResponseR2Key: null,
    rawResponseSha256: null,
    rawResponseBytes: null,
    metricsJson: JSON.stringify({ audioSha256: SHA_A }),
    reasonsJson: "[]",
    errorCode: null,
    createdAt: new Date("2026-11-22T10:00:00.000Z"),
    updatedAt: new Date("2026-11-22T10:00:00.000Z"),
    ...overrides,
  };
}

function readyJunction(takeId: string, r2Key: string, seconds: number): ResolvedGlueJunction {
  return {
    fromItemId: "item-a",
    toItemId: "item-b",
    glueEnabled: true,
    glueBeats: 4,
    descriptorKey: DESCRIPTOR_KEY,
    state: "ready",
    selectedTake: {
      id: takeId,
      ordinal: 1,
      seconds,
      r2Key,
      descriptorKey: DESCRIPTOR_KEY,
      status: "ok",
      reasons: [],
    },
    availableTakes: [],
    activeJob: null,
    quote: null,
    quotaLimit: 5,
    quotaUsed: 0,
    quotaRemaining: 5,
    quotaResetAt: null,
    expectedFromUpdatedAt: null,
    expectedToUpdatedAt: null,
    unavailableReason: null,
  };
}

const mockMetrics = vi.mocked(getSongsetRenderMetrics);
const mockResolve = vi.mocked(resolveGlueJunctions);
const mockRenderRow = {
  id: "mock-job-id",
  songsetId: "songset-1",
  userId: 1,
  status: "queued" as const,
  phase: "preparing",
  phaseIndex: 0,
  totalPhases: 5,
  elapsedSeconds: 0,
  errorMessage: null,
  estimatedTotalSeconds: null,
  totalDurationSeconds: null,
  startedAt: null,
  template: "dark",
  resolution: "720p",
  audioEnabled: true,
  videoEnabled: true,
  fontSizePreset: "M",
  fontFamily: "noto_serif_tc",
  includeTitleCard: false,
  titleCardDurationSeconds: null,
  titleCardLines: null,
  glueTransitions: null,
  mp3R2Key: null,
  mp4R2Key: null,
  chaptersR2Key: null,
  songCount: 2,
  songsetDurationSeconds: 200,
  createdAt: new Date(),
  updatedAt: new Date(),
  completedAt: null,
};

let txInsertValues: any;
let txUpdateCalls: any[];

function installTransaction(txRows: unknown[][]) {
  let call = 0;
  const txInsert = vi.fn().mockReturnValue({
    values: vi.fn().mockImplementation((values: unknown) => {
      txInsertValues = values;
      return { returning: vi.fn().mockResolvedValue([mockRenderRow]) };
    }),
  });
  const txUpdate = vi.fn().mockImplementation((...args: unknown[]) => {
    txUpdateCalls.push(args);
    return {
      set: vi.fn().mockReturnValue({ where: vi.fn().mockResolvedValue(undefined) }),
    };
  });
  vi.mocked(mutationDb.transaction).mockImplementation((async (callback: any) => {
    const tx = {
      select: vi.fn(() => selectChain(txRows[call++] ?? [])),
      insert: txInsert,
      update: txUpdate,
    };
    return callback(tx);
  }) as any);
  return { txInsert, txUpdate };
}

describe("createRenderJob frozen AI-transition snapshot", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    txInsertValues = undefined;
    txUpdateCalls = [];
    vi.mocked(db.select).mockReturnValue(selectChain([]) as any);
    mockMetrics.mockResolvedValue({
      items: [{ id: "item-a" }, { id: "item-b" }],
      songCount: 2,
      songsetDurationSeconds: 200,
    } as any);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("writes the exact ordered snapshot JSON with frozen field names", async () => {
    installTransaction([[itemRow("item-a", 0), itemRow("item-b", 1)], [takeRow()], []]);
    mockResolve.mockReturnValue([readyJunction(TAKE_A, R2_A, 2.786)]);

    await createRenderJob(1, { songsetId: "songset-1" });

    expect(txInsertValues.glueTransitions).toBe(
      JSON.stringify([
        {
          from_item_id: "item-a",
          from_updated_at: "2026-11-22T10:00:00.000Z",
          to_item_id: "item-b",
          to_updated_at: "2026-11-22T10:01:00.000Z",
          take_id: TAKE_A,
          descriptor_key: DESCRIPTOR_KEY,
          r2_key: R2_A,
          seconds: 2.786,
          sha256: SHA_A,
        },
      ])
    );
  });

  it("writes a null snapshot when no AI transition is enabled", async () => {
    installTransaction([
      [
        itemRow("item-a", 0, { glueEnabled: false }),
        itemRow("item-b", 1, { glueEnabled: false }),
      ],
    ]);

    await createRenderJob(1, { songsetId: "songset-1" });

    expect(txInsertValues.glueTransitions).toBeNull();
  });

  it("keeps the first job's snapshot after a later take selection changes", async () => {
    installTransaction([[itemRow("item-a", 0), itemRow("item-b", 1)], [takeRow()], []]);
    mockResolve.mockReturnValue([readyJunction(TAKE_A, R2_A, 2.786)]);
    await createRenderJob(1, { songsetId: "songset-1" });
    const firstSnapshot = txInsertValues.glueTransitions;

    // A later selection: the resolver now reports take B. A new job freezes
    // take B, and the already-written job's stored value is unchanged.
    installTransaction([
      [
        itemRow("item-a", 0, { glueActiveTakeId: TAKE_B }),
        itemRow("item-b", 1),
      ],
      [takeRow({ id: TAKE_B, r2Key: R2_B, metricsJson: JSON.stringify({ audioSha256: SHA_B }) })],
      [],
    ]);
    mockResolve.mockReturnValue([readyJunction(TAKE_B, R2_B, 2.786)]);
    await createRenderJob(1, { songsetId: "songset-1" });
    const secondSnapshot = txInsertValues.glueTransitions;

    expect(JSON.parse(firstSnapshot)[0].take_id).toBe(TAKE_A);
    expect(JSON.parse(secondSnapshot)[0].take_id).toBe(TAKE_B);
    // The only writes are the insert and the Songset latest-job pointer; no
    // update ever touches render_jobs.glue_transitions.
    expect(txUpdateCalls).toHaveLength(2);
  });

  it("rejects with GLUE_NOT_READY at create stage and logs the preflight event", async () => {
    const info = vi.spyOn(console, "info").mockImplementation(() => {});
    const { txInsert } = installTransaction([
      [itemRow("item-a", 0), itemRow("item-b", 1)],
      [],
      [],
    ]);
    mockResolve.mockReturnValue([
      {
        ...readyJunction(TAKE_A, R2_A, 2.786),
        state: "failed",
        selectedTake: null,
      },
    ]);

    const error = await createRenderJob(1, { songsetId: "songset-1" }).catch(
      (caught) => caught
    );

    expect(error).toBeInstanceOf(SongsetRenderValidationError);
    expect(error.code).toBe("GLUE_NOT_READY");
    expect(error.glue.glues).toHaveLength(1);
    expect(error.glue.glues[0]).toMatchObject({
      fromItemId: "item-a",
      toItemId: "item-b",
      reasonCode: "GLUE_WORKER_FAILED",
    });
    expect(txInsert).not.toHaveBeenCalled();
    expect(JSON.parse(info.mock.calls.at(-1)![0] as string)).toMatchObject({
      event: "glue.render.preflight.failed",
      songsetId: "songset-1",
      userId: 1,
      junctions: 1,
      stage: "create",
    });
  });

  it("rejects when the take row no longer matches the selected take", async () => {
    installTransaction([
      [itemRow("item-a", 0), itemRow("item-b", 1)],
      [takeRow({ r2Key: "glues/other.wav" })],
      [],
    ]);
    mockResolve.mockReturnValue([readyJunction(TAKE_A, R2_A, 2.786)]);

    const error = await createRenderJob(1, { songsetId: "songset-1" }).catch(
      (caught) => caught
    );

    expect(error).toBeInstanceOf(SongsetRenderValidationError);
    expect(error.code).toBe("GLUE_NOT_READY");
  });
});
