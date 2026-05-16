import { describe, it, expect, beforeEach, vi } from "vitest";
import { POST } from "@/app/api/songs/search/semantic/route";
import { auth } from "@/lib/auth";
import { generateEmbedding } from "@/lib/embed/client";
import { semanticSearchSongs } from "@/lib/db/songs";
import { NextRequest } from "next/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/lib/auth", () => ({
  auth: {
    api: {
      getSession: vi.fn(),
    },
  },
}));

vi.mock("@/lib/embed/client", () => ({
  generateEmbedding: vi.fn(),
}));

vi.mock("@/lib/db/songs", () => ({
  semanticSearchSongs: vi.fn(),
}));

function makeRequest(body: unknown): NextRequest {
  return new Request("http://localhost:3000/api/songs/search/semantic", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }) as unknown as NextRequest;
}

const mockEmbedding = Array.from({ length: 1024 }, () => 0.1);

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

describe("POST /api/songs/search/semantic", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await POST(makeRequest({ query: "grace songs" }));
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

  it("returns 400 when query is too long", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    const res = await POST(makeRequest({ query: "a".repeat(501) }));
    expect(res.status).toBe(400);
  });

  it("returns search results on success", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(generateEmbedding).mockResolvedValue(mockEmbedding);
    vi.mocked(semanticSearchSongs).mockResolvedValue([mockSong]);

    const res = await POST(makeRequest({ query: "songs about grace" }));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.songs).toHaveLength(1);
    expect(data.songs[0].title).toBe("Amazing Grace");
    expect(data.songs[0].similarity).toBe(0.87);
    expect(data.query).toBe("songs about grace");
    expect(data.total).toBe(1);
  });

  it("passes embedding to semanticSearchSongs", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(generateEmbedding).mockResolvedValue(mockEmbedding);
    vi.mocked(semanticSearchSongs).mockResolvedValue([]);

    await POST(makeRequest({ query: "worship music" }));
    expect(generateEmbedding).toHaveBeenCalledWith("worship music");
    expect(semanticSearchSongs).toHaveBeenCalledWith(mockEmbedding, 20);
  });

  it("respects custom limit", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(generateEmbedding).mockResolvedValue(mockEmbedding);
    vi.mocked(semanticSearchSongs).mockResolvedValue([]);

    await POST(makeRequest({ query: "praise songs", limit: 10 }));
    expect(semanticSearchSongs).toHaveBeenCalledWith(mockEmbedding, 10);
  });

  it("clamps limit to max 50", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(generateEmbedding).mockResolvedValue(mockEmbedding);
    vi.mocked(semanticSearchSongs).mockResolvedValue([]);

    const res = await POST(makeRequest({ query: "songs", limit: 100 }));
    expect(res.status).toBe(400);
  });

  it("returns 500 when generateEmbedding fails", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(generateEmbedding).mockRejectedValue(new Error("Model error"));

    const res = await POST(makeRequest({ query: "songs" }));
    expect(res.status).toBe(500);
    const data = await res.json();
    expect(data.error).toContain("Model error");
  });

  it("returns 500 when DB query fails", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(generateEmbedding).mockResolvedValue(mockEmbedding);
    vi.mocked(semanticSearchSongs).mockRejectedValue(new Error("DB error"));

    const res = await POST(makeRequest({ query: "songs" }));
    expect(res.status).toBe(500);
  });
});
