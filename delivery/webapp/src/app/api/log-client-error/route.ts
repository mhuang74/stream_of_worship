import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { Ratelimit } from "@upstash/ratelimit";
import { Redis } from "@upstash/redis";
import { db } from "@/db";
import { clientErrorLog } from "@/db/schema";

/**
 * /api/log-client-error — best-effort client-side telemetry sink for the
 * Cast/Presentation transport layer (see src/hooks/useCast.ts postClientError).
 *
 * Design constraints:
 *  - Auth is best-effort: an optional Better Auth session is accepted but the
 *    endpoint works without one (the TV-side/Cast paths often have no user
 *    session). User IDs are NEVER persisted — telemetry is anonymized.
 *  - Rate limited via @upstash/ratelimit (token bucket, 20 req/min per hashed
 *    client IP). 429 when exceeded.
 *  - PII redaction: full signed URLs are never logged — `meta.url` is reduced
 *    to host + path + (expired|fresh). The raw client IP is hashed with a
 *    rotating (daily) salt before persistence.
 *  - Persistence is best-effort: a DB write failure is swallowed so a
 *    telemetry hiccup can never surface to the user. Returns 202 regardless.
 */

export const runtime = "nodejs";

const metaSchema = z
  .object({
    browser: z.string().max(256).optional(),
    platform: z.string().max(64).optional(),
    castAppIdMode: z.enum(["set", "default", "unset"]).optional(),
    transportKind: z.enum(["cast", "presentation", "none"]).optional(),
    mediaSourceKind: z.enum(["songset", "share"]).optional(),
    urlExpired: z.boolean().optional(),
    url: z.string().max(2048).optional(),
  })
  .strict();

const clientErrorSchema = z.object({
  message: z.string().min(1).max(1024),
  kind: z.enum(["cast_load", "cast_transport", "presentation", "other"]),
  meta: metaSchema.optional(),
});

/**
 * Daily-rotating salt for IP hashing. Derived from the current UTC date so all
 * requests within the same UTC day share a salt (allowing per-IP rate-limit
 * keys + per-day aggregation) while preventing long-term linkage across days.
 */
function dailySalt(now = new Date()): string {
  const y = now.getUTCFullYear();
  const m = String(now.getUTCMonth() + 1).padStart(2, "0");
  const d = String(now.getUTCDate()).padStart(2, "0");
  return `sow-ce-${y}${m}${d}`;
}

async function sha256(input: string): Promise<string> {
  const data = new TextEncoder().encode(input);
  const buf = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/**
 * Reduce a (possibly signed) URL to host + path + (expired|fresh). The query
 * string (which carries the R2 signature/X-Amz-* params) is stripped entirely
 * so a signed URL can never be replayed from a log row.
 */
function redactUrl(rawUrl: string, urlExpired?: boolean): string {
  try {
    const u = new URL(rawUrl);
    const expiry = urlExpired ? "expired" : "fresh";
    return `${u.host}${u.pathname} (${expiry})`;
  } catch {
    // Not a parseable URL — redact aggressively rather than persist raw input.
    return "<invalid-url>";
  }
}

interface RateLimiter {
  limit: (id: string) => Promise<{ success: boolean }>;
}

let cachedLimiter: RateLimiter | null | undefined;

/**
 * Build (or reuse) the Upstash rate limiter. Returns null when Upstash env
 * vars are absent (dev/test) so callers can fall back to a no-op allow-all.
 * Tests inject via vi.mock("@upstash/ratelimit", ...).
 */
function getLimiter(): RateLimiter | null {
  if (cachedLimiter !== undefined) return cachedLimiter;
  const url = process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!url || !token) {
    cachedLimiter = null;
    return cachedLimiter;
  }
  const redis = new Redis({ url, token });
  cachedLimiter = new Ratelimit({
    redis,
    // tokenBucket(refillRate, interval, maxTokens): refill 20 tokens per
    // minute, bucket cap 20 (allows a single burst of 20, then refills
    // continuously at 20/min).
    limiter: Ratelimit.tokenBucket(20, "1 m", 20),
    prefix: "sow:log-client-error",
    analytics: false,
  });
  return cachedLimiter;
}

/** Test-only reset of the cached limiter. */
export function __resetLimiterForTests(): void {
  cachedLimiter = undefined;
}

export async function POST(request: NextRequest) {
  // 1. Parse body (malformed JSON → 400).
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
  }

  const parsed = clientErrorSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Invalid request body", details: parsed.error.issues },
      { status: 400 }
    );
  }

  // 2. Derive client IP and hash for the rate-limit key + persisted row.
  const rawIp =
    request.headers.get("x-forwarded-for")?.split(",")[0]?.trim() ||
    request.headers.get("x-real-ip") ||
    "unknown";

  const salt = dailySalt();
  const ipHash = await sha256(`${salt}:${rawIp}`);

  // 3. Rate limit (20 req/min per hashed IP). When Upstash is not configured
  //    (dev/local/test) the limiter is null and the request is allowed through.
  const limiter = getLimiter();
  if (limiter) {
    try {
      const { success } = await limiter.limit(ipHash);
      if (!success) {
        return NextResponse.json(
          { error: "Rate limit exceeded" },
          { status: 429 }
        );
      }
    } catch {
      // Upstash hiccup — never block a telemetry POST on infra failure.
    }
  }

  // 4. Redact PII in meta before persistence.
  const meta = parsed.data.meta;
  const redactedMeta =
    meta === undefined
      ? null
      : {
          browser: meta.browser,
          platform: meta.platform,
          castAppIdMode: meta.castAppIdMode,
          transportKind: meta.transportKind,
          mediaSourceKind: meta.mediaSourceKind,
          urlExpired: meta.urlExpired,
          url: meta.url !== undefined ? redactUrl(meta.url, meta.urlExpired) : undefined,
        };

  // 5. Persist (best-effort; swallow DB failures so telemetry never surfaces).
  try {
    await db.insert(clientErrorLog).values({
      ipHash,
      message: parsed.data.message,
      kind: parsed.data.kind,
      metaJson: redactedMeta === null ? null : JSON.stringify(redactedMeta),
    });
  } catch (e) {
    console.warn("log-client-error: DB write failed (swallowed)", e);
  }

  return NextResponse.json({ accepted: true }, { status: 202 });
}

export async function GET() {
  return NextResponse.json({ error: "Method Not Allowed" }, { status: 405 });
}

export async function PUT() {
  return NextResponse.json({ error: "Method Not Allowed" }, { status: 405 });
}

export async function DELETE() {
  return NextResponse.json({ error: "Method Not Allowed" }, { status: 405 });
}
