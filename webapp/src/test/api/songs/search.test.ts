import { describe, it, expect, beforeEach, vi } from "vitest";
import { GET } from "@/app/api/songs/search/route";
import { auth } from "@/lib/auth";
import { fullTextSearchSongs } from "@/lib/db/search";
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
  fullTextSearchSongs: vi.fn(),
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

describe("GET /api/songs/search", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const request = createMockRequest("http://localhost:3000/api/songs/search?q=test");
    const response = await GET(request);

    expect(response.status).toBe(401);
    const data = await response.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("returns 400 when query is missing", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/songs/search");
    const response = await GET(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Search query is required");
  });

  it("returns 400 when query is empty", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/songs/search?q=");
    const response = await GET(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Search query is required");
  });

  it("returns search results", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(fullTextSearchSongs).mockResolvedValue({
      songs: [
        {
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
        },
      ],
      total: 1,
    });

    const request = createMockRequest("http://localhost:3000/api/songs/search?q=amazing");
    const response = await GET(request);

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.songs).toHaveLength(1);
    expect(data.total).toBe(1);
    expect(data.songs[0].title).toBe("Amazing Grace");
    expect(data.songs[0].composer).toBe("John Newton");
  });

  it("applies limit and offset from query params", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(fullTextSearchSongs).mockResolvedValue({
      songs: [],
      total: 0,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/songs/search?q=test&limit=10&offset=5"
    );
    await GET(request);

    expect(fullTextSearchSongs).toHaveBeenCalledWith("test", 10, 5, "published");
  });

  it("caps limit at 100", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(fullTextSearchSongs).mockResolvedValue({
      songs: [],
      total: 0,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/songs/search?q=test&limit=200"
    );
    await GET(request);

    expect(fullTextSearchSongs).toHaveBeenCalledWith("test", 100, 0, "published");
  });

  it("defaults to published visibility status", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(fullTextSearchSongs).mockResolvedValue({
      songs: [],
      total: 0,
    });

    const request = createMockRequest("http://localhost:3000/api/songs/search?q=test");
    await GET(request);

    expect(fullTextSearchSongs).toHaveBeenCalledWith("test", 50, 0, "published");
  });

  it("allows overriding visibility status", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(fullTextSearchSongs).mockResolvedValue({
      songs: [],
      total: 0,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/songs/search?q=test&visibilityStatus=all"
    );
    await GET(request);

    expect(fullTextSearchSongs).toHaveBeenCalledWith("test", 50, 0, "all");
  });

  it("returns 500 on error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(fullTextSearchSongs).mockRejectedValue(new Error("Database error"));

    const request = createMockRequest("http://localhost:3000/api/songs/search?q=test");
    const response = await GET(request);

    expect(response.status).toBe(500);
    const data = await response.json();
    expect(data.error).toBe("Failed to search songs");
  });
});
