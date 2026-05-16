import { describe, it, expect, beforeEach, vi } from "vitest";
import { POST, PATCH, DELETE } from "@/app/api/songsets/[id]/items/route";
import { auth } from "@/lib/auth";
import {
  addSongsetItem,
  updateSongsetItem,
  deleteSongsetItem,
} from "@/lib/db/songsets";
import { NextRequest } from "next/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/lib/auth", () => ({
  auth: {
    api: {
      getSession: vi.fn(),
    },
  },
}));

vi.mock("@/lib/db/songsets", () => ({
  addSongsetItem: vi.fn(),
  updateSongsetItem: vi.fn(),
  deleteSongsetItem: vi.fn(),
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

describe("POST /api/songsets/[id]/items", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const request = createMockRequest(
      "http://localhost:3000/api/songsets/songset-1/items",
      {
        method: "POST",
        body: JSON.stringify({ songId: "song-1", position: 0 }),
      }
    );
    const response = await POST(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(401);
    const data = await response.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("adds item to songset", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const mockItem = {
      id: "item-1",
      songId: "song-1",
      recordingHashPrefix: null,
      position: 0,
      gapBeats: 2.0,
      crossfadeEnabled: 0,
      crossfadeDurationSeconds: null,
      keyShiftSemitones: 0,
      tempoRatio: 1.0,
      song: {
        id: "song-1",
        title: "Test Song",
        composer: null,
        lyricist: null,
        albumName: null,
        musicalKey: null,
      },
      recording: null,
    };

    vi.mocked(addSongsetItem).mockResolvedValue(mockItem);

    const request = createMockRequest(
      "http://localhost:3000/api/songsets/songset-1/items",
      {
        method: "POST",
        body: JSON.stringify({ songId: "song-1", position: 0 }),
      }
    );
    const response = await POST(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(201);
    const data = await response.json();
    expect(data.songId).toBe("song-1");
  });

  it("adds item with all transition parameters", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const mockItem = {
      id: "item-1",
      songId: "song-1",
      recordingHashPrefix: "abc123",
      position: 0,
      gapBeats: 4.0,
      crossfadeEnabled: 1,
      crossfadeDurationSeconds: 2.0,
      keyShiftSemitones: 2,
      tempoRatio: 1.1,
      song: {
        id: "song-1",
        title: "Test Song",
        composer: null,
        lyricist: null,
        albumName: null,
        musicalKey: null,
      },
      recording: null,
    };

    vi.mocked(addSongsetItem).mockResolvedValue(mockItem);

    const request = createMockRequest(
      "http://localhost:3000/api/songsets/songset-1/items",
      {
        method: "POST",
        body: JSON.stringify({
          songId: "song-1",
          recordingHashPrefix: "abc123",
          position: 0,
          gapBeats: 4.0,
          crossfadeEnabled: 1,
          crossfadeDurationSeconds: 2.0,
          keyShiftSemitones: 2,
          tempoRatio: 1.1,
        }),
      }
    );
    const response = await POST(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(201);
    const data = await response.json();
    expect(data.gapBeats).toBe(4.0);
    expect(data.crossfadeEnabled).toBe(1);
  });

  it("returns 400 when songId is missing", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest(
      "http://localhost:3000/api/songsets/songset-1/items",
      {
        method: "POST",
        body: JSON.stringify({ position: 0 }),
      }
    );
    const response = await POST(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
  });

  it("returns 400 when position is negative", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest(
      "http://localhost:3000/api/songsets/songset-1/items",
      {
        method: "POST",
        body: JSON.stringify({ songId: "song-1", position: -1 }),
      }
    );
    const response = await POST(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
  });

  it("returns 400 when keyShiftSemitones is out of range", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest(
      "http://localhost:3000/api/songsets/songset-1/items",
      {
        method: "POST",
        body: JSON.stringify({ songId: "song-1", position: 0, keyShiftSemitones: 13 }),
      }
    );
    const response = await POST(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
  });

  it("returns 400 when tempoRatio is not positive", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest(
      "http://localhost:3000/api/songsets/songset-1/items",
      {
        method: "POST",
        body: JSON.stringify({ songId: "song-1", position: 0, tempoRatio: 0 }),
      }
    );
    const response = await POST(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
  });

  it("returns 404 when songset not found", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(addSongsetItem).mockResolvedValue(null);

    const request = createMockRequest(
      "http://localhost:3000/api/songsets/songset-1/items",
      {
        method: "POST",
        body: JSON.stringify({ songId: "song-1", position: 0 }),
      }
    );
    const response = await POST(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(404);
    const data = await response.json();
    expect(data.error).toBe("Songset not found");
  });

  it("returns 500 on error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(addSongsetItem).mockRejectedValue(new Error("Database error"));

    const request = createMockRequest(
      "http://localhost:3000/api/songsets/songset-1/items",
      {
        method: "POST",
        body: JSON.stringify({ songId: "song-1", position: 0 }),
      }
    );
    const response = await POST(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(500);
    const data = await response.json();
    expect(data.error).toBe("Failed to add songset item");
  });
});

describe("PATCH /api/songsets/[id]/items", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const request = createMockRequest(
      "http://localhost:3000/api/songsets/songset-1/items",
      {
        method: "PATCH",
        body: JSON.stringify({ itemId: "item-1", position: 1 }),
      }
    );
    const response = await PATCH(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(401);
    const data = await response.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("updates songset item", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const mockItem = {
      id: "item-1",
      songId: "song-1",
      recordingHashPrefix: null,
      position: 1,
      gapBeats: 2.0,
      crossfadeEnabled: 0,
      crossfadeDurationSeconds: null,
      keyShiftSemitones: 0,
      tempoRatio: 1.0,
      song: {
        id: "song-1",
        title: "Test Song",
        composer: null,
        lyricist: null,
        albumName: null,
        musicalKey: null,
      },
      recording: null,
    };

    vi.mocked(updateSongsetItem).mockResolvedValue(mockItem);

    const request = createMockRequest(
      "http://localhost:3000/api/songsets/songset-1/items",
      {
        method: "PATCH",
        body: JSON.stringify({ itemId: "item-1", position: 1 }),
      }
    );
    const response = await PATCH(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.position).toBe(1);
  });

  it("returns 400 when itemId is missing", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest(
      "http://localhost:3000/api/songsets/songset-1/items",
      {
        method: "PATCH",
        body: JSON.stringify({ position: 1 }),
      }
    );
    const response = await PATCH(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
  });

  it("returns 400 when position is negative", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest(
      "http://localhost:3000/api/songsets/songset-1/items",
      {
        method: "PATCH",
        body: JSON.stringify({ itemId: "item-1", position: -1 }),
      }
    );
    const response = await PATCH(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
  });

  it("returns 404 when item not found", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(updateSongsetItem).mockResolvedValue(null);

    const request = createMockRequest(
      "http://localhost:3000/api/songsets/songset-1/items",
      {
        method: "PATCH",
        body: JSON.stringify({ itemId: "item-1", position: 1 }),
      }
    );
    const response = await PATCH(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(404);
    const data = await response.json();
    expect(data.error).toBe("Songset item not found");
  });

  it("returns 500 on error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(updateSongsetItem).mockRejectedValue(new Error("Database error"));

    const request = createMockRequest(
      "http://localhost:3000/api/songsets/songset-1/items",
      {
        method: "PATCH",
        body: JSON.stringify({ itemId: "item-1", position: 1 }),
      }
    );
    const response = await PATCH(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(500);
    const data = await response.json();
    expect(data.error).toBe("Failed to update songset item");
  });
});

describe("DELETE /api/songsets/[id]/items", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const request = createMockRequest(
      "http://localhost:3000/api/songsets/songset-1/items?itemId=item-1",
      {
        method: "DELETE",
      }
    );
    const response = await DELETE(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(401);
    const data = await response.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("deletes songset item", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(deleteSongsetItem).mockResolvedValue(true);

    const request = createMockRequest(
      "http://localhost:3000/api/songsets/songset-1/items?itemId=item-1",
      {
        method: "DELETE",
      }
    );
    const response = await DELETE(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.success).toBe(true);
  });

  it("returns 400 when itemId is missing", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest(
      "http://localhost:3000/api/songsets/songset-1/items",
      {
        method: "DELETE",
      }
    );
    const response = await DELETE(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("itemId is required");
  });

  it("returns 404 when item not found", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(deleteSongsetItem).mockResolvedValue(false);

    const request = createMockRequest(
      "http://localhost:3000/api/songsets/songset-1/items?itemId=item-1",
      {
        method: "DELETE",
      }
    );
    const response = await DELETE(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(404);
    const data = await response.json();
    expect(data.error).toBe("Songset item not found");
  });

  it("returns 500 on error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(deleteSongsetItem).mockRejectedValue(new Error("Database error"));

    const request = createMockRequest(
      "http://localhost:3000/api/songsets/songset-1/items?itemId=item-1",
      {
        method: "DELETE",
      }
    );
    const response = await DELETE(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(500);
    const data = await response.json();
    expect(data.error).toBe("Failed to delete songset item");
  });
});
