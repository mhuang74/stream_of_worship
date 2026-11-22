import { describe, it, expect, beforeEach, vi } from "vitest";

vi.mock("@/db", () => ({
  db: {
    query: {
      songsets: { findFirst: vi.fn() },
      renderJobs: { findFirst: vi.fn() },
    },
  },
}));
vi.mock("@/db/mutation-db", () => ({ mutationDb: { transaction: vi.fn() } }));

vi.mock("@/lib/glue/resolve", () => ({
  loadResolvedGlueJunctions: vi.fn(),
  loadResolvedGlueJunctionsForList: vi.fn(),
  glueTakeSelectionError: vi.fn(),
  takeRowFromDb: vi.fn(),
}));

import { db } from "@/db";
import { getSongset } from "@/lib/db/songsets";
import { loadResolvedGlueJunctions } from "@/lib/glue/resolve";
import type { ResolvedGlueJunction } from "@/lib/glue/resolve";

const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);
const TAKE_ID = "6b7f9c34-1c2b-4c4d-8a3a-1b2c3d4e5f60";

function junction(overrides: Partial<ResolvedGlueJunction>): ResolvedGlueJunction {
  return {
    fromItemId: "item-a",
    toItemId: "item-b",
    glueEnabled: true,
    glueBeats: 4,
    descriptorKey: "c".repeat(64),
    state: "ready",
    selectedTake: {
      id: TAKE_ID,
      ordinal: 1,
      seconds: 3,
      r2Key: "glues/take.wav",
      descriptorKey: "c".repeat(64),
      status: "ok",
      reasons: [],
    },
    activeJob: null,
    quote: null,
    quotaLimit: 5,
    quotaUsed: 0,
    quotaRemaining: 5,
    quotaResetAt: null,
    expectedFromUpdatedAt: "2026-11-22T10:00:00.000Z",
    expectedToUpdatedAt: "2026-11-22T10:01:00.000Z",
    unavailableReason: null,
    ...overrides,
  };
}

function itemRow(id: string, position: number, contentHash: string) {
  return {
    id,
    songsetId: "songset-1",
    songId: `song-${id}`,
    recordingHashPrefix: null,
    position,
    gapBeats: 0,
    crossfadeEnabled: 0,
    crossfadeDurationSeconds: 0,
    keyShiftSemitones: null,
    tempoRatio: null,
    arrangementSteps: null,
    glueEnabled: true,
    glueBeats: 4,
    glueActiveTakeId: id === "item-a" ? TAKE_ID : null,
    updatedAt: new Date("2026-11-22T10:00:00.000Z"),
    song: {
      id: `song-${id}`,
      title: `Song ${id}`,
      composer: null,
      lyricist: null,
      albumName: null,
      musicalKey: null,
      titlePinyin: null,
      albumSeries: null,
      createdAt: new Date(),
    },
    recording: {
      hashPrefix: null,
      contentHash,
      durationSeconds: 100,
      tempoBpm: 120,
      musicalKey: null,
      musicalMode: null,
      keyConfidence: null,
      keyScoreMargin: null,
      keyWindowAgreement: null,
      r2AudioUrl: null,
      theme: null,
      sections: "[]",
      beats: "[]",
      deletedAt: null,
      lyricMarks: [],
    },
  };
}

describe("getSongset resolved AI transitions", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(db.query.songsets.findFirst).mockResolvedValue({
      id: "songset-1",
      name: "Sunday Set",
      description: null,
      createdAt: new Date("2026-11-01T00:00:00.000Z"),
      updatedAt: new Date("2026-11-22T10:00:00.000Z"),
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
      lastCompletedRenderJobId: null,
      items: [
        itemRow("item-a", 0, HASH_A),
        itemRow("item-b", 1, HASH_B),
      ],
    } as never);
    vi.mocked(loadResolvedGlueJunctions).mockResolvedValue([
      junction({}),
      junction({
        fromItemId: "item-b",
        toItemId: null,
        glueBeats: 4,
        state: "disabled",
        selectedTake: null,
        expectedToUpdatedAt: null,
        unavailableReason: "no-next",
      }),
    ]);
  });

  it("returns flat glue fields plus the outgoing Junction per item", async () => {
    const songset = await getSongset("songset-1", 1);
    expect(songset).not.toBeNull();
    const [first, second] = songset!.items;

    expect(first.updatedAt).toBe("2026-11-22T10:00:00.000Z");
    expect(first.glueEnabled).toBe(true);
    expect(first.glueBeats).toBe(4);
    expect(first.glueTakeId).toBe(TAKE_ID);
    expect(first.glueSeconds).toBe(3);
    expect(first.glue.state).toBe("ready");
    expect(first.glue.selectedTake?.id).toBe(TAKE_ID);

    expect(second.glueTakeId).toBeNull();
    expect(second.glueSeconds).toBeNull();
    expect(second.glue.state).toBe("disabled");
    expect(second.glue.unavailableReason).toBe("no-next");
  });

  it("never treats glue_active_take_id as readiness on its own", async () => {
    // The resolver is the sole authority: the loader returns no selectedTake
    // even though the row stores glue_active_take_id.
    vi.mocked(loadResolvedGlueJunctions).mockResolvedValue([
      junction({ state: "stale", selectedTake: null }),
      junction({
        fromItemId: "item-b",
        toItemId: null,
        state: "disabled",
        selectedTake: null,
        expectedToUpdatedAt: null,
        unavailableReason: "no-next",
      }),
    ]);
    const songset = await getSongset("songset-1", 1);
    expect(songset!.items[0].glueTakeId).toBeNull();
    expect(songset!.items[0].glue.state).toBe("stale");
  });
});
