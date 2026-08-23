"use client";

import { useState } from "react";
import Link from "next/link";
import { requestPasswordReset } from "@/lib/auth-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { AuthLanguageSwitcher } from "@/components/auth/AuthLanguageSwitcher";
import { useLocale } from "@/hooks/useLocale";
import { isValidEmail } from "@/lib/validation";

export default function ForgotPasswordPage() {
  const { t } = useLocale();
  const [email, setEmail] = useState("");
  const [errors, setErrors] = useState<{ email?: string; form?: string }>({});
  const [loading, setLoading] = useState(false);
  // No-account-enumeration: always show the same confirmation regardless of
  // whether the account exists (Better Auth already returns a generic message).
  const [submitted, setSubmitted] = useState(false);

  function validate() {
    const next: typeof errors = {};
    if (!email) {
      next.email = t("auth.forgotPassword.validation.emailRequired");
    } else if (!isValidEmail(email)) {
      next.email = t("auth.forgotPassword.validation.emailFormat");
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
      const result = await requestPasswordReset({
        email,
        redirectTo: "/reset-password",
      });
      if (result.error) {
        setErrors({ form: t("auth.forgotPassword.error.unexpected") });
      } else {
        setSubmitted(true);
      }
    } catch {
      setErrors({ form: t("auth.forgotPassword.error.unexpected") });
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
          <CardTitle className="text-2xl">{t("auth.forgotPassword.title")}</CardTitle>
          <CardDescription>{t("auth.forgotPassword.subtitle")}</CardDescription>
        </CardHeader>
        <CardContent>
          {submitted ? (
            <div className="space-y-4">
              <p className="text-sm" role="status">
                {t("auth.forgotPassword.confirmation")}
              </p>
            </div>
          ) : (
            <form onSubmit={handleSubmit} noValidate className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="email">{t("auth.forgotPassword.email")}</Label>
                <Input
                  id="email"
                  type="email"
                  placeholder={t("auth.forgotPassword.emailPlaceholder")}
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
              {errors.form && (
                <p className="text-sm text-destructive" role="alert">
                  {errors.form}
                </p>
              )}
              <Button type="submit" className="w-full" disabled={loading}>
                {loading
                  ? t("auth.forgotPassword.submitting")
                  : t("auth.forgotPassword.submit")}
              </Button>
            </form>
          )}
          <p className="text-center text-sm text-muted-foreground mt-4">
            {t("auth.forgotPassword.backToSignIn")}{" "}
            <Link href="/login" className="text-primary underline-offset-4 hover:underline">
              {t("auth.signIn.title")}
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
