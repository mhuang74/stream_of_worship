import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";

import { GET } from "@/app/api/glues/[takeId]/audio/route";
import { auth, type Session } from "@/lib/auth";

const signedInSession = { user: { id: "7" } } as unknown as Session;

const mockLimit = vi.fn();
const mockGenerateSignedUrl = vi.fn();
const mockCreateR2ClientFromEnv = vi.fn();

const whereClauses: unknown[] = [];

vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: vi.fn() } },
}));

vi.mock("@/db", () => ({
  db: {
    select: () => ({
      from: () => ({
        where: (clause: unknown) => {
          whereClauses.push(clause);
          return { limit: (...args: unknown[]) => mockLimit(...args) };
        },
      }),
    }),
  },
}));

vi.mock("@/lib/r2/client", () => ({
  createR2ClientFromEnv: () => mockCreateR2ClientFromEnv(),
}));

/** Recursively collect string/number leaf values from a drizzle where clause. */
function collectLeafValues(node: unknown, out: (string | number)[] = [], depth = 0) {
  if (typeof node === "string" || typeof node === "number") {
    out.push(node);
    return out;
  }
  if (node == null || typeof node !== "object" || depth > 8) return out;
  for (const value of Object.values(node as Record<string, unknown>)) {
    collectLeafValues(value, out, depth + 1);
  }
  return out;
}

const TAKE_ID = "6b7f9c34-1c2b-4c4d-8a3a-1b2c3d4e5f60";
const R2_KEY = `glues/7/abc123/take-${TAKE_ID}.wav`;
const SIGNED_URL = "https://r2.example.com/signed.wav?sig=xyz";

function createRequest(
  headers: Record<string, string> = {},
  takeId = TAKE_ID,
): NextRequest {
  const url = `http://localhost:3000/api/glues/${takeId}/audio`;
  const request = new Request(url, { headers }) as unknown as NextRequest;
  Object.defineProperty(request, "nextUrl", { value: new URL(url), writable: false });
  return request;
}

function callGet(headers: Record<string, string> = {}, takeId = TAKE_ID) {
  return GET(createRequest(headers, takeId), { params: Promise.resolve({ takeId }) });
}

function streamingUpstream(
  chunks: string[],
  init: { status: number; headers?: Record<string, string> },
): Response {
  const encoder = new TextEncoder();
  let index = 0;
  return new Response(
    new ReadableStream<Uint8Array>({
      pull(controller) {
        if (index < chunks.length) {
          controller.enqueue(encoder.encode(chunks[index++]));
        } else {
          controller.close();
        }
      },
    }),
    init,
  );
}

describe("/api/glues/[takeId]/audio", () => {
  const mockFetch = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    whereClauses.length = 0;
    vi.stubGlobal("fetch", mockFetch);

    vi.mocked(auth.api.getSession).mockResolvedValue(signedInSession);
    mockLimit.mockResolvedValue([
      { id: TAKE_ID, status: "ready", r2Key: R2_KEY, seconds: 2.786 },
    ]);
    mockCreateR2ClientFromEnv.mockReturnValue({
      generateSignedUrl: (...args: unknown[]) => mockGenerateSignedUrl(...args),
    });
    mockGenerateSignedUrl.mockResolvedValue({
      url: SIGNED_URL,
      expiresAt: new Date("2026-01-01T00:01:00Z"),
      cacheControl: "private, max-age=3600",
    });
    mockFetch.mockResolvedValue(
      streamingUpstream(["RIFF"], {
        status: 200,
        headers: { "content-length": "4" },
      }),
    );
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("rejects unauthenticated requests", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);
    const response = await callGet();
    expect(response.status).toBe(401);
    expect(mockLimit).not.toHaveBeenCalled();
  });

  it("scopes the take lookup to the session owner and ready state", async () => {
    await callGet();
    const leaves = collectLeafValues(whereClauses[0]);
    expect(leaves).toContain(TAKE_ID);
    expect(leaves).toContain(7);
    expect(leaves).toContain("ready");
  });

  it("returns 404 for another user's take id (no existence leak)", async () => {
    // Owner-scoped SQL cannot return a foreign row; the route must not fall
    // back to a global lookup.
    mockLimit.mockResolvedValue([]);
    const response = await callGet();
    expect(response.status).toBe(404);
    expect(mockGenerateSignedUrl).not.toHaveBeenCalled();
  });

  it("returns 404 for a non-ready take, missing key, or zero seconds", async () => {
    for (const row of [
      { id: TAKE_ID, r2Key: R2_KEY, seconds: 2.786, status: "failed" },
      { id: TAKE_ID, status: "ready", r2Key: null, seconds: 2.786 },
      { id: TAKE_ID, status: "ready", r2Key: R2_KEY, seconds: 0 },
    ]) {
      mockLimit.mockResolvedValue([row]);
      const response = await callGet();
      expect(response.status).toBe(404);
    }
    // The status/seconds guards live in the SQL predicate as well.
    const leaves = collectLeafValues(whereClauses[0]);
    expect(leaves).toContain("ready");
  });

  it("streams the take's immutable key through a 60 s signed URL", async () => {
    const response = await callGet();
    expect(response.status).toBe(200);
    expect(mockGenerateSignedUrl).toHaveBeenCalledWith(R2_KEY, "audio", {
      expiresInSeconds: 60,
    });
    expect(response.headers.get("Content-Type")).toBe("audio/wav");
    expect(response.headers.get("Cache-Control")).toBe("private, max-age=3600");
    expect(response.headers.get("Accept-Ranges")).toBe("bytes");
    expect(response.headers.get("Content-Length")).toBe("4");
    // The client sent no R2 key; the URL carried only the take id.
    expect(mockFetch).toHaveBeenCalledWith(SIGNED_URL, { headers: {} });
  });

  it("forwards Range and mirrors a 206 with Content-Range", async () => {
    mockFetch.mockResolvedValue(
      streamingUpstream(["RIF"], {
        status: 206,
        headers: {
          "content-length": "3",
          "content-range": "bytes 0-2/4",
        },
      }),
    );
    const response = await callGet({ range: "bytes=0-2" });
    expect(mockFetch).toHaveBeenCalledWith(SIGNED_URL, {
      headers: { Range: "bytes=0-2" },
    });
    expect(response.status).toBe(206);
    expect(response.headers.get("Content-Range")).toBe("bytes 0-2/4");
    expect(response.headers.get("Content-Length")).toBe("3");
  });

  it("returns 503 when R2 is not configured", async () => {
    mockCreateR2ClientFromEnv.mockImplementation(() => {
      throw new Error("missing env");
    });
    const response = await callGet();
    expect(response.status).toBe(503);
  });

  it("returns 502 when the upstream fetch fails", async () => {
    mockFetch.mockRejectedValue(new Error("network"));
    const response = await callGet();
    expect(response.status).toBe(502);
  });
});
