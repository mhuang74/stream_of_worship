import { describe, it, expect, vi, beforeEach } from "vitest";
import { GET } from "@/app/api/share/[token]/route";
import { NextRequest } from "next/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

const mockFindFirstShare = vi.fn();
const mockFindFirstJob = vi.fn();
const mockGetSongsetPublicView = vi.fn();

vi.mock("@/db", () => ({
  db: {
    query: {
      songsetShares: { findFirst: (...args: unknown[]) => mockFindFirstShare(...args) },
      renderJobs: { findFirst: (...args: unknown[]) => mockFindFirstJob(...args) },
    },
  },
}));

vi.mock("@/lib/db/songsets", () => ({
  getSongsetPublicView: (...args: unknown[]) => mockGetSongsetPublicView(...args),
}));

const mockGenerateSignedUrl = vi.fn();
const mockGetObjectSize = vi.fn();
const mockCreateR2Client = vi.fn();

vi.mock("@/lib/r2/client", () => ({
  DEFAULT_EXPIRES_IN_SECONDS: 3600,
  CAST_PLAYBACK_EXPIRES_IN_SECONDS: 14400,
  createR2ClientFromEnv: (...args: unknown[]) => mockCreateR2Client(...args),
}));

vi.mock("@/lib/rate-limit", () => ({
  getClientIp: () => "203.0.113.10",
  hashIp: async () => "test-ip-hash",
  enforceRateLimit: async () => true,
}));

vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: vi.fn() } },
}));

function makeRequest(url: string, method = "GET"): NextRequest {
  return new Request(url, { method }) as unknown as NextRequest;
}

function makeParams(token: string) {
  return { params: Promise.resolve({ token }) };
}

const activeShare = {
  token: "cast-token-abc",
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
  items: [],
};

const signedUrlResult = (type: string) => ({
  url: `https://r2.example.com/${type}`,
  expiresAt: new Date("2026-01-01T02:00:00Z"),
  cacheControl: "public, max-age=3600",
});

describe("/api/share/[token] cast expiry", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCreateR2Client.mockReturnValue({
      generateSignedUrl: mockGenerateSignedUrl,
      getObjectSize: mockGetObjectSize,
    });
    mockGenerateSignedUrl.mockImplementation((_key, type) =>
      Promise.resolve(signedUrlResult(type))
    );
    mockGetObjectSize.mockResolvedValue(50 * 1024 * 1024);
    mockGetSongsetPublicView.mockResolvedValue(songsetPublicView);
    mockFindFirstJob.mockResolvedValue(completedJob);
  });

  it("mints the MP4 with 14400s (Cast playback) expiry", async () => {
    mockFindFirstShare.mockResolvedValue(activeShare);

    const res = await GET(
      makeRequest("http://localhost/api/share/cast-token-abc"),
      makeParams("cast-token-abc") as any
    );
    expect(res.status).toBe(200);

    const mp4Call = mockGenerateSignedUrl.mock.calls.find(
      (args: unknown[]) => args[1] === "video"
    );
    expect(mp4Call).toBeDefined();
    expect(mp4Call?.[2]).toEqual(
      expect.objectContaining({ expiresInSeconds: 14400 })
    );
  });

  it("mints non-mp4 artefacts with the default 3600s expiry", async () => {
    mockFindFirstShare.mockResolvedValue(activeShare);

    const res = await GET(
      makeRequest("http://localhost/api/share/cast-token-abc"),
      makeParams("cast-token-abc") as any
    );
    expect(res.status).toBe(200);

    const mp3Call = mockGenerateSignedUrl.mock.calls.find(
      (args: unknown[]) => args[1] === "audio"
    );
    expect(mp3Call).toBeDefined();
    expect(mp3Call?.[2]).toEqual(
      expect.objectContaining({ expiresInSeconds: 3600 })
    );

    const jsonCall = mockGenerateSignedUrl.mock.calls.find(
      (args: unknown[]) => args[1] === "json"
    );
    expect(jsonCall).toBeDefined();
    expect(jsonCall?.[2]).toEqual(
      expect.objectContaining({ expiresInSeconds: 3600 })
    );
  });

  it("returns 410 before minting when the share is revoked", async () => {
    mockFindFirstShare.mockResolvedValue({ ...activeShare, revokedAt: new Date() });

    const res = await GET(
      makeRequest("http://localhost/api/share/cast-token-abc"),
      makeParams("cast-token-abc") as any
    );
    expect(res.status).toBe(410);
    expect(mockGenerateSignedUrl).not.toHaveBeenCalled();
  });

  it("returns 410 before minting when the share is expired", async () => {
    mockFindFirstShare.mockResolvedValue({
      ...activeShare,
      expiresAt: new Date("2020-01-01"),
    });

    const res = await GET(
      makeRequest("http://localhost/api/share/cast-token-abc"),
      makeParams("cast-token-abc") as any
    );
    expect(res.status).toBe(410);
    expect(mockGenerateSignedUrl).not.toHaveBeenCalled();
  });
});
