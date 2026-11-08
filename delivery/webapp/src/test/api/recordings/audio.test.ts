import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NextRequest } from "next/server";
import { GET } from "@/app/api/recordings/[hashPrefix]/audio/route";
import { auth, type Session } from "@/lib/auth";

// The route only reads `session.user`, so a partial session is sufficient for
// the mock; the cast bridges the full better-auth Session shape.
const signedInSession = { user: { id: "1" } } as unknown as Session;

const mockRecordingFindFirst = vi.fn();
const mockGetAudioSignedUrl = vi.fn();
const mockCreateR2ClientFromEnv = vi.fn();

/** Recursively collect string leaf values from a drizzle where clause,
 * so tests can assert which literal values a query filter accepts. */
function collectLeafValues(node: unknown, out: string[] = [], depth = 0): string[] {
  if (typeof node === "string") {
    out.push(node);
    return out;
  }
  if (node == null || typeof node !== "object" || depth > 8) return out;
  for (const value of Object.values(node as Record<string, unknown>)) {
    collectLeafValues(value, out, depth + 1);
  }
  return out;
}

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
      recordings: {
        findFirst: (...args: unknown[]) => mockRecordingFindFirst(...args),
      },
    },
  },
}));

vi.mock("@/lib/r2/client", () => ({
  createR2ClientFromEnv: () => mockCreateR2ClientFromEnv(),
}));

const HASH_PREFIX = "abc123def456";
const SIGNED_URL = "https://r2.example.com/abc123def456/audio.mp3?sig=xyz";

function createRequest(
  headers: Record<string, string> = {},
  hashPrefix = HASH_PREFIX
): NextRequest {
  const url = `http://localhost:3000/api/recordings/${hashPrefix}/audio`;
  const request = new Request(url, { headers }) as unknown as NextRequest;
  Object.defineProperty(request, "nextUrl", {
    value: new URL(url),
    writable: false,
  });
  return request;
}

function callGet(headers: Record<string, string> = {}, hashPrefix = HASH_PREFIX) {
  return GET(createRequest(headers, hashPrefix), {
    params: Promise.resolve({ hashPrefix }),
  });
}

/**
 * Build an upstream response whose body lazily yields `chunks`, counting the
 * pulls. An implementation that buffers the MP3 (arrayBuffer/text) drains the
 * stream before returning; a streaming implementation has not pulled anything
 * when the route response comes back.
 */
function streamingUpstream(
  chunks: string[],
  init: { status: number; headers?: Record<string, string> },
  pulls: { count: number }
): Response {
  const encoder = new TextEncoder();
  let index = 0;
  const body = new ReadableStream<Uint8Array>({
    pull(controller) {
      pulls.count++;
      if (index < chunks.length) {
        controller.enqueue(encoder.encode(chunks[index]));
        index++;
      } else {
        controller.close();
      }
    },
  });
  return new Response(body, init);
}

describe("/api/recordings/[hashPrefix]/audio", () => {
  const mockFetch = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("fetch", mockFetch);

    vi.mocked(auth.api.getSession).mockResolvedValue(signedInSession);
    mockRecordingFindFirst.mockResolvedValue({ hashPrefix: HASH_PREFIX });
    mockCreateR2ClientFromEnv.mockReturnValue({
      getAudioSignedUrl: (...args: unknown[]) => mockGetAudioSignedUrl(...args),
    });
    mockGetAudioSignedUrl.mockResolvedValue({
      url: SIGNED_URL,
      expiresAt: new Date("2026-01-01T00:01:00Z"),
      cacheControl: "private, max-age=3600",
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns 401 when unauthenticated without touching the database or R2", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null);

    const response = await callGet();

    expect(response.status).toBe(401);
    expect(mockRecordingFindFirst).not.toHaveBeenCalled();
    expect(mockGetAudioSignedUrl).not.toHaveBeenCalled();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("returns 404 when the recording is not visible (published/review) or is deleted", async () => {
    mockRecordingFindFirst.mockResolvedValue(undefined);

    const response = await callGet();

    expect(response.status).toBe(404);
    expect(mockGetAudioSignedUrl).not.toHaveBeenCalled();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("filters the lookup by hashPrefix, allowed visibility, and non-deleted", async () => {
    mockFetch.mockResolvedValue(new Response("audio", { status: 200 }));

    await callGet({}, HASH_PREFIX);

    expect(mockRecordingFindFirst).toHaveBeenCalledTimes(1);
    const where = mockRecordingFindFirst.mock.calls[0][0].where;
    const leaves = collectLeafValues(where);
    expect(leaves).toEqual(
      expect.arrayContaining([HASH_PREFIX, "published", "review"])
    );
    expect(leaves.some((leaf) => /is null/i.test(leaf))).toBe(true);
  });

  it("returns 503 when R2 is unconfigured", async () => {
    mockCreateR2ClientFromEnv.mockImplementation(() => {
      throw new Error("R2 credentials not configured.");
    });

    const response = await callGet();

    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({
      error: "R2 storage not configured",
    });
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("mints a 60-second signed URL for {hashPrefix}/audio.mp3 via the audio helper", async () => {
    mockFetch.mockResolvedValue(new Response("audio", { status: 200 }));

    await callGet();

    expect(mockGetAudioSignedUrl).toHaveBeenCalledWith(HASH_PREFIX, {
      expiresInSeconds: 60,
    });
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch.mock.calls[0][0]).toBe(SIGNED_URL);
  });

  it("forwards the incoming Range header to R2 and mirrors 206 headers", async () => {
    mockFetch.mockResolvedValue(
      streamingUpstream(
        ["a".repeat(4)],
        {
          status: 206,
          headers: {
            "content-length": "4",
            "content-range": "bytes 0-3/1000",
            "content-type": "audio/mpeg",
          },
        },
        { count: 0 }
      )
    );

    const response = await callGet({ range: "bytes=0-3" });

    expect(mockFetch).toHaveBeenCalledTimes(1);
    const init = mockFetch.mock.calls[0][1];
    expect(init?.headers).toEqual({ Range: "bytes=0-3" });

    expect(response.status).toBe(206);
    expect(response.headers.get("Content-Range")).toBe("bytes 0-3/1000");
    expect(response.headers.get("Content-Length")).toBe("4");
    expect(response.headers.get("Accept-Ranges")).toBe("bytes");
    expect(response.headers.get("Content-Type")).toBe("audio/mpeg");
    expect(response.headers.get("Cache-Control")).toBe("private, max-age=3600");
  });

  it("returns 200 without a Range header and streams the body instead of buffering it", async () => {
    const pulls = { count: 0 };
    const chunks = ["first-", "second-", "third-", "fourth"];
    mockFetch.mockResolvedValue(
      streamingUpstream(
        chunks,
        {
          status: 200,
          headers: { "content-length": "25", "content-type": "audio/mpeg" },
        },
        pulls
      )
    );

    const response = await callGet();

    expect(response.status).toBe(200);
    expect(mockFetch.mock.calls[0][1]?.headers).toEqual({});
    expect(response.headers.get("Content-Length")).toBe("25");
    expect(response.headers.get("Content-Range")).toBeNull();
    expect(response.headers.get("Accept-Ranges")).toBe("bytes");

    // Not buffered: the upstream stream is still unread when GET returns.
    expect(pulls.count).toBeLessThan(chunks.length);
    expect(response.body).toBeInstanceOf(ReadableStream);

    // And the body streams through intact when the caller reads it.
    expect(await response.text()).toBe(chunks.join(""));
    expect(pulls.count).toBeGreaterThanOrEqual(chunks.length);
  });

  it("does not forward a Range header when the request has none", async () => {
    mockFetch.mockResolvedValue(new Response("audio", { status: 200 }));

    await callGet();

    expect(mockFetch.mock.calls[0][1]?.headers).toEqual({});
  });

  it("returns 502 when R2 responds with an error status", async () => {
    mockFetch.mockResolvedValue(new Response("upstream boom", { status: 500 }));

    const response = await callGet();

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({
      error: "Failed to fetch from storage",
    });
  });

  it("returns 502 when the upstream fetch throws", async () => {
    mockFetch.mockRejectedValue(new Error("network down"));

    const response = await callGet();

    expect(response.status).toBe(502);
    expect(await response.json()).toEqual({
      error: "Failed to fetch from storage",
    });
  });
});
