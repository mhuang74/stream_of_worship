"use client";

import { useCallback, useState } from "react";
import { requestVerificationEmail } from "@/lib/auth-client";

/**
 * Shared resend-verification flow for the login and register pages.
 * Centralizes the request call and its idle/sent/error state so both pages
 * render the same confirmation affordance without duplicating logic.
 */
export function useResendVerification(email: string | null): {
  resending: boolean;
  resendState: "idle" | "sent" | "error";
  resend: () => Promise<void>;
} {
  const [resending, setResending] = useState(false);
  const [resendState, setResendState] = useState<"idle" | "sent" | "error">("idle");

  const resend = useCallback(async () => {
    if (!email) return;
    setResending(true);
    setResendState("idle");
    try {
      const result = await requestVerificationEmail({ email, callbackURL: "/" });
      setResendState(result.error ? "error" : "sent");
    } catch {
      setResendState("error");
    } finally {
      setResending(false);
    }
  }, [email]);

  return { resending, resendState, resend };
}
