import { describe, it, expect, vi, beforeEach } from "vitest";
import { GET } from "@/app/api/render-jobs/[id]/artifact-sizes/route";
import { auth } from "@/lib/auth";
import { NextRequest } from "next/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: vi.fn() } },
}));

const mockFindFirst = vi.fn();

vi.mock("@/db", () => ({
  db: {
    query: {
      renderJobs: { findFirst: (...args: unknown[]) => mockFindFirst(...args) },
    },
  },
}));

const mockGetObjectSize = vi.fn();
const mockCreateR2Client = vi.fn();

vi.mock("@/lib/r2/client", () => ({
  createR2ClientFromEnv: (...args: unknown[]) => mockCreateR2Client(...args),
}));

function makeRequest(id: string): NextRequest {
  const req = new Request(`http://localhost/api/render-jobs/${id}/artifact-sizes`) as unknown as NextRequest;
  return req;
}

function makeParams(id: string) {
  return { params: Promise.resolve({ id }) };
}

const sessionUser = { user: { id: 42 } };

const completedJob = {
  id: "job-123",
  userId: 42,
  status: "completed",
  mp3R2Key: "renders/job-123/output.mp3",
  mp4R2Key: "renders/job-123/output.mp4",
};

describe("GET /api/render-jobs/[id]/artifact-sizes", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockCreateR2Client.mockReturnValue({ getObjectSize: mockGetObjectSize });
    mockGetObjectSize.mockResolvedValue(100 * 1024 * 1024); // 100MB default
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await GET(makeRequest("job-123"), makeParams("job-123") as any);
    expect(res.status).toBe(401);
  });

  it("returns 404 when job not found", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockResolvedValue(null);
    const res = await GET(makeRequest("missing"), makeParams("missing") as any);
    expect(res.status).toBe(404);
  });

  it("returns 409 when job not completed", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockResolvedValue({ ...completedJob, status: "running" });
    const res = await GET(makeRequest("job-123"), makeParams("job-123") as any);
    expect(res.status).toBe(409);
  });

  it("returns 503 when R2 not configured", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockResolvedValue(completedJob);
    mockCreateR2Client.mockImplementation(() => { throw new Error("R2 not configured"); });
    const res = await GET(makeRequest("job-123"), makeParams("job-123") as any);
    expect(res.status).toBe(503);
  });

  it("returns file sizes for both artifacts", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockResolvedValue(completedJob);
    mockGetObjectSize
      .mockResolvedValueOnce(50 * 1024 * 1024)  // mp3: 50MB
      .mockResolvedValueOnce(500 * 1024 * 1024); // mp4: 500MB

    const res = await GET(makeRequest("job-123"), makeParams("job-123") as any);
    expect(res.status).toBe(200);

    const data = await res.json();
    expect(data.renderJobId).toBe("job-123");
    expect(data.mp3SizeBytes).toBe(50 * 1024 * 1024);
    expect(data.mp4SizeBytes).toBe(500 * 1024 * 1024);
  });

  it("returns null for missing artifacts", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockResolvedValue({ ...completedJob, mp3R2Key: null, mp4R2Key: null });
    const res = await GET(makeRequest("job-123"), makeParams("job-123") as any);
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.mp3SizeBytes).toBeNull();
    expect(data.mp4SizeBytes).toBeNull();
  });

  it("returns 500 on unexpected error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirst.mockRejectedValue(new Error("DB error"));
    const res = await GET(makeRequest("job-123"), makeParams("job-123") as any);
    expect(res.status).toBe(500);
  });
});
