import { describe, it, expect, beforeEach, vi } from "vitest";
import { GET, POST } from "@/app/api/songsets/route";
import { auth } from "@/lib/auth";
import { listSongsetSummaries, createSongset } from "@/lib/db/songsets";
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
  listSongsetSummaries: vi.fn(),
  createSongset: vi.fn(),
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

describe("GET /api/songsets", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const request = createMockRequest("http://localhost:3000/api/songsets");
    const response = await GET(request);

    expect(response.status).toBe(401);
    const data = await response.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("returns paginated songsets", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongsetSummaries).mockResolvedValue({
      songsets: [
        {
          id: "songset-1",
          name: "Test Songset",
          description: null,
          createdAt: new Date(),
          updatedAt: new Date(),
          renderState: "unrendered",
          itemCount: 0,
          latestRenderJobId: null,
          lastFailedRenderJobId: null,
          lastCompletedRenderJobId: null,
        },
      ],
      total: 1,
    });

    const request = createMockRequest("http://localhost:3000/api/songsets");
    const response = await GET(request);

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.songsets).toHaveLength(1);
    expect(data.total).toBe(1);
  });

  it("applies limit and offset from query params", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongsetSummaries).mockResolvedValue({
      songsets: [],
      total: 0,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/songsets?limit=10&offset=5"
    );
    await GET(request);

    expect(listSongsetSummaries).toHaveBeenCalledWith(1, 10, 5, undefined);
  });

  it("caps limit at 100", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongsetSummaries).mockResolvedValue({
      songsets: [],
      total: 0,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/songsets?limit=200"
    );
    await GET(request);

    expect(listSongsetSummaries).toHaveBeenCalledWith(1, 100, 0, undefined);
  });

  it("returns 500 on error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongsetSummaries).mockRejectedValue(new Error("Database error"));

    const request = createMockRequest("http://localhost:3000/api/songsets");
    const response = await GET(request);

    expect(response.status).toBe(500);
    const data = await response.json();
    expect(data.error).toBe("Failed to list songsets");
  });
});

describe("POST /api/songsets", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const request = createMockRequest("http://localhost:3000/api/songsets", {
      method: "POST",
      body: JSON.stringify({ name: "Test Songset" }),
    });
    const response = await POST(request);

    expect(response.status).toBe(401);
    const data = await response.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("creates songset with valid input", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const mockSongset = {
      id: "songset-1",
      name: "Test Songset",
      description: null,
      createdAt: new Date(),
      updatedAt: new Date(),
      renderState: "unrendered",
      itemCount: 0,
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
      lastCompletedRenderJobId: null,
    };

    vi.mocked(createSongset).mockResolvedValue(mockSongset);

    const request = createMockRequest("http://localhost:3000/api/songsets", {
      method: "POST",
      body: JSON.stringify({ name: "Test Songset" }),
    });
    const response = await POST(request);

    expect(response.status).toBe(201);
    const data = await response.json();
    expect(data.name).toBe("Test Songset");
  });

  it("creates songset with description", async () => {
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
      itemCount: 0,
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
      lastCompletedRenderJobId: null,
    };

    vi.mocked(createSongset).mockResolvedValue(mockSongset);

    const request = createMockRequest("http://localhost:3000/api/songsets", {
      method: "POST",
      body: JSON.stringify({
        name: "Test Songset",
        description: "Test description",
      }),
    });
    const response = await POST(request);

    expect(response.status).toBe(201);
    const data = await response.json();
    expect(data.description).toBe("Test description");
  });

  it("returns 400 when name is missing", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/songsets", {
      method: "POST",
      body: JSON.stringify({}),
    });
    const response = await POST(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
  });

  it("returns 400 when name is empty", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/songsets", {
      method: "POST",
      body: JSON.stringify({ name: "" }),
    });
    const response = await POST(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
  });

  it("returns 400 when name exceeds max length", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/songsets", {
      method: "POST",
      body: JSON.stringify({ name: "a".repeat(256) }),
    });
    const response = await POST(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
  });

  it("returns 400 when description exceeds max length", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/songsets", {
      method: "POST",
      body: JSON.stringify({
        name: "Test Songset",
        description: "a".repeat(1001),
      }),
    });
    const response = await POST(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
  });

  it("passes songIds through and returns itemCount matching songIds.length", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const mockSongset = {
      id: "songset-1",
      name: "Test Songset",
      description: null,
      createdAt: new Date(),
      updatedAt: new Date(),
      renderState: "unrendered",
      itemCount: 3,
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
      lastCompletedRenderJobId: null,
    };
    vi.mocked(createSongset).mockResolvedValue(mockSongset);

    const request = createMockRequest("http://localhost:3000/api/songsets", {
      method: "POST",
      body: JSON.stringify({
        name: "Test Songset",
        songIds: ["song-1", "song-2", "song-3"],
      }),
    });
    const response = await POST(request);

    expect(response.status).toBe(201);
    expect(createSongset).toHaveBeenCalledWith(1, {
      name: "Test Songset",
      description: undefined,
      songIds: ["song-1", "song-2", "song-3"],
    });
    const data = await response.json();
    expect(data.itemCount).toBe(3);
  });

  it("returns 400 when songIds is not an array of strings", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/songsets", {
      method: "POST",
      body: JSON.stringify({ name: "Test Songset", songIds: [1, 2] }),
    });
    const response = await POST(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
  });

  it("returns 400 when songIds exceeds the max", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/songsets", {
      method: "POST",
      body: JSON.stringify({
        name: "Test Songset",
        // SONGSET_MAX_SONGS is 50 (src/lib/constants.ts); exceed it to trip zod.
        songIds: Array.from({ length: 51 }, (_, i) => `song-${i}`),
      }),
    });
    const response = await POST(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
  });

  it("returns 400 when a songIds entry is empty", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/songsets", {
      method: "POST",
      body: JSON.stringify({ name: "Test Songset", songIds: ["song-1", ""] }),
    });
    const response = await POST(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
  });

  it("returns 400 with JSON error when a songId does not exist", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    // createSongset returns a CreateSongsetErrorResult instead of throwing.
    vi.mocked(createSongset).mockResolvedValue({
      error: "Unknown song id(s): song-999",
      status: 400,
    });

    const request = createMockRequest("http://localhost:3000/api/songsets", {
      method: "POST",
      body: JSON.stringify({
        name: "Test Songset",
        songIds: ["song-999"],
      }),
    });
    const response = await POST(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Unknown song id(s): song-999");
  });

  it("creates songset without songIds (backward compat)", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const mockSongset = {
      id: "songset-1",
      name: "Test Songset",
      description: null,
      createdAt: new Date(),
      updatedAt: new Date(),
      renderState: "unrendered",
      itemCount: 0,
      latestRenderJobId: null,
      lastFailedRenderJobId: null,
      lastCompletedRenderJobId: null,
    };
    vi.mocked(createSongset).mockResolvedValue(mockSongset);

    const request = createMockRequest("http://localhost:3000/api/songsets", {
      method: "POST",
      body: JSON.stringify({ name: "Test Songset" }),
    });
    const response = await POST(request);

    expect(response.status).toBe(201);
    expect(createSongset).toHaveBeenCalledWith(1, {
      name: "Test Songset",
      description: undefined,
      songIds: undefined,
    });
    const data = await response.json();
    expect(data.itemCount).toBe(0);
  });

  it("returns 500 on error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(createSongset).mockRejectedValue(new Error("Database error"));

    const request = createMockRequest("http://localhost:3000/api/songsets", {
      method: "POST",
      body: JSON.stringify({ name: "Test Songset" }),
    });
    const response = await POST(request);

    expect(response.status).toBe(500);
    const data = await response.json();
    expect(data.error).toBe("Failed to create songset");
  });
});
