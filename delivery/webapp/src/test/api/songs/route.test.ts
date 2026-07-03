import { describe, it, expect, beforeEach, vi } from "vitest";
import { GET } from "@/app/api/songs/route";
import { auth } from "@/lib/auth";
import { listSongs } from "@/lib/db/songs";
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
  listSongs: vi.fn(),
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

describe("GET /api/songs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const request = createMockRequest("http://localhost:3000/api/songs");
    const response = await GET(request);

    expect(response.status).toBe(401);
    const data = await response.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("returns paginated songs", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongs).mockResolvedValue({
      songs: [
        {
          id: "song-1",
          title: "Test Song",
          titlePinyin: null,
          composer: "Test Composer",
          lyricist: "Test Lyricist",
          albumName: "Test Album",
          albumSeries: null,
          musicalKey: "C",
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
        },
      ],
      total: 1,
    });

    const request = createMockRequest("http://localhost:3000/api/songs");
    const response = await GET(request);

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.songs).toHaveLength(1);
    expect(data.total).toBe(1);
    expect(data.songs[0].title).toBe("Test Song");
    expect(data.songs[0].recordings).toHaveLength(1);
  });

  it("applies limit and offset from query params", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongs).mockResolvedValue({
      songs: [],
      total: 0,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/songs?limit=10&offset=5"
    );
    await GET(request);

    expect(listSongs).toHaveBeenCalledWith(10, 5, expect.any(Object));
  });

  it("caps limit at 100", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongs).mockResolvedValue({
      songs: [],
      total: 0,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/songs?limit=200"
    );
    await GET(request);

    expect(listSongs).toHaveBeenCalledWith(100, 0, expect.any(Object));
  });

  it("applies albumName filter", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongs).mockResolvedValue({
      songs: [],
      total: 0,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/songs?albumName=Test%20Album"
    );
    await GET(request);

    expect(listSongs).toHaveBeenCalledWith(
      50,
      0,
      expect.objectContaining({ albumNames: ["Test Album"] })
    );
  });

  it("applies repeated albumName filters", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongs).mockResolvedValue({
      songs: [],
      total: 0,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/songs?albumName=Hymns&albumName=Worship"
    );
    await GET(request);

    expect(listSongs).toHaveBeenCalledWith(
      50,
      0,
      expect.objectContaining({ albumNames: ["Hymns", "Worship"] })
    );
  });

  it("trims, de-dupes, and ignores empty albumName filters", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongs).mockResolvedValue({
      songs: [],
      total: 0,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/songs?albumName=%20Hymns%20,,Hymns,Worship"
    );
    await GET(request);

    expect(listSongs).toHaveBeenCalledWith(
      50,
      0,
      expect.objectContaining({ albumNames: ["Hymns", "Worship"] })
    );
  });

  it("applies structured album name and series filters", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongs).mockResolvedValue({
      songs: [],
      total: 0,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/songs?albumName=Hymns&albumSeries=Classic&albumName=Worship&albumSeries="
    );
    await GET(request);

    expect(listSongs).toHaveBeenCalledWith(
      50,
      0,
      expect.objectContaining({
        albumFilters: [
          { albumName: "Hymns", albumSeries: "Classic" },
          { albumName: "Worship", albumSeries: null },
        ],
      })
    );
  });

  it("applies composer filter", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongs).mockResolvedValue({
      songs: [],
      total: 0,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/songs?composer=Test%20Composer"
    );
    await GET(request);

    expect(listSongs).toHaveBeenCalledWith(
      50,
      0,
      expect.objectContaining({ composer: "Test Composer" })
    );
  });

  it("defaults to published + review visibility status", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongs).mockResolvedValue({
      songs: [],
      total: 0,
    });

    const request = createMockRequest("http://localhost:3000/api/songs");
    await GET(request);

    expect(listSongs).toHaveBeenCalledWith(
      50,
      0,
      expect.objectContaining({ visibilityStatus: ["published", "review"] })
    );
  });

  it("allows overriding visibility status", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongs).mockResolvedValue({
      songs: [],
      total: 0,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/songs?visibilityStatus=all"
    );
    await GET(request);

    expect(listSongs).toHaveBeenCalledWith(
      50,
      0,
      expect.objectContaining({ visibilityStatus: "all" })
    );
  });

  it("parses comma-separated visibility statuses", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongs).mockResolvedValue({
      songs: [],
      total: 0,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/songs?visibilityStatus=published,review"
    );
    await GET(request);

    expect(listSongs).toHaveBeenCalledWith(
      50,
      0,
      expect.objectContaining({ visibilityStatus: ["published", "review"] })
    );
  });

  it("parses keys filter param", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongs).mockResolvedValue({
      songs: [],
      total: 0,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/songs?keys=D,A"
    );
    await GET(request);

    expect(listSongs).toHaveBeenCalledWith(
      50,
      0,
      expect.objectContaining({ keys: ["D", "A"] })
    );
  });

  it("parses bpmRange filter param", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongs).mockResolvedValue({
      songs: [],
      total: 0,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/songs?bpmRange=slow"
    );
    await GET(request);

    expect(listSongs).toHaveBeenCalledWith(
      50,
      0,
      expect.objectContaining({ bpmRange: "slow" })
    );
  });

  it("parses combined keys + bpmRange filters", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongs).mockResolvedValue({
      songs: [],
      total: 0,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/songs?keys=D,A&bpmRange=fast"
    );
    await GET(request);

    expect(listSongs).toHaveBeenCalledWith(
      50,
      0,
      expect.objectContaining({ keys: ["D", "A"], bpmRange: "fast" })
    );
  });

  it("filters out invalid pitch classes from keys param", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongs).mockResolvedValue({
      songs: [],
      total: 0,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/songs?keys=D,H,Db"
    );
    await GET(request);

    expect(listSongs).toHaveBeenCalledWith(
      50,
      0,
      expect.objectContaining({ keys: ["D"] })
    );
  });

  it("ignores invalid bpmRange value", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongs).mockResolvedValue({
      songs: [],
      total: 0,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/songs?bpmRange=medium"
    );
    await GET(request);

    expect(listSongs).toHaveBeenCalledWith(
      50,
      0,
      expect.not.objectContaining({ bpmRange: expect.anything() })
    );
  });

  it("returns 500 on error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongs).mockRejectedValue(new Error("Database error"));

    const request = createMockRequest("http://localhost:3000/api/songs");
    const response = await GET(request);

    expect(response.status).toBe(500);
    const data = await response.json();
    expect(data.error).toBe("Failed to list songs");
  });
});
