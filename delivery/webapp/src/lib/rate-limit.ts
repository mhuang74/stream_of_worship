import { Ratelimit } from "@upstash/ratelimit";
import { Redis } from "@upstash/redis";
import type { NextRequest } from "next/server";

/**
 * Shared request-scoped rate-limiting helpers.
 *
 * - `getClientIp` derives a platform-trusted client IP from a NextRequest.
 *   It deliberately prefers Vercel's `x-vercel-forwarded-for` (set by the
 *   platform to the real client IP it determined after proxy handling) over
 *   `x-real-ip`, and never trusts the leftmost segment of a caller-supplied
 *   `x-forwarded-for` chain (which is trivially spoofable: Vercel appends the
 *   real client IP, so the leftmost is attacker-controlled).
 * - `hashIp` produces a daily-rotating salted SHA-256 hash so the same IP
 *   hashes identically within a UTC day but cannot be linked across days.
 * - `getLimiter` builds (and caches) an Upstash token-bucket limiter keyed by
 *   a caller-supplied prefix. Returns null when Upstash is not configured.
 * - `inMemoryLimit` is the fail-safe in-memory token-bucket fallback used when
 *   Upstash is absent so a config slip can never silently unlock unbounded
 *   writes (best-effort, per-instance in serverless).
 */

export interface RateLimiter {
  limit: (id: string) => Promise<{ success: boolean }>;
}

const DEFAULT_RPM = 20;
const IN_MEMORY_MAX_TOKENS = 20;
const IN_MEMORY_REFILL_PER_MS = IN_MEMORY_MAX_TOKENS / 60_000;

interface InMemoryBucket {
  tokens: number;
  lastRefillMs: number;
}

const inMemoryBuckets = new Map<string, InMemoryBucket>();
const limiterCache = new Map<string, RateLimiter | null>();
let warnedAboutMissingUpstash = false;

/**
 * Max number of in-memory buckets retained per instance. When exceeded, the
 * oldest-inserted bucket is evicted (LRU-ish). Bounds per-instance memory in
 * warm serverless instances under distributed client-IP load.
 */
const IN_MEMORY_MAX_BUCKETS = 10_000;

/**
 * Derive a platform-trusted client IP from a NextRequest. Order:
 *   1. `x-vercel-forwarded-for` (first segment) — Vercel sets this to the real
 *      client IP after its own proxy handling; not caller-controllable.
 *   2. `x-real-ip` — Vercel's single-IP header (the connecting client).
 *   3. `x-forwarded-for` RIGHTMOST routable segment — the segment attributed
 *      to the nearest trusted proxy when no platform header is present.
 *      On Vercel this header is never consulted (steps 1-2 always resolve).
 *      Off-Vercel deploys (or any proxy that strips `x-vercel-forwarded-for`
 *      / `x-real-ip`) would otherwise collapse every anonymous client into a
 *      single `"unknown"` bucket — letting one noisy log spammer starve
 *      share-token + error-telemetry budgets for ALL anonymous users. The
 *      rightmost routable segment is the least-spoofable hop in the chain
 *      (a proxy can append, but a client cannot rewrite the proxy's segment).
 *      Returns `"unknown"` only when XFF is also absent.
 *
 * `x-forwarded-for` LEFTMOST segment is intentionally never used: it is
 * attacker-controlled and rotating it trivially bypasses a per-IP rate limit.
 */
export function getClientIp(request: NextRequest): string {
  const vercel = request.headers.get("x-vercel-forwarded-for");
  if (vercel) {
    const first = vercel.split(",")[0]?.trim();
    if (first) return first;
  }
  const realIp = request.headers.get("x-real-ip");
  if (realIp) return realIp.trim();
  // Off-Vercel fallback: the rightmost routable XFF segment attributed to the
  // nearest trusted proxy. A client cannot rewrite the proxy's own segment,
  // so this is the least-spoofable hop when no platform-trusted header exists.
  const xff = request.headers.get("x-forwarded-for");
  if (xff) {
    const segments = xff.split(",").map((s) => s.trim()).filter(Boolean);
    for (let i = segments.length - 1; i >= 0; i--) {
      const seg = segments[i];
      // Skip non-routable sentinels (`unknown`, `::1`, `127.0.0.1`) so the
      // search converges on the nearest real proxy attribution.
      if (seg && seg !== "unknown" && seg !== "::1" && seg !== "127.0.0.1") {
        return seg;
      }
    }
    if (segments.length > 0) return segments[segments.length - 1];
  }
  return "unknown";
}

/**
 * Daily-rotating salt for IP hashing. All requests within the same UTC day
 * share a salt (per-IP aggregation) while preventing long-term linkage across
 * days.
 */
function dailySalt(now = new Date()): string {
  const y = now.getUTCFullYear();
  const m = String(now.getUTCMonth() + 1).padStart(2, "0");
  const d = String(now.getUTCDate()).padStart(2, "0");
  return `sow-${y}${m}${d}`;
}

export async function hashIp(ip: string): Promise<string> {
  const data = new TextEncoder().encode(`${dailySalt()}:${ip}`);
  const buf = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(buf))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/**
 * Build (or reuse) an Upstash token-bucket limiter for a given prefix. Returns
 * null when Upstash env vars are absent (dev/test/prod-misconfig) so callers
 * can fall back to `inMemoryLimit`. Tests inject via vi.mock.
 */
export function getLimiter(prefix: string, rpm = DEFAULT_RPM): RateLimiter | null {
  const cacheKey = `${prefix}:${rpm}`;
  const cached = limiterCache.get(cacheKey);
  if (cached !== undefined) return cached;
  const url = process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.UPSTASH_REDIS_REST_TOKEN;
  if (!url || !token) {
    limiterCache.set(cacheKey, null);
    return null;
  }
  const redis = new Redis({ url, token });
  const limiter = new Ratelimit({
    redis,
    // tokenBucket(refillRate, interval, maxTokens): refill `rpm` tokens per
    // minute, bucket cap `rpm` (allows a single burst, then refills
    // continuously).
    limiter: Ratelimit.tokenBucket(rpm, "1 m", rpm),
    prefix,
    analytics: false,
  });
  limiterCache.set(cacheKey, limiter);
  return limiter;
}

/** Max age (ms) after which an idle in-memory bucket is eligible for sweep. */
const IN_MEMORY_BUCKET_STALE_MS = 5 * 60_000;
/** Timestamp of the last stale-bucket sweep; throttles the sweep to ~1/s. */
let lastSweepMs = 0;

/**
 * Opportunistic sweep of stale in-memory buckets. Drops any bucket whose
 * `lastRefillMs` is older than `IN_MEMORY_BUCKET_STALE_MS` so the Map cannot
 * retain dead entries for the lifetime of a warm serverless instance under
 * distributed client-IP load. Throttled to one sweep per second.
 */
function sweepStaleBuckets(now: number): void {
  if (now - lastSweepMs < 1000) return;
  lastSweepMs = now;
  const cutoff = now - IN_MEMORY_BUCKET_STALE_MS;
  for (const [k, b] of inMemoryBuckets) {
    if (b.lastRefillMs < cutoff) inMemoryBuckets.delete(k);
  }
}

/**
 * In-memory token-bucket fallback. Best-effort (per-instance in serverless) —
 * its purpose is to keep a missing Upstash config from silently disabling rate
 * limiting entirely. Surfaces a one-time console.warn in production.
 */
export function inMemoryLimit(key: string): boolean {
  if (!warnedAboutMissingUpstash && process.env.NODE_ENV === "production") {
    warnedAboutMissingUpstash = true;
    console.warn(
      "rate-limit: UPSTASH_REDIS_REST_URL/TOKEN not configured — using in-memory fallback. " +
        "Configure Upstash in production for distributed rate limiting.",
    );
  }
  const now = Date.now();
  // Opportunistic sweep: periodically drop buckets that have been idle longer
  // than the staleness window so the Map does not retain dead entries for the
  // lifetime of the instance. Runs at most once per second (guarded by a
  // module-level timestamp) to keep the hot path cheap.
  sweepStaleBuckets(now);
  const bucket = inMemoryBuckets.get(key);
  if (!bucket) {
    // Evict the oldest bucket when the cap is reached so the Map cannot grow
    // unboundedly per serverless instance under distributed client-IP load.
    // Map iteration order is insertion order, so the first key is the oldest.
    if (inMemoryBuckets.size >= IN_MEMORY_MAX_BUCKETS) {
      const oldestKey = inMemoryBuckets.keys().next().value;
      if (oldestKey !== undefined) inMemoryBuckets.delete(oldestKey);
    }
    inMemoryBuckets.set(key, {
      tokens: IN_MEMORY_MAX_TOKENS - 1,
      lastRefillMs: now,
    });
    return true;
  }
  const elapsed = now - bucket.lastRefillMs;
  const refilled = Math.min(
    bucket.tokens + elapsed * IN_MEMORY_REFILL_PER_MS,
    IN_MEMORY_MAX_TOKENS,
  );
  if (refilled < 1) {
    bucket.tokens = refilled;
    bucket.lastRefillMs = now;
    return false;
  }
  bucket.tokens = refilled - 1;
  bucket.lastRefillMs = now;
  return true;
}

/** Reset all caches. Test-only — exported for vi.resetModules / per-test isolation. */
export function __resetRateLimitCacheForTests(): void {
  inMemoryBuckets.clear();
  limiterCache.clear();
  warnedAboutMissingUpstash = false;
  lastSweepMs = 0;
}

/**
 * Enforce a rate limit on a request. Returns `null` when allowed, or a
 * `{ status: 429 }` marker when exceeded. Uses Upstash when configured,
 * otherwise the in-memory fallback (never silently allows unbounded traffic).
 *
 * `key` is the pre-hashed identifier (e.g. `ip:<hash>` or `share:<token>:<hash>`).
 * `prefix` selects the Upstash bucket namespace.
 */
export async function enforceRateLimit(
  key: string,
  prefix: string,
  rpm = DEFAULT_RPM,
): Promise<boolean> {
  const limiter = getLimiter(prefix, rpm);
  if (limiter) {
    try {
      const { success } = await limiter.limit(key);
      return success;
    } catch {
      // Upstash hiccup (network blip, 5xx, timeout). Degrade to the
      // in-memory token-bucket fallback so a transient Upstash outage
      // degrades to per-instance limiting rather than unbounded access —
      // abuse resistance matters most exactly when Upstash is flaky.
      return inMemoryLimit(key);
    }
  }
  return inMemoryLimit(key);
}
