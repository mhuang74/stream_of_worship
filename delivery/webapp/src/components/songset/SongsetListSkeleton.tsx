"use client";

import { Skeleton } from "@/components/ui/skeleton";
import { useLocale } from "@/hooks/useLocale";

function SongsetRowSkeleton() {
  return (
    <div className="flex items-center gap-4 rounded-lg border p-4">
      <div className="flex-1 space-y-2">
        <Skeleton className="h-5 w-48" />
        <Skeleton className="h-4 w-32" />
      </div>
      <Skeleton className="h-9 w-20 rounded-md" />
    </div>
  );
}

export function SongsetListSkeleton() {
  const { t } = useLocale();
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3" aria-label={t("songsets.loading.songsets")} role="status">
      <span className="sr-only">{t("songsets.loading.songsetsSr")}</span>
      {Array.from({ length: 4 }).map((_, i) => (
        <SongsetRowSkeleton key={i} />
      ))}
    </div>
  );
}
