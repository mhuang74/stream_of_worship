import { describe, it, expect, beforeEach, vi } from "vitest";
import { NextRequest } from "next/server";

import { GET } from "@/app/api/songsets/[id]/glue-quote/route";
import { auth } from "@/lib/auth";
import { getGlueQuote, GlueQuoteError } from "@/lib/glue/quote";

// The route only needs the quote contract; the DB-backed loader is its own
// concern (quote.test.ts covers the pure values).
vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: vi.fn() } },
}));

vi.mock("@/lib/glue/quote", () => {
  class MockGlueQuoteError extends Error {
    readonly code: string;
    readonly status: number;
    constructor(code: string, status: number, message: string) {
      super(message);
      this.name = "GlueQuoteError";
      this.code = code;
      this.status = status;
    }
  }
  return { getGlueQuote: vi.fn(), GlueQuoteError: MockGlueQuoteError };
});

const mockGetGlueQuote = vi.mocked(getGlueQuote);

function createMockRequest(url: string): NextRequest {
  const request = new Request(url) as unknown as NextRequest;
  Object.defineProperty(request, "nextUrl", { value: new URL(url), writable: false });
  return request;
}

const QUOTE = {
  descriptorKey: "a".repeat(64),
  fromItemId: "item-a",
  toItemId: "item-b",
  expectedFromUpdatedAt: "2026-11-22T10:00:00.000Z",
  expectedToUpdatedAt: "2026-11-22T10:01:00.000Z",
  uncachedSides: [{ side: "A", seconds: 12, credits: 180 }],
  generationSeconds: 3,
  uploadCredits: 180,
  generationCredits: 45,
  estimatedNewCredits: 225,
  blockedSides: [],
  quotaLimit: 5,
  quotaUsed: 2,
  quotaRemaining: 3,
  quotaResetAt: "2026-11-23T09:15:00.000Z",
};

const params = { params: { id: "songset-1" } };

describe("GET /api/songsets/[id]/glue-quote", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns 401 when not authenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const response = await GET(
      createMockRequest(
        "http://localhost:3000/api/songsets/songset-1/glue-quote?fromItemId=item-a"
      ),
      params
    );
    expect(response.status).toBe(401);
    expect(mockGetGlueQuote).not.toHaveBeenCalled();
  });

  it("rejects a request without fromItemId", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as never);
    const response = await GET(
      createMockRequest("http://localhost:3000/api/songsets/songset-1/glue-quote"),
      params
    );
    expect(response.status).toBe(400);
    expect((await response.json()).code).toBe("GLUE_INVALID_REQUEST");
  });

  it("rejects an unknown takeMode", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as never);
    const response = await GET(
      createMockRequest(
        "http://localhost:3000/api/songsets/songset-1/glue-quote?fromItemId=item-a&takeMode=bogus"
      ),
      params
    );
    expect(response.status).toBe(400);
  });

  it("returns 404 for an unknown or unowned Songset item", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as never);
    mockGetGlueQuote.mockResolvedValue(null);
    const response = await GET(
      createMockRequest(
        "http://localhost:3000/api/songsets/songset-1/glue-quote?fromItemId=item-a"
      ),
      params
    );
    expect(response.status).toBe(404);
  });

  it("returns the stable code and status from a validation failure", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as never);
    mockGetGlueQuote.mockRejectedValue(
      new GlueQuoteError("GLUE_NOT_ENABLED", 409, "AI transition is not enabled.")
    );
    const response = await GET(
      createMockRequest(
        "http://localhost:3000/api/songsets/songset-1/glue-quote?fromItemId=item-a"
      ),
      params
    );
    expect(response.status).toBe(409);
    expect(await response.json()).toEqual({
      code: "GLUE_NOT_ENABLED",
      message: "AI transition is not enabled.",
    });
  });

  it("maps a duration-too-long failure to 422", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 1 } } as never);
    mockGetGlueQuote.mockRejectedValue(
      new GlueQuoteError("GLUE_DURATION_TOO_LONG", 422, "Too long.")
    );
    const response = await GET(
      createMockRequest(
        "http://localhost:3000/api/songsets/songset-1/glue-quote?fromItemId=item-a"
      ),
      params
    );
    expect(response.status).toBe(422);
    expect((await response.json()).code).toBe("GLUE_DURATION_TOO_LONG");
  });

  it("passes owner, item, and takeMode to the quote and returns it verbatim", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({ user: { id: 7 } } as never);
    mockGetGlueQuote.mockResolvedValue(QUOTE);
    const response = await GET(
      createMockRequest(
        "http://localhost:3000/api/songsets/songset-1/glue-quote?fromItemId=item-a&takeMode=new-take"
      ),
      params
    );
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual(QUOTE);
    expect(mockGetGlueQuote).toHaveBeenCalledWith({
      songsetId: "songset-1",
      userId: 7,
      fromItemId: "item-a",
      takeMode: "new-take",
    });
  });
});
