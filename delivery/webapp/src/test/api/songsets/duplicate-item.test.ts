import { describe, it, expect, beforeEach, vi } from "vitest";
import { duplicateSongsetItem, SongsetRenderValidationError } from "@/lib/db/songsets";
import { db } from "@/db";
import { mutationDb } from "@/db/mutation-db";
import { songsets, songsetItems } from "@/db/schema";

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
  orderBy: ReturnType<typeof vi.fn>;
  limit: ReturnType<typeof vi.fn>;
  for: ReturnType<typeof vi.fn>;
  then: (resolve: (rows: unknown[]) => unknown) => Promise<unknown>;
};

function createSelectChain(rows: unknown[]): MockSelectChain {
  const promise = Promise.resolve(rows);
  const chain = {} as MockSelectChain;
  for (const method of ["from", "leftJoin", "where", "orderBy", "limit", "for"] as const) {
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
}

/** Transaction double whose selects resolve `rowSets` in call order. */
function installMutationTransaction(...rowSets: unknown[][]): TransactionDouble {
  const selects = vi.fn();
  const writes: Write[] = [];
  const whereClauses: unknown[] = [];
  let callIndex = 0;

  selects.mockImplementation(() => {
    const chain = createSelectChain(rowSets[callIndex] ?? []);
    callIndex += 1;
    chain.where = vi.fn((clause: unknown) => {
      whereClauses.push(clause);
      return chain;
    });
    chain.for = vi.fn(() => chain);
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

  return { selects, writes, whereClauses };
}

/** Row shape selected by the source lookup inside the duplicate transaction. */
function sourceRow(extra: Record<string, unknown> = {}) {
  return {
    songId: "song-1",
    recordingHashPrefix: "abc",
    position: 1,
    gapBeats: 2,
    crossfadeEnabled: 1,
    crossfadeDurationSeconds: 3.5,
    keyShiftSemitones: -2,
    tempoRatio: 1.05,
    arrangementSteps: '{"version":1,"steps":[{"sectionIndex":0,"label":"intro","startSeconds":0,"endSeconds":10}]}',
    ...extra,
  };
}

/** Sibling row shape selected by the duration/cap read. */
function siblingRow(extra: Record<string, unknown> = {}) {
  return {
    id: "item-1",
    arrangementSteps: null,
    gapBeats: 2,
    crossfadeEnabled: 0,
    crossfadeDurationSeconds: null,
    durationSeconds: 120,
    tempoBpm: 120,
    recordingSections: null,
    recordingBeats: null,
    recordingDeletedAt: null,
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
  });

  it("inserts the copy below the source and shifts later siblings down", async () => {
    vi.mocked(db.query.songsetItems.findFirst).mockResolvedValueOnce(
      copiedItemRow() as never
    );
    const { writes } = installMutationTransaction(
      [{ id: "songset-1" }],
      [sourceRow()],
      [siblingRow(), siblingRow({ id: "item-2", position: 2 })]
    );

    const result = await duplicateSongsetItem("item-1", "songset-1", 1);

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
    const source = sourceRow();
    vi.mocked(db.query.songsetItems.findFirst).mockResolvedValueOnce(
      copiedItemRow() as never
    );
    const { writes } = installMutationTransaction(
      [{ id: "songset-1" }],
      [source],
      [siblingRow({ arrangementSteps: source.arrangementSteps })]
    );

    await duplicateSongsetItem("item-1", "songset-1", 1);

    const insert = writes.find((write) => write.kind === "insert");
    expect(insert?.values.arrangementSteps).toBe(source.arrangementSteps);
  });

  it("refuses a sixth item with SONGSET_FULL", async () => {
    installMutationTransaction(
      [{ id: "songset-1" }],
      [sourceRow()],
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
