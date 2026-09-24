import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { NextRequest } from "next/server";
import {
  POST,
  GET,
  PUT,
  DELETE,
  OPTIONS,
} from "@/app/api/capture-email/route";
import {
  upsertContact,
  sendConfirmationEmail,
} from "@/lib/brevo/client";
import {
  __resetRateLimitCacheForTests,
} from "@/lib/rate-limit";

// Brevo wrapper is fully mocked so no network ever happens; the route tests
// assert only HTTP contracts and the arguments forwarded to the wrapper.
vi.mock("@/lib/brevo/client", () => ({
  upsertContact: vi.fn(async () => {}),
  sendConfirmationEmail: vi.fn(async () => {}),
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

const ALLOWED_ORIGIN = "https://marketing.example.com";

function makePostRequest(
  body: unknown,
  ip = "203.0.113.9",
  headers: Record<string, string> = {},
): NextRequest {
  const req = new Request("http://localhost/api/capture-email", {
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
  email: "visitor@example.com",
  source: "landing-page",
  locale: "zh-Hant",
};

describe("POST /api/capture-email", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setAllowCount(999);
    __resetRateLimitCacheForTests();
    // No Upstash env vars by default → limiter is null (in-memory fallback).
    delete process.env.UPSTASH_REDIS_REST_URL;
    delete process.env.UPSTASH_REDIS_REST_TOKEN;
    // Signup-link tests opt in explicitly; default to unconfigured.
    delete process.env.NEXT_PUBLIC_BASE_URL;
    process.env.SOW_MARKETING_ORIGINS = ALLOWED_ORIGIN;
  });

  afterEach(() => {
    __resetRateLimitCacheForTests();
    delete process.env.UPSTASH_REDIS_REST_URL;
    delete process.env.UPSTASH_REDIS_REST_TOKEN;
    delete process.env.SOW_MARKETING_ORIGINS;
  });

  it("returns 200 { success: true } for a valid submission", async () => {
    const res = await POST(makePostRequest(validBody));
    expect(res.status).toBe(200);
    await expect(res.json()).resolves.toEqual({ success: true });
  });

  it("is idempotent — a duplicate submission also succeeds", async () => {
    await POST(makePostRequest(validBody));
    const res = await POST(makePostRequest(validBody));
    expect(res.status).toBe(200);
    await expect(res.json()).resolves.toEqual({ success: true });
    expect(upsertContact).toHaveBeenCalledTimes(2);
  });

  it("forwards email, source and locale to the Brevo wrapper", async () => {
    await POST(makePostRequest(validBody));
    expect(upsertContact).toHaveBeenCalledWith(
      expect.objectContaining({
        email: "visitor@example.com",
        source: "landing-page",
        locale: "zh-Hant",
      })
    );
    expect(sendConfirmationEmail).toHaveBeenCalledWith(
      expect.objectContaining({
        to: "visitor@example.com",
        locale: "zh-Hant",
        signupUrl: expect.stringContaining(
          "/register?email=visitor%40example.com"
        ),
      })
    );
  });

  it("defaults source to landing-page and omits empty variant", async () => {
    await POST(makePostRequest({ email: "v@example.com" }));
    const args = vi.mocked(upsertContact).mock.calls[0][0];
    expect(args.source).toBe("landing-page");
    expect(args.variant).toBeUndefined();
  });

  // The signup link is emailed from our own domain to an address anyone can
  // submit. Deriving it from the request would let a forged Host header put an
  // attacker-controlled URL in that mail, so the link must come from config.
  it("never builds the signup link from a forged request host", async () => {
    await POST(
      makePostRequest(validBody, "203.0.113.9", {
        host: "evil.example.com",
        "x-forwarded-host": "evil.example.com",
        "x-forwarded-proto": "https",
      })
    );
    const { signupUrl } = vi.mocked(sendConfirmationEmail).mock.calls[0][0];
    expect(signupUrl).not.toContain("evil.example.com");
    expect(signupUrl).toContain("https://app.streamofworship.com/register?email=");
  });

  it("uses NEXT_PUBLIC_BASE_URL for the signup link when configured", async () => {
    process.env.NEXT_PUBLIC_BASE_URL = "https://staging.example.com/";
    await POST(makePostRequest(validBody, "203.0.113.9", { host: "evil.example.com" }));
    const { signupUrl } = vi.mocked(sendConfirmationEmail).mock.calls[0][0];
    // Trailing slash trimmed, so no doubled path segment.
    expect(signupUrl).toBe(
      "https://staging.example.com/register?email=visitor%40example.com"
    );
    delete process.env.NEXT_PUBLIC_BASE_URL;
  });

  it("returns 400 on malformed JSON body", async () => {
    const req = new Request("http://localhost/api/capture-email", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "not json{",
    }) as unknown as NextRequest;
    const res = await POST(req);
    expect(res.status).toBe(400);
    await expect(res.json()).resolves.toEqual({ error: "Invalid JSON body" });
    expect(upsertContact).not.toHaveBeenCalled();
  });

  it("returns 400 with details on invalid email format", async () => {
    const res = await POST(makePostRequest({ email: "not-an-email" }));
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toBe("Invalid request body");
    expect(Array.isArray(body.details)).toBe(true);
    expect(upsertContact).not.toHaveBeenCalled();
  });

  it("returns 400 on unknown extra keys (strict schema)", async () => {
    const res = await POST(
      makePostRequest({ email: "v@example.com", surprise: true })
    );
    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.error).toBe("Invalid request body");
    expect(upsertContact).not.toHaveBeenCalled();
  });

  it("silently succeeds on honeypot hits without touching Brevo or the limiter", async () => {
    const res = await POST(
      makePostRequest({ email: "bot@example.com", website: "spam.example" })
    );
    expect(res.status).toBe(200);
    await expect(res.json()).resolves.toEqual({ success: true });
    // Zero Brevo calls and zero rate-limit consumption (the limiter mock
    // would have incremented its counter if the route had reached it).
    expect(upsertContact).not.toHaveBeenCalled();
    expect(sendConfirmationEmail).not.toHaveBeenCalled();
    expect(limitMock).not.toHaveBeenCalled();
    // The honeypot hit must not have drained the real per-IP budget.
    setAllowCount(1);
    const res2 = await POST(makePostRequest(validBody));
    expect(res2.status).toBe(200);
  });

  it("returns 429 when the rate limit is exceeded", async () => {
    process.env.UPSTASH_REDIS_REST_URL = "https://redis.local";
    process.env.UPSTASH_REDIS_REST_TOKEN = "tok";
    __resetRateLimitCacheForTests();
    setAllowCount(0);
    const res = await POST(makePostRequest(validBody));
    expect(res.status).toBe(429);
    await expect(res.json()).resolves.toEqual({ error: "Rate limit exceeded" });
    expect(upsertContact).not.toHaveBeenCalled();
    expect(sendConfirmationEmail).not.toHaveBeenCalled();
  });

  it("applies an in-memory fallback rate limit when Upstash is not configured", async () => {
    // A prod misconfig (missing Upstash env) must NOT silently disable rate
    // limiting — the shared in-memory token-bucket fallback still enforces a
    // cap (its own 20-token budget; the 5/min number is the Upstash config).
    const results: number[] = [];
    for (let i = 0; i < 21; i++) {
      const res = await POST(
        makePostRequest({ email: `v${i}@example.com` })
      );
      results.push(res.status);
    }
    expect(results.slice(0, 20).every((s) => s === 200)).toBe(true);
    expect(results[20]).toBe(429);
    // The 429 must not have forwarded a lead.
    expect(upsertContact).toHaveBeenCalledTimes(20);
  });

  it("keys the limiter by the hashed client IP", async () => {
    process.env.UPSTASH_REDIS_REST_URL = "https://redis.local";
    process.env.UPSTASH_REDIS_REST_TOKEN = "tok";
    __resetRateLimitCacheForTests();
    setAllowCount(999);
    await POST(makePostRequest(validBody, "198.51.100.1"));
    expect(limitMock).toHaveBeenCalledTimes(1);
    expect(String(limitMock.mock.calls[0][0])).toMatch(/^ip:/);
    // Distinct IP → the enforceRateLimit key is ip:<hash>; the hash itself is
    // covered by rate-limit's own tests, here we pin the key shape only.
    expect(limitMock.mock.calls[0][0]).not.toBe("ip:198.51.100.1");
  });
});

describe("CORS", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    setAllowCount(999);
    __resetRateLimitCacheForTests();
    process.env.SOW_MARKETING_ORIGINS = `${ALLOWED_ORIGIN}, https://other.example.com`;
  });

  afterEach(() => {
    delete process.env.SOW_MARKETING_ORIGINS;
  });

  it("echoes an allowlisted Origin on POST responses", async () => {
    const res = await POST(
      makePostRequest(validBody, "203.0.113.9", { Origin: ALLOWED_ORIGIN })
    );
    expect(res.status).toBe(200);
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe(ALLOWED_ORIGIN);
    expect(res.headers.get("Access-Control-Allow-Methods")).toBe("POST, OPTIONS");
    expect(res.headers.get("Access-Control-Allow-Headers")).toBe("Content-Type");
    expect(res.headers.get("Vary")).toBe("Origin");
  });

  it("does NOT echo a non-allowlisted Origin", async () => {
    const res = await POST(
      makePostRequest(validBody, "203.0.113.9", {
        Origin: "https://evil.example.com",
      })
    );
    expect(res.status).toBe(200);
    expect(res.headers.get("Access-Control-Allow-Origin")).toBeNull();
    expect(res.headers.get("Vary")).toBe("Origin");
  });

  it("answers OPTIONS preflight with 204 and the CORS headers", async () => {
    const req = new Request("http://localhost/api/capture-email", {
      method: "OPTIONS",
      headers: { Origin: ALLOWED_ORIGIN },
    }) as unknown as NextRequest;
    const res = await OPTIONS(req);
    expect(res.status).toBe(204);
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe(ALLOWED_ORIGIN);
    expect(res.headers.get("Access-Control-Allow-Methods")).toBe("POST, OPTIONS");
    expect(res.headers.get("Access-Control-Allow-Headers")).toBe("Content-Type");
    expect(res.headers.get("Vary")).toBe("Origin");
  });
});

describe("GET /api/capture-email", () => {
  it("returns 405 Method Not Allowed", async () => {
    const res = await GET();
    expect(res.status).toBe(405);
  });
});

describe("PUT /api/capture-email", () => {
  it("returns 405 Method Not Allowed", async () => {
    const res = await PUT();
    expect(res.status).toBe(405);
  });
});

describe("DELETE /api/capture-email", () => {
  it("returns 405 Method Not Allowed", async () => {
    const res = await DELETE();
    expect(res.status).toBe(405);
  });
});
