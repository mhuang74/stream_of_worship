import { beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET, POST } from "@/app/api/signed-url/route";
import { auth } from "@/lib/auth";

/* eslint-disable @typescript-eslint/no-explicit-any */

const mockRenderJobFindFirst = vi.fn();
const mockRecordingFindFirst = vi.fn();
const mockGenerateSignedUrl = vi.fn();
const mockGetAudioSignedUrl = vi.fn();
const mockGetLrcSignedUrl = vi.fn();
const mockGetVideoSignedUrl = vi.fn();
const mockGetRenderedAudioSignedUrl = vi.fn();
const mockGetChaptersSignedUrl = vi.fn();

vi.mock("@/lib/auth", () => ({
  auth: {
    api: {
      getSession: vi.fn(),
    },
  },
}));

vi.mock("@/db", () => ({
  db: {
    query: {
      renderJobs: {
        findFirst: (...args: unknown[]) => mockRenderJobFindFirst(...args),
      },
      recordings: {
        findFirst: (...args: unknown[]) => mockRecordingFindFirst(...args),
      },
    },
  },
}));

vi.mock("@/lib/r2/client", () => ({
  DEFAULT_EXPIRES_IN_SECONDS: 3600,
  CAST_PLAYBACK_EXPIRES_IN_SECONDS: 14400,
  createR2ClientFromEnv: vi.fn(() => ({
    generateSignedUrl: (...args: unknown[]) => mockGenerateSignedUrl(...args),
    getAudioSignedUrl: (...args: unknown[]) => mockGetAudioSignedUrl(...args),
    getLrcSignedUrl: (...args: unknown[]) => mockGetLrcSignedUrl(...args),
    getVideoSignedUrl: (...args: unknown[]) => mockGetVideoSignedUrl(...args),
    getRenderedAudioSignedUrl: (...args: unknown[]) =>
      mockGetRenderedAudioSignedUrl(...args),
    getChaptersSignedUrl: (...args: unknown[]) => mockGetChaptersSignedUrl(...args),
  })),
}));

function createMockRequest(url: string, options?: RequestInit): NextRequest {
  const request = new Request(url, options) as unknown as NextRequest;
  Object.defineProperty(request, "nextUrl", {
    value: new URL(url),
    writable: false,
  });
  return request;
}

describe("/api/signed-url", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const result = {
      url: "https://r2.example.com/file",
      expiresAt: new Date("2026-01-01T00:00:00Z"),
      cacheControl: "public, max-age=3600",
    };

    mockGetAudioSignedUrl.mockResolvedValue(result);
    mockGetLrcSignedUrl.mockResolvedValue(result);
    mockGetVideoSignedUrl.mockResolvedValue(result);
    mockGetRenderedAudioSignedUrl.mockResolvedValue(result);
    mockGetChaptersSignedUrl.mockResolvedValue(result);
    mockGenerateSignedUrl.mockResolvedValue(result);
  });

  it("returns 401 when unauthenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const response = await GET(
      createMockRequest("http://localhost:3000/api/signed-url?renderJobId=job-1")
    );

    expect(response.status).toBe(401);
  });

  it("returns 400 when no identifier is provided", async () => {
    const response = await GET(
      createMockRequest("http://localhost:3000/api/signed-url")
    );

    expect(response.status).toBe(400);
  });

  it("generates an owner-checked render job video URL", async () => {
    mockRenderJobFindFirst.mockResolvedValue({ id: "job-1", userId: 1 });

    const response = await GET(
      createMockRequest(
        "http://localhost:3000/api/signed-url?renderJobId=job-1&fileType=video"
      )
    );

    expect(response.status).toBe(200);
    expect(mockGetVideoSignedUrl).toHaveBeenCalledWith(
      "job-1",
      expect.objectContaining({ expiresInSeconds: 3600 })
    );
  });

  it("rejects access to another user's render job", async () => {
    mockRenderJobFindFirst.mockResolvedValue(null);

    const response = await GET(
      createMockRequest(
        "http://localhost:3000/api/signed-url?renderJobId=job-2&fileType=audio"
      )
    );

    expect(response.status).toBe(404);
  });

  it("generates a published recording audio URL from hashPrefix", async () => {
    mockRecordingFindFirst.mockResolvedValue({
      hashPrefix: "abc123",
      visibilityStatus: "published",
    });

    const response = await GET(
      createMockRequest(
        "http://localhost:3000/api/signed-url?hashPrefix=abc123&fileType=audio"
      )
    );

    expect(response.status).toBe(200);
    expect(mockGetAudioSignedUrl).toHaveBeenCalledWith(
      "abc123",
      expect.objectContaining({ expiresInSeconds: 3600 })
    );
  });

  it("rejects unpublished recording access by hashPrefix", async () => {
    mockRecordingFindFirst.mockResolvedValue(null);

    const response = await GET(
      createMockRequest(
        "http://localhost:3000/api/signed-url?hashPrefix=draft123&fileType=audio"
      )
    );

    expect(response.status).toBe(404);
  });

  it("returns 400 for unsupported render-job file types", async () => {
    mockRenderJobFindFirst.mockResolvedValue({ id: "job-1", userId: 1 });

    const response = await POST(
      createMockRequest("http://localhost:3000/api/signed-url", {
        method: "POST",
        body: JSON.stringify({
          renderJobId: "job-1",
          fileType: "lrc",
        }),
      })
    );

    expect(response.status).toBe(400);
  });

  it("returns 400 for invalid GET fileType values", async () => {
    const response = await GET(
      createMockRequest(
        "http://localhost:3000/api/signed-url?renderJobId=job-1&fileType=bad"
      )
    );

    expect(response.status).toBe(400);
  });

  describe("cast playback expiry", () => {
    it("mints with 14400s when cast=true (video, renderJobId)", async () => {
      mockRenderJobFindFirst.mockResolvedValue({ id: "job-1", userId: 1 });

      const response = await GET(
        createMockRequest(
          "http://localhost:3000/api/signed-url?renderJobId=job-1&fileType=video&cast=true"
        )
      );

      expect(response.status).toBe(200);
      expect(mockGetVideoSignedUrl).toHaveBeenCalledWith(
        "job-1",
        expect.objectContaining({ expiresInSeconds: 14400 })
      );
    });

    it("mints with default 3600s when cast is absent", async () => {
      mockRenderJobFindFirst.mockResolvedValue({ id: "job-1", userId: 1 });

      const response = await GET(
        createMockRequest(
          "http://localhost:3000/api/signed-url?renderJobId=job-1&fileType=video"
        )
      );

      expect(response.status).toBe(200);
      expect(mockGetVideoSignedUrl).toHaveBeenCalledWith(
        "job-1",
        expect.objectContaining({ expiresInSeconds: 3600 })
      );
    });

    it("explicit expiresInSeconds still wins over cast default (zod clamps to [60,86400])", async () => {
      mockRenderJobFindFirst.mockResolvedValue({ id: "job-1", userId: 1 });

      const response = await GET(
        createMockRequest(
          "http://localhost:3000/api/signed-url?renderJobId=job-1&fileType=video&cast=true&expiresInSeconds=120"
        )
      );

      expect(response.status).toBe(200);
      expect(mockGetVideoSignedUrl).toHaveBeenCalledWith(
        "job-1",
        expect.objectContaining({ expiresInSeconds: 120 })
      );
    });

    it("cast=true still requires a session (ownership enforced)", async () => {
      vi.mocked(auth.api.getSession).mockResolvedValue(null);

      const response = await GET(
        createMockRequest(
          "http://localhost:3000/api/signed-url?renderJobId=job-1&fileType=video&cast=true"
        )
      );

      expect(response.status).toBe(401);
      expect(mockGetVideoSignedUrl).not.toHaveBeenCalled();
    });

    it("cast=true rejects access to another user's render job", async () => {
      mockRenderJobFindFirst.mockResolvedValue(null);

      const response = await GET(
        createMockRequest(
          "http://localhost:3000/api/signed-url?renderJobId=job-2&fileType=video&cast=true"
        )
      );

      expect(response.status).toBe(404);
      expect(mockGetVideoSignedUrl).not.toHaveBeenCalled();
    });

    it("rejects expiresInSeconds below 60 (zod clamp)", async () => {
      const response = await GET(
        createMockRequest(
          "http://localhost:3000/api/signed-url?renderJobId=job-1&fileType=video&expiresInSeconds=30"
        )
      );

      expect(response.status).toBe(400);
    });

    it("rejects expiresInSeconds above 86400 (zod clamp)", async () => {
      const response = await GET(
        createMockRequest(
          "http://localhost:3000/api/signed-url?renderJobId=job-1&fileType=video&expiresInSeconds=100000"
        )
      );

      expect(response.status).toBe(400);
    });

    it("cast=true is scoped to video: audio artefacts keep the default 3600s", async () => {
      mockRenderJobFindFirst.mockResolvedValue({ id: "job-1", userId: 1 });

      const response = await GET(
        createMockRequest(
          "http://localhost:3000/api/signed-url?renderJobId=job-1&fileType=audio&cast=true"
        )
      );

      expect(response.status).toBe(200);
      expect(mockGetRenderedAudioSignedUrl).toHaveBeenCalledWith(
        "job-1",
        expect.objectContaining({ expiresInSeconds: 3600 })
      );
    });
  });
});
