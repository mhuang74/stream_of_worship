import { describe, it, expect, beforeEach, vi } from "vitest";
import { GET, DELETE } from "@/app/api/render-jobs/[id]/route";
import { auth } from "@/lib/auth";
import { getRenderJob, cancelRenderJob } from "@/lib/render/job-manager";
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
  getRenderJob: vi.fn(),
  cancelRenderJob: vi.fn(),
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

const mockJob = {
  id: "job-1",
  songsetId: "songset-1",
  userId: 1,
  status: "queued",
  phase: "preparing",
  phaseIndex: 0,
  totalPhases: 5,
  elapsedSeconds: 0,
  errorMessage: null,
  estimatedTotalSeconds: null,
  totalDurationSeconds: null,
  startedAt: null,
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

describe("GET /api/render-jobs/[id]", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1");
    const response = await GET(request, { params: Promise.resolve({ id: "job-1" }) });

    expect(response.status).toBe(401);
    const data = await response.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("returns render job", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(getRenderJob).mockResolvedValue(mockJob);

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1");
    const response = await GET(request, { params: Promise.resolve({ id: "job-1" }) });

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.id).toBe("job-1");
    expect(data.songsetId).toBe("songset-1");
    expect(data.status).toBe("queued");
    expect(getRenderJob).toHaveBeenCalledWith("job-1", 1);
  });

  it("returns completed render job with output keys", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const completedJob = {
      ...mockJob,
      status: "completed",
      phase: "completed",
      phaseIndex: 5,
      mp3R2Key: "renders/job-1/audio.mp3",
      mp4R2Key: "renders/job-1/video.mp4",
      chaptersR2Key: "renders/job-1/chapters.json",
      completedAt: new Date(),
    };

    vi.mocked(getRenderJob).mockResolvedValue(completedJob);

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1");
    const response = await GET(request, { params: Promise.resolve({ id: "job-1" }) });

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.status).toBe("completed");
    expect(data.phase).toBe("completed");
    expect(data.mp3R2Key).toBe("renders/job-1/audio.mp3");
    expect(data.mp4R2Key).toBe("renders/job-1/video.mp4");
    expect(data.chaptersR2Key).toBe("renders/job-1/chapters.json");
  });

  it("returns failed render job with error message", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const failedJob = {
      ...mockJob,
      status: "failed",
      errorMessage: "FFmpeg encoding failed",
    };

    vi.mocked(getRenderJob).mockResolvedValue(failedJob);

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1");
    const response = await GET(request, { params: Promise.resolve({ id: "job-1" }) });

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.status).toBe("failed");
    expect(data.errorMessage).toBe("FFmpeg encoding failed");
  });

  it("returns 404 when job not found", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(getRenderJob).mockResolvedValue(null);

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1");
    const response = await GET(request, { params: Promise.resolve({ id: "job-1" }) });

    expect(response.status).toBe(404);
    const data = await response.json();
    expect(data.error).toBe("Render job not found");
  });

  it("returns 500 on error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(getRenderJob).mockRejectedValue(new Error("Database error"));

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1");
    const response = await GET(request, { params: Promise.resolve({ id: "job-1" }) });

    expect(response.status).toBe(500);
    const data = await response.json();
    expect(data.error).toBe("Failed to get render job");
  });
});

describe("DELETE /api/render-jobs/[id]", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1", {
      method: "DELETE",
    });
    const response = await DELETE(request, { params: Promise.resolve({ id: "job-1" }) });

    expect(response.status).toBe(401);
    const data = await response.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("cancels queued job", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const cancelledJob = {
      ...mockJob,
      status: "cancelled",
    };

    vi.mocked(cancelRenderJob).mockResolvedValue(cancelledJob);

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1", {
      method: "DELETE",
    });
    const response = await DELETE(request, { params: Promise.resolve({ id: "job-1" }) });

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.status).toBe("cancelled");
    expect(cancelRenderJob).toHaveBeenCalledWith("job-1", 1);
  });

  it("cancels running job", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    const runningJob = {
      ...mockJob,
      status: "running",
      phase: "mixing_audio",
      phaseIndex: 1,
    };

    const cancelledJob = {
      ...runningJob,
      status: "cancelled",
    };

    vi.mocked(cancelRenderJob).mockResolvedValue(cancelledJob);

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1", {
      method: "DELETE",
    });
    const response = await DELETE(request, { params: Promise.resolve({ id: "job-1" }) });

    expect(response.status).toBe(200);
    const data = await response.json();
    expect(data.status).toBe("cancelled");
  });

  it("returns 404 when job not found", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(cancelRenderJob).mockResolvedValue(null);

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1", {
      method: "DELETE",
    });
    const response = await DELETE(request, { params: Promise.resolve({ id: "job-1" }) });

    expect(response.status).toBe(404);
    const data = await response.json();
    expect(data.error).toBe("Render job not found");
  });

  it("returns 400 when job cannot be cancelled", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(cancelRenderJob).mockRejectedValue(
      new Error("Cannot cancel job with status: completed")
    );

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1", {
      method: "DELETE",
    });
    const response = await DELETE(request, { params: Promise.resolve({ id: "job-1" }) });

    expect(response.status).toBe(400);
    const data = await response.json();
    expect(data.error).toBe("Cannot cancel job with status: completed");
  });

  it("returns 500 on unexpected error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(cancelRenderJob).mockRejectedValue(new Error("Database error"));

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1", {
      method: "DELETE",
    });
    const response = await DELETE(request, { params: Promise.resolve({ id: "job-1" }) });

    expect(response.status).toBe(500);
    const data = await response.json();
    expect(data.error).toBe("Failed to cancel render job");
  });
});
