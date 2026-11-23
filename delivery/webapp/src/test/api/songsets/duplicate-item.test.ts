import { describe, it, expect, beforeEach, vi } from "vitest";
import { duplicateSongsetItem, SongsetRenderValidationError } from "@/lib/db/songsets";
import { db } from "@/db";
import { mutationDb } from "@/db/mutation-db";
import { songsets, songsetItems } from "@/db/schema";
import { buildGlueDescriptor } from "@/lib/glue/descriptor";

vi.mock("@/db/mutation-db", () => ({
  mutationDb: { transaction: vi.fn() },
}));

vi.mock("@/db", () => ({
  db: {
    query: {
      songsets: { findFirst: vi.fn() },
      renderJobs: { findFirst: vi.fn(), findMany: vi.fn() },
      songsetItems: { findFirst: vi.fn(), findMany: vi.fn() },
      userSettings: { findFirst: vi.fn() },
    },
    select: vi.fn(),
    insert: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock("nanoid", () => ({
  nanoid: vi.fn(() => "copied-item-id"),
}));

type MockSelectChain = {
  from: ReturnType<typeof vi.fn>;
  leftJoin: ReturnType<typeof vi.fn>;
  where: ReturnType<typeof vi.fn>;
  innerJoin: ReturnType<typeof vi.fn>;
  orderBy: ReturnType<typeof vi.fn>;
  limit: ReturnType<typeof vi.fn>;
  for: ReturnType<typeof vi.fn>;
  then: (resolve: (rows: unknown[]) => unknown) => Promise<unknown>;
};

function createSelectChain(rows: unknown[]): MockSelectChain {
  const promise = Promise.resolve(rows);
  const chain = {} as MockSelectChain;
  for (const method of ["from", "innerJoin", "leftJoin", "where", "orderBy", "limit", "for"] as const) {
    chain[method] = vi.fn().mockReturnValue(chain);
  }
  chain.then = (resolve) => promise.then(resolve);
  return chain;
}

interface Write {
  kind: "insert" | "update";
  table: unknown;
  values: Record<string, unknown>;
}

interface TransactionDouble {
  selects: ReturnType<typeof vi.fn>;
  writes: Write[];
  whereClauses: unknown[];
  locks: unknown[];
}

/** Transaction double whose selects resolve `rowSets` in call order. */
function installMutationTransaction(...rowSets: unknown[][]): TransactionDouble {
  const selects = vi.fn();
  const writes: Write[] = [];
  const whereClauses: unknown[] = [];
  const locks: unknown[] = [];
  let callIndex = 0;

  selects.mockImplementation(() => {
    const chain = createSelectChain(rowSets[callIndex] ?? []);
    callIndex += 1;
    chain.where = vi.fn((clause: unknown) => {
      whereClauses.push(clause);
      return chain;
    });
    chain.for = vi.fn((mode: unknown) => {
      locks.push(mode);
      return chain;
    });
    return chain;
  });

  const update = vi.fn((table: unknown) => ({
    set: (values: Record<string, unknown>) => {
      writes.push({ kind: "update", table, values });
      return { where: () => Promise.resolve(undefined) };
    },
  }));
  const txInsert = vi.fn((table: unknown) => ({
    values: (values: Record<string, unknown>) => {
      writes.push({ kind: "insert", table, values });
      return {
        returning: vi.fn().mockResolvedValue([]),
        then: (resolve: (value: unknown) => unknown) =>
          Promise.resolve(undefined).then(resolve),
      };
    },
  }));

  const transaction = vi.mocked(mutationDb.transaction) as unknown as ReturnType<typeof vi.fn>;
  transaction.mockImplementation((callback: (handle: unknown) => Promise<unknown>) =>
    callback({ select: selects, update, insert: txInsert })
  );

  return { selects, writes, whereClauses, locks };
}

const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);
const TAKE_ID = "ready-take";

function siblingRow(extra: Record<string, unknown> = {}) {
  return {
    id: "item-1",
    songId: "song-1",
    recordingHashPrefix: "abc",
    contentHash: HASH_A,
    position: 1,
    arrangementSteps: null,
    gapBeats: 2,
    crossfadeEnabled: 0,
    crossfadeDurationSeconds: null,
    keyShiftSemitones: -2,
    tempoRatio: 1.05,
    durationSeconds: 120,
    tempoBpm: 120,
    recordingSections: null,
    recordingBeats: null,
    recordingDeletedAt: null,
    updatedAt: new Date("2026-11-22T10:00:00.000Z"),
    glueEnabled: false,
    glueBeats: 4,
    glueActiveTakeId: null,
    ...extra,
  };
}

function takeRow(extra: Record<string, unknown> = {}) {
  const descriptor = buildGlueDescriptor({
    beats: 8,
    toTempoBpm: 120,
    from: { contentHash: HASH_A, steps: null },
    to: { contentHash: HASH_B, steps: null },
  });
  return {
    id: TAKE_ID,
    userId: 1,
    descriptorKey: descriptor.key,
    descriptorJson: descriptor.canonicalJson,
    status: "ready",
    seconds: 20,
    ordinal: 1,
    r2Key: "glues/take.wav",
    metricsJson: "{}",
    reasonsJson: "[]",
    createdAt: new Date("2026-11-22T10:00:00.000Z"),
    ...extra,
  };
}

function copiedItemRow(extra: Record<string, unknown> = {}) {
  return {
    id: "copied-item-id",
    songsetId: "songset-1",
    songId: "song-1",
    recordingHashPrefix: "abc",
    position: 2,
    gapBeats: 2,
    crossfadeEnabled: 1,
    crossfadeDurationSeconds: 3.5,
    keyShiftSemitones: -2,
    tempoRatio: 1.05,
    arrangementSteps: null,
    glueEnabled: false,
    glueBeats: 4,
    glueActiveTakeId: null,
    createdAt: new Date(),
    updatedAt: new Date(),
    song: null,
    recording: null,
    ...extra,
  };
}

describe("duplicateSongsetItem", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // Most cases only exercise transaction admission. The readback resolver
    // sees empty rows unless the test supplies the committed junction inputs.
    vi.mocked(db.select).mockReturnValue(createSelectChain([]) as never);
  });

  it("inserts the copy below the source and shifts later siblings down", async () => {
    vi.mocked(db.query.songsetItems.findFirst).mockResolvedValueOnce(
      copiedItemRow() as never
    );
    const { writes, locks } = installMutationTransaction(
      [{ id: "songset-1" }],
      [
        siblingRow({ crossfadeEnabled: 1, crossfadeDurationSeconds: 3.5 }),
        siblingRow({ id: "item-2", position: 2 }),
      ]
    );

    const result = await duplicateSongsetItem("item-1", "songset-1", 1);

    // The sibling cap/duration read and the shift share the parent-row lock.
    expect(locks).toEqual(["update"]);
    const insert = writes.find((write) => write.kind === "insert");
    expect(insert?.table).toBe(songsetItems);
    expect(insert?.values).toMatchObject({
      id: "copied-item-id",
      songsetId: "songset-1",
      songId: "song-1",
      recordingHashPrefix: "abc",
      position: 2,
      gapBeats: 2,
      crossfadeEnabled: 1,
      crossfadeDurationSeconds: 3.5,
      keyShiftSemitones: -2,
      tempoRatio: 1.05,
    });

    const shift = writes.find(
      (write) => write.kind === "update" && write.table === songsetItems
    );
    expect(shift).toBeDefined();
    expect(shift!.values.position).toBeDefined();
    expect(shift!.values.updatedAt).toBeInstanceOf(Date);

    const songsetTouch = writes.find(
      (write) => write.kind === "update" && write.table === songsets
    );
    expect(songsetTouch?.values.updatedAt).toBeInstanceOf(Date);

    expect(result?.id).toBe("copied-item-id");
    expect(result?.position).toBe(2);
  });

  it("copies the Song Arrangement verbatim instead of re-serializing it", async () => {
    const source = siblingRow({
      arrangementSteps: '{"version":1,"steps":[{"sectionIndex":0,"label":"intro","startSeconds":0,"endSeconds":10}]}',
    });
    vi.mocked(db.query.songsetItems.findFirst).mockResolvedValueOnce(
      copiedItemRow() as never
    );
    const { writes } = installMutationTransaction(
      [{ id: "songset-1" }],
      [source]
    );

    await duplicateSongsetItem("item-1", "songset-1", 1);

    const insert = writes.find((write) => write.kind === "insert");
    expect(insert?.values.arrangementSteps).toBe(source.arrangementSteps);
  });

  it("admits a copy that fits only in its persisted position", async () => {
    // Persisted order A,A,B totals 1496s. Projecting the copy at the end
    // (A,B,A) would charge A's 5s fade after B's 1496s end and refuse a
    // Songset that actually fits under the 1500s cap.
    vi.mocked(db.query.songsetItems.findFirst).mockResolvedValueOnce(
      copiedItemRow() as never
    );
    installMutationTransaction(
      [{ id: "songset-1" }],
      [
        siblingRow({
          id: "item-a",
          position: 0,
          durationSeconds: 10,
          gapBeats: 0,
          crossfadeEnabled: 1,
          crossfadeDurationSeconds: 5,
        }),
        siblingRow({
          id: "item-b",
          position: 1,
          durationSeconds: 1496,
          gapBeats: 0,
          crossfadeEnabled: 1,
          crossfadeDurationSeconds: 60,
        }),
      ]
    );

    await expect(duplicateSongsetItem("item-a", "songset-1", 1)).resolves.toMatchObject({
      id: "copied-item-id",
    });
  });

  it("carries a ready take to the copy and re-resolves it against the successor", async () => {
    const source = siblingRow({
      position: 0,
      durationSeconds: 495,
      gapBeats: 0,
      glueEnabled: true,
      glueBeats: 8,
      glueActiveTakeId: TAKE_ID,
    });
    const successor = siblingRow({
      id: "item-2",
      position: 1,
      contentHash: HASH_B,
      durationSeconds: 480,
      gapBeats: 0,
    });
    const { writes } = installMutationTransaction(
      [{ id: "songset-1" }], [source, successor], [takeRow()]
    );
    const copy = { ...source, id: "copied-item-id", position: 1 };
    const committed = [source, copy, { ...successor, position: 2 }];
    vi.mocked(db.query.songsetItems.findFirst).mockResolvedValueOnce(
      copiedItemRow(copy) as never
    );
    vi.mocked(db.select)
      .mockReturnValueOnce(createSelectChain(committed.map((row) => ({
        ...row,
        itemId: row.id,
        songsetId: "songset-1",
        userId: 1,
        sections: row.recordingSections,
        beats: row.recordingBeats,
      }))) as never)
      .mockReturnValueOnce(createSelectChain([takeRow()]) as never);

    // One 20s take fits the 1470s projection. Applying the stale take to the
    // source's new A-to-A junction as well would exceed the cap.
    const result = await duplicateSongsetItem("item-1", "songset-1", 1);

    expect(writes.find((write) => write.kind === "insert")?.values).toMatchObject({
      glueEnabled: true,
      glueBeats: 8,
      glueActiveTakeId: TAKE_ID,
    });
    expect(result?.glue).toMatchObject({
      fromItemId: "copied-item-id",
      toItemId: "item-2",
      state: "ready",
      selectedTake: { id: TAKE_ID, seconds: 20 },
    });
  });

  it.each([
    { label: "disabled", glueEnabled: false, take: takeRow(), copiedTakeId: TAKE_ID },
    { label: "stale", glueEnabled: true, take: takeRow({ descriptorJson: "{}" }), copiedTakeId: TAKE_ID },
    { label: "foreign", glueEnabled: true, take: takeRow({ userId: 2 }), copiedTakeId: null },
  ])("copies only an owned pointer and excludes a $label take from duration", async ({
    glueEnabled, take, copiedTakeId,
  }) => {
    const source = siblingRow({
      position: 0,
      durationSeconds: 495,
      gapBeats: 0,
      glueEnabled,
      glueBeats: 8,
      glueActiveTakeId: TAKE_ID,
    });
    const successor = siblingRow({
      id: "item-2",
      position: 1,
      contentHash: HASH_B,
      durationSeconds: 500,
      gapBeats: 0,
    });
    vi.mocked(db.query.songsetItems.findFirst).mockResolvedValueOnce(copiedItemRow() as never);
    const { writes } = installMutationTransaction(
      [{ id: "songset-1" }], [source, successor], [take]
    );

    await expect(duplicateSongsetItem("item-1", "songset-1", 1)).resolves.not.toBeNull();
    expect(writes.find((write) => write.kind === "insert")?.values).toMatchObject({
      glueEnabled,
      glueBeats: 8,
      glueActiveTakeId: copiedTakeId,
    });
  });

  it.each([
    { gapBeats: 0, durationSeconds: 250, shouldFit: false },
    { gapBeats: 62, durationSeconds: 240, shouldFit: true },
  ])("applies staged seconds instead of the ordinary gap for admission ($shouldFit)", async ({
    gapBeats, durationSeconds, shouldFit,
  }) => {
    const { writes } = installMutationTransaction(
      [{ id: "songset-1" }],
      [
        siblingRow({
          id: "item-a", position: 0, durationSeconds: 495, gapBeats: 0,
          glueEnabled: true, glueBeats: 8, glueActiveTakeId: TAKE_ID,
        }),
        siblingRow({ id: "item-b", position: 1, contentHash: HASH_B, durationSeconds: 495, gapBeats }),
        siblingRow({ id: "item-c", position: 2, durationSeconds, gapBeats: 0 }),
      ],
      [takeRow()]
    );

    if (shouldFit) {
      // The ordinary 31s gap makes the projection 1501s. Replacing it with
      // the effective 20s take gives 1490s.
      vi.mocked(db.query.songsetItems.findFirst).mockResolvedValueOnce(copiedItemRow() as never);
      await expect(duplicateSongsetItem("item-c", "songset-1", 1)).resolves.not.toBeNull();
      expect(writes.some((write) => write.kind === "insert")).toBe(true);
    } else {
      // 1490s ordinary duration becomes 1510s with the staged take.
      await expect(duplicateSongsetItem("item-c", "songset-1", 1)).rejects.toMatchObject({
        code: "SONGSET_TOO_LONG",
      });
      expect(writes).toEqual([]);
    }
  });

  it("refuses a sixth item with SONGSET_FULL", async () => {
    installMutationTransaction(
      [{ id: "songset-1" }],
      [1, 2, 3, 4, 5].map((n) => siblingRow({ id: `item-${n}`, position: n - 1 }))
    );

    const error = await duplicateSongsetItem("item-1", "songset-1", 1).catch((err) => err);

    expect(error).toBeInstanceOf(SongsetRenderValidationError);
    expect(error.code).toBe("SONGSET_FULL");
    expect(error.message).toBe("Songset already has maximum of 5 songs");
  });

  it("returns null for an item the caller does not own", async () => {
    installMutationTransaction([]);

    await expect(duplicateSongsetItem("item-1", "songset-1", 1)).resolves.toBeNull();
  });

  it("returns null for an unknown item in an owned Songset", async () => {
    installMutationTransaction([{ id: "songset-1" }], []);

    await expect(duplicateSongsetItem("item-404", "songset-1", 1)).resolves.toBeNull();
  });
});
