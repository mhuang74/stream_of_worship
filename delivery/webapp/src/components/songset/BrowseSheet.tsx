"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { SongSearch } from "./SongSearch";
import { SharedFilters } from "./SharedFilters";
import { SongCard, SongCardData } from "./SongCard";
import { useSemanticSearch } from "@/components/search/SemanticSearch";
import type { StructuredSearchCriteria } from "./search/types";
import { Loader2, Music, AlertCircle, Search, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { useAudioPlayerContext } from "@/contexts/AudioPlayerContext";
import { useFavoriteToggle } from "@/hooks/useFavoriteToggle";
import { useSongPlayback } from "@/hooks/useSongPlayback";
import { SONGSET_MAX_SONGS } from "@/lib/constants";
import type { BpmBandKey, SongTheme } from "@/lib/constants";
import type { AlbumFilter, AlbumOption } from "@/lib/search/album-filter";
import { useLocale } from "@/hooks/useLocale";

type SearchMode = "keyword" | "describe";

interface BrowseSheetProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  onAddSong: (song: SongCardData) => Promise<void>;
  existingSongIds?: string[];
  itemCount?: number;
  className?: string;
}

interface SearchResult {
  songs: SongCardData[];
  total: number;
}

function normalizeAlbumOptions(value: unknown): AlbumOption[] {
  if (!Array.isArray(value)) return [];

  return value.flatMap((album) => {
    if (typeof album === "string") {
      const albumName = album.trim();
      return albumName ? [{ albumName, albumSeries: null, songCount: 0 }] : [];
    }
    if (album && typeof album === "object" && "albumName" in album) {
      const option = album as Partial<AlbumOption>;
      const albumName = typeof option.albumName === "string" ? option.albumName.trim() : "";
      if (!albumName) return [];
      return [{
        albumName,
        albumSeries: typeof option.albumSeries === "string" && option.albumSeries.trim()
          ? option.albumSeries.trim()
          : null,
        songCount: typeof option.songCount === "number" ? option.songCount : 0,
      }];
    }
    return [];
  });
}

export function BrowseSheet({
  isOpen,
  onOpenChange,
  onAddSong,
  existingSongIds = [],
  itemCount = 0,
  className,
}: BrowseSheetProps) {
  const { t } = useLocale();
  const [mode, setMode] = useState<SearchMode>("keyword");
  const [keywordQuery, setKeywordQuery] = useState("");
  const [selectedAlbums, setSelectedAlbums] = useState<AlbumFilter[]>([]);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [selectedBpm, setSelectedBpm] = useState<BpmBandKey[]>([]);
  const [selectedThemes, setSelectedThemes] = useState<SongTheme[]>([]);
  const [activeFilters, setActiveFilters] = useState<StructuredSearchCriteria | undefined>();
  const [results, setResults] = useState<SongCardData[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [hasKeywordSearched, setHasKeywordSearched] = useState(false);
  const [albums, setAlbums] = useState<AlbumOption[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingAlbums, setIsLoadingAlbums] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [addingSongIds, setAddingSongIds] = useState<Set<string>>(new Set());
  const [addedSongIds, setAddedSongIds] = useState<Set<string>>(new Set());
  const { favoriteIds, setFavoriteIds, toggleFavorite } = useFavoriteToggle();
  const latestSearchIdRef = useRef(0);
  const { currentTrack } = useAudioPlayerContext();

  const resolveSong = useCallback(
    (songId: string) => {
      const song = results.find((r) => r.id === songId);
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
    [results, t]
  );

  const { playingSongId, previewLoadingSongId, handlePlay: handlePlaySong, reset: resetPlayback } =
    useSongPlayback({
      resolveSong,
      noAudioMessage: t("browse.noAudioAvailable"),
      failedToLoadMessage: t("browse.failedToLoadPreview"),
    });

  // Load albums function
  const loadAlbums = useCallback(async () => {
    setIsLoadingAlbums(true);
    try {
      const response = await fetch("/api/songs/albums");
      if (!response.ok) {
        throw new Error("Failed to load albums");
      }
      const data = await response.json();
      setAlbums(normalizeAlbumOptions(data.albums));
    } catch (err) {
      console.error("Error loading albums:", err);
    } finally {
      setIsLoadingAlbums(false);
    }
  }, []);

  // Search function
  const handleSearch = useCallback(
    async (
      searchQuery: string,
      albumFilters?: AlbumFilter[],
      advanced?: StructuredSearchCriteria
    ) => {
      const searchId = latestSearchIdRef.current + 1;
      latestSearchIdRef.current = searchId;
      const nextFilters: StructuredSearchCriteria = {
        query: searchQuery.trim() || undefined,
        albums: albumFilters && albumFilters.length > 0 ? albumFilters : undefined,
        keys: advanced?.keys,
        bpmRange: advanced?.bpmRange,
        themes: advanced?.themes,
      };
      setKeywordQuery(searchQuery);
      setActiveFilters(nextFilters);
      setHasKeywordSearched(true);
      setIsLoading(true);
      setError(null);

      try {
        const params = new URLSearchParams();
        if (searchQuery.trim()) {
          params.set("q", searchQuery.trim());
        }
        for (const album of albumFilters ?? []) {
          params.append("albumName", album.albumName);
          params.append("albumSeries", album.albumSeries ?? "");
        }
        if (nextFilters.keys?.length) {
          params.set("keys", nextFilters.keys.join(","));
        }
        if (nextFilters.bpmRange?.length) {
          for (const band of nextFilters.bpmRange) {
            params.append("bpmRange", band);
          }
        }
        if (nextFilters.themes?.length) {
          for (const theme of nextFilters.themes) {
            params.append("themes", theme);
          }
        }
        params.set("limit", "50");

        const url = searchQuery.trim()
          ? `/api/songs/search?${params.toString()}`
          : `/api/songs?${params.toString()}`;

        const response = await fetch(url);
        if (!response.ok) {
          throw new Error(t("browse.search.failed"));
        }

        const data: SearchResult = await response.json();
        if (searchId !== latestSearchIdRef.current) return;
        setResults(data.songs || []);
        setTotalCount(data.total ?? 0);
      } catch (err) {
        if (searchId !== latestSearchIdRef.current) return;
        setError(err instanceof Error ? err.message : t("browse.search.failed"));
        setResults([]);
        setTotalCount(0);
      } finally {
        if (searchId === latestSearchIdRef.current) {
          setIsLoading(false);
        }
      }
    },
    [t]
  );

  const handleAddSong = useCallback(
    async (songOrId: string | SongCardData) => {
      const songId = typeof songOrId === "string" ? songOrId : songOrId.id;
      if (addingSongIds.has(songId) || addedSongIds.has(songId)) return;

      const song = typeof songOrId === "string"
        ? results.find((result) => result.id === songId)
        : songOrId;
      if (!song) {
        toast.error(t("browse.songNotFound"));
        return;
      }

      setAddingSongIds((prev) => new Set(prev).add(songId));

      try {
        await onAddSong(song);
        setAddedSongIds((prev) => new Set(prev).add(songId));
        toast.success(t("browse.songAddedToSongset"));
      } catch (err) {
        toast.error(t("browse.failedToAddSong"));
        console.error("Error adding song:", err);
      } finally {
        setAddingSongIds((prev) => {
          const next = new Set(prev);
          next.delete(songId);
          return next;
        });
      }
    },
    [onAddSong, addingSongIds, addedSongIds, results, t]
  );

  const isSongAdded = useCallback(
    (songId: string) => {
      return existingSongIds.includes(songId) || addedSongIds.has(songId);
    },
    [existingSongIds, addedSongIds]
  );

  const isSongAdding = useCallback(
    (songId: string) => addingSongIds.has(songId),
    [addingSongIds]
  );

  const isSongsetFull = itemCount >= SONGSET_MAX_SONGS;
  // The server pins favorites first; split by membership to label the section.
  const favoriteResults = results.filter((song) => favoriteIds.has(song.id));
  const otherResults = results.filter((song) => !favoriteIds.has(song.id));

  const loadFavoriteIds = useCallback(async () => {
    try {
      const response = await fetch("/api/favorites");
      if (!response.ok) return;
      const data = await response.json();
      setFavoriteIds(new Set(Array.isArray(data.songIds) ? data.songIds : []));
    } catch (err) {
      console.error("Error loading favorites:", err);
    }
  }, [setFavoriteIds]);

  const handleSwitchToSearchTab = useCallback((searchQuery: string) => {
    setKeywordQuery(searchQuery);
    setMode("keyword");
  }, []);

  const handleKeywordSubmit = useCallback(() => {
    const normalizedAlbums = selectedAlbums.length > 0 ? selectedAlbums : undefined;
    const hasAdvancedFilters =
      selectedAlbums.length > 0 ||
      selectedKeys.length > 0 ||
      selectedBpm.length > 0 ||
      selectedThemes.length > 0;

    handleSearch(
      keywordQuery,
      normalizedAlbums,
      hasAdvancedFilters
        ? {
            query: keywordQuery.trim() || undefined,
            keys: selectedKeys.length > 0 ? selectedKeys : undefined,
            bpmRange: selectedBpm.length > 0 ? selectedBpm : undefined,
            themes: selectedThemes.length > 0 ? selectedThemes : undefined,
            albums: normalizedAlbums,
          }
        : undefined
    );
  }, [handleSearch, keywordQuery, selectedAlbums, selectedKeys, selectedBpm, selectedThemes]);

  const sharedFilters = (
    <SharedFilters
      albums={albums}
      selectedAlbums={selectedAlbums}
      onSelectedAlbumsChange={setSelectedAlbums}
      selectedKeys={selectedKeys}
      onSelectedKeysChange={setSelectedKeys}
      selectedBpm={selectedBpm}
      onSelectedBpmChange={setSelectedBpm}
      selectedThemes={selectedThemes}
      onSelectedThemesChange={setSelectedThemes}
      onClearFilters={() => {
        setSelectedAlbums([]);
        setSelectedKeys([]);
        setSelectedBpm([]);
        setSelectedThemes([]);
      }}
      isLoading={isLoading || isLoadingAlbums}
      className="px-1"
    />
  );
  const sharedSearchButtonClassName = "h-8 w-[92px] gap-1.5 text-sm";

  const {
    controls: semanticControls,
    resultsContent: semanticResultsContent,
    search: handleSemanticSubmit,
    isLoading: isSemanticLoading,
    reset: resetSemanticSearch,
  } = useSemanticSearch({
    onAddSong: handleAddSong,
    existingSongIds,
    addingSongIds,
    addedSongIds,
    onSwitchToSearchTab: handleSwitchToSearchTab,
    albums: selectedAlbums,
    keys: selectedKeys,
    bpmRange: selectedBpm,
    themes: selectedThemes,
    showSearchButton: false,
  });

  // Handle sheet open/close
  useEffect(() => {
    if (isOpen) {
      const cleanups: Array<() => void> = [];
      if (albums.length === 0) {
        const albumTimeoutId = setTimeout(() => {
          loadAlbums();
        }, 0);
        cleanups.push(() => clearTimeout(albumTimeoutId));
      }
      const favoritesTimeoutId = setTimeout(() => {
        loadFavoriteIds();
      }, 0);
      cleanups.push(() => clearTimeout(favoritesTimeoutId));
      return () => cleanups.forEach((cleanup) => cleanup());
    } else {
      const timeoutId = setTimeout(() => {
        setKeywordQuery("");
        setSelectedAlbums([]);
        setSelectedKeys([]);
        setSelectedBpm([]);
        setSelectedThemes([]);
        setActiveFilters(undefined);
        setResults([]);
        setTotalCount(0);
        setHasKeywordSearched(false);
        setError(null);
        setAddingSongIds(new Set());
        setAddedSongIds(new Set());
        setMode("keyword");
        resetPlayback();
        resetSemanticSearch();
      }, 300);
      return () => clearTimeout(timeoutId);
    }
  }, [isOpen, albums.length, loadAlbums, resetSemanticSearch, loadFavoriteIds, resetPlayback]);

  const renderSongCard = (song: SongCardData) => (
    <SongCard
      key={song.id}
      song={song}
      onAdd={handleAddSong}
      onPlay={handlePlaySong}
      onToggleFavorite={(songId) => void toggleFavorite(songId)}
      isFavorite={favoriteIds.has(song.id)}
      isAdded={isSongAdded(song.id)}
      isAdding={isSongAdding(song.id)}
      disabled={isSongsetFull}
      isPlaying={playingSongId === song.id}
      isPreviewLoading={previewLoadingSongId === song.id}
    />
  );

  return (
    <Sheet open={isOpen} onOpenChange={onOpenChange}>
      <SheetContent side="bottom" className={cn("data-[side=bottom]:!h-[85vh] sm:data-[side=bottom]:!h-[90vh] overflow-hidden", className)}>
        <SheetHeader className="pb-2">
          <SheetTitle>{t("browse.sheet.title")}</SheetTitle>
          <SheetDescription>{t("browse.sheet.description")}</SheetDescription>
        </SheetHeader>

        <div className={cn("flex flex-col h-full min-h-0", currentTrack ? "pb-28 sm:pb-20" : "pb-8")}>
          {/* Mode tabs */}
          <div
            className="mb-4 flex w-fit gap-1 rounded-lg border bg-muted/50 p-1"
            role="tablist"
            aria-label={t("browse.sheet.modeAriaLabel")}
          >
            <Button
              role="tab"
              aria-selected={mode === "keyword"}
              variant="ghost"
              size="sm"
              onClick={() => setMode("keyword")}
              className={cn(
                "gap-1.5 text-muted-foreground hover:text-foreground",
                mode === "keyword" &&
                  "bg-sky-100 text-sky-950 shadow-sm hover:bg-sky-100 hover:text-sky-950 dark:bg-sky-950/60 dark:text-sky-100 dark:hover:bg-sky-950/60"
              )}
              data-testid="keyword-mode-tab"
            >
              <Search className="size-3.5" />
              {t("browse.sheet.keywordTab")}
            </Button>
            <Button
              role="tab"
              aria-selected={mode === "describe"}
              variant="ghost"
              size="sm"
              onClick={() => setMode("describe")}
              className={cn(
                "gap-1.5 text-muted-foreground hover:text-foreground",
                mode === "describe" &&
                  "bg-amber-100 text-amber-950 shadow-sm hover:bg-amber-100 hover:text-amber-950 dark:bg-amber-950/60 dark:text-amber-100 dark:hover:bg-amber-950/60"
              )}
              data-testid="describe-mode-tab"
            >
              <Sparkles className="size-3.5" />
              {t("browse.sheet.describeTab")}
            </Button>
          </div>

          <div
            className="shrink-0 pb-4"
            data-testid="search-controls-region"
          >
            {mode === "keyword" ? (
              <div role="tabpanel" aria-label={t("browse.sheet.keywordControls")} className="px-1">
                <SongSearch
                  onSearch={handleSearch}
                  onAdvancedSearch={(criteria) =>
                    handleSearch(criteria.query ?? "", criteria.albums, criteria)
                  }
                  isLoading={isLoading || isLoadingAlbums}
                  query={keywordQuery}
                  onQueryChange={setKeywordQuery}
                  selectedAlbums={selectedAlbums}
                  selectedKeys={selectedKeys}
                  selectedBpm={selectedBpm}
                  selectedThemes={selectedThemes}
                  showSearchButton={false}
                />
              </div>
            ) : (
              <div role="tabpanel" aria-label={t("browse.sheet.describeControls")} className="px-1">
                {semanticControls}
              </div>
            )}
          </div>

          <div className="shrink-0 pb-3" data-testid="filters-region">
            {sharedFilters}
          </div>

          <div className="flex shrink-0 justify-end px-1 pb-4" data-testid="search-action-row">
            {mode === "keyword" ? (
              <Button
                type="button"
                onClick={handleKeywordSubmit}
                disabled={isLoading || isLoadingAlbums}
                className={sharedSearchButtonClassName}
                data-testid="search-button"
                aria-label={isLoading ? t("browse.search.searchingSongs") : t("browse.search.runSearch")}
              >
                {isLoading || isLoadingAlbums ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Search className="size-4" />
                )}
                {t("browse.search.searchButton")}
              </Button>
            ) : (
              <Button
                type="button"
                onClick={handleSemanticSubmit}
                disabled={isSemanticLoading}
                className={sharedSearchButtonClassName}
                data-testid="semantic-search-button"
                aria-label={isSemanticLoading ? t("browse.search.searching") : t("browse.search.searchByDescription")}
              >
                {isSemanticLoading ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <Sparkles className="size-4" />
                )}
                {t("browse.search.searchButton")}
              </Button>
            )}
          </div>

          <div
            className="flex-1 overflow-y-auto px-1 -mx-1"
            data-testid="search-results-region"
            role="region"
            aria-label={mode === "keyword" ? t("browse.sheet.keywordResults") : t("browse.sheet.describeResults")}
          >
            {mode === "keyword" ? (
              <>
                {error && (
                  <div className="flex flex-col items-center justify-center py-8 text-center">
                    <AlertCircle className="size-8 text-destructive mb-2" />
                    <p className="text-destructive text-sm">{error}</p>
                    <Button
                      variant="outline"
                      size="sm"
                      className="mt-4"
                      onClick={() => handleSearch(keywordQuery, selectedAlbums, activeFilters)}
                    >
                      {t("browse.retry")}
                    </Button>
                  </div>
                )}

                {!error && isLoading && results.length === 0 && (
                  <div className="flex flex-col items-center justify-center py-12" role="status" aria-live="polite">
                    <Loader2 className="size-8 animate-spin text-muted-foreground mb-2" aria-hidden="true" />
                    <p className="text-muted-foreground text-sm">{t("browse.search.searchingStatus")}</p>
                  </div>
                )}

                {!error && !isLoading && hasKeywordSearched && results.length === 0 && keywordQuery && (
                  <div className="flex flex-col items-center justify-center py-12 text-center">
                    <Music className="size-8 text-muted-foreground mb-2" />
                    <p className="text-muted-foreground">
                      {`${t("browse.empty.noSongsFoundPrefix")}${keywordQuery}${t("browse.empty.noSongsFoundSuffix")}`}
                    </p>
                    <p className="text-sm text-muted-foreground mt-1">
                      {activeFilters?.keys?.length || activeFilters?.bpmRange || activeFilters?.themes?.length
                        ? t("browse.empty.tryAdjustFilters")
                        : t("browse.empty.tryDifferentTerm")}
                    </p>
                  </div>
                )}

                {!error && !isLoading && hasKeywordSearched && results.length === 0 && !keywordQuery && (activeFilters?.albums?.length || activeFilters?.keys?.length || activeFilters?.bpmRange || activeFilters?.themes?.length) && (
                  <div className="flex flex-col items-center justify-center py-12 text-center">
                    <Music className="size-8 text-muted-foreground mb-2" />
                    <p className="text-muted-foreground">{t("browse.empty.noMatchFilters")}</p>
                    <p className="text-sm text-muted-foreground mt-1">
                      {t("browse.empty.tryRemoveFilters")}
                    </p>
                  </div>
                )}

                {!error && !isLoading && hasKeywordSearched && results.length === 0 && !keywordQuery && (
                  <div className="flex flex-col items-center justify-center py-12 text-center">
                    <Music className="size-8 text-muted-foreground mb-2" />
                    <p className="text-muted-foreground">{t("browse.empty.noSongsAvailable")}</p>
                    <p className="text-sm text-muted-foreground mt-1">
                      {t("browse.empty.startTyping")}
                    </p>
                  </div>
                )}

                {!error && results.length > 0 && (
                  <>
                    {favoriteResults.length > 0 && (
                      <div data-testid="favorites-section">
                        <h3 className="px-1 pb-2 pt-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                          {t("browse.favorites")}
                        </h3>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 pb-4">
                          {favoriteResults.map(renderSongCard)}
                        </div>
                      </div>
                    )}
                    {otherResults.length > 0 && (
                      <div data-testid="all-songs-section">
                        {favoriteResults.length > 0 && (
                          <h3 className="px-1 pb-2 pt-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                            {t("browse.allSongs")}
                          </h3>
                        )}
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-2 pb-4">
                          {otherResults.map(renderSongCard)}
                        </div>
                      </div>
                    )}
                  </>
                )}
              </>
            ) : (
              semanticResultsContent
            )}
          </div>

          {/* Footer */}
          <div className="pt-4 border-t mt-4">
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">
                {isSongsetFull
                  ? t("browse.songsetFull")
                  : mode === "keyword" && totalCount > 0
                    ? `${totalCount} ${t("browse.songsUnit")}`
                    : ""}
              </p>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                {t("browse.done")}
              </Button>
            </div>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
