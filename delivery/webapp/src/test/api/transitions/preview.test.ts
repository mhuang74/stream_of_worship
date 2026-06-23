import { describe, it, expect, vi, beforeEach } from "vitest";
import { POST } from "@/app/api/transitions/preview/route";
import { auth } from "@/lib/auth";
import { NextRequest } from "next/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: vi.fn() } },
}));

const mockGetAudioSignedUrl = vi.fn();

vi.mock("@/lib/r2/client", () => ({
  createR2ClientFromEnv: () => ({
    getAudioSignedUrl: mockGetAudioSignedUrl,
  }),
}));

const mockFindFirst = vi.fn();

vi.mock("@/db", () => ({
  db: {
    query: {
      recordings: {
        findFirst: (...args: any[]) => mockFindFirst(...args),
      },
    },
  },
}));

const sessionUser = { user: { id: 1 } };

function makeRequest(body?: unknown): NextRequest {
  const init: RequestInit = {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
  }
  const request = new Request("http://localhost/api/transitions/preview", init);
  return request as unknown as NextRequest;
}

describe("POST /api/transitions/preview", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFindFirst.mockResolvedValue({
      hashPrefix: "hash-b",
      visibilityStatus: "published",
    });
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await POST(makeRequest({ toHash: "abc123" }));
    expect(res.status).toBe(401);
    const data = await res.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("returns 400 when body is not valid JSON", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const request = new Request("http://localhost/api/transitions/preview", {
      method: "POST",
      body: "not json",
      headers: { "Content-Type": "text/plain" },
    }) as unknown as NextRequest;
    const res = await POST(request);
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toBe("Invalid JSON body");
  });

  it("returns 400 when neither fromHash nor toHash is provided", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const res = await POST(makeRequest({}));
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toMatch(/fromHash|toHash|required/i);
  });

  it("returns 400 for invalid settings values", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const res = await POST(
      makeRequest({
        toHash: "abc",
        settings: { gapBeats: 999, crossfadeEnabled: "yes" },
      })
    );
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toBe("Invalid request body");
  });

  it("returns 404 when recording not found or not published", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockResolvedValue(null);

    const res = await POST(makeRequest({ toHash: "hash-b" }));
    expect(res.status).toBe(404);
    const data = await res.json();
    expect(data.error).toMatch(/not found|not published/i);
  });

  it("returns signed URL using toHash when provided", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const expiresAt = new Date(Date.now() + 3600_000);
    mockGetAudioSignedUrl.mockResolvedValue({
      url: "https://r2.example.com/audio/hash-b.mp3",
      expiresAt,
    });

    const res = await POST(
      makeRequest({
        fromHash: "hash-a",
        toHash: "hash-b",
        settings: {
          gapBeats: 2,
          crossfadeEnabled: false,
          crossfadeDurationSeconds: 2,
          keyShiftSemitones: 0,
          tempoRatio: 1.0,
        },
      })
    );

    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.url).toBe("https://r2.example.com/audio/hash-b.mp3");
    expect(data.previewHash).toBe("hash-b");
    expect(mockGetAudioSignedUrl).toHaveBeenCalledWith("hash-b", expect.objectContaining({ expiresInSeconds: 3600 }));
  });

  it("falls back to fromHash when toHash is not provided", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const expiresAt = new Date(Date.now() + 3600_000);
    mockGetAudioSignedUrl.mockResolvedValue({
      url: "https://r2.example.com/audio/hash-a.mp3",
      expiresAt,
    });
    mockFindFirst.mockResolvedValue({
      hashPrefix: "hash-a",
      visibilityStatus: "published",
    });

    const res = await POST(makeRequest({ fromHash: "hash-a" }));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.previewHash).toBe("hash-a");
    expect(mockGetAudioSignedUrl).toHaveBeenCalledWith("hash-a", expect.anything());
  });

  it("works with only toHash and no settings", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const expiresAt = new Date(Date.now() + 3600_000);
    mockGetAudioSignedUrl.mockResolvedValue({
      url: "https://r2.example.com/audio/hash-b.mp3",
      expiresAt,
    });

    const res = await POST(makeRequest({ toHash: "hash-b" }));
    expect(res.status).toBe(200);
  });

  it("returns 503 when R2 credentials not configured", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockGetAudioSignedUrl.mockRejectedValue(
      new Error("R2 credentials not configured")
    );

    const res = await POST(makeRequest({ toHash: "hash-b" }));
    expect(res.status).toBe(503);
    const data = await res.json();
    expect(data.error).toBe("R2 storage not configured");
  });

  it("returns 500 on unexpected error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockGetAudioSignedUrl.mockRejectedValue(new Error("Unexpected error"));

    const res = await POST(makeRequest({ toHash: "hash-b" }));
    expect(res.status).toBe(500);
    const data = await res.json();
    expect(data.error).toBe("Failed to generate preview URL");
  });

  it("includes expiresAt in response", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const expiresAt = new Date("2026-12-31T00:00:00.000Z");
    mockGetAudioSignedUrl.mockResolvedValue({
      url: "https://r2.example.com/audio/hash-b.mp3",
      expiresAt,
    });

    const res = await POST(makeRequest({ toHash: "hash-b" }));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.expiresAt).toBe("2026-12-31T00:00:00.000Z");
  });
});
