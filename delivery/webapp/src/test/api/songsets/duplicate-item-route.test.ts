import { describe, it, expect, beforeEach, vi } from "vitest";
import { POST } from "@/app/api/songsets/[id]/items/[itemId]/duplicate/route";
import { auth } from "@/lib/auth";
import { duplicateSongsetItem, SongsetRenderValidationError } from "@/lib/db/songsets";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/lib/auth", () => ({
  auth: {
    api: {
      getSession: vi.fn(),
    },
  },
}));

// The real module pulls in the Neon client at import time; the route test only
// needs its error class, so the data layer is stubbed out.
vi.mock("@/db", () => ({
  db: {
    query: {
      songsets: { findFirst: vi.fn() },
      renderJobs: { findFirst: vi.fn(), findMany: vi.fn() },
      songsetItems: { findFirst: vi.fn(), findMany: vi.fn() },
      userSettings: { findFirst: vi.fn() },
    },
    select: vi.fn(),
    insert: vi.fn(),
    update: vi.fn(),
    delete: vi.fn(),
  },
}));

vi.mock("@/db/mutation-db", () => ({
  mutationDb: { transaction: vi.fn() },
}));

vi.mock("@/lib/db/songsets", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/db/songsets")>();
  return { ...actual, duplicateSongsetItem: vi.fn() };
});

const params = { params: Promise.resolve({ id: "songset-1", itemId: "item-1" }) };

function duplicateRequest(): Request {
  return new Request(
    "http://localhost:3000/api/songsets/songset-1/items/item-1/duplicate",
    { method: "POST" }
  ) as unknown as Request;
}

describe("POST /api/songsets/[id]/items/[itemId]/duplicate", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const response = await POST(duplicateRequest() as any, params);

    expect(response.status).toBe(401);
    expect(await response.json()).toEqual({ error: "Unauthorized" });
    expect(duplicateSongsetItem).not.toHaveBeenCalled();
  });

  it("returns 404 when the item is missing or unowned", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: "1" } } as any);
    vi.mocked(duplicateSongsetItem).mockResolvedValue(null);

    const response = await POST(duplicateRequest() as any, params);

    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ error: "Item not found" });
    expect(duplicateSongsetItem).toHaveBeenCalledWith("item-1", "songset-1", 1);
  });

  it("returns 409 with the validation code when the Songset is full", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: "1" } } as any);
    vi.mocked(duplicateSongsetItem).mockRejectedValue(
      new SongsetRenderValidationError("SONGSET_FULL", "Songset already has maximum of 5 songs")
    );

    const response = await POST(duplicateRequest() as any, params);

    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({
      error: "Songset already has maximum of 5 songs",
      code: "SONGSET_FULL",
    });
  });

  it("returns 201 with the created item", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: "1" } } as any);
    const created = { id: "copied-item-id", songId: "song-1", position: 2 };
    vi.mocked(duplicateSongsetItem).mockResolvedValue(created as any);

    const response = await POST(duplicateRequest() as any, params);

    expect(response.status).toBe(201);
    expect(await response.json()).toEqual(created);
  });
});
