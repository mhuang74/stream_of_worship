"use client";

import { SongsetListSkeleton } from "@/components/songset/SongsetListSkeleton";
import { useLocale } from "@/hooks/useLocale";

export default function SongsetsLoading() {
  const { t } = useLocale();
  return (
    <div className="px-4 py-6 pb-24 lg:pb-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold">{t("nav.songsets")}</h1>
      </div>
      <SongsetListSkeleton />
    </div>
  );
}
