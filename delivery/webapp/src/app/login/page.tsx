"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { signIn } from "@/lib/auth-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { AuthLanguageSwitcher } from "@/components/auth/AuthLanguageSwitcher";
import { useLocale } from "@/hooks/useLocale";
import { useResendVerification } from "@/hooks/useResendVerification";
import { persistLocale } from "@/lib/persist-locale";
import { isValidEmail, MIN_PASSWORD_LENGTH } from "@/lib/validation";

export default function LoginPage() {
  const router = useRouter();
  const { t, locale } = useLocale();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [errors, setErrors] = useState<{ email?: string; password?: string; form?: string }>({});
  const [loading, setLoading] = useState(false);
  const [unverifiedEmail, setUnverifiedEmail] = useState<string | null>(null);
  const { resending, resendState, resend } = useResendVerification(unverifiedEmail);

  function validate() {
    const next: typeof errors = {};
    if (!email) {
      next.email = t("auth.signIn.validation.emailRequired");
    } else if (!isValidEmail(email)) {
      next.email = t("auth.signIn.validation.emailFormat");
    }
    if (!password) {
      next.password = t("auth.signIn.validation.passwordRequired");
    } else if (password.length < MIN_PASSWORD_LENGTH) {
      next.password = t("auth.signIn.validation.passwordShort");
    }
    return next;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const validation = validate();
    if (Object.keys(validation).length > 0) {
      setErrors(validation);
      return;
    }
    setErrors({});
    setLoading(true);
    try {
      const result = await signIn.email({ email, password });
      if (result.error) {
        // Better Auth returns 403 + code EMAIL_NOT_VERIFIED when the account
        // is unverified — surface a resend action instead of a dead-end error.
        if (result.error.code === "EMAIL_NOT_VERIFIED") {
          setUnverifiedEmail(email);
          setErrors({});
        } else {
          setErrors({ form: result.error.message ?? t("auth.signIn.error.invalid") });
        }
      } else {
        // Persist the chosen display locale to user_settings so the post-login
        // UI and future sessions/devices reflect the pre-login choice.
        // Best-effort: navigation proceeds even if this fails.
        await persistLocale(locale);
        // Honor deep-link callbackUrl from proxy.ts, but never open-redirect
        // (external/protocol-relative URLs) or loop back to auth pages.
        const callbackUrl = new URLSearchParams(window.location.search).get("callbackUrl");
        const safeCallback =
          callbackUrl &&
          callbackUrl.startsWith("/") &&
          !callbackUrl.startsWith("//") &&
          callbackUrl !== "/login" &&
          callbackUrl !== "/register"
            ? callbackUrl
            : "/";
        router.push(safeCallback);
        router.refresh();
      }
    } catch {
      setErrors({ form: t("auth.signIn.error.unexpected") });
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <Card className="w-full max-w-sm">
        <CardHeader className="space-y-1">
          <div className="flex justify-end">
            <AuthLanguageSwitcher />
          </div>
          <CardTitle className="text-2xl">{t("auth.signIn.title")}</CardTitle>
          <CardDescription>{t("auth.signIn.subtitle")}</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} noValidate className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="email">{t("auth.signIn.email")}</Label>
              <Input
                id="email"
                type="email"
                placeholder={t("auth.signIn.emailPlaceholder")}
                autoComplete="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                aria-describedby={errors.email ? "email-error" : undefined}
                aria-invalid={!!errors.email}
              />
              {errors.email && (
                <p id="email-error" className="text-sm text-destructive" role="alert">
                  {errors.email}
                </p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="password">{t("auth.signIn.password")}</Label>
              <Input
                id="password"
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                aria-describedby={errors.password ? "password-error" : undefined}
                aria-invalid={!!errors.password}
              />
              {errors.password && (
                <p id="password-error" className="text-sm text-destructive" role="alert">
                  {errors.password}
                </p>
              )}
            </div>
            {errors.form && (
              <p className="text-sm text-destructive" role="alert">
                {errors.form}
              </p>
            )}
            {unverifiedEmail && (
              <div className="text-sm">
                <p className="text-destructive" role="alert">
                  {t("auth.signIn.unverified.message")}
                </p>
                <Button
                  type="button"
                  variant="link"
                  className="px-0 h-auto"
                  onClick={resend}
                  disabled={resending}
                >
                  {resending
                    ? t("auth.signIn.unverified.resending")
                    : t("auth.signIn.unverified.resend")}
                </Button>
                {resendState === "sent" && (
                  <p className="text-muted-foreground" role="status">
                    {t("auth.signIn.unverified.resendSent")}
                  </p>
                )}
                {resendState === "error" && (
                  <p className="text-destructive" role="alert">
                    {t("auth.signIn.unverified.resendError")}
                  </p>
                )}
              </div>
            )}
            <Button type="submit" className="w-full" disabled={loading}>
              {loading ? t("auth.signIn.submitting") : t("auth.signIn.submit")}
            </Button>
          </form>
          <p className="text-center text-sm mt-2">
            <Link href="/forgot-password" className="text-primary underline-offset-4 hover:underline">
              {t("auth.signIn.forgotPassword")}
            </Link>
          </p>
          <p className="text-center text-sm text-muted-foreground mt-4">
            {t("auth.signIn.noAccount")}{" "}
            <Link href="/register" className="text-primary underline-offset-4 hover:underline">
              {t("auth.signIn.registerLink")}
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
