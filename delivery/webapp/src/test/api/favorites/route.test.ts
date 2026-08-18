import { describe, it, expect, beforeEach, vi } from "vitest";
import { GET, POST } from "@/app/api/favorites/route";
import { auth } from "@/lib/auth";
import { getFavoriteSongIds, addFavorite } from "@/lib/db/favorites";
import { NextRequest } from "next/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: vi.fn() } },
}));

vi.mock("@/lib/db/favorites", () => ({
  getFavoriteSongIds: vi.fn(),
  addFavorite: vi.fn(),
}));

function mockRequest(url = "http://localhost/api/favorites", body?: unknown): NextRequest {
  return new Request(url, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  } as RequestInit) as unknown as NextRequest;
}

describe("GET /api/favorites", () => {
  beforeEach(() => vi.clearAllMocks());

  it("returns 401 when unauthenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await GET(mockRequest());
    expect(res.status).toBe(401);
  });

  it("returns the session user's favorite song ids", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: "7" } } as any);
    vi.mocked(getFavoriteSongIds).mockResolvedValue(["s1", "s2"]);
    const res = await GET(mockRequest());
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.songIds).toEqual(["s1", "s2"]);
    expect(getFavoriteSongIds).toHaveBeenCalledWith(7);
  });
});

describe("POST /api/favorites", () => {
  beforeEach(() => vi.clearAllMocks());

  it("returns 401 when unauthenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await POST(mockRequest(undefined, { songId: "s1" }));
    expect(res.status).toBe(401);
  });

  it("adds a favorite for the session user", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: "7" } } as any);
    vi.mocked(addFavorite).mockResolvedValue(undefined);
    const res = await POST(mockRequest(undefined, { songId: "s1" }));
    expect(res.status).toBe(201);
    expect(addFavorite).toHaveBeenCalledWith(7, "s1");
  });

  it("returns 400 on an invalid body", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: "7" } } as any);
    const res = await POST(mockRequest(undefined, {}));
    expect(res.status).toBe(400);
  });
});
