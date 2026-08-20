import { describe, it, expect, vi, beforeEach } from "vitest";
import { db } from "@/db";
import { getDashboardStats, getCommunityFavoriteSample } from "@/lib/db/dashboard";
import { listSongs, mapSongWithRecordings } from "@/lib/db/songs";

/* eslint-disable @typescript-eslint/no-explicit-any */

const mockSelect = vi.fn();
const mockFrom = vi.fn();
const mockWhere = vi.fn();

vi.mock("@/db", () => ({
  db: {
    select: (...args: unknown[]) => mockSelect(...args),
    query: {
      songs: { findMany: vi.fn() },
    },
  },
}));

vi.mock("@/lib/db/songsets", () => ({
  listSongsetSummaries: vi.fn(),
}));

vi.mock("@/lib/db/favorites", () => ({
  getFavoriteSongIds: vi.fn(),
}));

vi.mock("@/lib/db/songs", () => ({
  listSongs: vi.fn(),
  mapSongWithRecordings: vi.fn(),
}));

describe("getDashboardStats", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // 5 sequential count queries (created, rendered, shared, favorites, catalog)
    const calls = [
      [{ n: 5 }], // created
      [{ n: 2 }], // rendered
      [{ n: 3 }], // shared
      [{ n: 4 }], // favorites
      [{ n: 321 }], // catalog
    ];
    mockSelect.mockReturnValue({ from: mockFrom });
    mockFrom.mockReturnValue({ where: mockWhere });
    mockWhere.mockImplementation(() => {
      const next = calls.shift();
      return Promise.resolve(next ?? [{ n: 0 }]);
    });
  });

  it("returns per-user aggregate counts plus catalog total", async () => {
    const stats = await getDashboardStats(42);
    expect(stats).toEqual({
      songsetsCreated: 5,
      songsetsRendered: 2,
      songsetsShared: 3,
      favoriteSongs: 4,
      catalogSongs: 321,
    });
  });
});

describe("getCommunityFavoriteSample", () => {
  // Distinct chains for the two select queries so their `.from()` shapes
  // never collide: the random sample goes select→from→where→groupBy→orderBy
  // →limit; the favorite-count query goes select→from→innerJoin→where→groupBy.
  const sampleFrom = vi.fn();
  const sampleWhere = vi.fn();
  const sampleGroupBy = vi.fn();
  const sampleOrderBy = vi.fn();
  const sampleLimit = vi.fn();

  const countFrom = vi.fn();
  const countInnerJoin = vi.fn();
  const countWhere = vi.fn();
  const countGroupBy = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(db.query.songs.findMany).mockReset();

    mockSelect.mockImplementation((args: any) => {
      if (args && "favoriteCount" in args) return { from: countFrom };
      return { from: sampleFrom };
    });

    sampleFrom.mockReturnValue({ where: sampleWhere });
    sampleWhere.mockReturnValue({ groupBy: sampleGroupBy });
    sampleGroupBy.mockReturnValue({ orderBy: sampleOrderBy });
    sampleOrderBy.mockReturnValue({ limit: sampleLimit });
    sampleLimit.mockResolvedValue([]);

    countFrom.mockReturnValue({ innerJoin: countInnerJoin });
    countInnerJoin.mockReturnValue({ where: countWhere });
    countWhere.mockReturnValue({ groupBy: countGroupBy });
    countGroupBy.mockResolvedValue([]);
  });

  it("returns [] when no other-user favorites exist", async () => {
    await expect(getCommunityFavoriteSample(42, 4)).resolves.toEqual([]);
  });

  it("returns sampled songs with favorite counts, fetched by songId", async () => {
    sampleLimit.mockResolvedValue([{ songId: "song-a" }, { songId: "song-b" }]);
    countGroupBy.mockResolvedValue([
      { songId: "song-a", favoriteCount: 7 },
      { songId: "song-b", favoriteCount: 3 },
    ]);

    vi.mocked(db.query.songs.findMany).mockResolvedValue([
      { id: "song-a", title: "A" },
      { id: "song-b", title: "B" },
    ] as any);
    vi.mocked(mapSongWithRecordings).mockImplementation(
      (song: any) =>
        ({
          id: song.id,
          title: song.title,
          composer: null,
          lyricist: null,
          albumName: null,
          musicalKey: null,
          recordings: [],
        }) as any
    );

    const result = await getCommunityFavoriteSample(42, 4);

    expect(result).toHaveLength(2);
    expect(result[0]).toMatchObject({ id: "song-a", favoriteCount: 7 });
    expect(result[1]).toMatchObject({ id: "song-b", favoriteCount: 3 });

    // The song fetch must be by the sampled songIds (not an unscoped listSongs)
    const findManyArgs = vi.mocked(db.query.songs.findMany).mock.calls[0][0];
    expect(findManyArgs).toHaveProperty("where");
    expect(listSongs).not.toHaveBeenCalled();
  });
});
