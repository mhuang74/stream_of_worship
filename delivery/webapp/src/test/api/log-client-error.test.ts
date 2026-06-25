import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { NextRequest } from "next/server";
import { POST, GET, PUT, DELETE, __resetLimiterForTests } from "@/app/api/log-client-error/route";

const mockInsertValues = vi.fn();

vi.mock("@/db", () => ({
  db: {
    insert: () => ({ values: (...args: unknown[]) => {
      mockInsertValues(...args);
      return { onConflictDoNothing: vi.fn() };
    } }),
  },
}));

// Mocked limiter factory — returns success true until the configured allow
// count is exceeded, then false. Reset per-test via `setAllowCount`.
let allowCount = 999;
let limitCallCount = 0;
const limitMock = vi.fn(async (id: string) => {
  limitCallCount += 1;
  void id;
  return { success: limitCallCount <= allowCount };
});

function setAllowCount(n: number): void {
  allowCount = n;
  limitCallCount = 0;
  limitMock.mockClear();
}

vi.mock("@upstash/ratelimit", () => ({
  Ratelimit: Object.assign(
    class MockRatelimit {
      constructor() {}
      limit = (...args: unknown[]) => limitMock(...(args as [string]));
    },
    {
      // tokenBucket(maxTokens, window) returns an opaque limiter fn; the mock
      // Ratelimit constructor ignores it (limit behavior driven by limitMock).
      tokenBucket: () => () => ({}),
    }
  ),
}));

vi.mock("@upstash/redis", () => ({
  Redis: class MockRedis {
    constructor() {}
  },
}));

function makePostRequest(
  body: unknown,
  ip = "203.0.113.9",
  headers: Record<string, string> = {},
): NextRequest {
  const req = new Request("http://localhost/api/log-client-error", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      // Use Vercel's platform-trusted header (not the spoofable leftmost XFF
      // segment) so the rate-limit key reflects the real client IP.
      "x-vercel-forwarded-for": ip,
      ...headers,
    },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
  return req as unknown as NextRequest;
}

const validBody = {
  message: "loadMedia rejected",
  kind: "cast_load" as const,
  meta: {
    platform: "android",
    castAppIdMode: "set" as const,
    transportKind: "cast" as const,
    mediaSourceKind: "songset" as const,
  },
};

describe("POST /api/log-client-error", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockInsertValues.mockReturnValue({ onConflictDoNothing: vi.fn() });
    setAllowCount(999);
    __resetLimiterForTests();
    // No Upstash env vars by default → limiter is null (allow-all, dev/test).
    delete process.env.UPSTASH_REDIS_REST_URL;
    delete process.env.UPSTASH_REDIS_REST_TOKEN;
  });

  afterEach(() => {
    __resetLimiterForTests();
    delete process.env.UPSTASH_REDIS_REST_URL;
    delete process.env.UPSTASH_REDIS_REST_TOKEN;
  });

  it("returns 400 on malformed JSON body", async () => {
    const req = new Request("http://localhost/api/log-client-error", {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-forwarded-for": "1.2.3.4" },
      body: "not json{",
    }) as unknown as NextRequest;
    const res = await POST(req);
    expect(res.status).toBe(400);
    expect(mockInsertValues).not.toHaveBeenCalled();
  });

  it("returns 400 when body fails zod validation", async () => {
    const res = await POST(makePostRequest({ message: "", kind: "cast_load" }));
    expect(res.status).toBe(400);
    const res2 = await POST(makePostRequest({ message: "x", kind: "bogus" }));
    expect(res2.status).toBe(400);
  });

  it("returns 202 and persists a row for a well-formed payload", async () => {
    const res = await POST(makePostRequest(validBody));
    expect(res.status).toBe(202);
    expect(mockInsertValues).toHaveBeenCalledOnce();
    const row = mockInsertValues.mock.calls[0][0];
    expect(row.kind).toBe("cast_load");
    expect(row.message).toBe("loadMedia rejected");
    expect(row.ipHash).toMatch(/^[0-9a-f]{64}$/);
    expect(row.metaJson).toContain("android");
    // No user ID column exists; verify only the redaction-safe fields persist.
    expect(Object.keys(row)).toEqual(
      expect.arrayContaining(["ipHash", "message", "kind", "metaJson"])
    );
    expect(Object.keys(row)).not.toContain("userId");
  });

  it("persists structured fields when provided (full meta)", async () => {
    const res = await POST(makePostRequest(validBody));
    expect(res.status).toBe(202);
    const row = mockInsertValues.mock.calls[0][0];
    const meta = JSON.parse(row.metaJson);
    expect(meta).toEqual(
      expect.objectContaining({
        platform: "android",
        castAppIdMode: "set",
        transportKind: "cast",
        mediaSourceKind: "songset",
      })
    );
  });

  it("accepts browser / castAppIdMode:'default' / urlRedacted meta fields", async () => {
    const res = await POST(
      makePostRequest({
        message: "m",
        kind: "other",
        meta: {
          browser: "chrome/120",
          castAppIdMode: "default",
          urlRedacted: {
            host: "r2.example.com",
            path: "/renders/job-1/out.mp4",
            expired: false,
          },
        },
      }),
    );
    expect(res.status).toBe(202);
    const row = mockInsertValues.mock.calls[0][0];
    const meta = JSON.parse(row.metaJson);
    expect(meta.browser).toBe("chrome/120");
    expect(meta.castAppIdMode).toBe("default");
    // The producer pre-redacts on the client: the wire field is `urlRedacted`
    // (matching persistence), and the raw URL is never received.
    expect(meta.url).toBeUndefined();
    expect(meta.urlRedacted).toEqual(
      expect.objectContaining({
        host: "r2.example.com",
        path: "/renders/job-1/out.mp4",
        expired: false,
      }),
    );
  });

  it("persists the client-pre-redacted urlRedacted summary verbatim (no server-side redaction)", async () => {
    const res = await POST(
      makePostRequest({
        message: "m",
        kind: "other",
        meta: {
          urlRedacted: {
            host: "r2.example.com",
            path: "/renders/job-1/out.mp4",
            expired: true,
          },
        },
      }),
    );
    expect(res.status).toBe(202);
    const meta = JSON.parse(mockInsertValues.mock.calls[0][0].metaJson);
    expect(meta.urlRedacted.expired).toBe(true);
    expect(meta.urlRedacted.host).toBe("r2.example.com");
    expect(meta.urlRedacted.path).toBe("/renders/job-1/out.mp4");
    // No raw URL field is ever accepted or persisted.
    expect(meta.url).toBeUndefined();
  });

  it("rejects raw `url` field (strict schema — only urlRedacted is accepted)", async () => {
    const res = await POST(
      makePostRequest({
        message: "m",
        kind: "other",
        meta: {
          url: "https://r2.example.com/renders/job-1/out.mp4?sig=abc",
        },
      }),
    );
    expect(res.status).toBe(400);
  });

  it("rejects unknown meta fields (strict schema)", async () => {
    const res = await POST(
      makePostRequest({
        message: "m",
        kind: "other",
        meta: { unexpectedField: "x" },
      }),
    );
    expect(res.status).toBe(400);
  });

  it("works without a session (no auth required)", async () => {
    // No session mocking at all; endpoint must still accept.
    const res = await POST(makePostRequest({ message: "m", kind: "other" }));
    expect(res.status).toBe(202);
  });

  it("hashes IP with a daily salt (same IP same day → same hash)", async () => {
    await POST(makePostRequest({ message: "m", kind: "other" }, "198.51.100.1"));
    await POST(makePostRequest({ message: "m", kind: "other" }, "198.51.100.1"));
    const a = mockInsertValues.mock.calls[0][0].ipHash;
    const b = mockInsertValues.mock.calls[1][0].ipHash;
    expect(a).toBe(b);
    expect(a).toMatch(/^[0-9a-f]{64}$/);
  });

  it("produces distinct hashes for distinct IPs", async () => {
    await POST(makePostRequest({ message: "m", kind: "other" }, "198.51.100.1"));
    await POST(makePostRequest({ message: "m", kind: "other" }, "198.51.100.2"));
    const a = mockInsertValues.mock.calls[0][0].ipHash;
    const b = mockInsertValues.mock.calls[1][0].ipHash;
    expect(a).not.toBe(b);
  });

  it("falls back to x-real-ip when x-forwarded-for is absent", async () => {
    await POST(
      makePostRequest({ message: "m", kind: "other" }, "", { "x-real-ip": "5.6.7.8" }),
    );
    await POST(makePostRequest({ message: "m", kind: "other" }, "9.9.9.9"));
    const real = mockInsertValues.mock.calls[0][0].ipHash;
    const xff = mockInsertValues.mock.calls[1][0].ipHash;
    expect(real).not.toBe(xff);
  });

  it("uses an 'unknown' bucket (stable hash) when no proxy header is present", async () => {
    const req = new Request("http://localhost/api/log-client-error", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: "m", kind: "other" }),
    }) as unknown as NextRequest;
    const res = await POST(req);
    expect(res.status).toBe(202);
    expect(mockInsertValues.mock.calls[0][0].ipHash).toMatch(/^[0-9a-f]{64}$/);
    // Repeated unknown-IP requests share the same bucket.
    const res2 = await POST(
      new Request("http://localhost/api/log-client-error", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: "m2", kind: "other" }),
      }) as unknown as NextRequest,
    );
    expect(res2.status).toBe(202);
    expect(mockInsertValues.mock.calls[1][0].ipHash).toBe(
      mockInsertValues.mock.calls[0][0].ipHash,
    );
  });

  it("returns 202 even when the DB write throws (best-effort persistence)", async () => {
    mockInsertValues.mockImplementation(() => {
      throw new Error("DB down");
    });
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const res = await POST(makePostRequest({ message: "m", kind: "other" }));
    expect(res.status).toBe(202);
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });

  it("returns 202 when meta is omitted entirely", async () => {
    const res = await POST(makePostRequest({ message: "no meta", kind: "other" }));
    expect(res.status).toBe(202);
    const row = mockInsertValues.mock.calls[0][0];
    expect(row.metaJson).toBeNull();
  });

  it("applies an in-memory fallback rate limit when Upstash is not configured", async () => {
    // A prod misconfig (missing Upstash env) must NOT silently disable rate
    // limiting — the in-memory token-bucket fallback still enforces 20/min.
    const results: number[] = [];
    for (let i = 0; i < 21; i++) {
      const res = await POST(makePostRequest({ message: `m${i}`, kind: "other" }));
      results.push(res.status);
    }
    expect(results.slice(0, 20).every((s) => s === 202)).toBe(true);
    expect(results[20]).toBe(429);
  });

  // ---- Rate limiting (Upstash-distributed) --------------------------------

  describe("rate limit (Upstash token bucket)", () => {
    beforeEach(() => {
      process.env.UPSTASH_REDIS_REST_URL = "https://redis.local";
      process.env.UPSTASH_REDIS_REST_TOKEN = "tok";
      __resetLimiterForTests();
      setAllowCount(20);
    });

    it("returns 429 after 20 req/min from one IP", async () => {
      const results: number[] = [];
      for (let i = 0; i < 21; i++) {
        const res = await POST(makePostRequest({ message: `m${i}`, kind: "other" }));
        results.push(res.status);
      }
      // First 20 accepted, 21st rate limited.
      expect(results.slice(0, 20).every((s) => s === 202)).toBe(true);
      expect(results[20]).toBe(429);
      // The 429 must not have persisted a row.
      expect(mockInsertValues).toHaveBeenCalledTimes(20);
    });

    it("does not persist a row when rate limited", async () => {
      setAllowCount(0);
      const res = await POST(makePostRequest({ message: "m", kind: "other" }));
      expect(res.status).toBe(429);
      expect(mockInsertValues).not.toHaveBeenCalled();
    });

    it("rate-limits per IP (limiter keyed by hashed IP, not a global constant)", async () => {
      // Send a handful of requests from two distinct IPs. The mock limiter
      // is a single global counter, so we cannot assert per-IP allowance
      // semantics here — instead we assert the limiter is INVOKED with
      // distinct keys derived from each IP (the per-IP rate-limit contract).
      setAllowCount(999);
      for (let i = 0; i < 5; i++) {
        await POST(makePostRequest({ message: `a${i}`, kind: "other" }, "1.1.1.1"));
        await POST(makePostRequest({ message: `b${i}`, kind: "other" }, "2.2.2.2"));
      }
      const ids = limitMock.mock.calls.map((c) => c[0]);
      const distinct = new Set(ids);
      // Two distinct IP-derived buckets.
      expect(distinct.size).toBe(2);
      // No cross-IP key collision.
      const firstIpKeys = ids.filter((_, i) => i % 2 === 0);
      const secondIpKeys = ids.filter((_, i) => i % 2 === 1);
      expect(new Set(firstIpKeys).size).toBe(1);
      expect(new Set(secondIpKeys).size).toBe(1);
      expect(firstIpKeys[0]).not.toBe(secondIpKeys[0]);
    });

    it("falls back to the in-memory limiter when Upstash throws (degrades, never fail-open)", async () => {
      // When Upstash is configured but throws (network blip / 5xx / timeout),
      // the contract is to degrade to the per-instance in-memory token bucket
      // — NOT to fail open with `return true`. On a fresh instance the
      // in-memory bucket has tokens, so the first request still succeeds.
      limitMock.mockRejectedValueOnce(new Error("upstash down"));
      const res = await POST(makePostRequest({ message: "m", kind: "other" }));
      expect(res.status).toBe(202);
      expect(mockInsertValues).toHaveBeenCalledOnce();
      // The in-memory fallback (not the Upstash mock) decided this request.
      expect(limitMock).toHaveBeenCalledTimes(1);
    });

    it("rate-limits via the in-memory fallback when Upstash throws and the bucket is empty", async () => {
      // Drain the in-memory fallback bucket first (20 tokens) by forcing the
      // Upstash limiter to throw on every call so each request falls through
      // to inMemoryLimit.
      limitMock.mockRejectedValue(new Error("upstash down"));
      const results: number[] = [];
      for (let i = 0; i < 21; i++) {
        const res = await POST(makePostRequest({ message: `m${i}`, kind: "other" }));
        results.push(res.status);
      }
      // First 20 fall through to the in-memory bucket and succeed; 21st is
      // rate-limited by the in-memory fallback — proving the route does NOT
      // fail open during an Upstash outage.
      expect(results.slice(0, 20).every((s) => s === 202)).toBe(true);
      expect(results[20]).toBe(429);
    });
  });
});

describe("GET /api/log-client-error", () => {
  it("returns 405 Method Not Allowed", async () => {
    const res = await GET();
    expect(res.status).toBe(405);
  });
});

describe("PUT /api/log-client-error", () => {
  it("returns 405 Method Not Allowed", async () => {
    const res = await PUT();
    expect(res.status).toBe(405);
  });
});

describe("DELETE /api/log-client-error", () => {
  it("returns 405 Method Not Allowed", async () => {
    const res = await DELETE();
    expect(res.status).toBe(405);
  });
});
