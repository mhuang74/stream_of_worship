import { describe, it, expect, beforeEach, vi } from "vitest";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/db", () => ({
  db: {
    select: vi.fn(),
    query: {
      songsets: { findFirst: vi.fn() },
      renderJobs: { findFirst: vi.fn() },
      songsetItems: { findFirst: vi.fn() },
    },
  },
}));
vi.mock("@/db/mutation-db", () => ({ mutationDb: { transaction: vi.fn() } }));

vi.mock("@/lib/glue/resolve", () => ({
  loadResolvedGlueJunctions: vi.fn(),
  loadResolvedGlueJunctionsForList: vi.fn(),
  glueTakeSelectionError: vi.fn(),
  takeRowFromDb: vi.fn((row: unknown) => row),
}));

// Keep the real descriptor derivation (the point of the PATCH) but feed it the
// fixture item rows instead of hitting the database.
vi.mock("@/lib/glue/quote", async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, loadGlueItemRows: vi.fn() };
});

import { db } from "@/db";
import { mutationDb } from "@/db/mutation-db";
import { getSongset, updateSongsetItem } from "@/lib/db/songsets";
import { loadGlueItemRows } from "@/lib/glue/quote";
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

interface QueryChain {
  from(): QueryChain;
  where(): QueryChain;
  orderBy(): QueryChain;
  limit(): QueryChain;
  for(): QueryChain;
  then(
    resolve: (value: unknown) => unknown,
    reject?: (reason: unknown) => unknown
  ): Promise<unknown>;
}

/** Chainable stand-in for a Drizzle query builder; awaits to `result`. */
function queryChain(result: unknown): QueryChain {
  const chain: QueryChain = {
    from: () => chain,
    where: () => chain,
    orderBy: () => chain,
    limit: () => chain,
    for: () => chain,
    then: (resolve, reject) => Promise.resolve(result).then(resolve, reject),
  };
  return chain;
}

interface UpdateChain {
  set(values: Record<string, unknown>): UpdateChain;
  where(): UpdateChain;
  then(resolve: (value: unknown) => unknown): Promise<unknown>;
}

function updateChain(record: (values: Record<string, unknown>) => void): UpdateChain {
  const chain: UpdateChain = {
    set: (values) => {
      record(values);
      return chain;
    },
    where: () => chain,
    then: (resolve) => Promise.resolve(undefined).then(resolve),
  };
  return chain;
}

describe("updateSongsetItem beats re-selection", () => {
  const updates: Array<Record<string, unknown>> = [];
  let takeRows: unknown[] = [];

  beforeEach(() => {
    vi.clearAllMocks();
    updates.length = 0;
    takeRows = [];
    vi.mocked(loadGlueItemRows).mockResolvedValue(
      new Map([
        [
          "songset-1",
          [
            {
              itemId: "item-a",
              position: 0,
              userId: 1,
              glueEnabled: true,
              glueBeats: 4,
              glueActiveTakeId: TAKE_ID,
              updatedAt: "2026-11-22T10:00:00.000Z",
              arrangementSteps: null,
              recording: {
                contentHash: HASH_A,
                durationSeconds: 100,
                tempoBpm: 120,
                sections: [],
                beats: [],
              },
            },
            {
              itemId: "item-b",
              position: 1,
              userId: 1,
              glueEnabled: true,
              glueBeats: 4,
              glueActiveTakeId: null,
              updatedAt: "2026-11-22T10:01:00.000Z",
              arrangementSteps: null,
              recording: {
                contentHash: HASH_B,
                durationSeconds: 100,
                tempoBpm: 120,
                sections: [],
                beats: [],
              },
            },
          ],
        ],
      ])
    );

    vi.mocked(db.select).mockReturnValue(queryChain(takeRows) as never);
    const tx = {
      select: vi
        .fn()
        .mockReturnValueOnce(queryChain([{ id: "songset-1" }]) as never)
        .mockReturnValueOnce(
          queryChain([
            {
              id: "item-a",
              recordingHashPrefix: null,
              updatedAt: new Date("2026-11-22T10:00:00.000Z"),
            },
          ]) as never
        ),
      update: vi.fn(() => updateChain((values) => updates.push(values))),
    };
    vi.mocked(mutationDb.transaction).mockImplementation(((
      callback: (value: unknown) => Promise<unknown>
    ) => callback(tx)) as never);
    vi.mocked(db.query.songsetItems.findFirst).mockResolvedValue(
      itemRow("item-a", 0, HASH_A) as never
    );
    vi.mocked(loadResolvedGlueJunctions).mockResolvedValue([junction({})]);
  });

  it("picks the newest ready take for the new Transition length", async () => {
    // `loadNewestReadyGlueTake` orders by createdAt desc, so the fixture lists
    // the newest row first.
    takeRows = [
      { id: TAKE_ID, createdAt: new Date("2026-11-22T10:00:00.000Z") },
      { id: "take-older", createdAt: new Date("2026-11-20T10:00:00.000Z") },
    ];
    vi.mocked(db.select).mockReturnValue(queryChain(takeRows) as never);

    await updateSongsetItem("item-a", "songset-1", 1, { glueBeats: 8 });

    const glueUpdate = updates.find((values) => "glueBeats" in values);
    expect(glueUpdate?.glueBeats).toBe(8);
    expect(glueUpdate?.glueActiveTakeId).toBe(TAKE_ID);
  });

  it("clears the selection when no take exists for the new length", async () => {
    takeRows = [];
    vi.mocked(db.select).mockReturnValue(queryChain(takeRows) as never);

    await updateSongsetItem("item-a", "songset-1", 1, { glueBeats: 2 });

    const glueUpdate = updates.find((values) => "glueBeats" in values);
    expect(glueUpdate?.glueBeats).toBe(2);
    expect(glueUpdate?.glueActiveTakeId).toBeNull();
  });
});
