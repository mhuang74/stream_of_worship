import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  findTopMatchingLines,
  listSongs,
  rrfRerank,
  semanticSearchSongs,
  SemanticSearchResult,
} from "@/lib/db/songs";
import { db } from "@/db";
import { PgDialect } from "drizzle-orm/pg-core";

vi.mock("@/db", () => ({
  db: {
    execute: vi.fn(),
    query: {
      songs: {
        findMany: vi.fn(),
      },
    },
    select: vi.fn(),
  },
}));

const dialect = new PgDialect();

const NON_ZERO_EMBEDDING = Array.from({ length: 1536 }, () => 0.01);

function makeSong(id: string, similarity: number): SemanticSearchResult {
  return {
    id,
    title: `Song ${id}`,
    titlePinyin: null,
    composer: null,
    lyricist: null,
    albumName: null,
    albumSeries: null,
    musicalKey: null,
    createdAt: null,
    updatedAt: null,
    similarity,
    modelVersion: "text-embedding-3-small",
    matchingSnippet: null,
    whyThisMatch: [],
    recordings: [],
  };
}

describe("findTopMatchingLines", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns empty Map for empty songIds without calling DB", async () => {
    const result = await findTopMatchingLines(NON_ZERO_EMBEDDING, []);
    expect(result.size).toBe(0);
    expect(db.execute).not.toHaveBeenCalled();
  });

  it("builds ARRAY[] syntax for ANY() clause", async () => {
    vi.mocked(db.execute).mockResolvedValue({ rows: [] } as unknown as Awaited<ReturnType<typeof db.execute>>);
    await findTopMatchingLines(NON_ZERO_EMBEDDING, ["song-1", "song-2"]);
    const callArgs = vi.mocked(db.execute).mock.calls[0][0];
    const query = dialect.sqlToQuery(callArgs);
    expect(query.sql).toContain("ARRAY[");
    expect(query.sql).toContain("::text[]");
  });

  it("throws on invalid embedding (wrong dimensions)", async () => {
    const badEmbedding = [1, 2, 3];
    await expect(
      findTopMatchingLines(badEmbedding, ["song-1"])
    ).rejects.toThrow("Invalid embedding");
  });

  it("throws on embedding with NaN", async () => {
    const nanEmbedding = Array.from({ length: 1536 }, (_, i) =>
      i === 0 ? NaN : 0.01
    );
    await expect(
      findTopMatchingLines(nanEmbedding, ["song-1"])
    ).rejects.toThrow("Invalid embedding");
  });
});

describe("semanticSearchSongs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("filters by effective catalog key before recording-key fallback", async () => {
    vi.mocked(db.execute).mockResolvedValue({
      rows: [],
    } as unknown as Awaited<ReturnType<typeof db.execute>>);

    await semanticSearchSongs(
      NON_ZERO_EMBEDDING,
      "text-embedding-3-small",
      20,
      ["published", "review"],
      { keys: ["A"] }
    );

    const callArgs = vi.mocked(db.execute).mock.calls[0][0];
    const query = dialect.sqlToQuery(callArgs);
    expect(query.sql).toContain("s.musical_key");
    expect(query.sql).toContain("s.musical_key_start_pitch_class");
    expect(query.sql).toContain("s.musical_key_end_pitch_class");
    expect(query.sql).toContain("r.musical_key");
    expect(query.sql).toContain("NOT (");
    expect(query.params).toContain(9);
  });
});

describe("listSongs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("filters keys by effective catalog key before recording-key fallback", async () => {
    const where = vi.fn().mockResolvedValue([{ count: 0 }]);
    const from = vi.fn().mockReturnValue({ where });

    vi.mocked(db.query.songs.findMany).mockResolvedValue([]);
    vi.mocked(db.select).mockReturnValue({
      from,
    } as unknown as ReturnType<typeof db.select>);

    await listSongs(50, 0, {
      visibilityStatus: ["published", "review"],
      keys: ["A"],
    });

    const findManyArgs = vi.mocked(db.query.songs.findMany).mock.calls[0][0];
    const query = dialect.sqlToQuery(findManyArgs.where);
    expect(query.sql).toContain("songs.musical_key");
    expect(query.sql).toContain("songs.musical_key_start_pitch_class");
    expect(query.sql).toContain("songs.musical_key_end_pitch_class");
    expect(query.sql).toContain("r2.musical_key");
    expect(query.sql).toContain("NOT (");
    expect(query.params).toContain(9);
  });
});

describe("rrfRerank", () => {
  it("song with strong line match overtakes song with weak line match", () => {
    const songs = [
      makeSong("A", 0.80),
      makeSong("C", 0.50),
      makeSong("B", 0.45),
      makeSong("D", 0.35),
    ];
    const snippets = new Map<string, { lineText: string; lineSimilarity: number }[]>();
    snippets.set("A", [{ lineText: "line A", lineSimilarity: 0.30 }]);
    snippets.set("B", [{ lineText: "line B", lineSimilarity: 0.65 }]);
    snippets.set("C", [{ lineText: "line C", lineSimilarity: 0.40 }]);
    snippets.set("D", [{ lineText: "line D", lineSimilarity: 0.60 }]);

    const result = rrfRerank(songs, snippets);

    expect(result[0].id).toBe("B");
    expect(result[1].id).toBe("A");
    expect(result[2].id).toBe("C");
    expect(result[3].id).toBe("D");
  });

  it("song with no line embeddings gets last-place line rank", () => {
    const songs = [
      makeSong("A", 0.80),
      makeSong("B", 0.45),
    ];
    const snippets = new Map<string, { lineText: string; lineSimilarity: number }[]>();
    snippets.set("A", [{ lineText: "line A", lineSimilarity: 0.30 }]);

    const result = rrfRerank(songs, snippets);

    expect(result[0].id).toBe("A");
    expect(result[1].id).toBe("B");
  });

  it("results are sorted by rrfScore DESC", () => {
    const songs = [
      makeSong("A", 0.80),
      makeSong("B", 0.45),
      makeSong("C", 0.50),
    ];
    const snippets = new Map<string, { lineText: string; lineSimilarity: number }[]>();
    snippets.set("A", [{ lineText: "line A", lineSimilarity: 0.30 }]);
    snippets.set("B", [{ lineText: "line B", lineSimilarity: 0.65 }]);
    snippets.set("C", [{ lineText: "line C", lineSimilarity: 0.40 }]);

    const result = rrfRerank(songs, snippets);

    for (let i = 1; i < result.length; i++) {
      expect(result[i - 1].rrfScore!).toBeGreaterThanOrEqual(result[i].rrfScore!);
    }
  });

  it("similarity field is preserved (not overwritten)", () => {
    const songs = [makeSong("A", 0.80), makeSong("B", 0.45)];
    const snippets = new Map<string, { lineText: string; lineSimilarity: number }[]>();
    snippets.set("A", [{ lineText: "line A", lineSimilarity: 0.30 }]);
    snippets.set("B", [{ lineText: "line B", lineSimilarity: 0.65 }]);

    const result = rrfRerank(songs, snippets);

    const songA = result.find((s) => s.id === "A")!;
    const songB = result.find((s) => s.id === "B")!;
    expect(songA.similarity).toBe(0.80);
    expect(songB.similarity).toBe(0.45);
  });

  it("rrfScore is present on returned results", () => {
    const songs = [makeSong("A", 0.80), makeSong("B", 0.45)];
    const snippets = new Map<string, { lineText: string; lineSimilarity: number }[]>();
    snippets.set("A", [{ lineText: "line A", lineSimilarity: 0.30 }]);
    snippets.set("B", [{ lineText: "line B", lineSimilarity: 0.65 }]);

    const result = rrfRerank(songs, snippets);

    for (const song of result) {
      expect(song.rrfScore).toBeDefined();
      expect(typeof song.rrfScore).toBe("number");
    }
  });

  it("single song: rrfScore = 2/(k+1)", () => {
    const songs = [makeSong("A", 0.80)];
    const snippets = new Map<string, { lineText: string; lineSimilarity: number }[]>();
    snippets.set("A", [{ lineText: "line A", lineSimilarity: 0.50 }]);

    const result = rrfRerank(songs, snippets, 60);

    expect(result).toHaveLength(1);
    expect(result[0].rrfScore).toBeCloseTo(2 / 61, 10);
  });

  it("all songs have equal line similarity: ordering matches song-level rank", () => {
    const songs = [
      makeSong("A", 0.90),
      makeSong("B", 0.70),
      makeSong("C", 0.50),
    ];
    const snippets = new Map<string, { lineText: string; lineSimilarity: number }[]>();
    snippets.set("A", [{ lineText: "line A", lineSimilarity: 0.50 }]);
    snippets.set("B", [{ lineText: "line B", lineSimilarity: 0.50 }]);
    snippets.set("C", [{ lineText: "line C", lineSimilarity: 0.50 }]);

    const result = rrfRerank(songs, snippets);

    expect(result.map((s) => s.id)).toEqual(["A", "B", "C"]);
  });

  it("all songs have equal song similarity: ordering matches line-level rank", () => {
    const songs = [
      makeSong("A", 0.50),
      makeSong("B", 0.50),
      makeSong("C", 0.50),
    ];
    const snippets = new Map<string, { lineText: string; lineSimilarity: number }[]>();
    snippets.set("A", [{ lineText: "line A", lineSimilarity: 0.30 }]);
    snippets.set("B", [{ lineText: "line B", lineSimilarity: 0.65 }]);
    snippets.set("C", [{ lineText: "line C", lineSimilarity: 0.40 }]);

    const result = rrfRerank(songs, snippets);

    expect(result.map((s) => s.id)).toEqual(["B", "A", "C"]);
  });

  it("low line coverage (< 50%): returns songs unchanged, no rrfScore added", () => {
    const songs = [
      makeSong("A", 0.80),
      makeSong("B", 0.45),
      makeSong("C", 0.50),
    ];
    const snippets = new Map<string, { lineText: string; lineSimilarity: number }[]>();
    snippets.set("A", [{ lineText: "line A", lineSimilarity: 0.30 }]);

    const result = rrfRerank(songs, snippets);

    expect(result.map((s) => s.id)).toEqual(["A", "B", "C"]);
    for (const song of result) {
      expect(song.rrfScore).toBeUndefined();
    }
  });

  it("empty songs array: returns empty array", () => {
    const result = rrfRerank([], new Map());
    expect(result).toEqual([]);
  });

  it("all songs have no line embeddings: returns songs unchanged (0% coverage)", () => {
    const songs = [makeSong("A", 0.80), makeSong("B", 0.45)];
    const snippets = new Map<string, { lineText: string; lineSimilarity: number }[]>();

    const result = rrfRerank(songs, snippets);

    expect(result.map((s) => s.id)).toEqual(["A", "B"]);
    for (const song of result) {
      expect(song.rrfScore).toBeUndefined();
    }
  });

  it("exactly 50% line coverage: RRF is applied", () => {
    const songs = [makeSong("A", 0.80), makeSong("B", 0.45)];
    const snippets = new Map<string, { lineText: string; lineSimilarity: number }[]>();
    snippets.set("A", [{ lineText: "line A", lineSimilarity: 0.90 }]);

    const result = rrfRerank(songs, snippets);

    for (const song of result) {
      expect(song.rrfScore).toBeDefined();
    }
  });

  it("uses first line similarity (lines sorted DESC by DB)", () => {
    const songs = [makeSong("A", 0.80)];
    const snippets = new Map<string, { lineText: string; lineSimilarity: number }[]>();
    snippets.set("A", [
      { lineText: "best line", lineSimilarity: 0.90 },
      { lineText: "second line", lineSimilarity: 0.50 },
    ]);

    const result = rrfRerank(songs, snippets, 60);

    expect(result[0].rrfScore).toBeCloseTo(2 / 61, 10);
  });

  it("custom k parameter is respected", () => {
    const songs = [makeSong("A", 0.80)];
    const snippets = new Map<string, { lineText: string; lineSimilarity: number }[]>();
    snippets.set("A", [{ lineText: "line A", lineSimilarity: 0.50 }]);

    const result = rrfRerank(songs, snippets, 10);

    expect(result[0].rrfScore).toBeCloseTo(2 / 11, 10);
  });
});
