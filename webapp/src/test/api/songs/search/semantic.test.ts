import { describe, it, expect, beforeEach, vi } from "vitest";
import { POST } from "@/app/api/songs/search/semantic/route";
import { auth } from "@/lib/auth";
import { getEmbeddingForRecording, semanticSearchSongs } from "@/lib/db/search";
import { NextRequest } from "next/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/lib/auth", () => ({
  auth: {
    api: {
      getSession: vi.fn(),
    },
  },
}));

vi.mock("@/lib/db/search", () => ({
  getEmbeddingForRecording: vi.fn(),
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
    const res = await POST(makeRequest({ recordingId: "hash123" }));
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

  it("returns 400 when recordingId is missing", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    const res = await POST(makeRequest({}));
    expect(res.status).toBe(400);
  });

  it("returns 400 when recordingId is empty", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    const res = await POST(makeRequest({ recordingId: "" }));
    expect(res.status).toBe(400);
  });

  it("returns 400 when recording has no embedding", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(getEmbeddingForRecording).mockResolvedValue(null);

    const res = await POST(makeRequest({ recordingId: "no-embedding-hash" }));
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toContain("No embedding found");
  });

  it("returns search results on success", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(getEmbeddingForRecording).mockResolvedValue(mockEmbedding);
    vi.mocked(semanticSearchSongs).mockResolvedValue([mockSong]);

    const res = await POST(makeRequest({ recordingId: "abc123" }));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.songs).toHaveLength(1);
    expect(data.songs[0].title).toBe("Amazing Grace");
    expect(data.songs[0].similarity).toBe(0.87);
    expect(data.recordingId).toBe("abc123");
    expect(data.total).toBe(1);
  });

  it("looks up embedding from DB and passes to semanticSearchSongs", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(getEmbeddingForRecording).mockResolvedValue(mockEmbedding);
    vi.mocked(semanticSearchSongs).mockResolvedValue([]);

    await POST(makeRequest({ recordingId: "hash123" }));
    expect(getEmbeddingForRecording).toHaveBeenCalledWith("hash123");
    expect(semanticSearchSongs).toHaveBeenCalledWith(mockEmbedding, 20);
  });

  it("respects custom limit", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(getEmbeddingForRecording).mockResolvedValue(mockEmbedding);
    vi.mocked(semanticSearchSongs).mockResolvedValue([]);

    await POST(makeRequest({ recordingId: "hash123", limit: 10 }));
    expect(semanticSearchSongs).toHaveBeenCalledWith(mockEmbedding, 10);
  });

  it("clamps limit to max 50", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    const res = await POST(makeRequest({ recordingId: "hash123", limit: 100 }));
    expect(res.status).toBe(400);
  });

  it("returns 500 when DB query fails", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(getEmbeddingForRecording).mockResolvedValue(mockEmbedding);
    vi.mocked(semanticSearchSongs).mockRejectedValue(new Error("DB error"));

    const res = await POST(makeRequest({ recordingId: "hash123" }));
    expect(res.status).toBe(500);
  });
});
