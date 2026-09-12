import { describe, it, expect, beforeEach, vi } from "vitest";
import { GET, PUT, DELETE } from "@/app/api/lyrics/feedback/[recordingContentHash]/route";
import { auth } from "@/lib/auth";
import { NextRequest } from "next/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: vi.fn() } },
}));

const mockFindFirst = vi.fn();
const mockInsert = vi.fn();
const mockDelete = vi.fn();
const mockUpdate = vi.fn();

vi.mock("@/db", () => ({
  db: {
    query: {
      lyricsFeedback: { findFirst: (...args: unknown[]) => mockFindFirst(...args) },
    },
    insert: (...args: unknown[]) => mockInsert(...args),
    delete: (...args: unknown[]) => mockDelete(...args),
    update: (...args: unknown[]) => mockUpdate(...args),
  },
}));

vi.mock("@/lib/lyrics/situation", async () => {
  const actual = await vi.importActual<typeof import("@/lib/lyrics/situation")>(
    "@/lib/lyrics/situation"
  );
  return {
    ...actual,
    resolveLyricsSituation: (...args: unknown[]) => mockResolveLyricsSituation(...args),
    recordingExists: (...args: unknown[]) =>
      (globalThis as Record<string, unknown>).__mockRecordingExists?.(...args) ??
      Promise.resolve(true),
  };
});

function mockResolveLyricsSituation(...args: unknown[]): unknown {
  return (globalThis as Record<string, unknown>).__mockResolveLyricsSituation?.(...args);
}
const sessionUser = { user: { id: 42 } };

function makeRequest(
  contentHash: string,
  method = "GET",
  body?: unknown
): NextRequest {
  const url = `http://localhost/api/lyrics/feedback/${contentHash}`;
  const init: RequestInit = { method };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "Content-Type": "application/json" };
  }
  const request = new Request(url, init) as unknown as NextRequest;
  const urlObj = new URL(url);
  Object.defineProperty(request, "nextUrl", { value: urlObj, writable: false });
  return request;
}

function mockParams(contentHash: string) {
  return { params: Promise.resolve({ recordingContentHash: contentHash }) };
}

function insertChain() {
  const onConflictDoUpdate = vi.fn().mockResolvedValue(undefined);
  const values = vi.fn().mockReturnValue({ onConflictDoUpdate });
  mockInsert.mockReturnValue({ values });
  return { values, onConflictDoUpdate };
}

// --------------------------------------------------------------------------
// Auth
// --------------------------------------------------------------------------

describe("auth gating", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    delete (globalThis as Record<string, unknown>).__mockResolveLyricsSituation;
  });

  it("GET returns 401 without session", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await GET(makeRequest("hash123"), mockParams("hash123"));
    expect(res.status).toBe(401);
  });

  it("PUT returns 401 without session", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await PUT(
      makeRequest("hash123", "PUT", { rating: "sad", reason: "missing" }),
      mockParams("hash123")
    );
    expect(res.status).toBe(401);
  });

  it("DELETE returns 401 without session", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await DELETE(makeRequest("hash123", "DELETE"), mockParams("hash123"));
    expect(res.status).toBe(401);
  });
});

// --------------------------------------------------------------------------
// GET — caller's current feedback (privacy: only caller's row is reachable)
// --------------------------------------------------------------------------

describe("GET /api/lyrics/feedback/[recordingContentHash]", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    delete (globalThis as Record<string, unknown>).__mockResolveLyricsSituation;
  });

  it("returns null feedback when none exists", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockResolvedValue(undefined);

    const res = await GET(makeRequest("hash123"), mockParams("hash123"));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.feedback).toBeNull();
  });

  it("returns the caller's current feedback with rating and reason", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockResolvedValue({ rating: "sad", reason: "timing" });

    const res = await GET(makeRequest("hash123"), mockParams("hash123"));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.feedback).toEqual({ rating: "sad", reason: "timing" });
  });

  it("returns 500 on database error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockRejectedValue(new Error("DB error"));

    const res = await GET(makeRequest("hash123"), mockParams("hash123"));
    expect(res.status).toBe(500);
  });
});

// --------------------------------------------------------------------------
// PUT — upsert with state-aware validation matrix
// --------------------------------------------------------------------------

describe("PUT /api/lyrics/feedback/[recordingContentHash] — validation matrix", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    delete (globalThis as Record<string, unknown>).__mockResolveLyricsSituation;
    delete (globalThis as Record<string, unknown>).__mockRecordingExists;
  });

  it("happy accepted when synced lyrics exist → upserts row with null reason", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    (globalThis as Record<string, unknown>).__mockResolveLyricsSituation = vi.fn().mockResolvedValue({ kind: "synced" });
    const { values, onConflictDoUpdate } = insertChain();

    const res = await PUT(
      makeRequest("hash123", "PUT", { rating: "happy" }),
      mockParams("hash123")
    );
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.feedback).toEqual({ rating: "happy", reason: null });
    expect(values).toHaveBeenCalledWith(
      expect.objectContaining({
        userId: 42,
        recordingContentHash: "hash123",
        rating: "happy",
        reason: null,
      })
    );
    expect(onConflictDoUpdate).toHaveBeenCalled();
  });

  it("happy rejected with 400 when no lyrics exist", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    (globalThis as Record<string, unknown>).__mockResolveLyricsSituation = vi.fn().mockResolvedValue({ kind: "none" });

    const res = await PUT(
      makeRequest("hash123", "PUT", { rating: "happy" }),
      mockParams("hash123")
    );
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toMatch(/lyrics/i);
  });

  it("happy accepted when unsynced lyrics exist → upserts row with null reason", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    (globalThis as Record<string, unknown>).__mockResolveLyricsSituation = vi.fn().mockResolvedValue({ kind: "unsynced" });
    insertChain();

    const res = await PUT(
      makeRequest("hash123", "PUT", { rating: "happy" }),
      mockParams("hash123")
    );
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.feedback).toEqual({ rating: "happy", reason: null });
  });

  it("sad+missing accepted when lyrics are unsynced-only", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    (globalThis as Record<string, unknown>).__mockResolveLyricsSituation = vi.fn().mockResolvedValue({ kind: "unsynced" });
    insertChain();

    const res = await PUT(
      makeRequest("hash123", "PUT", { rating: "sad", reason: "missing" }),
      mockParams("hash123")
    );
    expect(res.status).toBe(200);
  });

  it("sad+missing rejected with 400 when synced lyrics exist", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    (globalThis as Record<string, unknown>).__mockResolveLyricsSituation = vi.fn().mockResolvedValue({ kind: "synced" });

    const res = await PUT(
      makeRequest("hash123", "PUT", { rating: "sad", reason: "missing" }),
      mockParams("hash123")
    );
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toMatch(/synced/i);
  });

  it("sad+timing rejected with 400 when no synced lyrics", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    (globalThis as Record<string, unknown>).__mockResolveLyricsSituation = vi.fn().mockResolvedValue({ kind: "none" });

    const res = await PUT(
      makeRequest("hash123", "PUT", { rating: "sad", reason: "timing" }),
      mockParams("hash123")
    );
    expect(res.status).toBe(400);
  });

  it("sad+timing accepted when synced lyrics exist", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    (globalThis as Record<string, unknown>).__mockResolveLyricsSituation = vi.fn().mockResolvedValue({ kind: "synced" });
    insertChain();

    const res = await PUT(
      makeRequest("hash123", "PUT", { rating: "sad", reason: "timing" }),
      mockParams("hash123")
    );
    expect(res.status).toBe(200);
  });

  it("switch-overwrite: PUT sad+timing then PUT happy → latest opinion wins (upsert set carries updatedAt)", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    (globalThis as Record<string, unknown>).__mockResolveLyricsSituation = vi.fn().mockResolvedValue({ kind: "synced" });
    const { onConflictDoUpdate } = insertChain();

    const first = await PUT(
      makeRequest("hash123", "PUT", { rating: "sad", reason: "timing" }),
      mockParams("hash123")
    );
    expect(first.status).toBe(200);

    const second = await PUT(
      makeRequest("hash123", "PUT", { rating: "happy" }),
      mockParams("hash123")
    );
    expect(second.status).toBe(200);
    const data = await second.json();
    expect(data.feedback).toEqual({ rating: "happy", reason: null });

    // Second call still upserts (overwrite, not a new row), and the deploy
    // path has no updated_at trigger — the route must set it explicitly.
    expect(onConflictDoUpdate).toHaveBeenCalledTimes(2);
    expect(onConflictDoUpdate.mock.calls[1][0]).toEqual(
      expect.objectContaining({
        set: {
          rating: "happy",
          reason: null,
          updatedAt: expect.any(Date),
        },
      })
    );
  });

  it("sad+wrong_text accepted in any state", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    (globalThis as Record<string, unknown>).__mockResolveLyricsSituation = vi.fn().mockResolvedValue({ kind: "none" });
    insertChain();

    const res = await PUT(
      makeRequest("hash123", "PUT", { rating: "sad", reason: "wrong_text" }),
      mockParams("hash123")
    );
    expect(res.status).toBe(200);
  });

  it("sad+other accepted in any state", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    (globalThis as Record<string, unknown>).__mockResolveLyricsSituation = vi.fn().mockResolvedValue({ kind: "unsynced" });
    insertChain();

    const res = await PUT(
      makeRequest("hash123", "PUT", { rating: "sad", reason: "other" }),
      mockParams("hash123")
    );
    expect(res.status).toBe(200);
  });

  it("invalid rating returns 400", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    (globalThis as Record<string, unknown>).__mockResolveLyricsSituation = vi.fn().mockResolvedValue({ kind: "synced" });

    const res = await PUT(
      makeRequest("hash123", "PUT", { rating: "meh" }),
      mockParams("hash123")
    );
    expect(res.status).toBe(400);
  });

  it("invalid reason value returns 400", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    (globalThis as Record<string, unknown>).__mockResolveLyricsSituation = vi.fn().mockResolvedValue({ kind: "synced" });

    const res = await PUT(
      makeRequest("hash123", "PUT", { rating: "sad", reason: "shouting" }),
      mockParams("hash123")
    );
    expect(res.status).toBe(400);
  });

  it("sad without reason returns 400", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    (globalThis as Record<string, unknown>).__mockResolveLyricsSituation = vi.fn().mockResolvedValue({ kind: "synced" });

    const res = await PUT(
      makeRequest("hash123", "PUT", { rating: "sad" }),
      mockParams("hash123")
    );
    expect(res.status).toBe(400);
  });

  it("happy with reason returns 400", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    (globalThis as Record<string, unknown>).__mockResolveLyricsSituation = vi.fn().mockResolvedValue({ kind: "synced" });

    const res = await PUT(
      makeRequest("hash123", "PUT", { rating: "happy", reason: "timing" }),
      mockParams("hash123")
    );
    expect(res.status).toBe(400);
  });

  it("malformed JSON body returns 400", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const url = "http://localhost/api/lyrics/feedback/hash123";
    const request = new Request(url, {
      method: "PUT",
      body: "not json",
      headers: { "Content-Type": "application/json" },
    }) as unknown as NextRequest;
    const urlObj = new URL(url);
    Object.defineProperty(request, "nextUrl", { value: urlObj, writable: false });

    const res = await PUT(request, mockParams("hash123"));
    expect(res.status).toBe(400);
  });

  it("existing recording with no lyrics (situation none) allows sad+missing", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    (globalThis as Record<string, unknown>).__mockResolveLyricsSituation = vi.fn().mockResolvedValue({ kind: "none" });
    insertChain();

    const res = await PUT(
      makeRequest("hash123", "PUT", { rating: "sad", reason: "missing" }),
      mockParams("hash123")
    );
    expect(res.status).toBe(200);
  });

  it("unknown recording hash returns 400 recording not found", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    (globalThis as Record<string, unknown>).__mockRecordingExists = vi.fn().mockResolvedValue(false);

    const res = await PUT(
      makeRequest("hash123", "PUT", { rating: "happy" }),
      mockParams("hash123")
    );
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toBe("Recording not found");
  });

  it("500 on database error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    (globalThis as Record<string, unknown>).__mockResolveLyricsSituation = vi.fn().mockResolvedValue({ kind: "synced" });
    mockInsert.mockImplementation(() => {
      throw new Error("DB error");
    });

    const res = await PUT(
      makeRequest("hash123", "PUT", { rating: "happy" }),
      mockParams("hash123")
    );
    expect(res.status).toBe(500);
  });
});

// --------------------------------------------------------------------------
// DELETE — retract
// --------------------------------------------------------------------------

describe("DELETE /api/lyrics/feedback/[recordingContentHash]", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    delete (globalThis as Record<string, unknown>).__mockResolveLyricsSituation;
  });

  it("deletes the caller's row and returns success", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const where = vi.fn().mockResolvedValue(undefined);
    mockDelete.mockReturnValue({ where });

    const res = await DELETE(makeRequest("hash123", "DELETE"), mockParams("hash123"));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.success).toBe(true);
    expect(where).toHaveBeenCalled();
  });

  it("500 on database error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockDelete.mockImplementation(() => {
      throw new Error("DB error");
    });

    const res = await DELETE(makeRequest("hash123", "DELETE"), mockParams("hash123"));
    expect(res.status).toBe(500);
  });
});