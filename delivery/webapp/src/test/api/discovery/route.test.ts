import { describe, it, expect, beforeEach, vi } from "vitest";
import { GET } from "@/app/api/discovery/route";
import { auth } from "@/lib/auth";
import { listDiscoverySongs } from "@/lib/db/discovery";
import { NextRequest } from "next/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/lib/auth", () => ({
  auth: {
    api: {
      getSession: vi.fn(),
    },
  },
}));

vi.mock("@/lib/db/discovery", () => ({
  listDiscoverySongs: vi.fn(),
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

function makeSong(id: string, overrides?: Partial<Record<string, unknown>>) {
  return {
    id,
    title: `Song ${id}`,
    composer: "Composer",
    lyricist: "Lyricist",
    albumName: "Album",
    musicalKey: "A",
    recordings: [
      {
        contentHash: `hash-${id}`,
        hashPrefix: `pre-${id}`,
        durationSeconds: 180,
        tempoBpm: 120,
        musicalKey: "A",
        visibilityStatus: "published",
        theme: null,
      },
    ],
    ...overrides,
  };
}

describe("GET /api/discovery", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const request = createMockRequest("http://localhost:3000/api/discovery");
    const response = await GET(request);

    expect(response.status).toBe(401);
    const data = await response.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("returns 200 with songs and hasMore, anonymous shape (no user ids, usernames, or songset names)", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listDiscoverySongs).mockResolvedValue({
      songs: [
        {
          ...makeSong("song-1"),
          favoriteCount: 3,
          inclusionCount: 2,
        },
      ],
      hasMore: false,
    });

    const request = createMockRequest("http://localhost:3000/api/discovery");
    const response = await GET(request);

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.songs).toHaveLength(1);
    expect(data.hasMore).toBe(false);
    expect(data.songs[0].favoriteCount).toBe(3);
    expect(data.songs[0].inclusionCount).toBe(2);

    // Anonymous envelope: song card data + aggregates only.
    const serialized = JSON.stringify(data);
    expect(serialized).not.toContain("user");
    expect(serialized).not.toContain("userName");
    expect(serialized).not.toContain("songsetName");
    expect(data.songs[0].memberSongsets).toBeUndefined();
  });

  it("defaults limit 20, offset 0", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);
    vi.mocked(listDiscoverySongs).mockResolvedValue({
      songs: [],
      hasMore: false,
    });

    const request = createMockRequest("http://localhost:3000/api/discovery");
    await GET(request);

    expect(listDiscoverySongs).toHaveBeenCalledWith(1, 20, 0);
  });

  it("caps limit at 100", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);
    vi.mocked(listDiscoverySongs).mockResolvedValue({
      songs: [],
      hasMore: false,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/discovery?limit=200&offset=3"
    );
    await GET(request);

    expect(listDiscoverySongs).toHaveBeenCalledWith(1, 100, 3);
  });

  it("honors explicit limit and offset", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);
    vi.mocked(listDiscoverySongs).mockResolvedValue({
      songs: [],
      hasMore: false,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/discovery?limit=10&offset=25"
    );
    await GET(request);

    expect(listDiscoverySongs).toHaveBeenCalledWith(1, 10, 25);
  });

  it("returns 500 on error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);
    vi.mocked(listDiscoverySongs).mockRejectedValue(new Error("Database error"));

    const request = createMockRequest("http://localhost:3000/api/discovery");
    const response = await GET(request);

    expect(response.status).toBe(500);
    const data = await response.json();
    expect(data.error).toBe("Failed to load discovery songs");
  });
});
