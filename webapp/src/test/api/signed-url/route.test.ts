import { describe, it, expect, beforeEach, vi } from "vitest";
import { POST, GET } from "@/app/api/signed-url/route";
import { auth } from "@/lib/auth";
import { NextRequest } from "next/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

// Mock auth
vi.mock("@/lib/auth", () => ({
  auth: {
    api: {
      getSession: vi.fn(),
    },
  },
}));

// Mock R2 client
vi.mock("@/lib/r2/client", () => ({
  R2Client: vi.fn().mockImplementation(() => ({
    generateSignedUrl: vi.fn(),
    getAudioSignedUrl: vi.fn(),
    getLrcSignedUrl: vi.fn(),
    getVideoSignedUrl: vi.fn(),
    getRenderedAudioSignedUrl: vi.fn(),
    getChaptersSignedUrl: vi.fn(),
    fileExists: vi.fn(),
  })),
  createR2ClientFromEnv: vi.fn().mockImplementation(() => ({
    generateSignedUrl: vi.fn().mockResolvedValue({
      url: "https://test.r2.cloudflarestorage.com/test-bucket/test-key?X-Amz-Algorithm=AWS4-HMAC-SHA256",
      expiresAt: new Date("2024-01-01T02:00:00Z"),
      cacheControl: "public, max-age=3600",
    }),
    getAudioSignedUrl: vi.fn().mockResolvedValue({
      url: "https://test.r2.cloudflarestorage.com/test-bucket/abc123/audio.mp3?X-Amz-Algorithm=AWS4-HMAC-SHA256",
      expiresAt: new Date("2024-01-01T02:00:00Z"),
      cacheControl: "public, max-age=3600",
    }),
    getLrcSignedUrl: vi.fn().mockResolvedValue({
      url: "https://test.r2.cloudflarestorage.com/test-bucket/abc123/lyrics.lrc?X-Amz-Algorithm=AWS4-HMAC-SHA256",
      expiresAt: new Date("2024-01-01T02:00:00Z"),
      cacheControl: "public, max-age=86400",
    }),
    getVideoSignedUrl: vi.fn().mockResolvedValue({
      url: "https://test.r2.cloudflarestorage.com/test-bucket/renders/job-123/output.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256",
      expiresAt: new Date("2024-01-01T02:00:00Z"),
      cacheControl: "public, max-age=3600",
    }),
    getRenderedAudioSignedUrl: vi.fn().mockResolvedValue({
      url: "https://test.r2.cloudflarestorage.com/test-bucket/renders/job-123/output.mp3?X-Amz-Algorithm=AWS4-HMAC-SHA256",
      expiresAt: new Date("2024-01-01T02:00:00Z"),
      cacheControl: "public, max-age=3600",
    }),
    getChaptersSignedUrl: vi.fn().mockResolvedValue({
      url: "https://test.r2.cloudflarestorage.com/test-bucket/renders/job-123/chapters.json?X-Amz-Algorithm=AWS4-HMAC-SHA256",
      expiresAt: new Date("2024-01-01T02:00:00Z"),
      cacheControl: "public, max-age=3600",
    }),
  })),
}));

import { createR2ClientFromEnv } from "@/lib/r2/client";

function createMockRequest(
  url: string,
  options?: RequestInit
): NextRequest {
  const request = new Request(url, options) as unknown as NextRequest;
  const urlObj = new URL(url);
  Object.defineProperty(request, "nextUrl", {
    value: urlObj,
    writable: false,
  });
  return request;
}

describe("POST /api/signed-url", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const request = createMockRequest("http://localhost:3000/api/signed-url", {
      method: "POST",
      body: JSON.stringify({ key: "test-key" }),
    });
    const response = await POST(request);

    expect(response.status).toBe(401);
    const data = await response.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("returns 400 when no identifier is provided", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/signed-url", {
      method: "POST",
      body: JSON.stringify({}),
    });
    const response = await POST(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toContain("Must provide one of");
  });

  it("generates signed URL with direct key", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/signed-url", {
      method: "POST",
      body: JSON.stringify({
        key: "path/to/file.mp3",
        fileType: "audio",
      }),
    });
    const response = await POST(request);

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.url).toContain("test.r2.cloudflarestorage.com");
    expect(data.cacheControl).toBe("public, max-age=3600");
    expect(data.expiresAt).toBeDefined();
  });

  it("generates signed URL with hashPrefix for audio", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/signed-url", {
      method: "POST",
      body: JSON.stringify({
        hashPrefix: "abc123",
        fileType: "audio",
      }),
    });
    const response = await POST(request);

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.url).toContain("abc123/audio.mp3");
  });

  it("generates signed URL with hashPrefix for LRC", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/signed-url", {
      method: "POST",
      body: JSON.stringify({
        hashPrefix: "abc123",
        fileType: "lrc",
      }),
    });
    const response = await POST(request);

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.url).toContain("abc123/lyrics.lrc");
    expect(data.cacheControl).toBe("public, max-age=86400");
  });

  it("generates signed URL with renderJobId for video", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/signed-url", {
      method: "POST",
      body: JSON.stringify({
        renderJobId: "job-123",
        fileType: "video",
      }),
    });
    const response = await POST(request);

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.url).toContain("renders/job-123/output.mp4");
  });

  it("generates signed URL with renderJobId for rendered audio", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/signed-url", {
      method: "POST",
      body: JSON.stringify({
        renderJobId: "job-123",
        fileType: "audio",
      }),
    });
    const response = await POST(request);

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.url).toContain("renders/job-123/output.mp3");
  });

  it("generates signed URL with renderJobId for chapters", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/signed-url", {
      method: "POST",
      body: JSON.stringify({
        renderJobId: "job-123",
        fileType: "json",
      }),
    });
    const response = await POST(request);

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.url).toContain("renders/job-123/chapters.json");
  });

  it("returns 400 for invalid fileType with renderJobId", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/signed-url", {
      method: "POST",
      body: JSON.stringify({
        renderJobId: "job-123",
        fileType: "lrc",
      }),
    });
    const response = await POST(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toContain("fileType must be");
  });

  it("returns 400 for invalid fileType with hashPrefix", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/signed-url", {
      method: "POST",
      body: JSON.stringify({
        hashPrefix: "abc123",
        fileType: "video",
      }),
    });
    const response = await POST(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toContain("fileType must be");
  });

  it("respects custom expiresInSeconds", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/signed-url", {
      method: "POST",
      body: JSON.stringify({
        key: "test-key",
        expiresInSeconds: 7200,
      }),
    });
    const response = await POST(request);

    expect(response.status).toBe(200);
  });

  it("returns 400 for invalid expiresInSeconds", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/signed-url", {
      method: "POST",
      body: JSON.stringify({
        key: "test-key",
        expiresInSeconds: 30, // Too short
      }),
    });
    const response = await POST(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid request body");
  });

  it("returns 400 for invalid JSON body", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/signed-url", {
      method: "POST",
      body: "not valid json",
    });
    const response = await POST(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid JSON body");
  });

  it("returns 500 on R2 client error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(createR2ClientFromEnv).mockImplementationOnce(() => {
      throw new Error("R2 credentials not configured");
    });

    const request = createMockRequest("http://localhost:3000/api/signed-url", {
      method: "POST",
      body: JSON.stringify({ key: "test-key" }),
    });
    const response = await POST(request);

    expect(response.status).toBe(503);
    const data = await response.json();
    expect(data.error).toBe("R2 storage not configured");
  });

  it("includes contentDisposition when provided", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/signed-url", {
      method: "POST",
      body: JSON.stringify({
        key: "test-key",
        contentDisposition: 'attachment; filename="test.mp3"',
      }),
    });
    const response = await POST(request);

    expect(response.status).toBe(200);
  });
});

describe("GET /api/signed-url", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const request = createMockRequest(
      "http://localhost:3000/api/signed-url?key=test-key"
    );
    const response = await GET(request);

    expect(response.status).toBe(401);
    const data = await response.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("returns 400 when no identifier is provided", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/signed-url");
    const response = await GET(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toContain("Must provide one of");
  });

  it("generates signed URL with key query parameter", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest(
      "http://localhost:3000/api/signed-url?key=path/to/file.mp3&fileType=audio"
    );
    const response = await GET(request);

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.url).toContain("test.r2.cloudflarestorage.com");
    expect(data.cacheControl).toBe("public, max-age=3600");
  });

  it("generates signed URL with hashPrefix for audio", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest(
      "http://localhost:3000/api/signed-url?hashPrefix=abc123&fileType=audio"
    );
    const response = await GET(request);

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.url).toContain("abc123/audio.mp3");
  });

  it("generates signed URL with hashPrefix for LRC", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest(
      "http://localhost:3000/api/signed-url?hashPrefix=abc123&fileType=lrc"
    );
    const response = await GET(request);

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.url).toContain("abc123/lyrics.lrc");
  });

  it("generates signed URL with renderJobId for video", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest(
      "http://localhost:3000/api/signed-url?renderJobId=job-123&fileType=video"
    );
    const response = await GET(request);

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.url).toContain("renders/job-123/output.mp4");
  });

  it("generates signed URL with renderJobId for rendered audio", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest(
      "http://localhost:3000/api/signed-url?renderJobId=job-123&fileType=audio"
    );
    const response = await GET(request);

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.url).toContain("renders/job-123/output.mp3");
  });

  it("generates signed URL with renderJobId for chapters", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest(
      "http://localhost:3000/api/signed-url?renderJobId=job-123&fileType=json"
    );
    const response = await GET(request);

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.url).toContain("renders/job-123/chapters.json");
  });

  it("returns 400 for invalid fileType with renderJobId", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest(
      "http://localhost:3000/api/signed-url?renderJobId=job-123&fileType=lrc"
    );
    const response = await GET(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toContain("fileType must be");
  });

  it("returns 400 for invalid fileType with hashPrefix", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest(
      "http://localhost:3000/api/signed-url?hashPrefix=abc123&fileType=video"
    );
    const response = await GET(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toContain("fileType must be");
  });

  it("respects custom expiresInSeconds", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest(
      "http://localhost:3000/api/signed-url?key=test-key&expiresInSeconds=7200"
    );
    const response = await GET(request);

    expect(response.status).toBe(200);
  });

  it("returns 400 for invalid expiresInSeconds", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest(
      "http://localhost:3000/api/signed-url?key=test-key&expiresInSeconds=30"
    );
    const response = await GET(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toContain("expiresInSeconds must be between");
  });

  it("returns 400 for non-numeric expiresInSeconds", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest(
      "http://localhost:3000/api/signed-url?key=test-key&expiresInSeconds=invalid"
    );
    const response = await GET(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toContain("expiresInSeconds must be between");
  });

  it("returns 503 when R2 credentials are not configured", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(createR2ClientFromEnv).mockImplementationOnce(() => {
      throw new Error("R2 credentials not configured");
    });

    const request = createMockRequest(
      "http://localhost:3000/api/signed-url?key=test-key"
    );
    const response = await GET(request);

    expect(response.status).toBe(503);
    const data = await response.json();
    expect(data.error).toBe("R2 storage not configured");
  });

  it("includes contentDisposition when provided", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest(
      "http://localhost:3000/api/signed-url?key=test-key&contentDisposition=attachment%3B%20filename%3D%22test.mp3%22"
    );
    const response = await GET(request);

    expect(response.status).toBe(200);
  });

  it("defaults to audio fileType when not specified", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest(
      "http://localhost:3000/api/signed-url?hashPrefix=abc123"
    );
    const response = await GET(request);

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.url).toContain("abc123/audio.mp3");
  });
});
