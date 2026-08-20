"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { SettingsForm, UserSettingsData } from "@/components/settings/SettingsForm";
import { SettingsSkeleton } from "@/components/settings/SettingsSkeleton";
import { FontPreviewStylesheets } from "@/components/fonts/FontPreviewStylesheets";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";
import { useLocale } from "@/hooks/useLocale";
import { signOut } from "@/lib/auth-client";
import { Loader2, LogOut } from "lucide-react";

const DEFAULT_SETTINGS: UserSettingsData = {
  offlineAutoCache: true,
  defaultGapBeats: 2.0,
  defaultVideoTemplate: "dark",
  defaultResolution: "720p",
  lyricsLoopWindowSeconds: 3.0,
  defaultFontSizePreset: "M",
  defaultFontFamily: "noto_serif_tc",
  defaultKeyShiftSemitones: 0,
  timingReviewFont: "sans",
  locale: "en",
};

async function fetchSettings(): Promise<UserSettingsData> {
  const res = await fetch("/api/settings");
  if (!res.ok) throw new Error("Failed to load settings");
  const data = await res.json();
  return { ...DEFAULT_SETTINGS, ...data.settings };
}

export default function SettingsPage() {
  const router = useRouter();
  const { t, setLocale } = useLocale();
  const [settings, setSettings] = useState<UserSettingsData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [isSigningOut, setIsSigningOut] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadSettings() {
      try {
        setIsLoading(true);
        setError(null);
        const nextSettings = await fetchSettings();
        if (!cancelled) {
          setSettings(nextSettings);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : t("settings.failedLoad"));
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    loadSettings();

    return () => {
      cancelled = true;
    };
  }, [t]);

  async function handleSave(updated: UserSettingsData) {
    setIsSaving(true);
    try {
      const res = await fetch("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(updated),
      });

      if (!res.ok) {
        if (res.status === 401) {
          router.push("/login");
          return;
        }
        const data = await res.json();
        throw new Error(data.error || "Failed to save settings");
      }

      setSettings(updated);
      setLocale(updated.locale);
      toast.success(t("settings.saved"));
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("settings.failedSave"));
    } finally {
      setIsSaving(false);
    }
  }

  async function handleSignOut() {
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
  }

  return (
    <div className="px-4 py-6 max-w-2xl mx-auto">
      <FontPreviewStylesheets />
      <h1 className="text-2xl font-bold mb-6">{t("settings.title")}</h1>

      {isLoading && <SettingsSkeleton />}

      {error && !isLoading && (
        <p className="text-destructive">{error}</p>
      )}

      {settings && !isLoading && (
        <>
          <SettingsForm initialSettings={settings} onSave={handleSave} isSaving={isSaving} />
          <div className="mt-8 border-t pt-6">
            <h2 className="text-lg font-semibold mb-3">{t("settings.section.account")}</h2>
            <Button variant="outline" onClick={handleSignOut} disabled={isSigningOut}>
              {isSigningOut ? (
                <Loader2 className="size-4 mr-2 animate-spin" />
              ) : (
                <LogOut className="size-4 mr-2" />
              )}
              {t("settings.signOut")}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
