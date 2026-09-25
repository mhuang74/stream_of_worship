import { createHmac, timingSafeEqual } from "node:crypto";

/**
 * Signed email-validation tokens for the lead-capture funnel (issue #222
 * follow-up). The capture endpoint emails a link to
 * `/validated?token=<token>`; clicking it proves the address is deliverable
 * and marks the Brevo contact VALIDATED (marketing sends are gated on that
 * attribute server-side via a Brevo segment).
 *
 * Signing key: BETTER_AUTH_SECRET — reused deliberately (auth already
 * requires it in production; a leaked lead-validation link grants nothing
 * beyond "this email was clicked within 48h"). No insecure fallback: an
 * unset secret makes sign throw (fail loud at build/deploy time) and verify
 * return null.
 */

const TOKEN_TTL_SECONDS = 48 * 60 * 60;

/**
 * Build `<payloadB64url>.<sigB64url>` for `{ e: email, x: expiry }`.
 * Throws only when BETTER_AUTH_SECRET is unset/empty.
 */
export function signValidationToken(email: string): string {
  const secret = process.env.BETTER_AUTH_SECRET;
  if (!secret) throw new Error("BETTER_AUTH_SECRET not configured");
  const payload = Buffer.from(
    JSON.stringify({ e: email, x: Math.floor(Date.now() / 1000) + TOKEN_TTL_SECONDS })
  ).toString("base64url");
  const signature = createHmac("sha256", secret).update(payload).digest("base64url");
  return `${payload}.${signature}`;
}

/**
 * Verify a validation token: signature (timing-safe), payload shape and
 * expiry. Returns the validated email, or null on any malformed, tampered,
 * expired or mis-keyed input. Never throws — the /validated page renders an
 * invalid state on null.
 */
export function verifyValidationToken(token: string): { email: string } | null {
  try {
    const secret = process.env.BETTER_AUTH_SECRET;
    if (!secret) return null;

    const dot = token.indexOf(".");
    if (dot === -1) return null; // no signature
    if (token.indexOf(".", dot + 1) !== -1) return null; // more than one dot
    const payloadB64 = token.slice(0, dot);
    const sigB64 = token.slice(dot + 1);
    if (!payloadB64 || !sigB64) return null;

    const expected = Buffer.from(
      createHmac("sha256", secret).update(payloadB64).digest("base64url"),
      "base64url"
    );
    const provided = Buffer.from(sigB64, "base64url");
    if (expected.length !== provided.length || !timingSafeEqual(expected, provided)) {
      return null;
    }

    const payload: unknown = JSON.parse(Buffer.from(payloadB64, "base64url").toString("utf8"));
    if (typeof payload !== "object" || payload === null) return null;
    const { e, x } = payload as { e?: unknown; x?: unknown };
    if (typeof e !== "string" || e.length === 0 || typeof x !== "number") return null;
    if (x <= Math.floor(Date.now() / 1000)) return null; // expired
    return { email: e };
  } catch {
    return null;
  }
}
