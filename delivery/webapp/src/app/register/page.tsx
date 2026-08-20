"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { signUp } from "@/lib/auth-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { AuthLanguageSwitcher } from "@/components/auth/AuthLanguageSwitcher";
import { useLocale } from "@/hooks/useLocale";

export default function RegisterPage() {
  const router = useRouter();
  const { t, locale } = useLocale();
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [errors, setErrors] = useState<{
    name?: string;
    email?: string;
    password?: string;
    confirmPassword?: string;
    form?: string;
  }>({});
  const [loading, setLoading] = useState(false);

  function validate() {
    const next: typeof errors = {};
    if (!name) {
      next.name = t("auth.register.validation.nameRequired");
    }
    if (!email) {
      next.email = t("auth.register.validation.emailRequired");
    } else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
      next.email = t("auth.register.validation.emailFormat");
    }
    if (!password) {
      next.password = t("auth.register.validation.passwordRequired");
    } else if (password.length < 8) {
      next.password = t("auth.register.validation.passwordShort");
    }
    if (!confirmPassword) {
      next.confirmPassword = t("auth.register.validation.confirmRequired");
    } else if (confirmPassword !== password) {
      next.confirmPassword = t("auth.register.validation.confirmMismatch");
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
      const result = await signUp.email({ email, password, name });
      if (result.error) {
        setErrors({ form: result.error.message ?? t("auth.register.error.failed") });
      } else {
        // Persist the chosen display locale to user_settings so the post-login
        // UI is in the selected language. Best-effort: navigation proceeds even
        // if this fails (defaults to en).
        if (locale !== "en") {
          try {
            await fetch("/api/settings", {
              method: "PUT",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ locale }),
            });
          } catch {
            // best-effort
          }
        }
        router.push("/songsets");
        router.refresh();
      }
    } catch {
      setErrors({ form: t("auth.register.error.unexpected") });
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
          <CardTitle className="text-2xl">{t("auth.register.title")}</CardTitle>
          <CardDescription>{t("auth.register.subtitle")}</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} noValidate className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="name">{t("auth.register.name")}</Label>
              <Input
                id="name"
                type="text"
                placeholder={t("auth.register.namePlaceholder")}
                autoComplete="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                aria-describedby={errors.name ? "name-error" : undefined}
                aria-invalid={!!errors.name}
              />
              {errors.name && (
                <p id="name-error" className="text-sm text-destructive" role="alert">
                  {errors.name}
                </p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="email">{t("auth.register.email")}</Label>
              <Input
                id="email"
                type="email"
                placeholder={t("auth.register.emailPlaceholder")}
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
              <Label htmlFor="password">{t("auth.register.password")}</Label>
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
              <Label htmlFor="confirmPassword">{t("auth.register.confirmPassword")}</Label>
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
              {loading ? t("auth.register.submitting") : t("auth.register.submit")}
            </Button>
          </form>
          <p className="text-center text-sm text-muted-foreground mt-4">
            {t("auth.register.hasAccount")}{" "}
            <Link href="/login" className="text-primary underline-offset-4 hover:underline">
              {t("auth.register.signInLink")}
            </Link>
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
