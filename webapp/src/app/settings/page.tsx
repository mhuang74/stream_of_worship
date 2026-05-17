"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { SettingsForm, UserSettingsData } from "@/components/settings/SettingsForm";
import { SettingsSkeleton } from "@/components/settings/SettingsSkeleton";
import { useServerQuery } from "@/hooks/useServerQuery";
import { setQueryData } from "@/lib/query-client";
import { toast } from "sonner";

const SETTINGS_QUERY_KEY = "/api/settings";

const DEFAULT_SETTINGS: UserSettingsData = {
  offlineAutoCache: true,
  defaultGapBeats: 2.0,
  defaultVideoTemplate: "dark",
  defaultResolution: "720p",
  lyricsLoopWindowSeconds: 3.0,
  defaultFontSizePreset: "M",
  defaultKeyShiftSemitones: 0,
  timingReviewFont: "sans",
};

async function fetchSettings(): Promise<UserSettingsData> {
  const res = await fetch("/api/settings");
  if (!res.ok) throw new Error("Failed to load settings");
  const data = await res.json();
  return { ...DEFAULT_SETTINGS, ...data.settings };
}

export default function SettingsPage() {
  const router = useRouter();
  const [isSaving, setIsSaving] = useState(false);

  const { data: settings, isLoading, error } = useServerQuery<UserSettingsData>(
    SETTINGS_QUERY_KEY,
    fetchSettings,
    { staleTimeMs: 5 * 60_000 }
  );

  if (error?.message === "Failed to load settings" && !settings) {
    // Redirect on auth error; we can't distinguish 401 from fetch error here
    // without changing fetchSettings — so just show error.
  }

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

      // Update cache so the next page visit sees fresh data without a fetch
      setQueryData(SETTINGS_QUERY_KEY, updated);
      toast.success("Settings saved");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save settings");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="px-4 py-6 max-w-2xl mx-auto">
      <h1 className="text-2xl font-bold mb-6">Settings</h1>

      {isLoading && <SettingsSkeleton />}

      {error && !isLoading && (
        <p className="text-destructive">{error.message}</p>
      )}

      {settings && !isLoading && (
        <SettingsForm initialSettings={settings} onSave={handleSave} isSaving={isSaving} />
      )}
    </div>
  );
}
