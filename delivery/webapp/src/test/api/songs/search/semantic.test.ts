import { describe, it, expect, beforeEach, vi } from "vitest";
import { POST } from "@/app/api/songs/search/semantic/route";
import { auth } from "@/lib/auth";
import { embedQuery } from "@/lib/embedding";
import {
  semanticSearchSongs,
  findTopMatchingLines,
} from "@/lib/db/songs";
import { NextRequest } from "next/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/lib/auth", () => ({
  auth: {
    api: {
      getSession: vi.fn(),
    },
  },
}));

vi.mock("@/lib/embedding", () => ({
  embedQuery: vi.fn(),
  QUERY_MODEL: "text-embedding-3-small",
}));

vi.mock("@/lib/db/songs", () => ({
  semanticSearchSongs: vi.fn(),
  findTopMatchingLines: vi.fn(),
  rrfRerank: vi.fn((songs: any[]) => songs),
}));

function makeRequest(body: unknown): NextRequest {
  return new Request("http://localhost:3000/api/songs/search/semantic", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }) as unknown as NextRequest;
}

const mockEmbedding = Array.from({ length: 1536 }, () => 0.1);

const mockSong = {
  id: "song-1",
  title: "Amazing Grace",
  titlePinyin: null,
  composer: "John Newton",
  lyricist: null,
  albumName: "Hymns",
  albumSeries: null,
  musicalKey: "G",
  createdAt: new Date(),
  updatedAt: new Date(),
  similarity: 0.87,
  modelVersion: "text-embedding-3-small",
  matchingSnippet: null,
  whyThisMatch: [],
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
      r2AudioUrl: null,
      r2LrcUrl: null,
      visibilityStatus: "published",
      analysisStatus: "completed",
    },
  ],
};

const mockSnippets = new Map<string, { lineText: string; lineSimilarity: number }[]>([
  ["song-1", [{ lineText: "Amazing grace how sweet the sound", lineSimilarity: 0.85 }]],
]);

describe("POST /api/songs/search/semantic", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await POST(makeRequest({ query: "grace" }));
    expect(res.status).toBe(401);
    const data = await res.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("returns 400 for invalid JSON", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    const req = new Request("http://localhost:3000/api/songs/search/semantic", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "not-json",
    }) as unknown as NextRequest;
    const res = await POST(req);
    expect(res.status).toBe(400);
  });

  it("returns 400 when query is missing", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    const res = await POST(makeRequest({}));
    expect(res.status).toBe(400);
  });

  it("returns 400 when query is empty", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    const res = await POST(makeRequest({ query: "" }));
    expect(res.status).toBe(400);
  });

  it("returns 503 when OpenAI API fails", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(embedQuery).mockRejectedValue(new Error("OpenAI API error"));

    const res = await POST(makeRequest({ query: "grace" }));
    expect(res.status).toBe(503);
    const data = await res.json();
    expect(data.error).toContain("Semantic search unavailable");
  });

  it("returns search results on success", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(embedQuery).mockResolvedValue(mockEmbedding);
    vi.mocked(semanticSearchSongs).mockResolvedValue([mockSong]);
    vi.mocked(findTopMatchingLines).mockResolvedValue(mockSnippets);

    const res = await POST(makeRequest({ query: "God's faithfulness" }));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.songs).toHaveLength(1);
    expect(data.songs[0].title).toBe("Amazing Grace");
    expect(data.songs[0].similarity).toBe(0.87);
    expect(data.songs[0].matchingSnippet).toBe("Amazing grace how sweet the sound");
    expect(data.songs[0].whyThisMatch).toEqual(["Amazing grace how sweet the sound"]);
    expect(data.query).toBe("God's faithfulness");
    expect(data.total).toBe(1);
  });

  it("embeds query and passes to semanticSearchSongs with model version", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(embedQuery).mockResolvedValue(mockEmbedding);
    vi.mocked(semanticSearchSongs).mockResolvedValue([]);
    vi.mocked(findTopMatchingLines).mockResolvedValue(new Map());

    await POST(makeRequest({ query: "grace" }));
    expect(embedQuery).toHaveBeenCalledWith("grace");
    expect(semanticSearchSongs).toHaveBeenCalledWith(mockEmbedding, "text-embedding-3-small", 40, ["published", "review"]);
  });

  it("respects custom limit", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(embedQuery).mockResolvedValue(mockEmbedding);
    vi.mocked(semanticSearchSongs).mockResolvedValue([]);
    vi.mocked(findTopMatchingLines).mockResolvedValue(new Map());

    await POST(makeRequest({ query: "test", limit: 5 }));
    expect(semanticSearchSongs).toHaveBeenCalledWith(mockEmbedding, "text-embedding-3-small", 10, ["published", "review"]);
  });

  it("validates and forwards optional filters", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(embedQuery).mockResolvedValue(mockEmbedding);
    vi.mocked(semanticSearchSongs).mockResolvedValue([]);
    vi.mocked(findTopMatchingLines).mockResolvedValue(new Map());

    await POST(makeRequest({
      query: "test",
      albums: [" Hymns ", "", "Hymns", "Worship"],
      keys: ["D", "H"],
      bpmRange: "slow",
    }));

    expect(semanticSearchSongs).toHaveBeenCalledWith(
      mockEmbedding,
      "text-embedding-3-small",
      40,
      ["published", "review"],
      { albums: ["Hymns", "Worship"], keys: ["D"], bpmRange: "slow" }
    );
  });

  it("validates and forwards structured album filters", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(embedQuery).mockResolvedValue(mockEmbedding);
    vi.mocked(semanticSearchSongs).mockResolvedValue([]);
    vi.mocked(findTopMatchingLines).mockResolvedValue(new Map());

    await POST(makeRequest({
      query: "test",
      albums: [
        { albumName: " Hymns ", albumSeries: " Classic " },
        { albumName: "Worship", albumSeries: null },
        { albumName: "", albumSeries: "Ignored" },
      ],
      keys: ["D"],
      bpmRange: "slow",
    }));

    expect(semanticSearchSongs).toHaveBeenCalledWith(
      mockEmbedding,
      "text-embedding-3-small",
      40,
      ["published", "review"],
      {
        albumFilters: [
          { albumName: "Hymns", albumSeries: "Classic" },
          { albumName: "Worship", albumSeries: null },
        ],
        albums: undefined,
        keys: ["D"],
        bpmRange: "slow",
      }
    );
  });

  it("returns 400 when limit > 50", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    const res = await POST(makeRequest({ query: "test", limit: 100 }));
    expect(res.status).toBe(400);
  });

  it("returns null snippets when song has no line embeddings", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(embedQuery).mockResolvedValue(mockEmbedding);
    vi.mocked(semanticSearchSongs).mockResolvedValue([mockSong]);
    vi.mocked(findTopMatchingLines).mockResolvedValue(new Map());

    const res = await POST(makeRequest({ query: "grace" }));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.songs[0].matchingSnippet).toBeNull();
    expect(data.songs[0].whyThisMatch).toEqual([]);
  });

  it("returns 500 when DB query fails", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(embedQuery).mockResolvedValue(mockEmbedding);
    vi.mocked(semanticSearchSongs).mockRejectedValue(new Error("DB error"));

    const res = await POST(makeRequest({ query: "test" }));
    expect(res.status).toBe(500);
  });

  it("excludes songs with mismatched model_version from results", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(embedQuery).mockResolvedValue(mockEmbedding);
    vi.mocked(semanticSearchSongs).mockResolvedValue([mockSong]);
    vi.mocked(findTopMatchingLines).mockResolvedValue(mockSnippets);

    const res = await POST(makeRequest({ query: "grace" }));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.songs).toHaveLength(1);
    expect(semanticSearchSongs).toHaveBeenCalledWith(
      mockEmbedding,
      "text-embedding-3-small",
      40,
      ["published", "review"]
    );
  });
});
