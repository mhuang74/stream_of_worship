import { describe, it, expect, beforeEach, vi } from "vitest";
import { GET, POST } from "@/app/api/songsets/route";
import { auth } from "@/lib/auth";
import { listSongsets, createSongset } from "@/lib/db/songsets";
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
  listSongsets: vi.fn(),
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

    vi.mocked(listSongsets).mockResolvedValue({
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

    vi.mocked(listSongsets).mockResolvedValue({
      songsets: [],
      total: 0,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/songsets?limit=10&offset=5"
    );
    await GET(request);

    expect(listSongsets).toHaveBeenCalledWith(1, 10, 5);
  });

  it("caps limit at 100", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongsets).mockResolvedValue({
      songsets: [],
      total: 0,
    });

    const request = createMockRequest(
      "http://localhost:3000/api/songsets?limit=200"
    );
    await GET(request);

    expect(listSongsets).toHaveBeenCalledWith(1, 100, 0);
  });

  it("returns 500 on error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(listSongsets).mockRejectedValue(new Error("Database error"));

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
