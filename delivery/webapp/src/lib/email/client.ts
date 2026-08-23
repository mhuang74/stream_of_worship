import { Resend } from "resend";

// Thin Resend wrapper for transactional email (spec:
// specs/webapp-email-validation-password-reset-v1.md). No-op-safe: without
// RESEND_API_KEY (local dev) sends are logged and skipped — the webapp keeps
// working; production requires the key.

interface EmailSendArgs {
  to: string;
  url: string;
}

function getResend(): Resend | null {
  const apiKey = process.env.RESEND_API_KEY;
  if (!apiKey) return null;
  return new Resend(apiKey);
}

function fromAddress(): string {
  return (
    process.env.RESEND_FROM_ADDRESS ??
    "Stream of Worship <noreply@streamofworship.com>"
  );
}

/**
 * Send the email-verification message. `url` points at Better Auth's
 * `/verify-email?token=...&callbackURL=...` endpoint (auto sign-in + redirect
 * handled server-side).
 */
export async function sendVerificationEmail({
  to,
  url,
}: EmailSendArgs): Promise<void> {
  const resend = getResend();
  if (!resend) {
    console.warn(
      `[email] RESEND_API_KEY not set; skipping verification email to ${to} (${url})`
    );
    return;
  }
  const { error } = await resend.emails.send({
    from: fromAddress(),
    to,
    subject: "Verify your email",
    html: `<p>Welcome to Stream of Worship!</p><p><a href="${url}">Verify your email</a></p><p>If you didn't create an account, you can safely ignore this email.</p>`,
  });
  if (error) console.error("[email] Failed to send verification email:", error);
}

/**
 * Send the password-reset link. `url` points at Better Auth's
 * `/reset-password/{token}?callbackURL=...` endpoint, which validates the
 * token and redirects to `/reset-password?token=...`.
 */
export async function sendPasswordResetEmail({
  to,
  url,
}: EmailSendArgs): Promise<void> {
  const resend = getResend();
  if (!resend) {
    console.warn(
      `[email] RESEND_API_KEY not set; skipping password reset email to ${to} (${url})`
    );
    return;
  }
  const { error } = await resend.emails.send({
    from: fromAddress(),
    to,
    subject: "Reset your password",
    html: `<p>Click the link below to reset your password:</p><p><a href="${url}">Reset your password</a></p><p>If you didn't request this, you can safely ignore this email.</p>`,
  });
  if (error) console.error("[email] Failed to send reset email:", error);
}
