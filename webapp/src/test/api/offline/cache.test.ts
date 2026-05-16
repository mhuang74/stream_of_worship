import { describe, it, expect, beforeEach, vi } from "vitest";
import { GET, DELETE } from "@/app/api/offline/cache/route";
import { auth } from "@/lib/auth";
import { NextRequest } from "next/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

// --------------------------------------------------------------------------
// Mocks
// --------------------------------------------------------------------------

vi.mock("@/lib/auth", () => ({
  auth: {
    api: { getSession: vi.fn() },
  },
}));

const mockFindFirst = vi.fn();

vi.mock("@/db", () => ({
  db: {
    query: {
      renderJobs: {
        findFirst: (...args: unknown[]) => mockFindFirst(...args),
      },
    },
  },
}));

const mockGenerateSignedUrl = vi.fn();

vi.mock("@/lib/r2/client", () => ({
  createR2ClientFromEnv: vi.fn().mockReturnValue({
    generateSignedUrl: (...args: unknown[]) => mockGenerateSignedUrl(...args),
  }),
}));

import { createR2ClientFromEnv } from "@/lib/r2/client";

// --------------------------------------------------------------------------
// Helpers
// --------------------------------------------------------------------------

function makeRequest(url: string, method = "GET"): NextRequest {
  const request = new Request(url, { method }) as unknown as NextRequest;
  const urlObj = new URL(url);
  Object.defineProperty(request, "nextUrl", { value: urlObj, writable: false });
  return request;
}

const sessionUser = { user: { id: 42 } };

const completedJob = {
  id: "job-123",
  userId: 42,
  status: "completed",
  mp3R2Key: "renders/job-123/output.mp3",
  mp4R2Key: "renders/job-123/output.mp4",
  chaptersR2Key: "renders/job-123/chapters.json",
};

const signedUrlResult = (suffix: string) => ({
  url: `https://r2.example.com/${suffix}?sig=abc`,
  expiresAt: new Date("2026-01-01T02:00:00Z"),
  cacheControl: "public, max-age=3600",
});

// --------------------------------------------------------------------------
// GET /api/offline/cache
// --------------------------------------------------------------------------

describe("GET /api/offline/cache", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGenerateSignedUrl.mockImplementation((_key: string, fileType: string) =>
      Promise.resolve(signedUrlResult(fileType))
    );
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const res = await GET(makeRequest("http://localhost/api/offline/cache?renderJobId=job-123"));
    expect(res.status).toBe(401);
    const data = await res.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("returns 400 when renderJobId is missing", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);

    const res = await GET(makeRequest("http://localhost/api/offline/cache"));
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toMatch(/renderJobId/);
  });

  it("returns 404 when render job is not found", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockResolvedValue(null);

    const res = await GET(makeRequest("http://localhost/api/offline/cache?renderJobId=job-999"));
    expect(res.status).toBe(404);
  });

  it("returns 409 when render job is not completed", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockResolvedValue({ ...completedJob, status: "running" });

    const res = await GET(makeRequest("http://localhost/api/offline/cache?renderJobId=job-123"));
    expect(res.status).toBe(409);
    const data = await res.json();
    expect(data.error).toMatch(/not completed/i);
  });

  it("returns 404 when completed job has no artifacts", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockResolvedValue({
      ...completedJob,
      mp3R2Key: null,
      mp4R2Key: null,
    });

    const res = await GET(makeRequest("http://localhost/api/offline/cache?renderJobId=job-123"));
    expect(res.status).toBe(404);
    const data = await res.json();
    expect(data.error).toMatch(/no artifacts/i);
  });

  it("returns 503 when R2 is not configured", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockResolvedValue(completedJob);
    vi.mocked(createR2ClientFromEnv).mockImplementationOnce(() => {
      throw new Error("R2 credentials not configured");
    });

    const res = await GET(makeRequest("http://localhost/api/offline/cache?renderJobId=job-123"));
    expect(res.status).toBe(503);
  });

  it("returns signed URLs for all three artifacts", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockResolvedValue(completedJob);

    const res = await GET(makeRequest("http://localhost/api/offline/cache?renderJobId=job-123"));
    expect(res.status).toBe(200);

    const data = await res.json();
    expect(data.renderJobId).toBe("job-123");
    expect(data.mp3Url).toContain("r2.example.com");
    expect(data.mp4Url).toContain("r2.example.com");
    expect(data.chaptersUrl).toContain("r2.example.com");
    expect(data.expiresAt).toBeDefined();
  });

  it("returns null chaptersUrl when job has no chapters key", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockResolvedValue({ ...completedJob, chaptersR2Key: null });

    const res = await GET(makeRequest("http://localhost/api/offline/cache?renderJobId=job-123"));
    expect(res.status).toBe(200);

    const data = await res.json();
    expect(data.chaptersUrl).toBeNull();
  });

  it("returns 500 on unexpected error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockRejectedValue(new Error("DB connection lost"));

    const res = await GET(makeRequest("http://localhost/api/offline/cache?renderJobId=job-123"));
    expect(res.status).toBe(500);
  });
});

// --------------------------------------------------------------------------
// DELETE /api/offline/cache
// --------------------------------------------------------------------------

describe("DELETE /api/offline/cache", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const res = await DELETE(
      makeRequest("http://localhost/api/offline/cache?renderJobId=job-123", "DELETE")
    );
    expect(res.status).toBe(401);
  });

  it("returns 400 when renderJobId is missing", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);

    const res = await DELETE(
      makeRequest("http://localhost/api/offline/cache", "DELETE")
    );
    expect(res.status).toBe(400);
  });

  it("returns 404 when render job is not found", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockResolvedValue(null);

    const res = await DELETE(
      makeRequest("http://localhost/api/offline/cache?renderJobId=job-999", "DELETE")
    );
    expect(res.status).toBe(404);
  });

  it("returns 200 with renderJobId and invalidated:true on success", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockResolvedValue(completedJob);

    const res = await DELETE(
      makeRequest("http://localhost/api/offline/cache?renderJobId=job-123", "DELETE")
    );
    expect(res.status).toBe(200);

    const data = await res.json();
    expect(data.renderJobId).toBe("job-123");
    expect(data.invalidated).toBe(true);
  });

  it("returns 500 on unexpected error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockRejectedValue(new Error("DB error"));

    const res = await DELETE(
      makeRequest("http://localhost/api/offline/cache?renderJobId=job-123", "DELETE")
    );
    expect(res.status).toBe(500);
  });
});
