"use client";

import { useEffect, useRef, useState } from "react";
import { changePassword, updateUser, useSession } from "@/lib/auth-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useLocale } from "@/hooks/useLocale";
import { useSignOut } from "@/hooks/useSignOut";
import { Loader2, LogOut } from "lucide-react";

// Account section of Settings: name + password change + sign out (spec v1,
// Phase 7). Email change is out of scope (would require re-verification).
// Each form has its own submit, validation, and error handling.

interface NameErrors {
  name?: string;
  form?: string;
}

interface PasswordErrors {
  currentPassword?: string;
  newPassword?: string;
  confirmPassword?: string;
  form?: string;
}

export function AccountSettings() {
  const { t } = useLocale();
  const { data: session } = useSession();
  const user = session?.user;
  const { isSigningOut, signOutAndRedirect } = useSignOut();

  // Name
  const [name, setName] = useState("");
  // useSession() returns { data: null } on the first render while the session
  // loads, so the field cannot be initialized from user.name in useState.
  // Sync once when the session arrives; never clobber edits afterwards.
  const nameInitialized = useRef(false);
  useEffect(() => {
    if (!nameInitialized.current && user?.name != null) {
      setName(user.name);
      nameInitialized.current = true;
    }
  }, [user?.name]);
  const [nameErrors, setNameErrors] = useState<NameErrors>({});
  const [savingName, setSavingName] = useState(false);
  const [nameSaved, setNameSaved] = useState(false);

  // Password
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordErrors, setPasswordErrors] = useState<PasswordErrors>({});
  const [savingPassword, setSavingPassword] = useState(false);
  const [passwordSaved, setPasswordSaved] = useState(false);

  async function handleNameSubmit(e: React.FormEvent) {
    e.preventDefault();
    const next: NameErrors = {};
    if (!name.trim()) {
      next.name = t("settings.account.nameRequired");
    }
    if (Object.keys(next).length > 0) {
      setNameErrors(next);
      return;
    }
    setNameErrors({});
    setSavingName(true);
    setNameSaved(false);
    try {
      const result = await updateUser({ name });
      if (result.error) {
        setNameErrors({ form: result.error.message ?? t("settings.account.nameFailed") });
      } else {
        setNameSaved(true);
      }
    } catch {
      setNameErrors({ form: t("settings.account.nameFailed") });
    } finally {
      setSavingName(false);
    }
  }

  async function handlePasswordSubmit(e: React.FormEvent) {
    e.preventDefault();
    const next: PasswordErrors = {};
    if (!currentPassword) {
      next.currentPassword = t("settings.account.currentPasswordRequired");
    }
    if (!newPassword) {
      next.newPassword = t("settings.account.newPasswordRequired");
    } else if (newPassword.length < 8) {
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
    <div className="space-y-6">
      {/* Name */}
      <form onSubmit={handleNameSubmit} noValidate className="space-y-2">
        <Label htmlFor="account-name">{t("settings.account.name")}</Label>
        <Input
          id="account-name"
          type="text"
          autoComplete="name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          aria-describedby={nameErrors.name ? "account-name-error" : undefined}
          aria-invalid={!!nameErrors.name}
        />
        {nameErrors.name && (
          <p id="account-name-error" className="text-sm text-destructive" role="alert">
            {nameErrors.name}
          </p>
        )}
        {nameErrors.form && (
          <p className="text-sm text-destructive" role="alert">
            {nameErrors.form}
          </p>
        )}
        {nameSaved && (
          <p className="text-sm text-muted-foreground" role="status">
            {t("settings.account.nameSaved")}
          </p>
        )}
        <Button type="submit" disabled={savingName}>
          {savingName ? t("settings.account.saving") : t("settings.account.saveName")}
        </Button>
      </form>

      {/* Password */}
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

      {/* Sign out */}
      <Button variant="outline" onClick={signOutAndRedirect} disabled={isSigningOut}>
        {isSigningOut ? (
          <Loader2 className="size-4 mr-2 animate-spin" />
        ) : (
          <LogOut className="size-4 mr-2" />
        )}
        {t("settings.signOut")}
      </Button>
    </div>
  );
}
