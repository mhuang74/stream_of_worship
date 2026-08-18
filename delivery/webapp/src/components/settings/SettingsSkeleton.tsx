"use client";

import { Skeleton } from "@/components/ui/skeleton";
import { useLocale } from "@/hooks/useLocale";

export function SettingsSkeleton() {
  const { t } = useLocale();
  return (
    <div className="space-y-6" role="status" aria-label={t("settings.loading")}>
      <span className="sr-only">{t("settings.loading")}</span>
      {Array.from({ length: 4 }).map((_, i) => (
        <div key={i} className="rounded-lg border p-4 space-y-3">
          <Skeleton className="h-5 w-32" />
          <Skeleton className="h-10 w-full rounded-md" />
        </div>
      ))}
    </div>
  );
}
