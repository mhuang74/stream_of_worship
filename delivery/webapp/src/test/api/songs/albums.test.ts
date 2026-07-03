import { describe, it, expect, beforeEach, vi } from "vitest";
import { GET } from "@/app/api/songs/albums/route";
import { auth } from "@/lib/auth";
import { getAlbums } from "@/lib/db/songs";
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
  getAlbums: vi.fn(),
}));

describe("GET /api/songs/albums", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const response = await GET(new Request("http://localhost:3000/api/songs/albums") as NextRequest);

    expect(response.status).toBe(401);
  });

  it("returns distinct album name and series objects", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as any);
    vi.mocked(getAlbums).mockResolvedValue([
      { albumName: "Hymns", albumSeries: "Classic", songCount: 12 },
      { albumName: "Hymns", albumSeries: "Modern", songCount: 5 },
      { albumName: "Worship", albumSeries: null, songCount: 8 },
    ]);

    const response = await GET(new Request("http://localhost:3000/api/songs/albums") as NextRequest);
    const data = await response.json();

    expect(response.status).toBe(200);
    expect(data.albums).toEqual([
      { albumName: "Hymns", albumSeries: "Classic", songCount: 12 },
      { albumName: "Hymns", albumSeries: "Modern", songCount: 5 },
      { albumName: "Worship", albumSeries: null, songCount: 8 },
    ]);
  });
});
