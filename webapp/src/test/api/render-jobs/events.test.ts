import { describe, it, expect, beforeEach, vi } from "vitest";
import { GET } from "@/app/api/render-jobs/[id]/events/route";
import { auth } from "@/lib/auth";
import { getRenderJob } from "@/lib/render/job-manager";
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

const mockQueuedJob = {
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

const mockRunningJob = {
  ...mockQueuedJob,
  status: "running",
  phase: "mixing_audio",
  phaseIndex: 1,
  percentComplete: 25,
  estimatedSecondsLeft: 120,
  elapsedSeconds: 30,
};

describe("GET /api/render-jobs/[id]/events (SSE)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1/events");
    const response = await GET(request, { params: { id: "job-1" } });

    expect(response.status).toBe(401);
    const data = JSON.parse(await response.text());
    expect(data.error).toBe("Unauthorized");
  });

  it("returns 404 when job not found", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(getRenderJob).mockResolvedValue(null);

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1/events");
    const response = await GET(request, { params: { id: "job-1" } });

    expect(response.status).toBe(404);
    const data = JSON.parse(await response.text());
    expect(data.error).toBe("Render job not found");
  });

  it("returns 410 when job is completed", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(getRenderJob).mockResolvedValue({
      ...mockQueuedJob,
      status: "completed",
      phase: "completed",
      phaseIndex: 5,
      percentComplete: 100,
    });

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1/events");
    const response = await GET(request, { params: { id: "job-1" } });

    expect(response.status).toBe(410);
    const data = JSON.parse(await response.text());
    expect(data.error).toBe("Job is no longer active");
  });

  it("returns 410 when job is failed", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(getRenderJob).mockResolvedValue({
      ...mockQueuedJob,
      status: "failed",
      errorMessage: "FFmpeg error",
    });

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1/events");
    const response = await GET(request, { params: { id: "job-1" } });

    expect(response.status).toBe(410);
    const data = JSON.parse(await response.text());
    expect(data.error).toBe("Job is no longer active");
  });

  it("returns 410 when job is cancelled", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(getRenderJob).mockResolvedValue({
      ...mockQueuedJob,
      status: "cancelled",
    });

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1/events");
    const response = await GET(request, { params: { id: "job-1" } });

    expect(response.status).toBe(410);
    const data = JSON.parse(await response.text());
    expect(data.error).toBe("Job is no longer active");
  });

  it("sets up SSE stream with correct headers", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(getRenderJob).mockResolvedValue(mockQueuedJob);

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1/events");
    const response = await GET(request, { params: { id: "job-1" } });

    expect(response.status).toBe(200);
    expect(response.headers.get("Content-Type")).toBe("text/event-stream");
    expect(response.headers.get("Cache-Control")).toBe("no-cache");
    expect(response.headers.get("Connection")).toBe("keep-alive");
  });

  it("sends initial event with current job state", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(getRenderJob).mockResolvedValue(mockRunningJob);

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1/events");
    const response = await GET(request, { params: { id: "job-1" } });

    const reader = response.body?.getReader();
    expect(reader).toBeDefined();

    if (reader) {
      const { value } = await reader.read();
      const text = new TextDecoder().decode(value);
      
      // Parse SSE event
      const match = text.match(/data: (.+)/);
      expect(match).toBeTruthy();
      
      const event = JSON.parse(match![1]);
      expect(event.phase).toBe("mixing_audio");
      expect(event.phaseIndex).toBe(1);
      expect(event.totalPhases).toBe(5);
      expect(event.percentComplete).toBe(25);
      expect(event.estimatedSecondsLeft).toBe(120);
      expect(event.elapsedSeconds).toBe(30);
    }
  });

  it("sends event with preparing phase for queued job", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(getRenderJob).mockResolvedValue(mockQueuedJob);

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1/events");
    const response = await GET(request, { params: { id: "job-1" } });

    const reader = response.body?.getReader();
    expect(reader).toBeDefined();

    if (reader) {
      const { value } = await reader.read();
      const text = new TextDecoder().decode(value);
      
      const match = text.match(/data: (.+)/);
      expect(match).toBeTruthy();
      
      const event = JSON.parse(match![1]);
      expect(event.phase).toBe("preparing");
      expect(event.phaseIndex).toBe(0);
      expect(event.percentComplete).toBe(0);
    }
  });

  it("polls for updates and sends progress events", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    // First call returns queued, subsequent calls return running
    let callCount = 0;
    vi.mocked(getRenderJob).mockImplementation(() => {
      callCount++;
      if (callCount === 1) {
        return Promise.resolve(mockQueuedJob);
      }
      return Promise.resolve({
        ...mockRunningJob,
        percentComplete: 50,
        phase: "rendering_frames",
        phaseIndex: 2,
      });
    });

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1/events");
    const response = await GET(request, { params: { id: "job-1" } });

    const reader = response.body?.getReader();
    expect(reader).toBeDefined();

    if (reader) {
      // Read initial event
      await reader.read();
      
      // Advance timers to trigger poll
      vi.advanceTimersByTime(1000);
      
      const { value } = await reader.read();
      const text = new TextDecoder().decode(value);
      
      const match = text.match(/data: (.+)/);
      expect(match).toBeTruthy();
      
      const event = JSON.parse(match![1]);
      expect(event.phase).toBe("rendering_frames");
      expect(event.phaseIndex).toBe(2);
      expect(event.percentComplete).toBe(50);
    }
  });

  it("sends final event when job completes", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    let callCount = 0;
    vi.mocked(getRenderJob).mockImplementation(() => {
      callCount++;
      if (callCount === 1) {
        return Promise.resolve(mockRunningJob);
      }
      return Promise.resolve({
        ...mockQueuedJob,
        status: "completed",
        phase: "completed",
        phaseIndex: 5,
        percentComplete: 100,
        elapsedSeconds: 180,
      });
    });

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1/events");
    const response = await GET(request, { params: { id: "job-1" } });

    const reader = response.body?.getReader();
    expect(reader).toBeDefined();

    if (reader) {
      // Read initial event
      await reader.read();
      
      // Advance timers to trigger poll
      vi.advanceTimersByTime(1000);
      
      const { value } = await reader.read();
      
      const text = new TextDecoder().decode(value);
      const match = text.match(/data: (.+)/);
      expect(match).toBeTruthy();
      
      const event = JSON.parse(match![1]);
      expect(event.phase).toBe("completed");
      expect(event.percentComplete).toBe(100);
      expect(event.elapsedSeconds).toBe(180);
    }
  });

  it("sends final event when job fails", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    let callCount = 0;
    vi.mocked(getRenderJob).mockImplementation(() => {
      callCount++;
      if (callCount === 1) {
        return Promise.resolve(mockRunningJob);
      }
      return Promise.resolve({
        ...mockQueuedJob,
        status: "failed",
        phase: "encoding_video",
        phaseIndex: 3,
        errorMessage: "Encoding failed",
        percentComplete: 75,
        elapsedSeconds: 120,
      });
    });

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1/events");
    const response = await GET(request, { params: { id: "job-1" } });

    const reader = response.body?.getReader();
    expect(reader).toBeDefined();

    if (reader) {
      // Read initial event
      await reader.read();
      
      // Advance timers to trigger poll
      vi.advanceTimersByTime(1000);
      
      const { value } = await reader.read();
      
      const text = new TextDecoder().decode(value);
      const match = text.match(/data: (.+)/);
      expect(match).toBeTruthy();
      
      const event = JSON.parse(match![1]);
      expect(event.phase).toBe("encoding_video"); // Failed jobs keep their actual phase
      expect(event.percentComplete).toBe(75); // Failed jobs keep their progress
    }
  });

  it("returns 500 on error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: 1 },
    } as any);

    vi.mocked(getRenderJob).mockRejectedValue(new Error("Database error"));

    const request = createMockRequest("http://localhost:3000/api/render-jobs/job-1/events");
    const response = await GET(request, { params: { id: "job-1" } });

    expect(response.status).toBe(500);
    const data = JSON.parse(await response.text());
    expect(data.error).toBe("Failed to set up SSE stream");
  });
});
