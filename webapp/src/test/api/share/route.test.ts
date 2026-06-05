import { describe, it, expect, vi, beforeEach } from "vitest";
import { POST, GET } from "@/app/api/share/route";
import { auth } from "@/lib/auth";
import { NextRequest } from "next/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: vi.fn() } },
}));

const mockFindFirstJob = vi.fn();
const mockFindFirstSongset = vi.fn();
const mockFindFirstShare = vi.fn();
const mockInsert = vi.fn();
const mockSelect = vi.fn();

vi.mock("@/db", () => ({
  db: {
    query: {
      renderJobs: { findFirst: (...args: unknown[]) => mockFindFirstJob(...args) },
      songsets: { findFirst: (...args: unknown[]) => mockFindFirstSongset(...args) },
      songsetShares: { findFirst: (...args: unknown[]) => mockFindFirstShare(...args) },
    },
    insert: () => ({ values: mockInsert }),
    select: () => ({ from: () => ({ where: mockSelect }) }),
  },
}));

vi.mock("nanoid", () => ({ nanoid: () => "test-token-abc123456789012" }));

const sessionUser = { user: { id: 1 } };

const completedJob = {
  id: "job-123",
  userId: 1,
  songsetId: "songset-abc",
  status: "completed",
  mp3R2Key: "renders/job-123/output.mp3",
  mp4R2Key: "renders/job-123/output.mp4",
};

const ownedSongset = {
  id: "songset-abc",
  userId: 1,
  name: "Sunday Worship",
};

function makePostRequest(body: unknown): NextRequest {
  const req = new Request("http://localhost/api/share", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return req as unknown as NextRequest;
}

function makeGetRequest(query = ""): NextRequest {
  const url = `http://localhost/api/share${query}`;
  const req = new Request(url) as unknown as NextRequest;
  const urlObj = new URL(url);
  Object.defineProperty(req, "nextUrl", { value: urlObj, writable: false });
  return req;
}

// --------------------------------------------------------------------------
// POST /api/share
// --------------------------------------------------------------------------

describe("POST /api/share", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockInsert.mockResolvedValue([]);
    mockSelect.mockResolvedValue([{ value: 0 }]);
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await POST(makePostRequest({ songsetId: "songset-abc" }));
    expect(res.status).toBe(401);
    const data = await res.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("returns 400 when body is invalid JSON", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const req = new Request("http://localhost/api/share", {
      method: "POST",
      body: "not json",
    }) as unknown as NextRequest;
    const res = await POST(req);
    expect(res.status).toBe(400);
  });

  it("returns 400 when both songsetId and renderJobId provided", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const res = await POST(makePostRequest({ songsetId: "songset-abc", renderJobId: "job-123" }));
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toMatch(/both/i);
  });

  it("returns 400 when neither target provided", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const res = await POST(makePostRequest({}));
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toMatch(/either/i);
  });

  it("returns 404 when songset not found or not owned", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirstSongset.mockResolvedValue(null);
    const res = await POST(makePostRequest({ songsetId: "missing-songset" }));
    expect(res.status).toBe(404);
  });

  it("creates songset-level share and returns 201", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirstSongset.mockResolvedValue(ownedSongset);
    mockFindFirstShare.mockResolvedValue(null);
    mockSelect.mockResolvedValue([{ value: 5 }]);

    const res = await POST(makePostRequest({ songsetId: "songset-abc", allowDownload: false }));
    expect(res.status).toBe(201);

    const data = await res.json();
    expect(data.token).toBe("test-token-abc123456789012");
    expect(data.songsetId).toBe("songset-abc");
    expect(data.renderJobId).toBeNull();
    expect(data.allowDownload).toBe(false);
    expect(mockInsert).toHaveBeenCalledOnce();
  });

  it("reuses existing active share for same songset", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirstSongset.mockResolvedValue(ownedSongset);
    mockFindFirstShare.mockResolvedValue({
      token: "existing-tok",
      songsetId: "songset-abc",
      renderJobId: null,
      allowDownload: false,
    });

    const res = await POST(makePostRequest({ songsetId: "songset-abc" }));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.token).toBe("existing-tok");
    expect(mockInsert).not.toHaveBeenCalled();
  });

  it("returns 422 when user has 20 active shares", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirstSongset.mockResolvedValue(ownedSongset);
    mockFindFirstShare.mockResolvedValue(null);
    mockSelect.mockResolvedValue([{ value: 20 }]);
    const res = await POST(makePostRequest({ songsetId: "songset-abc" }));
    expect(res.status).toBe(422);
    const data = await res.json();
    expect(data.error).toMatch(/20/);
  });

  it("renderJobId path still works", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirstJob.mockResolvedValue(completedJob);
    mockSelect.mockResolvedValue([{ value: 5 }]);

    const res = await POST(makePostRequest({ renderJobId: "job-123", allowDownload: false }));
    expect(res.status).toBe(201);

    const data = await res.json();
    expect(data.token).toBe("test-token-abc123456789012");
    expect(data.renderJobId).toBe("job-123");
    expect(data.allowDownload).toBe(false);
    expect(mockInsert).toHaveBeenCalledOnce();
  });

  it("returns 404 when render job not found", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirstJob.mockResolvedValue(null);
    const res = await POST(makePostRequest({ renderJobId: "missing-job" }));
    expect(res.status).toBe(404);
  });

  it("returns 409 when render job is not completed", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirstJob.mockResolvedValue({ ...completedJob, status: "running" });
    const res = await POST(makePostRequest({ renderJobId: "job-123" }));
    expect(res.status).toBe(409);
    const data = await res.json();
    expect(data.error).toMatch(/not completed/);
  });

  it("normalizes allowDownload to boolean", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirstJob.mockResolvedValue(completedJob);
    mockSelect.mockResolvedValue([{ value: 0 }]);

    const res = await POST(makePostRequest({ renderJobId: "job-123", allowDownload: true }));
    expect(res.status).toBe(201);
    const data = await res.json();
    expect(data.allowDownload).toBe(true);
  });

  it("returns 500 on unexpected error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirstJob.mockRejectedValue(new Error("DB error"));
    const res = await POST(makePostRequest({ renderJobId: "job-123" }));
    expect(res.status).toBe(500);
  });
});

// --------------------------------------------------------------------------
// GET /api/share
// --------------------------------------------------------------------------

describe("GET /api/share", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await GET(makeGetRequest());
    expect(res.status).toBe(401);
  });

  it("returns list of active shares", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockSelect.mockResolvedValue([
      {
        token: "tok1",
        renderJobId: "job-123",
        songsetId: "set-1",
        createdByUserId: 1,
        allowDownload: false,
        revokedAt: null,
        createdAt: new Date("2026-01-01"),
      },
    ]);

    const res = await GET(makeGetRequest());
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.shares).toHaveLength(1);
    expect(data.shares[0].token).toBe("tok1");
    expect(data.shares[0].shareUrl).toContain("/share/tok1");
  });

  it("returns empty list when no shares", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockSelect.mockResolvedValue([]);
    const res = await GET(makeGetRequest());
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.shares).toHaveLength(0);
  });

  it("filters by renderJobId when provided", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirstJob.mockResolvedValue(completedJob);
    mockSelect.mockResolvedValue([]);
    const res = await GET(makeGetRequest("?renderJobId=job-123"));
    expect(res.status).toBe(200);
  });

  it("filters by songsetId when provided", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirstSongset.mockResolvedValue(ownedSongset);
    mockSelect.mockResolvedValue([]);
    const res = await GET(makeGetRequest("?songsetId=songset-abc"));
    expect(res.status).toBe(200);
  });

  it("returns 404 when songsetId not owned", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockFindFirstSongset.mockResolvedValue(null);
    const res = await GET(makeGetRequest("?songsetId=missing"));
    expect(res.status).toBe(404);
  });

  it("returns 500 on unexpected error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockSelect.mockRejectedValue(new Error("DB error"));
    const res = await GET(makeGetRequest());
    expect(res.status).toBe(500);
  });
});
