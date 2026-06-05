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
const mockGetSongsetPublicView = vi.fn();
const mockSet = vi.fn();
const mockWhere = vi.fn();

vi.mock("@/db", () => ({
  db: {
    query: {
      songsetShares: { findFirst: (...args: unknown[]) => mockFindFirstShare(...args) },
      renderJobs: { findFirst: (...args: unknown[]) => mockFindFirstJob(...args) },
    },
    update: () => ({ set: (v: unknown) => { mockSet(v); return { where: mockWhere }; } }),
  },
}));

vi.mock("@/lib/db/songsets", () => ({
  getSongsetPublicView: (...args: unknown[]) => mockGetSongsetPublicView(...args),
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

const songsetLevelShare = {
  ...activeShare,
  renderJobId: null,
};

const completedJob = {
  id: "job-123",
  status: "completed",
  songsetId: "songset-1",
  mp3R2Key: "renders/job-123/output.mp3",
  mp4R2Key: "renders/job-123/output.mp4",
  chaptersR2Key: "renders/job-123/chapters.json",
  completedAt: new Date("2026-01-01T01:00:00Z"),
};

const songsetPublicView = {
  id: "songset-1",
  name: "Sunday Worship",
  description: "Weekly service songs",
  updatedAt: new Date("2026-01-01T00:00:00Z"),
  totalDurationSeconds: 1080,
  renderState: "fresh",
  latestRenderJobId: "job-123",
  lastCompletedRenderJobId: "job-123",
  items: [
    {
      id: "item-1",
      position: 0,
      songTitle: "Amazing Grace",
      composer: "John Newton",
      lyricist: null,
      albumName: "Hymns Collection",
      songMusicalKey: "G",
      durationSeconds: 240,
      tempoBpm: 80,
      recordingMusicalKey: "G",
    },
  ],
};

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
    mockGetObjectSize.mockResolvedValue(50 * 1024 * 1024);
  });

  it("returns 404 when share not found", async () => {
    mockFindFirstShare.mockResolvedValue(null);
    const res = await GET(makeRequest("http://localhost/api/share/bad-token"), makeParams("bad-token") as any);
    expect(res.status).toBe(404);
    const data = await res.json();
    expect(data.error).toMatch(/not found/i);
  });

  it("returns 410 when share is revoked", async () => {
    mockFindFirstShare.mockResolvedValue({ ...activeShare, revokedAt: new Date() });
    const res = await GET(makeRequest("http://localhost/api/share/valid-token-abc"), makeParams("valid-token-abc") as any);
    expect(res.status).toBe(410);
    const data = await res.json();
    expect(data.error).toMatch(/revoked/i);
  });

  it("returns 410 when share is expired", async () => {
    mockFindFirstShare.mockResolvedValue({
      ...activeShare,
      expiresAt: new Date("2020-01-01"),
    });
    const res = await GET(makeRequest("http://localhost/api/share/valid-token-abc"), makeParams("valid-token-abc") as any);
    expect(res.status).toBe(410);
    const data = await res.json();
    expect(data.error).toMatch(/expired/i);
  });

  it("returns 404 when songset deleted", async () => {
    mockFindFirstShare.mockResolvedValue(activeShare);
    mockGetSongsetPublicView.mockResolvedValue(null);
    const res = await GET(makeRequest("http://localhost/api/share/valid-token-abc"), makeParams("valid-token-abc") as any);
    expect(res.status).toBe(404);
  });

  it("returns live songset details with items and playback", async () => {
    mockFindFirstShare.mockResolvedValue(activeShare);
    mockGetSongsetPublicView.mockResolvedValue(songsetPublicView);
    mockFindFirstJob.mockResolvedValue(completedJob);

    const res = await GET(makeRequest("http://localhost/api/share/valid-token-abc"), makeParams("valid-token-abc") as any);
    expect(res.status).toBe(200);

    const data = await res.json();
    expect(data.token).toBe("valid-token-abc");
    expect(data.shareType).toBe("renderJob");
    expect(data.songset.name).toBe("Sunday Worship");
    expect(data.songset.description).toBe("Weekly service songs");
    expect(data.songset.totalDurationSeconds).toBe(1080);
    expect(data.items).toHaveLength(1);
    expect(data.items[0].songTitle).toBe("Amazing Grace");
    expect(data.items[0].composer).toBe("John Newton");
    expect(data.playback.mp3Url).toContain("r2.example.com");
    expect(data.playback.mp4Url).toContain("r2.example.com");
    expect(data.playback.isStale).toBe(false);
  });

  it("does not expose sensitive fields in items", async () => {
    mockFindFirstShare.mockResolvedValue(activeShare);
    mockGetSongsetPublicView.mockResolvedValue(songsetPublicView);
    mockFindFirstJob.mockResolvedValue(completedJob);

    const res = await GET(makeRequest("http://localhost/api/share/valid-token-abc"), makeParams("valid-token-abc") as any);
    expect(res.status).toBe(200);

    const data = await res.json();
    const item = data.items[0];
    expect(item).not.toHaveProperty("sourceUrl");
    expect(item).not.toHaveProperty("hashPrefix");
    expect(item).not.toHaveProperty("contentHash");
    expect(item).not.toHaveProperty("lyricsRaw");
    expect(item).not.toHaveProperty("lyricsLines");
    expect(item).not.toHaveProperty("gapBeats");
    expect(item).not.toHaveProperty("crossfadeEnabled");
    expect(item).not.toHaveProperty("keyShiftSemitones");
    expect(item).not.toHaveProperty("tempoRatio");
    expect(data).not.toHaveProperty("ownerId");
  });

  it("returns songset data with unavailable playback when no artifacts", async () => {
    mockFindFirstShare.mockResolvedValue(songsetLevelShare);
    mockGetSongsetPublicView.mockResolvedValue({
      ...songsetPublicView,
      lastCompletedRenderJobId: null,
      renderState: "unrendered",
    });

    const res = await GET(makeRequest("http://localhost/api/share/valid-token-abc"), makeParams("valid-token-abc") as any);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.shareType).toBe("songset");
    expect(data.playback.mp3Url).toBeNull();
    expect(data.playback.mp4Url).toBeNull();
    expect(data.playback.selectedRenderJobId).toBeNull();
  });

  it("flags stale playback when songset updated after render", async () => {
    mockFindFirstShare.mockResolvedValue(activeShare);
    mockGetSongsetPublicView.mockResolvedValue({
      ...songsetPublicView,
      updatedAt: new Date("2026-01-02T00:00:00Z"),
    });
    mockFindFirstJob.mockResolvedValue(completedJob);

    const res = await GET(makeRequest("http://localhost/api/share/valid-token-abc"), makeParams("valid-token-abc") as any);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.playback.isStale).toBe(true);
    expect(data.playback.staleStatus).toMatch(/earlier render/i);
  });

  it("returns songset-level share with shareType songset", async () => {
    mockFindFirstShare.mockResolvedValue(songsetLevelShare);
    mockGetSongsetPublicView.mockResolvedValue(songsetPublicView);
    mockFindFirstJob.mockResolvedValue(completedJob);

    const res = await GET(makeRequest("http://localhost/api/share/valid-token-abc"), makeParams("valid-token-abc") as any);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.shareType).toBe("songset");
  });

  it("returns render-job-level share with shareType renderJob", async () => {
    mockFindFirstShare.mockResolvedValue(activeShare);
    mockGetSongsetPublicView.mockResolvedValue(songsetPublicView);
    mockFindFirstJob.mockResolvedValue(completedJob);

    const res = await GET(makeRequest("http://localhost/api/share/valid-token-abc"), makeParams("valid-token-abc") as any);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.shareType).toBe("renderJob");
  });

  it("returns no-cache headers", async () => {
    mockFindFirstShare.mockResolvedValue(activeShare);
    mockGetSongsetPublicView.mockResolvedValue(songsetPublicView);
    mockFindFirstJob.mockResolvedValue(completedJob);

    const res = await GET(makeRequest("http://localhost/api/share/valid-token-abc"), makeParams("valid-token-abc") as any);
    expect(res.headers.get("Cache-Control")).toContain("no-store");
  });

  it("returns null URLs when R2 not configured", async () => {
    mockFindFirstShare.mockResolvedValue(activeShare);
    mockGetSongsetPublicView.mockResolvedValue(songsetPublicView);
    mockFindFirstJob.mockResolvedValue(completedJob);
    mockCreateR2Client.mockImplementation(() => { throw new Error("R2 not configured"); });

    const res = await GET(makeRequest("http://localhost/api/share/valid-token-abc"), makeParams("valid-token-abc") as any);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.playback.mp3Url).toBeNull();
    expect(data.playback.mp4Url).toBeNull();
  });

  it("handles rendering songset with no playback", async () => {
    mockFindFirstShare.mockResolvedValue(songsetLevelShare);
    mockGetSongsetPublicView.mockResolvedValue({
      ...songsetPublicView,
      renderState: "rendering",
      lastCompletedRenderJobId: null,
    });

    const res = await GET(makeRequest("http://localhost/api/share/valid-token-abc"), makeParams("valid-token-abc") as any);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.playback.selectedRenderJobId).toBeNull();
  });

  it("handles failed songset with no playback", async () => {
    mockFindFirstShare.mockResolvedValue(songsetLevelShare);
    mockGetSongsetPublicView.mockResolvedValue({
      ...songsetPublicView,
      renderState: "failed",
      lastCompletedRenderJobId: null,
    });

    const res = await GET(makeRequest("http://localhost/api/share/valid-token-abc"), makeParams("valid-token-abc") as any);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.playback.selectedRenderJobId).toBeNull();
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
