import { NextRequest, NextResponse } from "next/server";
import { z } from "zod";
import { getClientIp, hashIp, enforceRateLimit } from "@/lib/rate-limit";
import { upsertContact, sendConfirmationEmail } from "@/lib/brevo/client";
import { signValidationToken } from "@/lib/lead-validation";

/**
 * /api/capture-email — landing-page "Notify me" lead capture (issue #222).
 * Receives a visitor email from the marketing site (cross-origin, hence the
 * strict CORS allowlist below) and forwards it to Brevo as a Lead.
 *
 * Design constraints:
 *  - Idempotent by contract: a valid email always yields 200 { success: true }
 *    (repeat submissions re-upsert the contact) — a "duplicate" error would
 *    only confuse hesitant visitors. Brevo failures are logged server-side and
 *    never surface to the client (issue story 17: internals must not leak).
 *  - Rate limited via the shared limiter (5 req/min per hashed client IP,
 *    dedicated `sow:capture-email` prefix) so abusive scripts cannot burn
 *    email-spend or pollute the contact list.
 *  - Honeypot: the marketing form hides a `website` field; a non-empty value
 *    means a bot filled it. Response pretends success (bots must learn
 *    nothing) WITHOUT consuming rate-limit budget or touching Brevo.
 *  - CORS: exact-match allowlist from SOW_MARKETING_ORIGINS (comma-separated).
 *    Unset/empty falls back to the production marketing origin with a
 *    one-time warning — mirroring rate-limit.ts's warn-on-fallback convention
 *    — so a forgotten env var cannot silently kill the funnel in production.
 *  - No-op-safe Brevo wrapper: without BREVO_API_KEY (local dev) the calls
 *    are skipped with a warning and the route still returns success.
 */

export const runtime = "nodejs";

/**
 * Canonical app origin used to build the email-validation link in the
 * confirmation email. Deliberately NOT derived from the request: the link is
 * emailed from our own domain to whatever address was submitted, and anyone
 * can post a harvested third-party email — a forged Host header must never
 * turn our confirmation mail into a vector pointing at an attacker's host.
 */
const CANONICAL_APP_ORIGIN = "https://app.streamofworship.com";

function validationUrlFor(email: string): string {
  // Spec: derive from the existing public base URL env; no new URL env var.
  // Falls back to the canonical production origin (never the request origin).
  const configured = process.env.NEXT_PUBLIC_BASE_URL?.trim().replace(/\/$/, "");
  const origin = configured || CANONICAL_APP_ORIGIN;
  const token = signValidationToken(email);
  return `${origin}/validated?token=${encodeURIComponent(token)}`;
}

const captureEmailSchema = z
  .object({
    email: z.string().email().max(320),
    source: z.string().max(64).default("landing-page"),
    locale: z.string().max(16).optional(),
    // Accepted pass-through only — nothing generates variants (issue: out of scope).
    variant: z.string().max(64).optional(),
    // Honeypot: must be absent or empty for a real visitor.
    website: z.string().max(256).optional(),
  })
  .strict();

// Warn once (not per request) when the allowlist env is missing — a forgotten
// env var must be visible in logs but must not spam every submission.
let warnedAboutMissingOrigins = false;

function allowedOrigins(): string[] {
  const raw = process.env.SOW_MARKETING_ORIGINS;
  if (raw) {
    const origins = raw
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (origins.length > 0) return origins;
  }
  if (!warnedAboutMissingOrigins) {
    warnedAboutMissingOrigins = true;
    console.warn(
      "capture-email: SOW_MARKETING_ORIGINS not configured — falling back to the production marketing origin. " +
        "Configure the allowlist so non-production surfaces can submit leads."
    );
  }
  return ["https://streamofworship.com"];
}

/**
 * CORS headers shared by every response. The origin is echoed ONLY on an
 * exact allowlist match — a mismatched origin gets no ACAO header, so
 * browsers block the cross-origin read.
 */
function corsHeaders(request: NextRequest): HeadersInit {
  const headers: Record<string, string> = {
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    Vary: "Origin",
  };
  const origin = request.headers.get("origin");
  if (origin && allowedOrigins().includes(origin)) {
    headers["Access-Control-Allow-Origin"] = origin;
  }
  return headers;
}

export async function OPTIONS(request: NextRequest) {
  return new NextResponse(null, { status: 204, headers: corsHeaders(request) });
}

export async function POST(request: NextRequest) {
  const cors = corsHeaders(request);

  // 1. Parse body (malformed JSON → 400).
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json(
      { error: "Invalid JSON body" },
      { status: 400, headers: cors }
    );
  }

  const parsed = captureEmailSchema.safeParse(body);
  if (!parsed.success) {
    return NextResponse.json(
      { error: "Invalid request body", details: parsed.error.issues },
      { status: 400, headers: cors }
    );
  }

  // 2. Honeypot: a filled `website` field means a bot. Pretend success so the
  //    bot learns nothing — and crucially BEFORE rate limiting, so honeypot
  //    hits never consume a real visitor's per-IP budget or reach Brevo.
  if (parsed.data.website && parsed.data.website.length > 0) {
    return NextResponse.json({ success: true }, { status: 200, headers: cors });
  }

  // 3. Rate limit (5 req/min per hashed IP) before any third-party side
  //    effects — a rejected request must not burn email-spend.
  const rawIp = getClientIp(request);
  const ipHash = await hashIp(rawIp);
  const allowed = await enforceRateLimit(`ip:${ipHash}`, "sow:capture-email", 5);
  if (!allowed) {
    return NextResponse.json(
      { error: "Rate limit exceeded" },
      { status: 429, headers: cors }
    );
  }

  // 4. Forward to Brevo (never-throwing wrapper; failures are logged
  //    server-side only — the visitor always gets a coherent success).
  const email = parsed.data.email;
  const locale = parsed.data.locale;
  const variant = parsed.data.variant;
  await upsertContact({
    email,
    source: parsed.data.source,
    ...(locale ? { locale } : {}),
    ...(variant ? { variant } : {}),
  });

  // 5. Email-validation link (the conversion path). Built from the configured
  //    public base URL — never from the request host (see validationUrlFor).
  await sendConfirmationEmail({
    to: email,
    ...(locale ? { locale } : {}),
    validateUrl: validationUrlFor(email),
  });

  return NextResponse.json({ success: true }, { status: 200, headers: cors });
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
