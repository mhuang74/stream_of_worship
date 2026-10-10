"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { SongSearch } from "@/components/songset/SongSearch";
import { SharedFilters } from "@/components/songset/SharedFilters";
import { SongCard, SongCardData } from "@/components/songset/SongCard";
import { useSemanticSearch } from "@/components/search/SemanticSearch";
import type { StructuredSearchCriteria } from "@/components/songset/search/types";
import {
  Loader2,
  Music,
  AlertCircle,
  Search,
  Sparkles,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { useFavoriteToggle } from "@/hooks/useFavoriteToggle";
import { useSongPlayback } from "@/hooks/useSongPlayback";
import type { BpmBandKey, SongTheme } from "@/lib/constants";
import type { AlbumFilter, AlbumOption } from "@/lib/search/album-filter";
import { useLocale } from "@/hooks/useLocale";

type SearchMode = "keyword" | "describe";

const SEARCH_PAGE_SIZE = 20;

export type CatalogSearchMode = SearchMode;

interface SearchResult {
  songs: SongCardData[];
  total: number;
}

export function normalizeAlbumOptions(value: unknown): AlbumOption[] {
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

/**
 * Shared catalog search surface (issue #253): keyword/browse + Describe modes
 * with album/key/BPM/theme filters and the favorites-pinned result layout.
 * Consumed by the pull-up catalog (BrowseSheet, with add-to-songset card
 * actions) and the /listen page (play+favorite only) so both stay identical
 * by construction.
 */
export interface CatalogSearchProps {
  /** Human-readable suffix for the mode-tab aria label in tests/automation. */
  controlsAriaRegion?: string;
  /** Listen mode: hide the add-to-songset button and omit it from props. */
  mode: "browse" | "listen";
  /** browse mode — add flow (mirrors former BrowseSheet behavior). */
  onAddSong?: (song: SongCardData) => Promise<void>;
  existingSongIds?: string[];
  /** browse mode — songset capacity gating for the add button. */
  isSongsetFull?: boolean;
  /** browse mode — toast chrome for the add flow. */
  songNotFoundMessage?: string;
  songAddedMessage?: string;
  failedToAddMessage?: string;
  /** listen mode — playback messages. */
  unknownArtistMessage: string;
  noAudioMessage: string;
  failedToLoadMessage: string;
  /** Results region classNames when embedded in a scrolling page section. */
  resultsClassName?: string;
  /** Extra chrome below the search-action row (e.g. nothing in BrowseSheet). */
  className?: string;
  SharedFiltersClassName?: string;
  SongSearchClassName?: string;
}

export function CatalogSearch({
  mode,
  onAddSong,
  existingSongIds = [],
  isSongsetFull = false,
  songNotFoundMessage,
  songAddedMessage,
  failedToAddMessage,
  unknownArtistMessage,
  noAudioMessage,
  failedToLoadMessage,
  resultsClassName,
  className,
  SharedFiltersClassName,
  SongSearchClassName,
}: CatalogSearchProps) {
  const { t } = useLocale();
  const [catalogMode, setCatalogMode] = useState<SearchMode>("keyword");
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

  const hasSearchCriteria =
    keywordQuery.trim().length > 0 ||
    selectedAlbums.length > 0 ||
    selectedKeys.length > 0 ||
    selectedBpm.length > 0 ||
    selectedThemes.length > 0;

  const { favoriteIds, setFavoriteIds, toggleFavorite } = useFavoriteToggle();
  const latestSearchIdRef = useRef(0);

  const resolveSong = useCallback(
    (songId: string) => {
      const song = results.find((r) => r.id === songId);
      if (!song) return null;
      const recording = song.recordings[0];
      return {
        id: song.id,
        title: song.title,
        artist: song.composer || song.lyricist || unknownArtistMessage,
        recording: recording
          ? {
              hashPrefix: recording.hashPrefix,
              contentHash: recording.contentHash,
              durationSeconds: recording.durationSeconds,
            }
          : null,
      };
    },
    [results, unknownArtistMessage]
  );

  const { playingSongId, previewLoadingSongId, handlePlay: handlePlaySong, reset: resetPlayback } =
    useSongPlayback({
      resolveSong,
      noAudioMessage,
      failedToLoadMessage,
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
      advanced?: StructuredSearchCriteria,
      offset: number = 0,
      append: boolean = false
    ) => {
      const nextFilters: StructuredSearchCriteria = {
        query: searchQuery.trim() || undefined,
        albums: albumFilters && albumFilters.length > 0 ? albumFilters : undefined,
        keys: advanced?.keys,
        bpmRange: advanced?.bpmRange,
        themes: advanced?.themes,
      };
      const hasCriteria =
        !!nextFilters.query ||
        (nextFilters.albums?.length ?? 0) > 0 ||
        (nextFilters.keys?.length ?? 0) > 0 ||
        (nextFilters.bpmRange?.length ?? 0) > 0 ||
        (nextFilters.themes?.length ?? 0) > 0;
      if (!hasCriteria) return;

      const searchId = latestSearchIdRef.current + 1;
      latestSearchIdRef.current = searchId;
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
        params.set("limit", String(SEARCH_PAGE_SIZE));
        params.set("offset", String(offset));

        const url = searchQuery.trim()
          ? `/api/songs/search?${params.toString()}`
          : `/api/songs?${params.toString()}`;

        const response = await fetch(url);
        if (!response.ok) {
          throw new Error(t("browse.search.failed"));
        }

        const data: SearchResult = await response.json();
        if (searchId !== latestSearchIdRef.current) return;
        const fetched = data.songs || [];
        if (append) {
          setResults((prev) => [...prev, ...fetched.filter((s) => !prev.some((p) => p.id === s.id))]);
        } else {
          setResults(fetched);
        }
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

  const buildCriteria = useCallback(
    (): [string, AlbumFilter[] | undefined, StructuredSearchCriteria | undefined] => {
      const normalizedAlbums = selectedAlbums.length > 0 ? selectedAlbums : undefined;
      const hasAdvancedFilters =
        selectedAlbums.length > 0 ||
        selectedKeys.length > 0 ||
        selectedBpm.length > 0 ||
        selectedThemes.length > 0;
      return [
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
          : undefined,
      ];
    },
    [keywordQuery, selectedAlbums, selectedKeys, selectedBpm, selectedThemes]
  );

  const handleKeywordSubmit = useCallback(() => {
    const [q, albums, advanced] = buildCriteria();
    handleSearch(q, albums, advanced);
  }, [handleSearch, buildCriteria]);

  const handleLoadMore = useCallback(() => {
    if (isLoading) return;
    const [q, albums, advanced] = buildCriteria();
    void handleSearch(q, albums, advanced, results.length, true);
  }, [handleSearch, buildCriteria, isLoading, results.length]);

  const handleAddSong = useCallback(
    async (songOrId: string | SongCardData) => {
      const songId = typeof songOrId === "string" ? songOrId : songOrId.id;
      if (addingSongIds.has(songId) || addedSongIds.has(songId)) return;

      const song = typeof songOrId === "string"
        ? results.find((result) => result.id === songId)
        : songOrId;
      if (!song) {
        if (songNotFoundMessage) toast.error(songNotFoundMessage);
        return;
      }

      setAddingSongIds((prev) => new Set(prev).add(songId));

      try {
        await onAddSong!(song);
        setAddedSongIds((prev) => new Set(prev).add(songId));
        if (songAddedMessage) toast.success(songAddedMessage);
      } catch (err) {
        if (failedToAddMessage) toast.error(failedToAddMessage);
        console.error("Error adding song:", err);
      } finally {
        setAddingSongIds((prev) => {
          const next = new Set(prev);
          next.delete(songId);
          return next;
        });
      }
    },
    [onAddSong, addingSongIds, addedSongIds, results, songNotFoundMessage, songAddedMessage, failedToAddMessage]
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
    setCatalogMode("keyword");
  }, []);

  const {
    controls: semanticControls,
    resultsContent: semanticResultsContent,
    search: handleSemanticSubmit,
    isLoading: isSemanticLoading,
    hasCriteria: hasSemanticCriteria,
    reset: resetSemanticSearch,
  } = useSemanticSearch({
    onAddSong: mode === "browse" ? handleAddSong : async () => {},
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

  // Load albums + favorite ids on mount (action depends on mode).
  useEffect(() => {
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
    return () => {
      cleanups.forEach((cleanup) => cleanup());
      // Unmount (BrowseSheet close unmounts CatalogSearch after its 300ms
      // delay) clears playback highlight and semantic-search state, matching
      // the former BrowseSheet close-reset behavior.
      resetPlayback();
      resetSemanticSearch();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadAlbums, loadFavoriteIds, resetPlayback, resetSemanticSearch]);

  const renderSongCardLocal = useCallback(
    (song: SongCardData) => {
      return (
        <SongCard
          key={song.id}
          song={song}
          onAdd={mode === "browse" && onAddSong ? handleAddSong : undefined}
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
    },
    [mode, onAddSong, handleAddSong, handlePlaySong, isSongAdded, isSongAdding, isSongsetFull, favoriteIds, toggleFavorite, playingSongId, previewLoadingSongId]
  );

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
      className={SharedFiltersClassName ?? "px-1"}
    />
  );
  const sharedSearchButtonClassName = "h-8 w-[92px] gap-1.5 text-sm";

  const renderFavoriteSection = (cards: SongCardData[]) => (
    <div data-testid="favorites-section">
      <h3 className="px-1 pb-2 pt-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {t("browse.favorites")}
      </h3>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2 pb-4">
        {cards.map(renderSongCardLocal)}
      </div>
    </div>
  );

  const renderAllSongsSection = (cards: SongCardData[], withHeading: boolean) => (
    <div data-testid="all-songs-section">
      {withHeading && (
        <h3 className="px-1 pb-2 pt-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {t("browse.allSongs")}
        </h3>
      )}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-2 pb-4">
        {cards.map(renderSongCardLocal)}
      </div>
    </div>
  );

  const keywordResultsContent = (
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
            disabled={!hasSearchCriteria}
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
          {favoriteResults.length > 0 && renderFavoriteSection(favoriteResults)}
          {otherResults.length > 0 &&
            renderAllSongsSection(otherResults, favoriteResults.length > 0)}
          {results.length < totalCount && (
            <div className="flex justify-center pb-4">
              <Button variant="outline" onClick={handleLoadMore} disabled={isLoading} data-testid="search-load-more">
                {isLoading ? (
                  <Loader2 className="size-4 mr-2 animate-spin" />
                ) : (
                  t("browse.search.loadMore")
                )}
              </Button>
            </div>
          )}
        </>
      )}
    </>
  );

  return (
    <div className={cn("flex flex-col h-full min-h-0", className)}>
      {/* Mode tabs */}
      <div
        className="mb-4 flex w-fit gap-1 rounded-lg border bg-muted/50 p-1"
        role="tablist"
        aria-label={t("browse.sheet.modeAriaLabel")}
      >
        <Button
          role="tab"
          aria-selected={catalogMode === "keyword"}
          variant="ghost"
          size="sm"
          onClick={() => setCatalogMode("keyword")}
          className={cn(
            "gap-1.5 text-muted-foreground hover:text-foreground",
            catalogMode === "keyword" &&
              "bg-sky-100 text-sky-950 shadow-sm hover:bg-sky-100 hover:text-sky-950 dark:bg-sky-950/60 dark:text-sky-100 dark:hover:bg-sky-950/60"
          )}
          data-testid="keyword-mode-tab"
        >
          <Search className="size-3.5" />
          {t("browse.sheet.keywordTab")}
        </Button>
        <Button
          role="tab"
          aria-selected={catalogMode === "describe"}
          variant="ghost"
          size="sm"
          onClick={() => setCatalogMode("describe")}
          className={cn(
            "gap-1.5 text-muted-foreground hover:text-foreground",
            catalogMode === "describe" &&
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
        {catalogMode === "keyword" ? (
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
              className={SongSearchClassName}
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

      <div className="flex shrink-0 justify-between items-center px-1 pb-4" data-testid="search-action-row">
        {mode === "browse" && catalogMode === "keyword" && totalCount > 0 ? (
          <p className="text-sm text-muted-foreground">{`${totalCount} ${t("browse.songsUnit")}`}</p>
        ) : !hasSearchCriteria ? (
          <p className="text-sm text-muted-foreground" data-testid="search-no-criteria-hint">
            {t("browse.search.noCriteriaHint")}
          </p>
        ) : (
          <span />
        )}
        {catalogMode === "keyword" ? (
          <Button
            type="button"
            onClick={handleKeywordSubmit}
            disabled={isLoading || isLoadingAlbums || !hasSearchCriteria}
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
            disabled={isSemanticLoading || !hasSemanticCriteria}
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
        className={cn("flex-1 overflow-y-auto px-1 -mx-1", resultsClassName)}
        data-testid="search-results-region"
        role="region"
        aria-label={catalogMode === "keyword" ? t("browse.sheet.keywordResults") : t("browse.sheet.describeResults")}
      >
        {catalogMode === "keyword" ? (
          keywordResultsContent
        ) : (
          semanticResultsContent
        )}
      </div>
    </div>
  );
}

