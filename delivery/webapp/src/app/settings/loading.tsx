"use client";

import { SettingsSkeleton } from "@/components/settings/SettingsSkeleton";
import { useLocale } from "@/hooks/useLocale";

export default function SettingsLoading() {
  const { t } = useLocale();
  return (
    <div className="px-4 py-6 max-w-2xl mx-auto">
      <h1 className="text-2xl font-bold mb-6">{t("settings.title")}</h1>
      <SettingsSkeleton />
    </div>
  );
}
