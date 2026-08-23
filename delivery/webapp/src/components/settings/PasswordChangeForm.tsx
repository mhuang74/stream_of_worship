"use client";

import { useState } from "react";
import { changePassword } from "@/lib/auth-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useLocale } from "@/hooks/useLocale";
import { MIN_PASSWORD_LENGTH } from "@/lib/validation";

interface PasswordErrors {
  currentPassword?: string;
  newPassword?: string;
  confirmPassword?: string;
  form?: string;
}

// Password-change form of the Account settings section (spec v1, Phase 7).
// Has its own submit, validation, and error handling.
export function PasswordChangeForm() {
  const { t } = useLocale();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordErrors, setPasswordErrors] = useState<PasswordErrors>({});
  const [savingPassword, setSavingPassword] = useState(false);
  const [passwordSaved, setPasswordSaved] = useState(false);

  async function handlePasswordSubmit(e: React.FormEvent) {
    e.preventDefault();
    const next: PasswordErrors = {};
    if (!currentPassword) {
      next.currentPassword = t("settings.account.currentPasswordRequired");
    }
    if (!newPassword) {
      next.newPassword = t("settings.account.newPasswordRequired");
    } else if (newPassword.length < MIN_PASSWORD_LENGTH) {
      next.newPassword = t("settings.account.newPasswordShort");
    }
    if (!confirmPassword) {
      next.confirmPassword = t("settings.account.confirmPasswordRequired");
    } else if (confirmPassword !== newPassword) {
      next.confirmPassword = t("settings.account.confirmMismatch");
    }
    if (Object.keys(next).length > 0) {
      setPasswordErrors(next);
      return;
    }
    setPasswordErrors({});
    setSavingPassword(true);
    setPasswordSaved(false);
    try {
      const result = await changePassword({ currentPassword, newPassword });
      if (result.error) {
        // INVALID_PASSWORD = wrong current password.
        if (result.error.code === "INVALID_PASSWORD") {
          setPasswordErrors({ currentPassword: t("settings.account.currentPasswordWrong") });
        } else {
          setPasswordErrors({
            form: result.error.message ?? t("settings.account.passwordFailed"),
          });
        }
      } else {
        setPasswordSaved(true);
        setCurrentPassword("");
        setNewPassword("");
        setConfirmPassword("");
      }
    } catch {
      setPasswordErrors({ form: t("settings.account.passwordFailed") });
    } finally {
      setSavingPassword(false);
    }
  }

  return (
    <form onSubmit={handlePasswordSubmit} noValidate className="space-y-4">
      <div className="space-y-2">
        <Label htmlFor="account-current-password">{t("settings.account.currentPassword")}</Label>
        <Input
          id="account-current-password"
          type="password"
          autoComplete="current-password"
          value={currentPassword}
          onChange={(e) => setCurrentPassword(e.target.value)}
          aria-describedby={
            passwordErrors.currentPassword ? "account-current-password-error" : undefined
          }
          aria-invalid={!!passwordErrors.currentPassword}
        />
        {passwordErrors.currentPassword && (
          <p id="account-current-password-error" className="text-sm text-destructive" role="alert">
            {passwordErrors.currentPassword}
          </p>
        )}
      </div>
      <div className="space-y-2">
        <Label htmlFor="account-new-password">{t("settings.account.newPassword")}</Label>
        <Input
          id="account-new-password"
          type="password"
          autoComplete="new-password"
          value={newPassword}
          onChange={(e) => setNewPassword(e.target.value)}
          aria-describedby={passwordErrors.newPassword ? "account-new-password-error" : undefined}
          aria-invalid={!!passwordErrors.newPassword}
        />
        {passwordErrors.newPassword && (
          <p id="account-new-password-error" className="text-sm text-destructive" role="alert">
            {passwordErrors.newPassword}
          </p>
        )}
      </div>
      <div className="space-y-2">
        <Label htmlFor="account-confirm-password">{t("settings.account.confirmPassword")}</Label>
        <Input
          id="account-confirm-password"
          type="password"
          autoComplete="new-password"
          value={confirmPassword}
          onChange={(e) => setConfirmPassword(e.target.value)}
          aria-describedby={
            passwordErrors.confirmPassword ? "account-confirm-password-error" : undefined
          }
          aria-invalid={!!passwordErrors.confirmPassword}
        />
        {passwordErrors.confirmPassword && (
          <p id="account-confirm-password-error" className="text-sm text-destructive" role="alert">
            {passwordErrors.confirmPassword}
          </p>
        )}
      </div>
      {passwordErrors.form && (
        <p className="text-sm text-destructive" role="alert">
          {passwordErrors.form}
        </p>
      )}
      {passwordSaved && (
        <p className="text-sm text-muted-foreground" role="status">
          {t("settings.account.passwordSaved")}
        </p>
      )}
      <Button type="submit" disabled={savingPassword}>
        {savingPassword ? t("settings.account.saving") : t("settings.account.savePassword")}
      </Button>
    </form>
  );
}
