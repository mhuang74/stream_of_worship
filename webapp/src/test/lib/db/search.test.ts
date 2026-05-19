import { describe, it, expect, beforeEach, vi } from "vitest";
import { fullTextSearchSongs, getEmbeddingForRecording } from "@/lib/db/search";
import { db } from "@/db";

vi.mock("@/db", () => ({
  db: {
    select: vi.fn(),
    from: vi.fn(),
    where: vi.fn(),
    query: {
      songs: {
        findMany: vi.fn(),
      },
    },
  },
}));

vi.mock("@/db/schema", () => ({
  songs: {
    id: "id",
    searchVector: "search_vector",
    deletedAt: "deleted_at",
  },
  recordings: {},
  songEmbeddings: {
    embedding: "embedding",
    recordingContentHash: "recording_content_hash",
  },
}));

describe("fullTextSearchSongs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("calls db with tsvector query for Chinese characters", async () => {
    const mockFindMany = vi.fn().mockResolvedValue([]);
    const mockSelect = vi.fn().mockReturnValue({
      from: vi.fn().mockReturnValue({
        where: vi.fn().mockResolvedValue([{ count: 0 }]),
      }),
    });

    (db.select as ReturnType<typeof vi.fn>) = mockSelect;
    (db.query.songs.findMany as ReturnType<typeof vi.fn>) = mockFindMany;

    await fullTextSearchSongs("恩典", 50, 0, "published");

    expect(mockFindMany).toHaveBeenCalled();
  });

  it("calls db with tsvector query for pinyin", async () => {
    const mockFindMany = vi.fn().mockResolvedValue([]);
    const mockSelect = vi.fn().mockReturnValue({
      from: vi.fn().mockReturnValue({
        where: vi.fn().mockResolvedValue([{ count: 0 }]),
      }),
    });

    (db.select as ReturnType<typeof vi.fn>) = mockSelect;
    (db.query.songs.findMany as ReturnType<typeof vi.fn>) = mockFindMany;

    await fullTextSearchSongs("en dian", 50, 0, "published");

    expect(mockFindMany).toHaveBeenCalled();
  });

  it("returns songs with recordings from search results", async () => {
    const mockSong = {
      id: "song-1",
      title: "奇妙恩典",
      titlePinyin: "qi miao en dian",
      composer: "John Newton",
      lyricist: null,
      albumName: "Hymns",
      albumSeries: null,
      musicalKey: "G",
      createdAt: new Date(),
      updatedAt: new Date(),
      recordings: [
        {
          contentHash: "abc123",
          hashPrefix: "abc",
          originalFilename: "amazing_grace.mp3",
          durationSeconds: 240,
          tempoBpm: 72,
          musicalKey: "G",
          musicalMode: "major",
          loudnessDb: -14,
          r2AudioUrl: "https://r2.example.com/audio.mp3",
          r2LrcUrl: "https://r2.example.com/lyrics.lrc",
          visibilityStatus: "published",
          analysisStatus: "completed",
        },
      ],
    };

    const mockFindMany = vi.fn().mockResolvedValue([mockSong]);
    const mockSelect = vi.fn().mockReturnValue({
      from: vi.fn().mockReturnValue({
        where: vi.fn().mockResolvedValue([{ count: 1 }]),
      }),
    });

    (db.select as ReturnType<typeof vi.fn>) = mockSelect;
    (db.query.songs.findMany as ReturnType<typeof vi.fn>) = mockFindMany;

    const result = await fullTextSearchSongs("恩典", 50, 0, "published");

    expect(result.songs).toHaveLength(1);
    expect(result.songs[0].title).toBe("奇妙恩典");
    expect(result.songs[0].recordings).toHaveLength(1);
    expect(result.total).toBe(1);
  });

  it("returns empty results for non-matching query", async () => {
    const mockFindMany = vi.fn().mockResolvedValue([]);
    const mockSelect = vi.fn().mockReturnValue({
      from: vi.fn().mockReturnValue({
        where: vi.fn().mockResolvedValue([{ count: 0 }]),
      }),
    });

    (db.select as ReturnType<typeof vi.fn>) = mockSelect;
    (db.query.songs.findMany as ReturnType<typeof vi.fn>) = mockFindMany;

    const result = await fullTextSearchSongs("nonexistent", 50, 0, "published");

    expect(result.songs).toHaveLength(0);
    expect(result.total).toBe(0);
  });

  it("respects limit and offset parameters", async () => {
    const mockFindMany = vi.fn().mockResolvedValue([]);
    const mockSelect = vi.fn().mockReturnValue({
      from: vi.fn().mockReturnValue({
        where: vi.fn().mockResolvedValue([{ count: 0 }]),
      }),
    });

    (db.select as ReturnType<typeof vi.fn>) = mockSelect;
    (db.query.songs.findMany as ReturnType<typeof vi.fn>) = mockFindMany;

    await fullTextSearchSongs("test", 10, 5, "published");

    expect(mockFindMany).toHaveBeenCalled();
    const callArgs = mockFindMany.mock.calls[0][0];
    expect(callArgs.limit).toBe(10);
    expect(callArgs.offset).toBe(5);
  });

  it("handles visibilityStatus=all without filtering", async () => {
    const mockFindMany = vi.fn().mockResolvedValue([]);
    const mockSelect = vi.fn().mockReturnValue({
      from: vi.fn().mockReturnValue({
        where: vi.fn().mockResolvedValue([{ count: 0 }]),
      }),
    });

    (db.select as ReturnType<typeof vi.fn>) = mockSelect;
    (db.query.songs.findMany as ReturnType<typeof vi.fn>) = mockFindMany;

    await fullTextSearchSongs("test", 50, 0, "all");

    expect(mockFindMany).toHaveBeenCalled();
  });

  it("handles missing visibilityStatus", async () => {
    const mockFindMany = vi.fn().mockResolvedValue([]);
    const mockSelect = vi.fn().mockReturnValue({
      from: vi.fn().mockReturnValue({
        where: vi.fn().mockResolvedValue([{ count: 0 }]),
      }),
    });

    (db.select as ReturnType<typeof vi.fn>) = mockSelect;
    (db.query.songs.findMany as ReturnType<typeof vi.fn>) = mockFindMany;

    await fullTextSearchSongs("test", 50, 0);

    expect(mockFindMany).toHaveBeenCalled();
  });
});

describe("getEmbeddingForRecording", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns parsed embedding array when found", async () => {
    const mockEmbedding = Array.from({ length: 1024 }, () => 0.1);
    const mockFrom = vi.fn().mockReturnValue({
      where: vi.fn().mockReturnValue({
        limit: vi.fn().mockResolvedValue([
          { embedding: JSON.stringify(mockEmbedding) },
        ]),
      }),
    });
    const mockSelect = vi.fn().mockReturnValue({ from: mockFrom });

    (db.select as ReturnType<typeof vi.fn>) = mockSelect;

    const result = await getEmbeddingForRecording("hash123");

    expect(result).toEqual(mockEmbedding);
  });

  it("returns null when no embedding found", async () => {
    const mockFrom = vi.fn().mockReturnValue({
      where: vi.fn().mockReturnValue({
        limit: vi.fn().mockResolvedValue([]),
      }),
    });
    const mockSelect = vi.fn().mockReturnValue({ from: mockFrom });

    (db.select as ReturnType<typeof vi.fn>) = mockSelect;

    const result = await getEmbeddingForRecording("nonexistent");

    expect(result).toBeNull();
  });

  it("returns null when embedding is null", async () => {
    const mockFrom = vi.fn().mockReturnValue({
      where: vi.fn().mockReturnValue({
        limit: vi.fn().mockResolvedValue([{ embedding: null }]),
      }),
    });
    const mockSelect = vi.fn().mockReturnValue({ from: mockFrom });

    (db.select as ReturnType<typeof vi.fn>) = mockSelect;

    const result = await getEmbeddingForRecording("hash123");

    expect(result).toBeNull();
  });

  it("returns null when embedding is not valid JSON", async () => {
    const mockFrom = vi.fn().mockReturnValue({
      where: vi.fn().mockReturnValue({
        limit: vi.fn().mockResolvedValue([{ embedding: "not-json" }]),
      }),
    });
    const mockSelect = vi.fn().mockReturnValue({ from: mockFrom });

    (db.select as ReturnType<typeof vi.fn>) = mockSelect;

    const result = await getEmbeddingForRecording("hash123");

    expect(result).toBeNull();
  });

  it("returns null when parsed embedding is not an array", async () => {
    const mockFrom = vi.fn().mockReturnValue({
      where: vi.fn().mockReturnValue({
        limit: vi.fn().mockResolvedValue([{ embedding: '{"key": "value"}' }]),
      }),
    });
    const mockSelect = vi.fn().mockReturnValue({ from: mockFrom });

    (db.select as ReturnType<typeof vi.fn>) = mockSelect;

    const result = await getEmbeddingForRecording("hash123");

    expect(result).toBeNull();
  });
});
