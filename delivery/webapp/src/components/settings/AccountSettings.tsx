"use client";

import { Button } from "@/components/ui/button";
import { useLocale } from "@/hooks/useLocale";
import { useSignOut } from "@/hooks/useSignOut";
import { Loader2, LogOut } from "lucide-react";
import { NameForm } from "@/components/settings/NameForm";
import { PasswordChangeForm } from "@/components/settings/PasswordChangeForm";

// Account section of Settings: name + password change + sign out (spec v1,
// Phase 7). Email change is out of scope (would require re-verification).
// Composition wrapper — the two forms own their state, validation, and errors.
export function AccountSettings() {
  const { t } = useLocale();
  const { isSigningOut, signOutAndRedirect } = useSignOut();

  return (
    <div className="space-y-6">
      <NameForm />
      <PasswordChangeForm />

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
