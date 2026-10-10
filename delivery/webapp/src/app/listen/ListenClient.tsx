"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { CatalogSearch } from "@/components/search/CatalogSearch";
import { SongCard, SongCardData } from "@/components/songset/SongCard";
import { Button } from "@/components/ui/button";
import { Loader2, FolderOpen, Compass } from "lucide-react";
import { useLocale } from "@/hooks/useLocale";
import { useOfflineRedirect } from "@/hooks/useOfflineRedirect";
import { useFavoriteToggle } from "@/hooks/useFavoriteToggle";
import { useSongPlayback } from "@/hooks/useSongPlayback";

interface ListenClientProps {
  /** All of the viewer's favorite songIds (may exceed the first page). */
  favoriteSongIds: string[];
}

interface ListenSongData extends SongCardData {
  memberSongsets?: { id: string; name: string }[];
}

interface DiscoverySong extends SongCardData {
  favoriteCount?: number;
}

interface DiscoveryResponse {
  songs: DiscoverySong[];
  hasMore: boolean;
}

const DISCOVERY_PAGE_SIZE = 20;

const MY_SONGSETS_FETCH_LIMIT = 100;

export function ListenClient({
  favoriteSongIds,
}: ListenClientProps) {
  const { t } = useLocale();
  useOfflineRedirect();
  const { favoriteIds, setFavoriteIds, toggleFavorite } = useFavoriteToggle(
    new Set(favoriteSongIds)
  );

  // Re-sync when a server navigation hands us a fresh favoriteSongIds array
  // (initial Set construction alone would not observe later RSC refreshes).
  useEffect(() => {
    setFavoriteIds(new Set(favoriteSongIds));
  }, [favoriteSongIds, setFavoriteIds]);

  // --- Songs in My Songsets (client fetch of the inMySongsets listing) ---
  const [mySongsetSongs, setMySongsetSongs] = useState<ListenSongData[] | null>(null);
  const [mySongsetFailed, setMySongsetFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const response = await fetch(
          `/api/songs?inMySongsets=1&limit=${MY_SONGSETS_FETCH_LIMIT}`
        );
        if (!response.ok) throw new Error("Failed to load my songset songs");
        const data = (await response.json()) as { songs: ListenSongData[] };
        if (!cancelled) setMySongsetSongs(data.songs ?? []);
      } catch (err) {
        console.error("Error loading my songset songs:", err);
        if (!cancelled) setMySongsetFailed(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // --- Discovery (client fetch, offset pagination, hasMore-driven) ---
  const [discoverySongs, setDiscoverySongs] = useState<DiscoverySong[]>([]);
  const [discoveryHasMore, setDiscoveryHasMore] = useState(false);
  const [discoveryLoading, setDiscoveryLoading] = useState(true);
  const [discoveryFailed, setDiscoveryFailed] = useState(false);
  const discoveryOffsetRef = useRef(0);

  // Shared by the mount effect and the "Load more" button; state updates all
  // happen after the first await, so the mount path never sets state
  // synchronously within the effect body.
  const loadDiscovery = useCallback(async (offset: number) => {
    try {
      const response = await fetch(
        `/api/discovery?limit=${DISCOVERY_PAGE_SIZE}&offset=${offset}`
      );
      if (!response.ok) throw new Error("Failed to load discovery songs");
      const data = (await response.json()) as DiscoveryResponse;
      discoveryOffsetRef.current = offset + (data.songs?.length ?? 0);
      setDiscoverySongs((existing) => {
        if (offset === 0) return data.songs ?? [];
        const seen = new Set(existing.map((s) => s.id));
        return [...existing, ...(data.songs ?? []).filter((s) => !seen.has(s.id))];
      });
      setDiscoveryHasMore(Boolean(data.hasMore));
      setDiscoveryFailed(false);
    } catch (err) {
      console.error("Error loading discovery songs:", err);
      setDiscoveryFailed(true);
    } finally {
      setDiscoveryLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- async loader; setState runs post-await, rule can't see it
    void loadDiscovery(0);
  }, [loadDiscovery]);

  // --- Playback: one resolver across favorites + my-songsets + discovery so a
  // tap on any card resolves audio the same way (search keeps CatalogSearch's
  // own resolver, since only its results carry the tapped song). ---
  const allLoadedSongs = useMemo(() => {
    const lists: SongCardData[] = [
      ...(mySongsetSongs ?? []),
      ...discoverySongs,
    ];
    const byId = new Map<string, SongCardData>();
    for (const song of lists) {
      if (!byId.has(song.id)) byId.set(song.id, song);
    }
    return Array.from(byId.values());
  }, [mySongsetSongs, discoverySongs]);

  const resolveSong = useCallback(
    (songId: string) => {
      const song = allLoadedSongs.find((s) => s.id === songId);
      if (!song) return null;
      const recording = song.recordings[0];
      return {
        id: song.id,
        title: song.title,
        artist: song.composer || song.lyricist || t("browse.unknownArtist"),
        recording: recording
          ? {
              hashPrefix: recording.hashPrefix,
              contentHash: recording.contentHash,
              durationSeconds: recording.durationSeconds,
            }
          : null,
      };
    },
    [allLoadedSongs, t]
  );

  const { playingSongId, previewLoadingSongId, handlePlay } = useSongPlayback({
    resolveSong,
    noAudioMessage: t("browse.noAudioAvailable"),
    failedToLoadMessage: t("browse.failedToLoadPreview"),
  });

  const handleToggleFavorite = useCallback(
    (songId: string) => void toggleFavorite(songId),
    [toggleFavorite]
  );

  return (
    <div className="mx-auto max-w-5xl px-4 py-6 space-y-8 pb-24">
      <div>
        <h1 className="text-2xl font-bold">{t("listen.title")}</h1>
        <p className="text-sm text-muted-foreground mt-1">{t("listen.description")}</p>
      </div>

      {/* Search — pinned top; the same shared component the pull-up catalog
          renders, so modes/filters/results stay identical by construction. */}
      <section aria-label={t("listen.section.search")}>
        <h2 className="text-lg font-semibold mb-3">{t("listen.section.search")}</h2>
        <CatalogSearch
          mode="listen"
          unknownArtistMessage={t("browse.unknownArtist")}
          noAudioMessage={t("browse.noAudioAvailable")}
          failedToLoadMessage={t("browse.failedToLoadPreview")}
          className="rounded-xl border border-border bg-card p-4"
          resultsClassName="max-h-[480px]"
        />
      </section>

      {/* Songs in My Songsets — every song across the viewer's songsets,
          deduplicated; chips link to each containing songset. */}
      <section aria-label={t("listen.section.mySongsets")}>
        <h2 className="text-lg font-semibold mb-3">{t("listen.section.mySongsets")}</h2>
        {mySongsetFailed ? (
          <p className="text-sm text-muted-foreground py-6 text-center">
            {t("listen.discovery.loadFailed")}
          </p>
        ) : mySongsetSongs === null ? (
          <div className="flex justify-center py-10">
            <Loader2 className="size-6 animate-spin text-muted-foreground" />
          </div>
        ) : mySongsetSongs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-10 text-center">
            <FolderOpen className="size-8 text-muted-foreground mb-2" />
            <p className="font-medium">{t("listen.mySongsets.empty.title")}</p>
            <p className="text-sm text-muted-foreground mt-1 max-w-md">
              {t("listen.mySongsets.empty.description")}
            </p>
          </div>
        ) : (
          <div
            className="grid grid-cols-1 md:grid-cols-2 gap-2"
            data-testid="listen-my-songsets-list"
          >
            {mySongsetSongs.map((song) => (
              <div key={song.id} className="relative">
                <SongCard
                  song={song}
                  isFavorite={favoriteIds.has(song.id)}
                  onToggleFavorite={handleToggleFavorite}
                  onPlay={handlePlay}
                  isPlaying={playingSongId === song.id}
                  isPreviewLoading={previewLoadingSongId === song.id}
                />
                {(song.memberSongsets?.length ?? 0) > 0 && (
                  <div
                    className="flex flex-wrap gap-1 pl-3 pb-2 -mt-1"
                    data-testid={`member-songset-chips-${song.id}`}
                  >
                    {song.memberSongsets!.map((songset) => (
                      <Link
                        key={songset.id}
                        href={`/songsets/${songset.id}`}
                        className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                      >
                        {songset.name}
                      </Link>
                    ))}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      {/* Discovery — anonymous community favorites/songset inclusions the
          viewer does not already have, recency-first; Load more appends. */}
      <section aria-label={t("listen.section.discovery")}>
        <h2 className="text-lg font-semibold mb-3">{t("listen.section.discovery")}</h2>
        {discoveryFailed ? (
          <p className="text-sm text-muted-foreground py-6 text-center">
            {t("listen.discovery.loadFailed")}
          </p>
        ) : !discoveryLoading && discoverySongs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-10 text-center">
            <Compass className="size-8 text-muted-foreground mb-2" />
            <p className="font-medium">{t("listen.discovery.empty.title")}</p>
            <p className="text-sm text-muted-foreground mt-1 max-w-md">
              {t("listen.discovery.empty.description")}
            </p>
          </div>
        ) : (
          <>
            <div
              className="grid grid-cols-1 md:grid-cols-2 gap-2"
              data-testid="listen-discovery-list"
            >
              {discoverySongs.map((song) => (
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
            {discoveryHasMore && (
              <div className="flex justify-center mt-4">
                <Button
                  variant="outline"
                  onClick={() => void loadDiscovery(discoveryOffsetRef.current)}
                  disabled={discoveryLoading}
                  data-testid="discovery-load-more"
                >
                  {discoveryLoading ? (
                    <>
                      <Loader2 className="size-4 mr-2 animate-spin" />
                      {t("listen.discovery.loading")}
                    </>
                  ) : (
                    t("listen.discovery.loadMore")
                  )}
                </Button>
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}

export type { ListenSongData };
