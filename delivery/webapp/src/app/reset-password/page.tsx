"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { resetPassword } from "@/lib/auth-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { AuthLanguageSwitcher } from "@/components/auth/AuthLanguageSwitcher";
import { useLocale } from "@/hooks/useLocale";
import { MIN_PASSWORD_LENGTH } from "@/lib/validation";

function ResetPasswordForm() {
  const router = useRouter();
  const { t } = useLocale();
  const searchParams = useSearchParams();
  const token = searchParams.get("token");
  const error = searchParams.get("error");

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [errors, setErrors] = useState<{
    password?: string;
    confirmPassword?: string;
    form?: string;
  }>({});
  const [loading, setLoading] = useState(false);

  function validate() {
    const next: typeof errors = {};
    if (!password) {
      next.password = t("auth.resetPassword.validation.passwordRequired");
    } else if (password.length < MIN_PASSWORD_LENGTH) {
      next.password = t("auth.resetPassword.validation.passwordShort");
    }
    if (!confirmPassword) {
      next.confirmPassword = t("auth.resetPassword.validation.confirmRequired");
    } else if (confirmPassword !== password) {
      next.confirmPassword = t("auth.resetPassword.validation.confirmMismatch");
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
      const result = await resetPassword({ newPassword: password, token: token ?? "" });
      if (result.error) {
        // INVALID_TOKEN covers missing/expired/consumed tokens.
        if (result.error.code === "INVALID_TOKEN") {
          setErrors({ form: t("auth.resetPassword.error.invalidToken") });
        } else {
          setErrors({ form: result.error.message ?? t("auth.resetPassword.error.failed") });
        }
      } else {
        // No auto sign-in: reset is a credential-recovery moment, not an
        // ownership proof (spec v1) — send the user to the sign-in page.
        router.push("/login");
      }
    } catch {
      setErrors({ form: t("auth.resetPassword.error.unexpected") });
    } finally {
      setLoading(false);
    }
  }

  if (error === "INVALID_TOKEN") {
    return (
      <Card className="w-full max-w-sm">
        <CardHeader className="space-y-1">
          <div className="flex justify-end">
            <AuthLanguageSwitcher />
          </div>
          <CardTitle className="text-2xl">{t("auth.resetPassword.title")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm" role="alert">
            {t("auth.resetPassword.error.invalidToken")}
          </p>
          <p className="text-center text-sm text-muted-foreground">
            <Link href="/forgot-password" className="text-primary underline-offset-4 hover:underline">
              {t("auth.resetPassword.requestNewLink")}
            </Link>
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="w-full max-w-sm">
      <CardHeader className="space-y-1">
        <div className="flex justify-end">
          <AuthLanguageSwitcher />
        </div>
        <CardTitle className="text-2xl">{t("auth.resetPassword.title")}</CardTitle>
        <CardDescription>{t("auth.resetPassword.subtitle")}</CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleSubmit} noValidate className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="password">{t("auth.resetPassword.password")}</Label>
            <Input
              id="password"
              type="password"
              autoComplete="new-password"
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
          <div className="space-y-2">
            <Label htmlFor="confirmPassword">{t("auth.resetPassword.confirmPassword")}</Label>
            <Input
              id="confirmPassword"
              type="password"
              autoComplete="new-password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              aria-describedby={errors.confirmPassword ? "confirmPassword-error" : undefined}
              aria-invalid={!!errors.confirmPassword}
            />
            {errors.confirmPassword && (
              <p id="confirmPassword-error" className="text-sm text-destructive" role="alert">
                {errors.confirmPassword}
              </p>
            )}
          </div>
          {errors.form && (
            <p className="text-sm text-destructive" role="alert">
              {errors.form}
            </p>
          )}
          <Button type="submit" className="w-full" disabled={loading}>
            {loading ? t("auth.resetPassword.submitting") : t("auth.resetPassword.submit")}
          </Button>
        </form>
        <p className="text-center text-sm text-muted-foreground mt-4">
          <Link href="/login" className="text-primary underline-offset-4 hover:underline">
            {t("auth.signIn.title")}
          </Link>
        </p>
      </CardContent>
    </Card>
  );
}

export default function ResetPasswordPage() {
  return (
    <div className="min-h-screen flex items-center justify-center p-4">
      <Suspense fallback={null}>
        <ResetPasswordForm />
      </Suspense>
    </div>
  );
}
