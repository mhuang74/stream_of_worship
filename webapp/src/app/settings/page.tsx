"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { SettingsForm, UserSettingsData } from "@/components/settings/SettingsForm";
import { toast } from "sonner";

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

export default function SettingsPage() {
  const router = useRouter();
  const [settings, setSettings] = useState<UserSettingsData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadSettings() {
      try {
        setIsLoading(true);
        setError(null);

        const res = await fetch("/api/settings");

        if (!res.ok) {
          if (res.status === 401) {
            router.push("/login");
            return;
          }
          throw new Error("Failed to load settings");
        }

        const data = await res.json();

        if (!cancelled) {
          setSettings({ ...DEFAULT_SETTINGS, ...data.settings });
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load settings");
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
  }, [router]);

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

      {isLoading && (
        <div className="flex min-h-[30vh] items-center justify-center">
          <div
            role="status"
            className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary"
          />
        </div>
      )}

      {error && !isLoading && (
        <p className="text-destructive">{error}</p>
      )}

      {settings && !isLoading && (
        <SettingsForm initialSettings={settings} onSave={handleSave} isSaving={isSaving} />
      )}
    </div>
  );
}
