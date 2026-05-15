import { describe, it, expect, beforeEach, vi } from "vitest";
import { GET } from "@/app/api/songs/[id]/route";
import { auth } from "@/lib/auth";
import { getSong } from "@/lib/db/songs";
import { NextRequest } from "next/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/lib/auth", () => ({
  auth: {
    api: {
      getSession: vi.fn(),
    },
  },
}));

vi.mock("@/lib/db/songs", () => ({
  getSong: vi.fn(),
}));

function createMockRequest(url: string, options?: RequestInit): NextRequest {
  const request = new Request(url, options) as unknown as NextRequest;
  const urlObj = new URL(url);
  Object.defineProperty(request, "nextUrl", {
    value: urlObj,
    writable: false,
  });
  return request;
}

describe("GET /api/songs/[id]", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const request = createMockRequest("http://localhost:3000/api/songs/song-1");
    const response = await GET(request, { params: Promise.resolve({ id: "song-1" }) });

    expect(response.status).toBe(401);
    const data = await response.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("returns song with recordings", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const mockSong = {
      id: "song-1",
      title: "Test Song",
      titlePinyin: null,
      composer: "Test Composer",
      lyricist: "Test Lyricist",
      albumName: "Test Album",
      albumSeries: null,
      musicalKey: "C",
      lyricsRaw: "Test lyrics raw",
      lyricsLines: "Test lyrics lines",
      sections: null,
      sourceUrl: "https://example.com/song",
      createdAt: new Date(),
      updatedAt: new Date(),
      recordings: [
        {
          contentHash: "abc123",
          hashPrefix: "abc",
          originalFilename: "test.mp3",
          durationSeconds: 180,
          tempoBpm: 120,
          musicalKey: "C",
          musicalMode: "major",
          loudnessDb: -14,
          r2AudioUrl: "https://r2.example.com/audio.mp3",
          r2LrcUrl: "https://r2.example.com/lyrics.lrc",
          visibilityStatus: "published",
          analysisStatus: "completed",
        },
      ],
    };

    vi.mocked(getSong).mockResolvedValue(mockSong);

    const request = createMockRequest("http://localhost:3000/api/songs/song-1");
    const response = await GET(request, { params: Promise.resolve({ id: "song-1" }) });

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.id).toBe("song-1");
    expect(data.title).toBe("Test Song");
    expect(data.composer).toBe("Test Composer");
    expect(data.lyricist).toBe("Test Lyricist");
    expect(data.albumName).toBe("Test Album");
    expect(data.lyricsRaw).toBe("Test lyrics raw");
    expect(data.lyricsLines).toBe("Test lyrics lines");
    expect(data.sourceUrl).toBe("https://example.com/song");
    expect(data.recordings).toHaveLength(1);
    expect(data.recordings[0].contentHash).toBe("abc123");
    expect(data.recordings[0].durationSeconds).toBe(180);
    expect(data.recordings[0].tempoBpm).toBe(120);
  });

  it("returns 404 when song not found", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(getSong).mockResolvedValue(null);

    const request = createMockRequest("http://localhost:3000/api/songs/song-1");
    const response = await GET(request, { params: Promise.resolve({ id: "song-1" }) });

    expect(response.status).toBe(404);
    const data = await response.json();
    expect(data.error).toBe("Song not found");
  });

  it("returns 500 on error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(getSong).mockRejectedValue(new Error("Database error"));

    const request = createMockRequest("http://localhost:3000/api/songs/song-1");
    const response = await GET(request, { params: Promise.resolve({ id: "song-1" }) });

    expect(response.status).toBe(500);
    const data = await response.json();
    expect(data.error).toBe("Failed to get song");
  });
});
