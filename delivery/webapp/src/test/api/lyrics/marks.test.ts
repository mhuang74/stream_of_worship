import { describe, it, expect, beforeEach, vi } from "vitest";
import { GET, POST, DELETE } from "@/app/api/lyrics/marks/route";
import { auth } from "@/lib/auth";
import { NextRequest } from "next/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: vi.fn() } },
}));

const mockSelect = vi.fn();
const mockInsert = vi.fn();
const mockDelete = vi.fn();

vi.mock("@/db", () => ({
  db: {
    select: (...args: unknown[]) => mockSelect(...args),
    insert: (...args: unknown[]) => mockInsert(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
  },
}));

vi.mock("nanoid", () => ({ nanoid: () => "test-id-123" }));

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

// --------------------------------------------------------------------------
// GET /api/lyrics/marks
// --------------------------------------------------------------------------

describe("GET /api/lyrics/marks", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await GET(makeRequest("http://localhost/api/lyrics/marks?recordingContentHash=abc"));
    expect(res.status).toBe(401);
    const data = await res.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("returns 400 when recordingContentHash is missing", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const res = await GET(makeRequest("http://localhost/api/lyrics/marks"));
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toMatch(/recordingContentHash/);
  });

  it("returns marks array on success", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const mockFrom = vi.fn().mockReturnThis();
    const mockWhere = vi.fn().mockResolvedValue([
      { timestampSeconds: 10.5 },
      { timestampSeconds: 25.0 },
    ]);
    mockSelect.mockReturnValue({ from: mockFrom });
    mockFrom.mockReturnValue({ where: mockWhere });

    const res = await GET(
      makeRequest("http://localhost/api/lyrics/marks?recordingContentHash=hash123")
    );
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.marks).toEqual([10.5, 25.0]);
  });

  it("returns empty array when no marks exist", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const mockFrom = vi.fn().mockReturnThis();
    const mockWhere = vi.fn().mockResolvedValue([]);
    mockSelect.mockReturnValue({ from: mockFrom });
    mockFrom.mockReturnValue({ where: mockWhere });

    const res = await GET(
      makeRequest("http://localhost/api/lyrics/marks?recordingContentHash=hash123")
    );
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.marks).toEqual([]);
  });

  it("returns 500 on database error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const mockFrom = vi.fn().mockReturnThis();
    const mockWhere = vi.fn().mockRejectedValue(new Error("DB error"));
    mockSelect.mockReturnValue({ from: mockFrom });
    mockFrom.mockReturnValue({ where: mockWhere });

    const res = await GET(
      makeRequest("http://localhost/api/lyrics/marks?recordingContentHash=hash123")
    );
    expect(res.status).toBe(500);
  });
});

// --------------------------------------------------------------------------
// POST /api/lyrics/marks
// --------------------------------------------------------------------------

describe("POST /api/lyrics/marks", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await POST(
      makeRequest("http://localhost/api/lyrics/marks", "POST", {
        recordingContentHash: "abc",
        timestampSeconds: 10.5,
      })
    );
    expect(res.status).toBe(401);
  });

  it("returns 400 when recordingContentHash is missing", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const res = await POST(
      makeRequest("http://localhost/api/lyrics/marks", "POST", {
        timestampSeconds: 10.5,
      })
    );
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toMatch(/recordingContentHash/);
  });

  it("returns 400 when timestampSeconds is not a number", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const res = await POST(
      makeRequest("http://localhost/api/lyrics/marks", "POST", {
        recordingContentHash: "abc",
        timestampSeconds: "bad",
      })
    );
    expect(res.status).toBe(400);
  });

  it("returns 201 on success", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const mockValues = vi.fn().mockReturnThis();
    const mockOnConflict = vi.fn().mockResolvedValue(undefined);
    mockInsert.mockReturnValue({ values: mockValues });
    mockValues.mockReturnValue({ onConflictDoNothing: mockOnConflict });

    const res = await POST(
      makeRequest("http://localhost/api/lyrics/marks", "POST", {
        recordingContentHash: "hash123",
        timestampSeconds: 10.5,
      })
    );
    expect(res.status).toBe(201);
    const data = await res.json();
    expect(data.success).toBe(true);
  });

  it("returns 500 on database error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const mockValues = vi.fn().mockReturnThis();
    const mockOnConflict = vi.fn().mockRejectedValue(new Error("DB error"));
    mockInsert.mockReturnValue({ values: mockValues });
    mockValues.mockReturnValue({ onConflictDoNothing: mockOnConflict });

    const res = await POST(
      makeRequest("http://localhost/api/lyrics/marks", "POST", {
        recordingContentHash: "hash123",
        timestampSeconds: 10.5,
      })
    );
    expect(res.status).toBe(500);
  });
});

// --------------------------------------------------------------------------
// DELETE /api/lyrics/marks
// --------------------------------------------------------------------------

describe("DELETE /api/lyrics/marks", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await DELETE(
      makeRequest(
        "http://localhost/api/lyrics/marks?recordingContentHash=abc&timestampSeconds=10.5",
        "DELETE"
      )
    );
    expect(res.status).toBe(401);
  });

  it("returns 400 when recordingContentHash is missing", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const res = await DELETE(
      makeRequest("http://localhost/api/lyrics/marks?timestampSeconds=10.5", "DELETE")
    );
    expect(res.status).toBe(400);
  });

  it("returns 400 when timestampSeconds is missing", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const res = await DELETE(
      makeRequest(
        "http://localhost/api/lyrics/marks?recordingContentHash=abc",
        "DELETE"
      )
    );
    expect(res.status).toBe(400);
  });

  it("returns 400 when timestampSeconds is not a valid number", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const res = await DELETE(
      makeRequest(
        "http://localhost/api/lyrics/marks?recordingContentHash=abc&timestampSeconds=notanumber",
        "DELETE"
      )
    );
    expect(res.status).toBe(400);
  });

  it("returns 200 on success", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const mockWhere = vi.fn().mockResolvedValue(undefined);
    mockDelete.mockReturnValue({ where: mockWhere });

    const res = await DELETE(
      makeRequest(
        "http://localhost/api/lyrics/marks?recordingContentHash=hash123&timestampSeconds=10.5",
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
        "http://localhost/api/lyrics/marks?recordingContentHash=hash123&timestampSeconds=10.5",
        "DELETE"
      )
    );
    expect(res.status).toBe(500);
  });
});
