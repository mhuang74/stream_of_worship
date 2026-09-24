// Thin Brevo wrapper for lead-capture email (issue #222). No SDK — plain
// fetch, mirroring the Resend wrapper (src/lib/email/client.ts). Brevo is the
// marketing/lead-capture vendor; Resend stays the transactional vendor.
// No-op-safe: without BREVO_API_KEY (local dev) calls are logged and skipped —
// the capture endpoint still returns success; production requires the key.

export interface UpsertContactArgs {
  email: string;
  source: string; // → attribute SOURCE
  locale?: string; // → attribute LOCALE, omitted when empty/undefined
  variant?: string; // → attribute VARIANT, omitted when empty/undefined
}

export interface SendConfirmationEmailArgs {
  to: string;
  locale?: string; // "zh-Hant" → Traditional Chinese fallback copy, else English
  signupUrl: string; // fully-built signup URL with the email already query-encoded
}

const BREVO_API_BASE = "https://api.brevo.com/v3";
const DEFAULT_FROM_ADDRESS = "Stream of Worship <noreply@streamofworship.com>";

function parsePositiveIntEnv(name: string): number | null {
  const raw = process.env[name];
  if (!raw) return null;
  const parsed = Number.parseInt(raw, 10);
  // Non-positive values are treated as unset: a negative template ID would
  // take the template path with an ID Brevo rejects, failing the send outright
  // instead of falling back to inline HTML — i.e. it would silently eat a lead.
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

/**
 * Parse `Name <email>` into Brevo's sender shape; a bare address has no name.
 */
function parseFromAddress(value: string): { email: string; name?: string } {
  const match = value.match(/^(.*?)\s*<([^>]+)>$/);
  if (match) {
    const name = match[1].trim();
    return { email: match[2].trim(), name: name || undefined };
  }
  return { email: value.trim() };
}

/**
 * Inline-HTML fallback copy so a missing template ID never silently eats a
 * lead. The signup URL is embedded as an `<a href>` (the conversion path).
 */
function fallbackCopy(
  signupUrl: string,
  locale?: string
): { subject: string; html: string } {
  if (locale === "zh-Hant") {
    return {
      subject: "已收到您的通知需求 — Stream of Worship",
      html: `<p>感謝您對 Stream of Worship 的關注！</p><p><a href="${signupUrl}">完成建立帳號</a></p>`,
    };
  }
  return {
    subject: "You're on the list — Stream of Worship",
    html: `<p>Thanks for your interest in Stream of Worship!</p><p><a href="${signupUrl}">Finish creating your account</a></p>`,
  };
}

/**
 * Idempotent create-or-update of a Brevo contact. Never throws: Brevo
 * failures are logged server-side only so they can never surface to the
 * visitor (the capture endpoint always reports success).
 */
export async function upsertContact(args: UpsertContactArgs): Promise<void> {
  const apiKey = process.env.BREVO_API_KEY;
  if (!apiKey) {
    console.warn(
      `[brevo] BREVO_API_KEY not set; skipping contact upsert for ${args.email}`
    );
    return;
  }

  const attributes: Record<string, string> = { SOURCE: args.source };
  if (args.locale) attributes.LOCALE = args.locale;
  if (args.variant) attributes.VARIANT = args.variant;

  const listId = parsePositiveIntEnv("BREVO_LIST_ID");
  const body: Record<string, unknown> = {
    email: args.email,
    updateEnabled: true,
    attributes,
    ...(listId !== null ? { listIds: [listId] } : {}),
  };

  try {
    const res = await fetch(`${BREVO_API_BASE}/contacts`, {
      method: "POST",
      headers: {
        "api-key": apiKey,
        "content-type": "application/json",
        accept: "application/json",
      },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      console.error("[brevo] Failed to upsert contact:", res.status, text);
    }
  } catch (error) {
    console.error("[brevo] Failed to upsert contact:", error);
  }
}

/**
 * Send the lead-capture confirmation email. Uses the configured Brevo
 * template when BREVO_TEMPLATE_ID is set; otherwise falls back to inline
 * HTML so a missing template ID never silently eats a lead. Never throws.
 */
export async function sendConfirmationEmail(
  args: SendConfirmationEmailArgs
): Promise<void> {
  const apiKey = process.env.BREVO_API_KEY;
  if (!apiKey) {
    console.warn(
      `[brevo] BREVO_API_KEY not set; skipping confirmation email to ${args.to}`
    );
    return;
  }

  const from = parseFromAddress(
    process.env.BREVO_FROM_ADDRESS ?? DEFAULT_FROM_ADDRESS
  );

  let body: Record<string, unknown>;
  const templateId = parsePositiveIntEnv("BREVO_TEMPLATE_ID");
  if (templateId !== null) {
    body = {
      templateId,
      to: [{ email: args.to }],
      params: { email: args.to, signupUrl: args.signupUrl },
    };
  } else {
    const copy = fallbackCopy(args.signupUrl, args.locale);
    body = {
      sender: from,
      to: [{ email: args.to }],
      subject: copy.subject,
      htmlContent: copy.html,
    };
  }

  try {
    const res = await fetch(`${BREVO_API_BASE}/smtp/email`, {
      method: "POST",
      headers: {
        "api-key": apiKey,
        "content-type": "application/json",
        accept: "application/json",
      },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      console.error(
        "[brevo] Failed to send confirmation email:",
        res.status,
        text
      );
    }
  } catch (error) {
    console.error("[brevo] Failed to send confirmation email:", error);
  }
}
