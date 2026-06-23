import { describe, it, expect, beforeEach, vi } from "vitest";
import { GET, PATCH, DELETE } from "@/app/api/songsets/[id]/route";
import { auth } from "@/lib/auth";
import { getSongsetEditorData, updateSongset, deleteSongset } from "@/lib/db/songsets";
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
  getSongsetEditorData: vi.fn(),
  updateSongset: vi.fn(),
  deleteSongset: vi.fn(),
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

describe("GET /api/songsets/[id]", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const request = createMockRequest("http://localhost:3000/api/songsets/songset-1");
    const response = await GET(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(401);
    const data = await response.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("returns songset with items", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const mockSongset = {
      id: "songset-1",
      name: "Test Songset",
      description: "Test description",
      createdAt: new Date(),
      updatedAt: new Date(),
      renderState: "unrendered",
      itemCount: 1,
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
      lastCompletedRenderJobId: null,
      items: [
        {
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
        },
      ],
    };

    vi.mocked(getSongsetEditorData).mockResolvedValue(mockSongset);

    const request = createMockRequest("http://localhost:3000/api/songsets/songset-1");
    const response = await GET(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.name).toBe("Test Songset");
    expect(data.items).toHaveLength(1);
  });

  it("returns 404 when songset not found", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(getSongsetEditorData).mockResolvedValue(null);

    const request = createMockRequest("http://localhost:3000/api/songsets/songset-1");
    const response = await GET(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(404);
    const data = await response.json();
    expect(data.error).toBe("Songset not found");
  });

  it("returns 500 on error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(getSongsetEditorData).mockRejectedValue(new Error("Database error"));

    const request = createMockRequest("http://localhost:3000/api/songsets/songset-1");
    const response = await GET(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(500);
    const data = await response.json();
    expect(data.error).toBe("Failed to get songset");
  });
});

describe("PATCH /api/songsets/[id]", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const request = createMockRequest("http://localhost:3000/api/songsets/songset-1", {
      method: "PATCH",
      body: JSON.stringify({ name: "Updated Name" }),
    });
    const response = await PATCH(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(401);
    const data = await response.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("updates songset name", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const mockSongset = {
      id: "songset-1",
      name: "Updated Name",
      description: null,
      createdAt: new Date(),
      updatedAt: new Date(),
      renderState: "unrendered",
      itemCount: 0,
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
      lastCompletedRenderJobId: null,
    };

    vi.mocked(updateSongset).mockResolvedValue(mockSongset);

    const request = createMockRequest("http://localhost:3000/api/songsets/songset-1", {
      method: "PATCH",
      body: JSON.stringify({ name: "Updated Name" }),
    });
    const response = await PATCH(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.name).toBe("Updated Name");
  });

  it("updates songset description", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const mockSongset = {
      id: "songset-1",
      name: "Test Songset",
      description: "Updated description",
      createdAt: new Date(),
      updatedAt: new Date(),
      renderState: "unrendered",
      itemCount: 0,
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
      lastCompletedRenderJobId: null,
    };

    vi.mocked(updateSongset).mockResolvedValue(mockSongset);

    const request = createMockRequest("http://localhost:3000/api/songsets/songset-1", {
      method: "PATCH",
      body: JSON.stringify({ description: "Updated description" }),
    });
    const response = await PATCH(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.description).toBe("Updated description");
  });

  it("returns 400 when name is empty", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/songsets/songset-1", {
      method: "PATCH",
      body: JSON.stringify({ name: "" }),
    });
    const response = await PATCH(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
  });

  it("returns 400 when name exceeds max length", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/songsets/songset-1", {
      method: "PATCH",
      body: JSON.stringify({ name: "a".repeat(256) }),
    });
    const response = await PATCH(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
  });

  it("returns 400 when description exceeds max length", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/songsets/songset-1", {
      method: "PATCH",
      body: JSON.stringify({
        name: "Test Songset",
        description: "a".repeat(1001),
      }),
    });
    const response = await PATCH(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
  });

  it("returns 404 when songset not found", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(updateSongset).mockResolvedValue(null);

    const request = createMockRequest("http://localhost:3000/api/songsets/songset-1", {
      method: "PATCH",
      body: JSON.stringify({ name: "Updated Name" }),
    });
    const response = await PATCH(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(404);
    const data = await response.json();
    expect(data.error).toBe("Songset not found");
  });

  it("returns 500 on error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(updateSongset).mockRejectedValue(new Error("Database error"));

    const request = createMockRequest("http://localhost:3000/api/songsets/songset-1", {
      method: "PATCH",
      body: JSON.stringify({ name: "Updated Name" }),
    });
    const response = await PATCH(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(500);
    const data = await response.json();
    expect(data.error).toBe("Failed to update songset");
  });
});

describe("DELETE /api/songsets/[id]", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const request = createMockRequest("http://localhost:3000/api/songsets/songset-1", {
      method: "DELETE",
    });
    const response = await DELETE(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(401);
    const data = await response.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("deletes songset", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(deleteSongset).mockResolvedValue(true);

    const request = createMockRequest("http://localhost:3000/api/songsets/songset-1", {
      method: "DELETE",
    });
    const response = await DELETE(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.success).toBe(true);
  });

  it("returns 404 when songset not found", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(deleteSongset).mockResolvedValue(false);

    const request = createMockRequest("http://localhost:3000/api/songsets/songset-1", {
      method: "DELETE",
    });
    const response = await DELETE(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(404);
    const data = await response.json();
    expect(data.error).toBe("Songset not found");
  });

  it("returns 500 on error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(deleteSongset).mockRejectedValue(new Error("Database error"));

    const request = createMockRequest("http://localhost:3000/api/songsets/songset-1", {
      method: "DELETE",
    });
    const response = await DELETE(request, { params: { id: "songset-1" } });

    expect(response.status).toBe(500);
    const data = await response.json();
    expect(data.error).toBe("Failed to delete songset");
  });
});
