import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { db } from "@/db";
import { clientErrorLog } from "@/db/schema";
import {
  getClientIp,
  hashIp,
  enforceRateLimit,
  __resetRateLimitCacheForTests,
} from "@/lib/rate-limit";

/**
 * /api/log-client-error — best-effort client-side telemetry sink for the
 * Cast/Presentation transport layer (see src/hooks/useCast.ts postClientError).
 *
 * Design constraints:
 *  - Auth is best-effort: an optional Better Auth session is accepted but the
 *    endpoint works without one (the TV-side/Cast paths often have no user
 *    session). User IDs are NEVER persisted — telemetry is anonymized.
 *  - Rate limited via @upstash/ratelimit (token bucket, 20 req/min per hashed
 *    client IP). 429 when exceeded. When Upstash is not configured (dev/test
 *    or a prod misconfig) an in-memory token-bucket fallback applies so a
 *    missing env var can never silently unlock unbounded writes.
 *  - PII redaction: full signed URLs are NEVER received — the producer
 *    (`useCast.reportTransportError`) reduces the raw presigned R2 URL to
 *    `{ host, path, expired }` on the CLIENT before posting, so the raw URL
 *    (carrying `X-Amz-Signature` / `X-Amz-Credential` granting 4h R2 access)
 *    never transits the network. This persistence step stores the
 *    pre-redacted summary under the field name `urlRedacted` (matching the
 *    wire contract) — downstream telemetry consumers query `meta.urlRedacted`,
 *    not `meta.url`. The raw client IP is hashed with a rotating (daily) salt.
 *  - Persistence is best-effort: a DB write failure is swallowed so a
 *    telemetry hiccup can never surface to the user. Returns 202 regardless.
 */

export const runtime = "nodejs";

const metaSchema = z
  .object({
    platform: z.string().max(64).optional(),
    browser: z.string().max(128).optional(),
    castAppIdMode: z.enum(["set", "default", "unset"]).optional(),
    transportKind: z.enum(["cast", "presentation", "none"]).optional(),
    mediaSourceKind: z.enum(["songset", "share"]).optional(),
    /**
     * Pre-redacted URL summary computed on the CLIENT before posting. The raw
     * presigned R2 URL is NEVER transmitted over the network — the producer
     * (`useCast.reportTransportError` → `redactUrlClientSide`) reduces it to
     * `{ host, path, expired }` before POSTing. Field name matches the
     * persistence key (`urlRedacted`) so downstream consumers query a single
     * field across the wire contract + persistence schema.
     */
    urlRedacted: z
      .object({
        host: z.string().max(255),
        path: z.string().max(2048),
        expired: z.boolean(),
      })
      .optional(),
  })
  .strict();

const clientErrorSchema = z.object({
  message: z.string().min(1).max(1024),
  kind: z.enum(["cast_load", "cast_transport", "presentation", "other"]),
  meta: metaSchema.optional(),
});

/** Re-export the shared cache reset under the legacy name for tests. */
export { __resetRateLimitCacheForTests as __resetLimiterForTests };

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

  // 2. Derive client IP (platform-trusted; never the spoofable leftmost XFF
  //    segment) and hash it for the rate-limit key + persisted row.
  const rawIp = getClientIp(request);
  const ipHash = await hashIp(rawIp);

  // 3. Rate limit (20 req/min per hashed IP). Upstash is used when configured;
  //    otherwise an in-memory token-bucket fallback applies so a missing env
  //    var can never silently disable rate limiting.
  const allowed = await enforceRateLimit(ipHash, "sow:log-client-error", 20);
  if (!allowed) {
    return NextResponse.json({ error: "Rate limit exceeded" }, { status: 429 });
  }

  // 4. Persist (best-effort; swallow DB failures so telemetry never surfaces).
  //    The producer pre-redacts the URL on the client (see `redactUrlClientSide`
  //    in src/hooks/useCast.ts) so the raw presigned R2 URL never transits the
  //    network — defense-in-depth against server-side body-capture middleware.
  const meta = parsed.data.meta;
  const redactedMeta =
    meta === undefined
      ? null
      : {
          platform: meta.platform,
          browser: meta.browser,
          castAppIdMode: meta.castAppIdMode,
          transportKind: meta.transportKind,
          mediaSourceKind: meta.mediaSourceKind,
          urlRedacted: meta.urlRedacted,
        };

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
