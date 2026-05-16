import { describe, it, expect, beforeEach, vi } from "vitest";
import { POST } from "@/app/api/render-jobs/route";
import { auth } from "@/lib/auth";
import { createRenderJob } from "@/lib/render/job-manager";
import { NextRequest } from "next/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/lib/auth", () => ({
  auth: {
    api: {
      getSession: vi.fn(),
    },
  },
}));

vi.mock("@/lib/render/job-manager", () => ({
  createRenderJob: vi.fn(),
}));

vi.mock("@/lib/render/pipeline", () => ({
  executeRenderPipeline: vi.fn().mockResolvedValue(undefined),
}));

function createMockRequest(url: string, options?: RequestInit): NextRequest {
  const request = new Request(url, options) as unknown as NextRequest;
  const urlObj = new URL(url);
  Object.defineProperty(request, "nextUrl", {
    value: urlObj,
    writable: false,
  });
  return request;
}

describe("POST /api/render-jobs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const request = createMockRequest("http://localhost:3000/api/render-jobs", {
      method: "POST",
      body: JSON.stringify({ songsetId: "songset-1" }),
    });
    const response = await POST(request);

    expect(response.status).toBe(401);
    const data = await response.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("creates render job with minimal input", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const mockJob = {
      id: "job-1",
      songsetId: "songset-1",
      userId: 1,
      status: "queued",
      phase: "preparing",
      phaseIndex: 0,
      totalPhases: 5,
      percentComplete: 0,
      estimatedSecondsLeft: null,
      elapsedSeconds: 0,
      errorMessage: null,
      template: "dark",
      resolution: "720p",
      audioEnabled: true,
      videoEnabled: true,
      fontSizePreset: "M",
      includeTitleCard: false,
      titleCardDurationSeconds: null,
      mp3R2Key: null,
      mp4R2Key: null,
      chaptersR2Key: null,
      createdAt: new Date(),
      updatedAt: new Date(),
      completedAt: null,
    };

    vi.mocked(createRenderJob).mockResolvedValue(mockJob);

    const request = createMockRequest("http://localhost:3000/api/render-jobs", {
      method: "POST",
      body: JSON.stringify({ songsetId: "songset-1" }),
    });
    const response = await POST(request);

    expect(response.status).toBe(201);
    const data = await response.json();
    expect(data.id).toBe("job-1");
    expect(data.songsetId).toBe("songset-1");
    expect(data.status).toBe("queued");
    expect(data.phase).toBe("preparing");
    expect(createRenderJob).toHaveBeenCalledWith(1, { songsetId: "songset-1" });
  });

  it("creates render job with all options", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const mockJob = {
      id: "job-1",
      songsetId: "songset-1",
      userId: 1,
      status: "queued",
      phase: "preparing",
      phaseIndex: 0,
      totalPhases: 5,
      percentComplete: 0,
      estimatedSecondsLeft: null,
      elapsedSeconds: 0,
      errorMessage: null,
      template: "gradient_warm",
      resolution: "1080p",
      audioEnabled: true,
      videoEnabled: true,
      fontSizePreset: "L",
      includeTitleCard: true,
      titleCardDurationSeconds: 15,
      mp3R2Key: null,
      mp4R2Key: null,
      chaptersR2Key: null,
      createdAt: new Date(),
      updatedAt: new Date(),
      completedAt: null,
    };

    vi.mocked(createRenderJob).mockResolvedValue(mockJob);

    const request = createMockRequest("http://localhost:3000/api/render-jobs", {
      method: "POST",
      body: JSON.stringify({
        songsetId: "songset-1",
        template: "gradient_warm",
        resolution: "1080p",
        audioEnabled: true,
        videoEnabled: true,
        fontSizePreset: "L",
        includeTitleCard: true,
        titleCardDurationSeconds: 15,
      }),
    });
    const response = await POST(request);

    expect(response.status).toBe(201);
    const data = await response.json();
    expect(data.template).toBe("gradient_warm");
    expect(data.resolution).toBe("1080p");
    expect(data.fontSizePreset).toBe("L");
    expect(data.includeTitleCard).toBe(true);
    expect(data.titleCardDurationSeconds).toBe(15);
  });

  it("returns 400 when songsetId is missing", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/render-jobs", {
      method: "POST",
      body: JSON.stringify({}),
    });
    const response = await POST(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
  });

  it("returns 400 when template is invalid", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/render-jobs", {
      method: "POST",
      body: JSON.stringify({
        songsetId: "songset-1",
        template: "invalid_template",
      }),
    });
    const response = await POST(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
  });

  it("returns 400 when resolution is invalid", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/render-jobs", {
      method: "POST",
      body: JSON.stringify({
        songsetId: "songset-1",
        resolution: "4k",
      }),
    });
    const response = await POST(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
  });

  it("returns 400 when fontSizePreset is invalid", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/render-jobs", {
      method: "POST",
      body: JSON.stringify({
        songsetId: "songset-1",
        fontSizePreset: "XXL",
      }),
    });
    const response = await POST(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
  });

  it("returns 400 when titleCardDurationSeconds is too low", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/render-jobs", {
      method: "POST",
      body: JSON.stringify({
        songsetId: "songset-1",
        includeTitleCard: true,
        titleCardDurationSeconds: 3,
      }),
    });
    const response = await POST(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
  });

  it("returns 400 when titleCardDurationSeconds is too high", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const request = createMockRequest("http://localhost:3000/api/render-jobs", {
      method: "POST",
      body: JSON.stringify({
        songsetId: "songset-1",
        includeTitleCard: true,
        titleCardDurationSeconds: 35,
      }),
    });
    const response = await POST(request);

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Invalid input");
  });

  it("returns 404 when songset not found", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(createRenderJob).mockRejectedValue(
      new Error("Songset not found or access denied")
    );

    const request = createMockRequest("http://localhost:3000/api/render-jobs", {
      method: "POST",
      body: JSON.stringify({ songsetId: "nonexistent" }),
    });
    const response = await POST(request);

    expect(response.status).toBe(404);
    const data = await response.json();
    expect(data.error).toBe("Songset not found or access denied");
  });

  it("returns 500 on unexpected error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(createRenderJob).mockRejectedValue(new Error("Database error"));

    const request = createMockRequest("http://localhost:3000/api/render-jobs", {
      method: "POST",
      body: JSON.stringify({ songsetId: "songset-1" }),
    });
    const response = await POST(request);

    expect(response.status).toBe(500);
    const data = await response.json();
    expect(data.error).toBe("Failed to create render job");
  });
});
