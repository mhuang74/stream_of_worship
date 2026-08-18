import { describe, it, expect, beforeEach, vi } from "vitest";
import { DELETE } from "@/app/api/favorites/[songId]/route";
import { auth } from "@/lib/auth";
import { removeFavorite } from "@/lib/db/favorites";
import { NextRequest } from "next/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: vi.fn() } },
}));

vi.mock("@/lib/db/favorites", () => ({
  removeFavorite: vi.fn(),
}));

function mockRequest(url = "http://localhost/api/favorites/s1"): NextRequest {
  return new Request(url, { method: "DELETE" } as RequestInit) as unknown as NextRequest;
}

describe("DELETE /api/favorites/:songId", () => {
  beforeEach(() => vi.clearAllMocks());

  it("returns 401 when unauthenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await DELETE(mockRequest(), { params: Promise.resolve({ songId: "s1" }) });
    expect(res.status).toBe(401);
  });

  it("removes the favorite for the session user", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: "7" } } as any);
    vi.mocked(removeFavorite).mockResolvedValue(undefined);
    const res = await DELETE(mockRequest(), { params: Promise.resolve({ songId: "s1" }) });
    expect(res.status).toBe(200);
    expect(removeFavorite).toHaveBeenCalledWith(7, "s1");
  });
});
