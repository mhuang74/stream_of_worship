import { describe, it, expect, vi } from "vitest";

// The resolver module stays pure; its quote helper imports the DB client for
// the endpoint path only. Mock it so pure tests never need a database URL.
vi.mock("@/db", () => ({ db: {} }));

import { buildGlueDescriptor } from "@/lib/glue/descriptor";
import { buildGlueExcerptKey } from "@/lib/glue/excerpt-key";
import type { GlueResolverItem } from "@/lib/glue/quote";
import {
  resolveGlueJunctions,
  type GlueJobRow,
  type GlueTakeRow,
} from "@/lib/glue/resolve";

const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);
const ISO = "2026-11-22T10:00:00.000Z";
const QUOTA = { limit: 5 as const, used: 0, remaining: 5, resetAt: null };

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
    updatedAt: "2026-11-22T10:05:00.000Z",
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

function descriptor(beats: 2 | 4 | 8 | 16 = 4, toTempoBpm = 120) {
  return buildGlueDescriptor({
    beats,
    toTempoBpm,
    from: { contentHash: HASH_A, steps: null },
    to: { contentHash: HASH_B, steps: null },
  });
}

function take(overrides: Partial<GlueTakeRow> = {}): GlueTakeRow {
  const built = descriptor();
  return {
    id: "take-1",
    userId: 1,
    descriptorKey: built.key,
    descriptorJson: built.canonicalJson,
    ordinal: 1,
    status: "ready",
    r2Key: "glues/1/take-1.wav",
    seconds: 3,
    audioSha256: "c".repeat(64),
    metricsJson: "{}",
    reasonsJson: "[]",
    createdAt: ISO,
    ...overrides,
  };
}

function job(overrides: Partial<GlueJobRow> = {}): GlueJobRow {
  const built = descriptor();
  return {
    id: "job-1",
    userId: 1,
    fromItemId: "item-a",
    toItemId: "item-b",
    takeMode: "initial",
    descriptorKey: built.key,
    phase: "composing",
    active: true,
    attemptId: "attempt-1",
    providerContacted: true,
    resultingTakeId: null,
    activeTakeId: null,
    errorCode: null,
    errorDetail: null,
    retryKind: null,
    createdAt: ISO,
    startedAt: ISO,
    finishedAt: null,
    heartbeatAt: ISO,
    ...overrides,
  };
}

function resolve(
  items: GlueResolverItem[],
  options: {
    takes?: GlueTakeRow[];
    jobs?: GlueJobRow[];
    quota?: typeof QUOTA;
    cached?: string[];
    rejected?: string[];
  } = {}
) {
  return resolveGlueJunctions({
    items,
    takes: options.takes ?? [],
    jobs: options.jobs ?? [],
    quota: options.quota ?? QUOTA,
    cachedExcerptKeys: new Set(options.cached ?? []),
    rejectedExcerptKeys: new Set(options.rejected ?? []),
  });
}

describe("resolveGlueJunctions", () => {
  it("returns one junction per item in position order and marks the last disabled/no-next", () => {
    const result = resolve([item(), successor()]);
    expect(result).toHaveLength(2);
    expect(result[0].fromItemId).toBe("item-a");
    expect(result[1].fromItemId).toBe("item-b");
    expect(result[1]).toMatchObject({
      toItemId: null,
      state: "disabled",
      unavailableReason: "no-next",
      descriptorKey: null,
      selectedTake: null,
      activeJob: null,
      quote: null,
    });
    expect(result[0]).toMatchObject({
      toItemId: "item-b",
      expectedFromUpdatedAt: ISO,
      expectedToUpdatedAt: "2026-11-22T10:05:00.000Z",
    });
  });

  it("reports disabled without a quote when AI transition is off", () => {
    const [junction] = resolve([item({ glueEnabled: false }), successor()]);
    expect(junction.state).toBe("disabled");
    expect(junction.unavailableReason).toBeNull();
    expect(junction.quote).toBeNull();
    expect(junction.descriptorKey).not.toBeNull();
  });

  it("reports needs-generation with exact quote credits when no take is selected", () => {
    const [junction] = resolve([item(), successor()]);
    expect(junction.state).toBe("needs-generation");
    expect(junction.selectedTake).toBeNull();
    expect(junction.activeJob).toBeNull();
    expect(junction.quote).toEqual({
      uncachedSides: [
        { side: "A", seconds: 12, credits: 180 },
        { side: "B", seconds: 12, credits: 180 },
      ],
      generationSeconds: 3,
      uploadCredits: 360,
      generationCredits: 45,
      estimatedNewCredits: 405,
    });
    expect(junction.descriptorKey).toBe(descriptor().key);
  });

  it("never marks ready from glue_active_take_id alone", () => {
    const [junction] = resolve([
      item({ glueActiveTakeId: "missing-take" }),
      successor(),
    ]);
    expect(junction.state).toBe("needs-generation");
    expect(junction.selectedTake).toBeNull();
  });

  it("marks ready only for an effective owner take whose stored descriptor matches", () => {
    const [junction] = resolve(
      [item({ glueActiveTakeId: "take-1" }), successor()],
      { takes: [take()] }
    );
    expect(junction.state).toBe("ready");
    expect(junction.selectedTake).toEqual({
      id: "take-1",
      ordinal: 1,
      seconds: 3,
      beats: 4,
      r2Key: "glues/1/take-1.wav",
      descriptorKey: descriptor().key,
      status: "ok",
      reasons: [],
    });
    expect(junction.quote?.estimatedNewCredits).toBe(405);
  });

  it("exposes measured check status and reasons", () => {
    const [junction] = resolve(
      [item({ glueActiveTakeId: "take-1" }), successor()],
      {
        takes: [
          take({
            metricsJson: JSON.stringify({ status: "check", audioSha256: "d".repeat(64) }),
            reasonsJson: JSON.stringify(["B envelope correlation 0.71"]),
          }),
        ],
      }
    );
    expect(junction.selectedTake?.status).toBe("check");
    expect(junction.selectedTake?.reasons).toEqual(["B envelope correlation 0.71"]);
  });

  it("marks a selected take with a stale descriptor as stale, never ready", () => {
    const stale = take({ descriptorKey: descriptor(8).key, descriptorJson: descriptor(8).canonicalJson });
    const [junction] = resolve(
      [item({ glueActiveTakeId: "take-1" }), successor()],
      { takes: [stale] }
    );
    expect(junction.state).toBe("stale");
    expect(junction.selectedTake).toBeNull();
  });

  it("rejects a cross-user take", () => {
    const [junction] = resolve(
      [item({ glueActiveTakeId: "take-1" }), successor()],
      { takes: [take({ userId: 2 })] }
    );
    expect(junction.state).toBe("stale");
    expect(junction.selectedTake).toBeNull();
  });

  it("rejects a non-ready take", () => {
    const [junction] = resolve(
      [item({ glueActiveTakeId: "take-1" }), successor()],
      { takes: [take({ status: "failed", r2Key: null, seconds: null })] }
    );
    expect(junction.state).toBe("stale");
    expect(junction.selectedTake).toBeNull();
  });

  it("reports no-tempo before deriving a descriptor", () => {
    const [junction] = resolve([
      item(),
      successor({
        recording: {
          contentHash: HASH_B,
          durationSeconds: 180,
          tempoBpm: null,
          sections: null,
          beats: [],
        },
      }),
    ]);
    expect(junction.state).toBe("needs-generation");
    expect(junction.unavailableReason).toBe("no-tempo");
    expect(junction.descriptorKey).toBeNull();
    expect(junction.quote).toBeNull();
  });

  it("reports no-analysis when a side has no recording hash", () => {
    const [junction] = resolve([item({ recording: null }), successor()]);
    expect(junction.state).toBe("needs-generation");
    expect(junction.unavailableReason).toBe("no-analysis");
  });

  it("reports invalid-arrangement for malformed saved arrangement JSON", () => {
    const [junction] = resolve([
      item({ arrangementSteps: "{not-json" }),
      successor(),
    ]);
    expect(junction.state).toBe("needs-generation");
    expect(junction.unavailableReason).toBe("invalid-arrangement");
  });

  it("reports quota exhaustion without changing the state", () => {
    const [junction] = resolve([item(), successor()], {
      quota: { limit: 5, used: 5, remaining: 0, resetAt: "2026-11-23T09:15:00.000Z" },
    });
    expect(junction.state).toBe("needs-generation");
    expect(junction.unavailableReason).toBe("quota");
    expect(junction.quotaUsed).toBe(5);
    expect(junction.quotaRemaining).toBe(0);
    expect(junction.quotaResetAt).toBe("2026-11-23T09:15:00.000Z");
  });

  it("maps an active job phase and identifies the active job", () => {
    const [junction] = resolve([item(), successor()], { jobs: [job()] });
    expect(junction.state).toBe("composing");
    expect(junction.activeJob).toEqual({
      id: "job-1",
      phase: "composing",
      createdAt: ISO,
      startedAt: ISO,
    });
    expect(junction.unavailableReason).toBe("active-job");
  });

  it("keeps the effective selected take while a Try-another job runs", () => {
    const [junction] = resolve(
      [item({ glueActiveTakeId: "take-1" }), successor()],
      { takes: [take()], jobs: [job()] }
    );
    expect(junction.state).toBe("composing");
    expect(junction.selectedTake?.id).toBe("take-1");
  });

  it("maps a terminal failed job with no effective take", () => {
    const [junction] = resolve([item(), successor()], {
      jobs: [job({ active: false, phase: "failed" })],
    });
    expect(junction.state).toBe("failed");
    expect(junction.activeJob).toBeNull();
  });

  it("ignores a terminal failure whose descriptor no longer matches", () => {
    const [junction] = resolve([item(), successor()], {
      jobs: [
        job({
          active: false,
          phase: "failed",
          descriptorKey: descriptor(8).key,
        }),
      ],
    });
    expect(junction.state).toBe("needs-generation");
    expect(junction.descriptorKey).toBe(descriptor(4).key);
  });

  it("marks another Junction's active job as the reason for a needs-generation junction", () => {
    const [first] = resolve([item(), successor({ itemId: "item-b" })], {
      jobs: [job({ fromItemId: "item-b", toItemId: "item-c" })],
    });
    expect(first.state).toBe("needs-generation");
    expect(first.unavailableReason).toBe("active-job");
  });

  it("excludes cached sides from upload credits", () => {
    const cachedKey = buildGlueExcerptKey({
      side: "A",
      seconds: 12,
      contentHash: HASH_A,
      steps: null,
    }).key;
    const [junction] = resolve([item(), successor()], { cached: [cachedKey] });
    expect(junction.quote?.uncachedSides).toEqual([
      { side: "B", seconds: 12, credits: 180 },
    ]);
    expect(junction.quote?.uploadCredits).toBe(180);
    expect(junction.quote?.estimatedNewCredits).toBe(225);
  });

  it("excludes rejected sides from upload credits", () => {
    const rejectedKey = buildGlueExcerptKey({
      side: "B",
      seconds: 12,
      contentHash: HASH_B,
      steps: null,
    }).key;
    const [junction] = resolve([item(), successor()], { rejected: [rejectedKey] });
    expect(junction.quote?.uncachedSides).toEqual([
      { side: "A", seconds: 12, credits: 180 },
    ]);
    expect(junction.quote?.estimatedNewCredits).toBe(225);
  });

  it("changes the descriptor key when beats change", () => {
    const [four] = resolve([item({ glueBeats: 4 }), successor()]);
    const [eight] = resolve([item({ glueBeats: 8 }), successor()]);
    expect(four.descriptorKey).not.toBe(eight.descriptorKey);
    expect(eight.quote?.generationSeconds).toBe(4);
  });
});
