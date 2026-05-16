import { describe, it, expect, beforeEach, vi } from "vitest";
import { GET, POST, DELETE } from "@/app/api/lyrics/overrides/route";
import { auth } from "@/lib/auth";
import { NextRequest } from "next/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: vi.fn() } },
}));

const mockFindFirst = vi.fn();
const mockInsert = vi.fn();
const mockDelete = vi.fn();

vi.mock("@/db", () => ({
  db: {
    query: {
      userLrcOverrides: { findFirst: (...args: unknown[]) => mockFindFirst(...args) },
    },
    insert: (...args: unknown[]) => mockInsert(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
  },
}));

vi.mock("nanoid", () => ({ nanoid: () => "test-id-456" }));

function makeRequest(url: string, method = "GET", body?: unknown): NextRequest {
  const init: RequestInit = { method };
  if (body) {
    init.body = JSON.stringify(body);
    init.headers = { "Content-Type": "application/json" };
  }
  const request = new Request(url, init) as unknown as NextRequest;
  const urlObj = new URL(url);
  Object.defineProperty(request, "nextUrl", { value: urlObj, writable: false });
  return request;
}

const sessionUser = { user: { id: 42 } };
const sampleLrc = "[00:01.00]Hello world\n[00:05.00]Second line";

// --------------------------------------------------------------------------
// GET /api/lyrics/overrides
// --------------------------------------------------------------------------

describe("GET /api/lyrics/overrides", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await GET(
      makeRequest("http://localhost/api/lyrics/overrides?recordingContentHash=abc")
    );
    expect(res.status).toBe(401);
    const data = await res.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("returns 400 when recordingContentHash is missing", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const res = await GET(makeRequest("http://localhost/api/lyrics/overrides"));
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toMatch(/recordingContentHash/);
  });

  it("returns lrcContent: null when no override exists", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockResolvedValue(undefined);

    const res = await GET(
      makeRequest("http://localhost/api/lyrics/overrides?recordingContentHash=hash123")
    );
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.lrcContent).toBeNull();
  });

  it("returns lrcContent when override exists", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockResolvedValue({ lrcContent: sampleLrc });

    const res = await GET(
      makeRequest("http://localhost/api/lyrics/overrides?recordingContentHash=hash123")
    );
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.lrcContent).toBe(sampleLrc);
  });

  it("returns 500 on database error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockRejectedValue(new Error("DB error"));

    const res = await GET(
      makeRequest("http://localhost/api/lyrics/overrides?recordingContentHash=hash123")
    );
    expect(res.status).toBe(500);
  });
});

// --------------------------------------------------------------------------
// POST /api/lyrics/overrides
// --------------------------------------------------------------------------

describe("POST /api/lyrics/overrides", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await POST(
      makeRequest("http://localhost/api/lyrics/overrides", "POST", {
        recordingContentHash: "abc",
        lrcContent: sampleLrc,
      })
    );
    expect(res.status).toBe(401);
  });

  it("returns 400 when recordingContentHash is missing", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const res = await POST(
      makeRequest("http://localhost/api/lyrics/overrides", "POST", {
        lrcContent: sampleLrc,
      })
    );
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toMatch(/recordingContentHash/);
  });

  it("returns 400 when lrcContent is not a string", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const res = await POST(
      makeRequest("http://localhost/api/lyrics/overrides", "POST", {
        recordingContentHash: "abc",
        lrcContent: 123,
      })
    );
    expect(res.status).toBe(400);
  });

  it("returns 200 on success (upsert)", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const mockValues = vi.fn().mockReturnThis();
    const mockOnConflict = vi.fn().mockResolvedValue(undefined);
    mockInsert.mockReturnValue({ values: mockValues });
    mockValues.mockReturnValue({ onConflictDoUpdate: mockOnConflict });

    const res = await POST(
      makeRequest("http://localhost/api/lyrics/overrides", "POST", {
        recordingContentHash: "hash123",
        lrcContent: sampleLrc,
      })
    );
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.success).toBe(true);
  });

  it("passes correct lrcContent to upsert", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const mockValues = vi.fn().mockReturnThis();
    const mockOnConflict = vi.fn().mockResolvedValue(undefined);
    mockInsert.mockReturnValue({ values: mockValues });
    mockValues.mockReturnValue({ onConflictDoUpdate: mockOnConflict });

    await POST(
      makeRequest("http://localhost/api/lyrics/overrides", "POST", {
        recordingContentHash: "hash123",
        lrcContent: sampleLrc,
      })
    );

    expect(mockValues).toHaveBeenCalledWith(
      expect.objectContaining({
        lrcContent: sampleLrc,
        recordingContentHash: "hash123",
      })
    );
  });

  it("returns 500 on database error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const mockValues = vi.fn().mockReturnThis();
    const mockOnConflict = vi.fn().mockRejectedValue(new Error("DB error"));
    mockInsert.mockReturnValue({ values: mockValues });
    mockValues.mockReturnValue({ onConflictDoUpdate: mockOnConflict });

    const res = await POST(
      makeRequest("http://localhost/api/lyrics/overrides", "POST", {
        recordingContentHash: "hash123",
        lrcContent: sampleLrc,
      })
    );
    expect(res.status).toBe(500);
  });
});

// --------------------------------------------------------------------------
// DELETE /api/lyrics/overrides
// --------------------------------------------------------------------------

describe("DELETE /api/lyrics/overrides", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await DELETE(
      makeRequest(
        "http://localhost/api/lyrics/overrides?recordingContentHash=abc",
        "DELETE"
      )
    );
    expect(res.status).toBe(401);
  });

  it("returns 400 when recordingContentHash is missing", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const res = await DELETE(
      makeRequest("http://localhost/api/lyrics/overrides", "DELETE")
    );
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toMatch(/recordingContentHash/);
  });

  it("returns 200 on success", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const mockWhere = vi.fn().mockResolvedValue(undefined);
    mockDelete.mockReturnValue({ where: mockWhere });

    const res = await DELETE(
      makeRequest(
        "http://localhost/api/lyrics/overrides?recordingContentHash=hash123",
        "DELETE"
      )
    );
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.success).toBe(true);
  });

  it("returns 500 on database error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const mockWhere = vi.fn().mockRejectedValue(new Error("DB error"));
    mockDelete.mockReturnValue({ where: mockWhere });

    const res = await DELETE(
      makeRequest(
        "http://localhost/api/lyrics/overrides?recordingContentHash=hash123",
        "DELETE"
      )
    );
    expect(res.status).toBe(500);
  });
});
