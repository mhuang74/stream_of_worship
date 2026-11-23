import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/db", () => ({
  db: {
    query: {
      songsets: { findFirst: vi.fn() },
      renderJobs: { findFirst: vi.fn() },
      userSettings: { findFirst: vi.fn() },
    },
    select: vi.fn(),
  },
}));
vi.mock("@/db/mutation-db", () => ({ mutationDb: { transaction: vi.fn() } }));
vi.mock("@/lib/glue/resolve", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/glue/resolve")>();
  return {
    ...actual,
    loadResolvedGlueJunctions: vi.fn(),
    loadResolvedGlueJunctionsForList: vi.fn(),
  };
});

import { db } from "@/db";
import { loadResolvedGlueJunctions } from "@/lib/glue/resolve";
import type { ResolvedGlueJunction } from "@/lib/glue/resolve";
import {
  getRenderPageData,
  getSongsetRenderMetrics,
  SongsetRenderValidationError,
} from "@/lib/db/songsets";

const TAKE_ID = "6b7f9c34-1c2b-4c4d-8a3a-1b2c3d4e5f60";

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

function junction(overrides: Partial<ResolvedGlueJunction>): ResolvedGlueJunction {
  return {
    fromItemId: "item-a",
    toItemId: "item-b",
    glueEnabled: true,
    glueBeats: 4,
    descriptorKey: "c".repeat(64),
    state: "ready",
    selectedTake: null,
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
    ...overrides,
  };
}

function arrangementRow(id: string, overrides: Record<string, unknown> = {}) {
  return {
    id,
    arrangementSteps: null,
    gapBeats: 0,
    crossfadeEnabled: 0,
    crossfadeDurationSeconds: 0,
    durationSeconds: 10,
    tempoBpm: 120,
    recordingSections: null,
    recordingBeats: null,
    recordingDeletedAt: null,
    glueEnabled: true,
    ...overrides,
  };
}

function identityRow(id: string, position: number, title: string) {
  return { id, position, title };
}

function jobFactRow(
  id: string,
  fromItemId: string,
  phase: string,
  retryKind: string | null,
  errorCode: string | null,
  createdAt: Date
) {
  return { id, fromItemId, phase, retryKind, errorCode, createdAt };
}

describe("getSongsetRenderMetrics AI-transition preflight", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue({
      id: "songset-1",
    } as any);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("rejects ordered GLUE_NOT_READY details with the latest job facts", async () => {
    const info = vi.spyOn(console, "info").mockImplementation(() => {});

    vi.mocked(db.select)
      .mockReturnValueOnce(
        selectChain([
          arrangementRow("item-a"),
          arrangementRow("item-b"),
          arrangementRow("item-c", { glueEnabled: false }),
        ]) as any
      )
      .mockReturnValueOnce(
        selectChain([
          identityRow("item-c", 2, "Song C"),
          identityRow("item-a", 0, "Song A"),
          identityRow("item-b", 1, "Song B"),
        ]) as any
      )
      .mockReturnValueOnce(
        selectChain([
          jobFactRow(
            "job-1",
            "item-a",
            "failed",
            "paid",
            "GLUE_PROVIDER_UNKNOWN",
            new Date("2026-11-22T10:00:00.000Z")
          ),
          jobFactRow(
            "job-2",
            "item-b",
            "uploading",
            null,
            null,
            new Date("2026-11-22T10:05:00.000Z")
          ),
        ]) as any
      );

    // Resolver order is deliberately shuffled: the payload must be ordered by
    // the outgoing item's Songset position.
    vi.mocked(loadResolvedGlueJunctions).mockResolvedValue([
      junction({
        fromItemId: "item-b",
        toItemId: "item-c",
        state: "uploading",
        activeJob: {
          id: "job-2",
          phase: "uploading",
          createdAt: "2026-11-22T10:05:00.000Z",
          startedAt: null,
        },
      }),
      junction({ fromItemId: "item-c", toItemId: null, glueEnabled: true, state: "disabled" }),
      junction({ fromItemId: "item-a", toItemId: "item-b", state: "failed" }),
    ]);

    const error = await getSongsetRenderMetrics("songset-1", 1).catch((caught) => caught);

    expect(error).toBeInstanceOf(SongsetRenderValidationError);
    expect(error.code).toBe("GLUE_NOT_READY");
    expect(JSON.stringify(error.glue)).toBe(
      JSON.stringify({
        code: "GLUE_NOT_READY",
        glues: [
          {
            fromItemId: "item-a",
            toItemId: "item-b",
            fromTitle: "Song A",
            toTitle: "Song B",
            reasonCode: "GLUE_PROVIDER_UNKNOWN",
            jobId: "job-1",
            jobPhase: "failed",
            retryKind: "paid",
          },
          {
            fromItemId: "item-b",
            toItemId: "item-c",
            fromTitle: "Song B",
            toTitle: "Song C",
            reasonCode: "GLUE_ACTIVE_JOB",
            jobId: "job-2",
            jobPhase: "uploading",
            retryKind: null,
          },
        ],
      })
    );

    const event = JSON.parse(info.mock.calls.at(-1)![0] as string);
    expect(event).toEqual({
      event: "glue.render.preflight.failed",
      songsetId: "songset-1",
      userId: 1,
      junctions: 2,
      reasonCodes: ["GLUE_PROVIDER_UNKNOWN", "GLUE_ACTIVE_JOB"],
    });
  });

  it("includes staged AI-transition seconds in the render duration when ready", async () => {
    vi.mocked(db.select)
      .mockReturnValueOnce(
        selectChain([arrangementRow("item-a"), arrangementRow("item-b")]) as any
      )
      .mockReturnValueOnce(
        selectChain([
          identityRow("item-a", 0, "Song A"),
          identityRow("item-b", 1, "Song B"),
        ]) as any
      )
      .mockReturnValueOnce(selectChain([]) as any);

    vi.mocked(loadResolvedGlueJunctions).mockResolvedValue([
      junction({
        fromItemId: "item-a",
        toItemId: "item-b",
        state: "ready",
        selectedTake: {
          id: TAKE_ID,
          ordinal: 1,
          seconds: 2.5,
          r2Key: "glues/take.wav",
          descriptorKey: "c".repeat(64),
          status: "ok",
          reasons: [],
        },
      }),
      junction({ fromItemId: "item-b", toItemId: null, state: "disabled" }),
    ]);

    const metrics = await getSongsetRenderMetrics("songset-1", 1);

    expect(metrics.songsetDurationSeconds).toBe(22.5);
  });
});

describe("getRenderPageData AI-transition preflight", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("surfaces the blocking preflight as page state instead of throwing", async () => {
    const info = vi.spyOn(console, "info").mockImplementation(() => {});

    vi.mocked(db.select)
      .mockReturnValueOnce(
        selectChain([
          {
            id: "songset-1",
            name: "Sunday Set",
            description: null,
            latestRenderJobId: null,
            lastFailedRenderJobId: null,
            lastCompletedRenderJobId: null,
            latestJobStatus: null,
            latestJobCompletedAt: null,
          },
        ]) as any
      )
      .mockReturnValueOnce(
        selectChain([
          {
            id: "item-a",
            position: 0,
            songTitle: "Song A",
            markedLineCount: 0,
            recordingDeletedAt: null,
            recordingDurationSeconds: 10,
            arrangementSteps: null,
            gapBeats: 0,
            crossfadeEnabled: 0,
            crossfadeDurationSeconds: 0,
            recordingSections: null,
            recordingBeats: null,
            tempoBpm: 120,
            updatedAt: new Date("2026-11-22T10:00:00.000Z"),
          },
          {
            id: "item-b",
            position: 1,
            songTitle: "Song B",
            markedLineCount: 0,
            recordingDeletedAt: null,
            recordingDurationSeconds: 10,
            arrangementSteps: null,
            gapBeats: 0,
            crossfadeEnabled: 0,
            crossfadeDurationSeconds: 0,
            recordingSections: null,
            recordingBeats: null,
            tempoBpm: 120,
            updatedAt: new Date("2026-11-22T10:01:00.000Z"),
          },
        ]) as any
      )
      // Job facts for the blocked Junction; the panel needs id/phase/retry.
      .mockReturnValueOnce(
        selectChain([
          jobFactRow(
            "job-1",
            "item-a",
            "queued",
            null,
            null,
            new Date("2026-11-22T10:00:00.000Z")
          ),
        ]) as any
      );

    vi.mocked(loadResolvedGlueJunctions).mockResolvedValue([
      junction({ fromItemId: "item-a", toItemId: "item-b", state: "queued" }),
      junction({ fromItemId: "item-b", toItemId: null, state: "disabled" }),
    ]);
    vi.mocked(db.query.userSettings.findFirst).mockResolvedValue(null);

    const data = await getRenderPageData("songset-1", 1);

    expect(data).not.toBeNull();
    expect(data!.gluePreflight).toEqual({
      code: "GLUE_NOT_READY",
      glues: [
        {
          fromItemId: "item-a",
          toItemId: "item-b",
          fromTitle: "Song A",
          toTitle: "Song B",
          reasonCode: "GLUE_ACTIVE_JOB",
          jobId: "job-1",
          jobPhase: "queued",
          retryKind: null,
        },
      ],
    });
    expect(info).toHaveBeenCalled();
    expect(JSON.parse(info.mock.calls.at(-1)![0] as string)).toEqual({
      event: "glue.render.preflight.failed",
      songsetId: "songset-1",
      userId: 1,
      junctions: 1,
      reasonCodes: ["GLUE_ACTIVE_JOB"],
    });
  });
});
