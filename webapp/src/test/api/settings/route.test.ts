import { describe, it, expect, vi, beforeEach } from "vitest";
import { GET, PUT } from "@/app/api/settings/route";
import { auth } from "@/lib/auth";
import { NextRequest } from "next/server";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: vi.fn() } },
}));

const mockSelect = vi.fn();
const mockInsert = vi.fn();

vi.mock("@/db", () => ({
  db: {
    select: (...args: unknown[]) => mockSelect(...args),
    insert: (...args: unknown[]) => mockInsert(...args),
  },
}));

const sessionUser = { user: { id: 42 } };

const storedSettings = {
  userId: 42,
  offlineAutoCache: false,
  defaultGapBeats: 3.0,
  defaultVideoTemplate: "gradient_warm",
  defaultResolution: "1080p",
  lyricsLoopWindowSeconds: 5.0,
  defaultFontSizePreset: "L",
  defaultKeyShiftSemitones: 2,
  timingReviewFont: "mono",
};

function makeRequest(method: string, body?: unknown): NextRequest {
  const init: RequestInit = { method };
  if (body !== undefined) {
    init.body = JSON.stringify(body);
    init.headers = { "Content-Type": "application/json" };
  }
  const req = new Request("http://localhost/api/settings", init) as unknown as NextRequest;
  Object.defineProperty(req, "nextUrl", {
    value: new URL("http://localhost/api/settings"),
    writable: false,
  });
  return req;
}

// --------------------------------------------------------------------------
// GET /api/settings
// --------------------------------------------------------------------------

describe("GET /api/settings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await GET(makeRequest("GET"));
    expect(res.status).toBe(401);
    const data = await res.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("returns default settings when no record exists", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const mockFrom = vi.fn().mockReturnThis();
    const mockWhere = vi.fn().mockResolvedValue([]);
    mockSelect.mockReturnValue({ from: mockFrom });
    mockFrom.mockReturnValue({ where: mockWhere });

    const res = await GET(makeRequest("GET"));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.settings.defaultGapBeats).toBe(2.0);
    expect(data.settings.defaultVideoTemplate).toBe("dark");
    expect(data.settings.defaultResolution).toBe("720p");
    expect(data.settings.lyricsLoopWindowSeconds).toBe(3.0);
    expect(data.settings.defaultFontSizePreset).toBe("M");
    expect(data.settings.offlineAutoCache).toBe(true);
    expect(data.settings.defaultKeyShiftSemitones).toBe(0);
    expect(data.settings.timingReviewFont).toBe("sans");
  });

  it("returns stored settings when record exists", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const mockFrom = vi.fn().mockReturnThis();
    const mockWhere = vi.fn().mockResolvedValue([storedSettings]);
    mockSelect.mockReturnValue({ from: mockFrom });
    mockFrom.mockReturnValue({ where: mockWhere });

    const res = await GET(makeRequest("GET"));
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.settings.defaultGapBeats).toBe(3.0);
    expect(data.settings.defaultVideoTemplate).toBe("gradient_warm");
    expect(data.settings.defaultResolution).toBe("1080p");
    expect(data.settings.defaultFontSizePreset).toBe("L");
    expect(data.settings.timingReviewFont).toBe("mono");
  });

  it("returns 500 on database error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const mockFrom = vi.fn().mockReturnThis();
    const mockWhere = vi.fn().mockRejectedValue(new Error("DB error"));
    mockSelect.mockReturnValue({ from: mockFrom });
    mockFrom.mockReturnValue({ where: mockWhere });

    const res = await GET(makeRequest("GET"));
    expect(res.status).toBe(500);
    const data = await res.json();
    expect(data.error).toBe("Failed to fetch settings");
  });
});

// --------------------------------------------------------------------------
// PUT /api/settings
// --------------------------------------------------------------------------

describe("PUT /api/settings", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  function mockUpsert() {
    const mockValues = vi.fn().mockReturnThis();
    const mockOnConflict = vi.fn().mockResolvedValue([]);
    mockInsert.mockReturnValue({ values: mockValues });
    mockValues.mockReturnValue({ onConflictDoUpdate: mockOnConflict });
    return { mockValues, mockOnConflict };
  }

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const res = await PUT(makeRequest("PUT", { offlineAutoCache: true }));
    expect(res.status).toBe(401);
    const data = await res.json();
    expect(data.error).toBe("Unauthorized");
  });

  it("returns 400 for invalid JSON", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const req = new Request("http://localhost/api/settings", {
      method: "PUT",
      body: "not json",
    }) as unknown as NextRequest;
    const res = await PUT(req);
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toBe("Invalid JSON body");
  });

  it("saves settings successfully", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    mockUpsert();

    const res = await PUT(
      makeRequest("PUT", {
        offlineAutoCache: false,
        defaultGapBeats: 4.0,
        defaultVideoTemplate: "dark",
        defaultResolution: "1080p",
        lyricsLoopWindowSeconds: 5.0,
        defaultFontSizePreset: "XL",
        defaultKeyShiftSemitones: -2,
        timingReviewFont: "mono",
      })
    );
    expect(res.status).toBe(200);
    const data = await res.json();
    expect(data.settings.defaultGapBeats).toBe(4.0);
    expect(data.settings.defaultResolution).toBe("1080p");
    expect(data.settings.timingReviewFont).toBe("mono");
  });

  it("returns 400 for invalid defaultVideoTemplate", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const res = await PUT(makeRequest("PUT", { defaultVideoTemplate: "neon" }));
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toMatch(/defaultVideoTemplate/);
  });

  it("returns 400 for invalid defaultResolution", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const res = await PUT(makeRequest("PUT", { defaultResolution: "4k" }));
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toMatch(/defaultResolution/);
  });

  it("returns 400 for invalid defaultFontSizePreset", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const res = await PUT(makeRequest("PUT", { defaultFontSizePreset: "XXL" }));
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toMatch(/defaultFontSizePreset/);
  });

  it("returns 400 for invalid timingReviewFont", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const res = await PUT(makeRequest("PUT", { timingReviewFont: "comic-sans" }));
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toMatch(/timingReviewFont/);
  });

  it("returns 400 for out-of-range defaultGapBeats", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const res = await PUT(makeRequest("PUT", { defaultGapBeats: 20 }));
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toMatch(/defaultGapBeats/);
  });

  it("returns 400 for out-of-range lyricsLoopWindowSeconds", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const res = await PUT(makeRequest("PUT", { lyricsLoopWindowSeconds: 60 }));
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toMatch(/lyricsLoopWindowSeconds/);
  });

  it("returns 400 for out-of-range defaultKeyShiftSemitones", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const res = await PUT(makeRequest("PUT", { defaultKeyShiftSemitones: 12 }));
    expect(res.status).toBe(400);
    const data = await res.json();
    expect(data.error).toMatch(/defaultKeyShiftSemitones/);
  });

  it("returns 500 on database error", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(sessionUser as any);
    const mockValues = vi.fn().mockReturnThis();
    const mockOnConflict = vi.fn().mockRejectedValue(new Error("DB error"));
    mockInsert.mockReturnValue({ values: mockValues });
    mockValues.mockReturnValue({ onConflictDoUpdate: mockOnConflict });

    const res = await PUT(makeRequest("PUT", { defaultGapBeats: 2.0 }));
    expect(res.status).toBe(500);
    const data = await res.json();
    expect(data.error).toBe("Failed to save settings");
  });
});
