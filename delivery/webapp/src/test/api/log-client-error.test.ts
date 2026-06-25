import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { NextRequest } from "next/server";
import { POST, GET, __resetLimiterForTests } from "@/app/api/log-client-error/route";

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

function makePostRequest(body: unknown, ip = "203.0.113.9"): NextRequest {
  const req = new Request("http://localhost/api/log-client-error", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "x-forwarded-for": ip,
    },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
  return req as unknown as NextRequest;
}

const validBody = {
  message: "loadMedia rejected",
  kind: "cast_load" as const,
  meta: {
    browser: "Mozilla/5.0 (Android)",
    platform: "android",
    castAppIdMode: "default" as const,
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
        browser: "Mozilla/5.0 (Android)",
        platform: "android",
        castAppIdMode: "default",
        transportKind: "cast",
        mediaSourceKind: "songset",
      })
    );
  });

  it("redacts meta.url to host+path+expiry age, stripping the query/signature", async () => {
    const signedUrl =
      "https://r2.example.com/renders/job-123/output.mp4?X-Amz-Signature=abc&X-Amz-Expires=14400";
    const res = await POST(
      makePostRequest({
        message: "media load failed",
        kind: "cast_load",
        meta: { url: signedUrl, urlExpired: false },
      })
    );
    expect(res.status).toBe(202);
    const row = mockInsertValues.mock.calls[0][0];
    const meta = JSON.parse(row.metaJson);
    expect(meta.url).toBe("r2.example.com/renders/job-123/output.mp4 (fresh)");
    // The signature must never reach the row.
    expect(row.metaJson).not.toContain("X-Amz-Signature");
    expect(row.metaJson).not.toContain("abc");
  });

  it("marks url as expired when meta.urlExpired is true", async () => {
    const res = await POST(
      makePostRequest({
        message: "stale url",
        kind: "presentation",
        meta: { url: "https://r2.example.com/renders/x/y.mp4?sig=zzz", urlExpired: true },
      })
    );
    expect(res.status).toBe(202);
    const meta = JSON.parse(mockInsertValues.mock.calls[0][0].metaJson);
    expect(meta.url).toBe("r2.example.com/renders/x/y.mp4 (expired)");
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
  });
});

describe("GET /api/log-client-error", () => {
  it("returns 405 Method Not Allowed", async () => {
    const res = await GET();
    expect(res.status).toBe(405);
  });
});
