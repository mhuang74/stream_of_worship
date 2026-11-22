import { describe, it, expect, beforeEach, vi } from "vitest";
import { findSongsetsContainingSong } from "@/lib/db/songsets";
import { db } from "@/db";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/db/mutation-db", () => ({ mutationDb: { transaction: vi.fn() } }));

vi.mock("@/db", () => ({
  db: {
    query: {
      songsets: { findFirst: vi.fn(), findMany: vi.fn() },
      songsetItems: { findFirst: vi.fn(), findMany: vi.fn() },
    },
    select: vi.fn(),
    with: vi.fn(),
    $with: vi.fn(),
  },
}));

vi.mock("nanoid", () => ({ nanoid: vi.fn(() => "test-id") }));

type Chain = Record<string, any> & { then: (cb: (rows: unknown[]) => unknown) => Promise<unknown> };

/** Main-query chain: every builder method returns itself, thenable resolves `rows`. */
function createChain(rows: unknown[]): Chain {
  const promise = Promise.resolve(rows);
  const chain: Chain = {} as Chain;
  for (const method of ["select", "from", "innerJoin", "leftJoin", "where", "groupBy", "orderBy", "limit"]) {
    chain[method] = vi.fn().mockReturnValue(chain);
  }
  chain.then = (cb) => promise.then(cb);
  return chain;
}

function mockQuery(rows: unknown[]): Chain {
  const chain = createChain(rows);
  // The item-count CTE is built from its own `db.select(...)` chain.
  vi.mocked(db.select).mockImplementation((() => createChain([])) as never);
  vi.mocked(db.$with).mockReturnValue({
    as: vi.fn(() => "item_counts"),
  } as never);
  vi.mocked(db.with).mockReturnValue(chain as never);
  return chain;
}

function groupedRow(extra: Record<string, unknown> = {}) {
  return {
    id: "ss-1",
    name: "Sunday Set",
    description: null,
    updatedAt: new Date("2024-01-02T00:00:00Z"),
    itemCount: 5,
    occurrenceCount: 2,
    positions: [0, 2],
    ownerId: 1,
    ownerName: "Owner",
    ...extra,
  };
}

describe("findSongsetsContainingSong", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns one row per Songset with every occurrence position", async () => {
    mockQuery([groupedRow()]);

    const result = await findSongsetsContainingSong("song-1", 1);

    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      id: "ss-1",
      itemCount: 5,
      occurrenceCount: 2,
      positions: [0, 2],
    });
    expect(result[0]).not.toHaveProperty("songPosition");
  });

  it("groups the occurrence rows by Songset", async () => {
    const chain = mockQuery([groupedRow()]);

    await findSongsetsContainingSong("song-1", 1);

    // Without the grouping, a Song at two positions would return two rows.
    expect(chain.groupBy).toHaveBeenCalledTimes(1);
  });

  it("puts the origin Songset first", async () => {
    mockQuery([
      groupedRow({ id: "ss-newer", updatedAt: new Date("2024-05-01T00:00:00Z") }),
      groupedRow({ id: "ss-origin", updatedAt: new Date("2024-01-01T00:00:00Z") }),
    ]);

    const result = await findSongsetsContainingSong("song-1", 1, "ss-origin");

    expect(result.map((row) => row.id)).toEqual(["ss-origin", "ss-newer"]);
    expect(result[0].isOrigin).toBe(true);
    expect(result[1].isOrigin).toBe(false);
  });
});
