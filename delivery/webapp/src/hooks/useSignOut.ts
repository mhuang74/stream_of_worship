"use client";

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { useLocale } from "@/hooks/useLocale";
import { signOut } from "@/lib/auth-client";

/**
 * Shared sign-out hook used by the Header and Settings page. Centralizes the
 * full flow (signOut → success toast → redirect to /login → refresh) so the
 * i18n keys and redirect target live in exactly one place.
 */
export function useSignOut(): {
  isSigningOut: boolean;
  signOutAndRedirect: () => Promise<void>;
} {
  const router = useRouter();
  const { t } = useLocale();
  const [isSigningOut, setIsSigningOut] = useState(false);

  const signOutAndRedirect = useCallback(async () => {
    setIsSigningOut(true);
    try {
      await signOut();
      toast.success(t("settings.signOut.success"));
      router.push("/login");
      router.refresh();
    } catch {
      toast.error(t("settings.signOut.error"));
    } finally {
      setIsSigningOut(false);
    }
  }, [router, t]);

  return { isSigningOut, signOutAndRedirect };
}
