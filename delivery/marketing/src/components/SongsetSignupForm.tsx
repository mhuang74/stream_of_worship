"use client";

import { useState, type FormEvent } from "react";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { t, type Locale, type MessageKey } from "@/messages";
import { APP_URL } from "@/lib/urls";

// Same idiom as the webapp's src/lib/validation.ts; the API stays the source of truth.
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

type FormState = "idle" | "submitting" | "success" | "invalid" | "error";

/**
 * Feedback renders below the pill; the form is a plain `w-full` column whose
 * width and alignment are the caller's concern (hero is left-aligned, bottom
 * CTA is centered).
 */
const FEEDBACK: Partial<
  Record<FormState, { role: "status" | "alert"; tone: string; key: MessageKey }>
> = {
  success: {
    role: "status",
    tone: "text-muted-foreground",
    key: "home.signedOut.songsets.success",
  },
  invalid: {
    role: "alert",
    tone: "text-destructive",
    key: "home.signedOut.songsets.invalidEmail",
  },
  error: {
    role: "alert",
    tone: "text-destructive",
    key: "home.signedOut.songsets.error",
  },
};

export function SongsetSignupForm({ locale }: { locale: Locale }) {
  const [email, setEmail] = useState("");
  const [state, setState] = useState<FormState>("idle");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    // Honeypot: bots fill the hidden field; real users never see it. Fake success, no request.
    const website = event.currentTarget.elements.namedItem("website");
    if (website instanceof HTMLInputElement && website.value !== "") {
      setState("success");
      return;
    }

    if (!EMAIL_PATTERN.test(email)) {
      setState("invalid");
      return;
    }

    setState("submitting");
    try {
      const response = await fetch(`${APP_URL}/api/capture-email`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, source: "landing-page", locale }),
      });
      setState(response.ok ? "success" : "error");
    } catch {
      setState("error");
    }
  }

  const submitting = state === "submitting";
  const feedback = FEEDBACK[state];

  return (
    <form onSubmit={handleSubmit} className="flex w-full flex-col gap-2" noValidate>
      <div aria-hidden="true" className="hidden">
        <input name="website" tabIndex={-1} autoComplete="off" />
      </div>
      {/* Pill: the focus ring lives on the container (focus-within), the input
          itself is borderless; the submit button is embedded at the right edge. */}
      <div className="flex h-12 items-center gap-1.5 rounded-full border border-border bg-background p-1.5 pl-4 shadow-sm focus-within:border-ring focus-within:ring-3 focus-within:ring-ring/50">
        <input
          type="email"
          name="email"
          aria-label={t(locale, "home.signedOut.songsets.emailLabel")}
          placeholder={t(locale, "home.signedOut.songsets.placeholder")}
          value={email}
          onChange={(event) => {
            setEmail(event.target.value);
            if (state === "invalid" || state === "error") setState("idle");
          }}
          disabled={submitting}
          className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={submitting}
          className={cn(buttonVariants(), "h-9 shrink-0 rounded-full px-4")}
        >
          {submitting
            ? t(locale, "home.signedOut.songsets.submitting")
            : t(locale, "home.signedOut.songsets.submit")}
        </button>
      </div>
      {feedback && (
        <p role={feedback.role} className={cn("px-4 text-left text-sm", feedback.tone)}>
          {t(locale, feedback.key)}
        </p>
      )}
    </form>
  );
}
