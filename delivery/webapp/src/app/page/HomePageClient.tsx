"use client";

import { useCallback, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import dynamic from "next/dynamic";
import Link from "next/link";
import { SongCard } from "@/components/songset/SongCard";
import type { SongCardData } from "@/components/songset/SongCard";
import { StatCard } from "@/components/dashboard/StatCard";
import { DashboardSongsetCard } from "@/components/dashboard/DashboardSongsetCard";
import type { DashboardSongset } from "@/components/dashboard/DashboardSongsetCard";
import { buttonVariants } from "@/components/ui/button";
import { FileMusic, Heart, Library, Share2, Video } from "lucide-react";
import { cn } from "@/lib/utils";
import { useFavoriteToggle } from "@/hooks/useFavoriteToggle";
import { useSongPlayback } from "@/hooks/useSongPlayback";
import type { DashboardStats } from "@/lib/db/dashboard";
import { t } from "@/lib/i18n/messages";
import type { Locale, TranslationKey } from "@/lib/i18n/messages";

const ShareDialog = dynamic(
  () => import("@/components/share/ShareDialog").then((m) => ({ default: m.ShareDialog })),
  { ssr: false }
);

interface HomePageClientProps {
  locale: Locale;
  userName: string;
  stats: DashboardStats;
  recentSongsets: DashboardSongset[];
  recentFavoriteSongs: SongCardData[];
  communityFavorites: Array<SongCardData & { favoriteCount: number }>;
}

export function HomePageClient({
  locale,
  userName,
  stats,
  recentSongsets,
  recentFavoriteSongs,
  communityFavorites,
}: HomePageClientProps) {
  const router = useRouter();
  const [songs, setSongs] = useState<SongCardData[]>(recentFavoriteSongs);
  const { favoriteIds, toggleFavorite } = useFavoriteToggle(
    useMemo(() => new Set(recentFavoriteSongs.map((s) => s.id)), [recentFavoriteSongs])
  );
  const [shareTarget, setShareTarget] = useState<{
    id: string;
    name: string;
    durationSeconds: number | null;
  } | null>(null);
  const [isShareOpen, setIsShareOpen] = useState(false);

  const allSongs = useMemo(
    () => [...recentFavoriteSongs, ...communityFavorites],
    [recentFavoriteSongs, communityFavorites]
  );

  const resolveSong = useCallback(
    (songId: string) => {
      const song = allSongs.find((s) => s.id === songId);
      if (!song) return null;
      const recording = song.recordings[0];
      return {
        id: song.id,
        title: song.title,
        artist: song.composer || song.lyricist || t(locale, "browse.unknownArtist"),
        recording: recording
          ? {
              hashPrefix: recording.hashPrefix,
              contentHash: recording.contentHash,
              durationSeconds: recording.durationSeconds,
            }
          : null,
      };
    },
    [allSongs, locale]
  );

  const { playingSongId, previewLoadingSongId, handlePlay } = useSongPlayback({
    resolveSong,
    noAudioMessage: t(locale, "browse.noAudioAvailable"),
    failedToLoadMessage: t(locale, "browse.failedToLoadPreview"),
  });

  const handleToggleFavorite = useCallback(
    async (songId: string) => {
      const ok = await toggleFavorite(songId);
      if (ok) {
        setSongs((prev) => prev.filter((s) => s.id !== songId));
      }
    },
    [toggleFavorite]
  );

  const statCards: Array<{ key: TranslationKey; value: number; icon: React.ComponentType<{ className?: string }> }> = [
    { key: "home.stats.songsetsCreated", value: stats.songsetsCreated, icon: FileMusic },
    { key: "home.stats.songsetsRendered", value: stats.songsetsRendered, icon: Video },
    { key: "home.stats.songsetsShared", value: stats.songsetsShared, icon: Share2 },
    { key: "home.stats.favoriteSongs", value: stats.favoriteSongs, icon: Heart },
    { key: "home.stats.catalogSongs", value: stats.catalogSongs, icon: Library },
  ];

  const handleSongsetPlay = useCallback(
    (songsetId: string) => {
      router.push(`/songsets/${songsetId}/play`);
    },
    [router]
  );

  const handleSongsetShare = useCallback(
    (songsetId: string, name: string) => {
      const songset = recentSongsets.find((s) => s.id === songsetId);
      setShareTarget({
        id: songsetId,
        name,
        durationSeconds: songset?.durationSeconds ?? null,
      });
      setIsShareOpen(true);
    },
    [recentSongsets]
  );

  return (
    <div className="mx-auto max-w-5xl px-4 py-6 space-y-8">
      <h1 className="text-2xl font-bold">
        {t(locale, "home.welcomeBack").replace("${name}", userName)}
      </h1>

      {/* Stats */}
      <section aria-label={t(locale, "home.stats.songsetsCreated")} className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-5 gap-3">
        {statCards.map((card) => (
          <StatCard
            key={card.key}
            labelKey={card.key}
            value={card.value}
            icon={card.icon}
          />
        ))}
      </section>

      {/* Recent songsets */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">{t(locale, "home.section.recentSongsets")}</h2>
          <Link href="/songsets" className="text-sm text-primary hover:underline">
            {t(locale, "home.action.viewAll")}
          </Link>
        </div>
        {recentSongsets.length === 0 ? (
          <div className="rounded-xl border border-border bg-card p-6 text-center">
            <p className="text-sm text-muted-foreground">{t(locale, "home.empty.recentSongsets")}</p>
            <Link
              href="/songsets"
              className={cn(buttonVariants({ variant: "outline", size: "sm" }), "mt-3")}
            >
              {t(locale, "home.empty.createSongset")}
            </Link>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            {recentSongsets.map((songset) => (
              <DashboardSongsetCard
                key={songset.id}
                songset={songset}
                onPlay={handleSongsetPlay}
                onShare={handleSongsetShare}
              />
            ))}
          </div>
        )}
      </section>

      {/* Recent favorites */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold">{t(locale, "home.section.recentFavorites")}</h2>
          <Link href="/favorites" className="text-sm text-primary hover:underline">
            {t(locale, "home.action.viewAll")}
          </Link>
        </div>
        {songs.length === 0 ? (
          <div className="rounded-xl border border-border bg-card p-6 text-center">
            <p className="text-sm text-muted-foreground">{t(locale, "home.empty.recentFavorites")}</p>
            <Link
              href="/songsets"
              className={cn(buttonVariants({ variant: "outline", size: "sm" }), "mt-3")}
            >
              {t(locale, "home.empty.browseCatalog")}
            </Link>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {songs.map((song) => (
              <SongCard
                key={song.id}
                song={song}
                isFavorite={favoriteIds.has(song.id)}
                onToggleFavorite={handleToggleFavorite}
                onPlay={handlePlay}
                isPlaying={playingSongId === song.id}
                isPreviewLoading={previewLoadingSongId === song.id}
              />
            ))}
          </div>
        )}
      </section>

      {/* Community favorites */}
      {communityFavorites.length > 0 && (
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-lg font-semibold">{t(locale, "home.section.communityFavorites")}</h2>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {communityFavorites.map((song) => (
              <SongCard
                key={song.id}
                song={song}
                favoriteCount={song.favoriteCount}
                isFavorite={favoriteIds.has(song.id)}
                onToggleFavorite={handleToggleFavorite}
                onPlay={handlePlay}
                isPlaying={playingSongId === song.id}
                isPreviewLoading={previewLoadingSongId === song.id}
              />
            ))}
          </div>
        </section>
      )}

      {shareTarget && (
        <ShareDialog
          open={isShareOpen}
          onOpenChange={setIsShareOpen}
          songsetId={shareTarget.id}
          songsetName={shareTarget.name}
          durationSeconds={shareTarget.durationSeconds}
        />
      )}
    </div>
  );
}
