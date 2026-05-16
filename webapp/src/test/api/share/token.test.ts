import { describe, it, expect, vi, beforeEach } from "vitest";
import { GET, DELETE } from "@/app/api/share/[token]/route";
import { auth } from "@/lib/auth";
import { NextRequest } from "next/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: vi.fn() } },
}));

const mockFindFirstShare = vi.fn();
const mockFindFirstJob = vi.fn();
const mockFindFirstSongset = vi.fn();
const mockUpdate = vi.fn();
const mockSet = vi.fn();
const mockWhere = vi.fn();

vi.mock("@/db", () => ({
  db: {
    query: {
      songsetShares: { findFirst: (...args: unknown[]) => mockFindFirstShare(...args) },
      renderJobs: { findFirst: (...args: unknown[]) => mockFindFirstJob(...args) },
      songsets: { findFirst: (...args: unknown[]) => mockFindFirstSongset(...args) },
    },
    update: () => ({ set: (v: unknown) => { mockSet(v); return { where: mockWhere }; } }),
  },
}));

const mockGenerateSignedUrl = vi.fn();
const mockGetObjectSize = vi.fn();
const mockCreateR2Client = vi.fn();

vi.mock("@/lib/r2/client", () => ({
  createR2ClientFromEnv: (...args: unknown[]) => mockCreateR2Client(...args),
}));

function makeRequest(url: string, method = "GET"): NextRequest {
  const req = new Request(url, { method }) as unknown as NextRequest;
  return req;
}

function makeParams(token: string) {
  return { params: Promise.resolve({ token }) };
}

const activeShare = {
  token: "valid-token-abc",
  songsetId: "songset-1",
  renderJobId: "job-123",
  createdByUserId: 42,
  allowDownload: false,
  revokedAt: null,
  expiresAt: null,
  createdAt: new Date("2026-01-01"),
};

const completedJob = {
  id: "job-123",
  status: "completed",
  mp3R2Key: "renders/job-123/output.mp3",
  mp4R2Key: "renders/job-123/output.mp4",
  chaptersR2Key: "renders/job-123/chapters.json",
};

const songset = { id: "songset-1", name: "Sunday Worship" };

const signedUrl = (type: string) => ({
  url: `https://r2.example.com/${type}`,
  expiresAt: new Date("2026-01-01T02:00:00Z"),
});

// --------------------------------------------------------------------------
// GET /api/share/[token]
// --------------------------------------------------------------------------

describe("GET /api/share/[token]", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCreateR2Client.mockReturnValue({
      generateSignedUrl: mockGenerateSignedUrl,
      getObjectSize: mockGetObjectSize,
    });
    mockGenerateSignedUrl.mockImplementation((_key: string, type: string) =>
      Promise.resolve(signedUrl(type))
    );
    mockGetObjectSize.mockResolvedValue(50 * 1024 * 1024); // 50MB
  });

  it("returns 404 when share not found", async () => {
    mockFindFirstShare.mockResolvedValue(null);
    const res = await GET(makeRequest("http://localhost/api/share/bad-token"), makeParams("bad-token") as any);
    expect(res.status).toBe(404);
    const data = await res.json();
    expect(data.error).toMatch(/not found/);
  });

  it("returns 410 when share is revoked", async () => {
    mockFindFirstShare.mockResolvedValue({ ...activeShare, revokedAt: new Date() });
    const res = await GET(makeRequest("http://localhost/api/share/valid-token-abc"), makeParams("valid-token-abc") as any);
    expect(res.status).toBe(410);
    const data = await res.json();
    expect(data.error).toMatch(/revoked/);
  });

  it("returns 410 when share is expired", async () => {
    mockFindFirstShare.mockResolvedValue({
      ...activeShare,
      expiresAt: new Date("2020-01-01"),
    });
    const res = await GET(makeRequest("http://localhost/api/share/valid-token-abc"), makeParams("valid-token-abc") as any);
    expect(res.status).toBe(410);
    const data = await res.json();
    expect(data.error).toMatch(/expired/);
  });

  it("returns 404 when render job not found", async () => {
    mockFindFirstShare.mockResolvedValue(activeShare);
    mockFindFirstJob.mockResolvedValue(null);
    const res = await GET(makeRequest("http://localhost/api/share/valid-token-abc"), makeParams("valid-token-abc") as any);
    expect(res.status).toBe(404);
    const data = await res.json();
    expect(data.error).toMatch(/artifacts/);
  });

  it("returns 404 when render job not completed", async () => {
    mockFindFirstShare.mockResolvedValue(activeShare);
    mockFindFirstJob.mockResolvedValue({ ...completedJob, status: "running" });
    const res = await GET(makeRequest("http://localhost/api/share/valid-token-abc"), makeParams("valid-token-abc") as any);
    expect(res.status).toBe(404);
  });

  it("returns share info with signed URLs", async () => {
    mockFindFirstShare.mockResolvedValue(activeShare);
    mockFindFirstJob.mockResolvedValue(completedJob);
    mockFindFirstSongset.mockResolvedValue(songset);

    const res = await GET(makeRequest("http://localhost/api/share/valid-token-abc"), makeParams("valid-token-abc") as any);
    expect(res.status).toBe(200);

    const data = await res.json();
    expect(data.token).toBe("valid-token-abc");
    expect(data.songsetName).toBe("Sunday Worship");
    expect(data.mp3Url).toContain("r2.example.com");
    expect(data.mp4Url).toContain("r2.example.com");
    expect(data.mp3SizeBytes).toBe(50 * 1024 * 1024);
    expect(data.mp4SizeBytes).toBe(50 * 1024 * 1024);
  });

  it("returns no-cache headers", async () => {
    mockFindFirstShare.mockResolvedValue(activeShare);
    mockFindFirstJob.mockResolvedValue(completedJob);
    mockFindFirstSongset.mockResolvedValue(songset);

    const res = await GET(makeRequest("http://localhost/api/share/valid-token-abc"), makeParams("valid-token-abc") as any);
    expect(res.headers.get("Cache-Control")).toContain("no-store");
  });

  it("returns null URLs when R2 not configured", async () => {
    mockFindFirstShare.mockResolvedValue(activeShare);
    mockFindFirstJob.mockResolvedValue(completedJob);
    mockFindFirstSongset.mockResolvedValue(songset);
    mockCreateR2Client.mockImplementation(() => { throw new Error("R2 not configured"); });

    const res = await GET(makeRequest("http://localhost/api/share/valid-token-abc"), makeParams("valid-token-abc") as any);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.mp3Url).toBeNull();
    expect(data.mp4Url).toBeNull();
  });

  it("returns 500 on unexpected error", async () => {
    mockFindFirstShare.mockRejectedValue(new Error("DB error"));
    const res = await GET(makeRequest("http://localhost/api/share/valid-token-abc"), makeParams("valid-token-abc") as any);
    expect(res.status).toBe(500);
  });
});

// --------------------------------------------------------------------------
// DELETE /api/share/[token]
// --------------------------------------------------------------------------

describe("DELETE /api/share/[token]", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockWhere.mockResolvedValue([]);
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await DELETE(makeRequest("http://localhost/api/share/tok", "DELETE"), makeParams("tok") as any);
    expect(res.status).toBe(401);
  });

  it("returns 404 when share not found or not owned by user", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 42 } } as any);
    mockFindFirstShare.mockResolvedValue(null);
    const res = await DELETE(makeRequest("http://localhost/api/share/bad-token", "DELETE"), makeParams("bad-token") as any);
    expect(res.status).toBe(404);
  });

  it("returns 409 when share already revoked", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 42 } } as any);
    mockFindFirstShare.mockResolvedValue({ ...activeShare, revokedAt: new Date() });
    const res = await DELETE(makeRequest("http://localhost/api/share/valid-token-abc", "DELETE"), makeParams("valid-token-abc") as any);
    expect(res.status).toBe(409);
    const data = await res.json();
    expect(data.error).toMatch(/already revoked/);
  });

  it("revokes share and returns success", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 42 } } as any);
    mockFindFirstShare.mockResolvedValue(activeShare);

    const res = await DELETE(makeRequest("http://localhost/api/share/valid-token-abc", "DELETE"), makeParams("valid-token-abc") as any);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.success).toBe(true);
    expect(mockSet).toHaveBeenCalledWith(expect.objectContaining({ revokedAt: expect.any(Date) }));
  });

  it("returns 500 on unexpected error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 42 } } as any);
    mockFindFirstShare.mockRejectedValue(new Error("DB error"));
    const res = await DELETE(makeRequest("http://localhost/api/share/tok", "DELETE"), makeParams("tok") as any);
    expect(res.status).toBe(500);
  });
});
