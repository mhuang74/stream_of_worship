import { describe, it, expect, beforeEach, vi } from "vitest";
import { NextRequest } from "next/server";

import { PATCH } from "@/app/api/songsets/[id]/items/route";
import { auth } from "@/lib/auth";
import { GlueSelectionError, updateSongsetItem } from "@/lib/db/songsets";

vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: vi.fn() } },
}));

vi.mock("@/lib/db/songsets", () => {
  class MockGlueSelectionError extends Error {
    readonly code: string;
    readonly status = 409;
    constructor(code: string, message: string) {
      super(message);
      this.name = "GlueSelectionError";
      this.code = code;
    }
  }
  return {
    addSongsetItem: vi.fn(),
    deleteSongsetItem: vi.fn(),
    updateSongsetItem: vi.fn(),
    GlueSelectionError: MockGlueSelectionError,
  };
});

vi.mock("@/db", () => ({ db: {} }));
vi.mock("@/db/mutation-db", () => ({ mutationDb: {} }));

const mockUpdate = vi.mocked(updateSongsetItem);
const params = { params: Promise.resolve({ id: "songset-1" }) };

function patchRequest(body: unknown): NextRequest {
  return {
    headers: new Headers(),
    json: async () => body,
    nextUrl: new URL("http://localhost:3000/api/songsets/songset-1/items"),
  } as unknown as NextRequest;
}

const DETAIL = {
  id: "item-a",
  songId: "song-1",
  recordingHashPrefix: null,
  position: 0,
  gapBeats: null,
  crossfadeEnabled: null,
  crossfadeDurationSeconds: null,
  keyShiftSemitones: null,
  tempoRatio: null,
  updatedAt: "2026-11-22T10:00:00.000Z",
  glueEnabled: true,
  glueBeats: 4,
  glueTakeId: "6b7f9c34-1c2b-4c4d-8a3a-1b2c3d4e5f60",
  glueSeconds: 3,
  glue: {
    fromItemId: "item-a",
    toItemId: "item-b",
    glueEnabled: true,
    glueBeats: 4,
    descriptorKey: "a".repeat(64),
    state: "ready",
    selectedTake: {
      id: "6b7f9c34-1c2b-4c4d-8a3a-1b2c3d4e5f60",
      ordinal: 1,
      seconds: 3,
      r2Key: "glues/take.wav",
      descriptorKey: "a".repeat(64),
      status: "ok",
      reasons: [],
    },
    activeJob: null,
    quote: null,
    quotaLimit: 5,
    quotaUsed: 0,
    quotaRemaining: 5,
    quotaResetAt: null,
    expectedFromUpdatedAt: "2026-11-22T10:00:00.000Z",
    expectedToUpdatedAt: "2026-11-22T10:01:00.000Z",
    unavailableReason: null,
  },
};

describe("PATCH /api/songsets/[id]/items AI-transition selection", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const response = await PATCH(
      patchRequest({ itemId: "item-a", glueEnabled: true }),
      params
    );
    expect(response.status).toBe(401);
  });

  it("rejects an invalid beat choice before touching the database", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as never);
    const response = await PATCH(
      patchRequest({ itemId: "item-a", glueBeats: 3 }),
      params
    );
    expect(response.status).toBe(400);
    expect(mockUpdate).not.toHaveBeenCalled();
  });

  it("returns 404 for an unknown or unowned item", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as never);
    mockUpdate.mockResolvedValue(null);
    const response = await PATCH(
      patchRequest({ itemId: "item-a", glueEnabled: true }),
      params
    );
    expect(response.status).toBe(404);
  });

  it("rejects a stale take with GLUE_STALE_SNAPSHOT", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as never);
    mockUpdate.mockRejectedValue(
      new GlueSelectionError(
        "GLUE_STALE_SNAPSHOT",
        "This AI transition belongs to a different version of the Song sequence."
      )
    );
    const response = await PATCH(
      patchRequest({
        itemId: "item-a",
        glueActiveTakeId: "6b7f9c34-1c2b-4c4d-8a3a-1b2c3d4e5f60",
      }),
      params
    );
    expect(response.status).toBe(409);
    expect((await response.json()).code).toBe("GLUE_STALE_SNAPSHOT");
  });

  it("rejects a cross-user or non-ready take with GLUE_NOT_READY", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as never);
    mockUpdate.mockRejectedValue(
      new GlueSelectionError("GLUE_NOT_READY", "This AI transition is not available.")
    );
    const response = await PATCH(
      patchRequest({
        itemId: "item-a",
        glueActiveTakeId: "6b7f9c34-1c2b-4c4d-8a3a-1b2c3d4e5f60",
      }),
      params
    );
    expect(response.status).toBe(409);
    expect((await response.json()).code).toBe("GLUE_NOT_READY");
  });

  it("passes selection fields through and returns the flat detail", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 7 } } as never);
    mockUpdate.mockResolvedValue(DETAIL as never);
    const body = {
      itemId: "item-a",
      glueEnabled: true,
      glueBeats: 8,
      glueActiveTakeId: "6b7f9c34-1c2b-4c4d-8a3a-1b2c3d4e5f60",
    };
    const response = await PATCH(patchRequest(body), params);
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(DETAIL);
    expect(mockUpdate).toHaveBeenCalledWith("item-a", "songset-1", 7, body);
  });

  it("allows clearing a selection with null", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 7 } } as never);
    mockUpdate.mockResolvedValue(DETAIL as never);
    const body = { itemId: "item-a", glueActiveTakeId: null };
    const response = await PATCH(patchRequest(body), params);
    expect(response.status).toBe(200);
    expect(mockUpdate).toHaveBeenCalledWith("item-a", "songset-1", 7, body);
  });
});
