"use client";

import { useState, useCallback, useRef } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { SongCard, SongCardData } from "@/components/songset/SongCard";
import { Loader2, Sparkles, Music, ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { useSongPlayback } from "@/hooks/useSongPlayback";
import { toast } from "sonner";
import type { StructuredSearchCriteria } from "@/components/songset/search/types";
import type { BpmBandKey } from "@/lib/constants";
import type { AlbumFilter } from "@/lib/search/album-filter";
import { useLocale } from "@/hooks/useLocale";

interface SemanticSearchResult extends SongCardData {
  similarity?: number;
  matchingSnippet?: string | null;
  whyThisMatch?: string[];
}

type ResultMode = "semantic" | "browse";

interface SemanticSearchProps {
  onAddSong: (song: SongCardData) => Promise<void>;
  existingSongIds?: string[];
  addingSongIds?: Set<string>;
  addedSongIds?: Set<string>;
  onSwitchToSearchTab?: (query: string) => void;
  albums?: AlbumFilter[];
  keys?: string[];
  bpmRange?: StructuredSearchCriteria["bpmRange"];
  searchButtonClassName?: string;
  showSearchButton?: boolean;
  className?: string;
}

interface UseSemanticSearchOptions {
  onAddSong: (song: SongCardData) => Promise<void>;
  existingSongIds?: string[];
  addingSongIds?: Set<string>;
  addedSongIds?: Set<string>;
  onSwitchToSearchTab?: (query: string) => void;
  albums?: AlbumFilter[];
  keys?: string[];
  bpmRange?: StructuredSearchCriteria["bpmRange"];
  searchButtonClassName?: string;
  showSearchButton?: boolean;
}

export function useSemanticSearch({
  onAddSong,
  existingSongIds = [],
  addingSongIds = new Set(),
  addedSongIds = new Set(),
  onSwitchToSearchTab,
  albums = [],
  keys = [],
  bpmRange,
  searchButtonClassName,
  showSearchButton = true,
}: UseSemanticSearchOptions) {
  const { t } = useLocale();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SemanticSearchResult[]>([]);
  const [resultMode, setResultMode] = useState<ResultMode | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasSearched, setHasSearched] = useState(false);
  const [expandedSongId, setExpandedSongId] = useState<string | null>(null);
  const latestSearchIdRef = useRef(0);

  const resolveSong = useCallback(
    (songId: string) => {
      const song = results.find((r) => r.id === songId);
      if (!song) return null;
      const recording = song.recordings[0];
      return {
        id: song.id,
        title: song.title,
        artist: song.composer || song.lyricist || t("audio.search.unknownArtist"),
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

  const {
    playingSongId,
    previewLoadingSongId,
    handlePlay: handlePlaySong,
    reset: resetPlayback,
  } = useSongPlayback({
    resolveSong,
    noAudioMessage: t("audio.search.noAudioForSong"),
    failedToLoadMessage: t("audio.search.failedLoadPreview"),
  });

  const reset = useCallback(() => {
    setQuery("");
    setResults([]);
    setResultMode(null);
    setIsLoading(false);
    setError(null);
    setHasSearched(false);
    setExpandedSongId(null);
    resetPlayback();
    latestSearchIdRef.current += 1;
  }, [resetPlayback]);

  const handleSearch = useCallback(async () => {
    const trimmed = query.trim();

    const searchId = latestSearchIdRef.current + 1;
    latestSearchIdRef.current = searchId;
    setIsLoading(true);
    setError(null);
    setHasSearched(true);
    setExpandedSongId(null);

    try {
      let response: Response;
      let nextResultMode: ResultMode;

      if (trimmed) {
        nextResultMode = "semantic";
        const body: {
          query: string;
          limit: number;
          albums?: AlbumFilter[];
          keys?: string[];
          bpmRange?: BpmBandKey[];
        } = { query: trimmed, limit: 20 };
        if (albums.length > 0) body.albums = albums;
        if (keys.length > 0) body.keys = keys;
        if (bpmRange && bpmRange.length > 0) body.bpmRange = bpmRange;

        response = await fetch("/api/songs/search/semantic", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
      } else {
        nextResultMode = "browse";
        const params = new URLSearchParams();
        for (const album of albums) {
          params.append("albumName", album.albumName);
          params.append("albumSeries", album.albumSeries ?? "");
        }
        if (keys.length > 0) {
          params.set("keys", keys.join(","));
        }
        if (bpmRange && bpmRange.length > 0) {
          for (const band of bpmRange) {
            params.append("bpmRange", band);
          }
        }
        params.set("limit", "50");

        response = await fetch(`/api/songs?${params.toString()}`);
      }

      if (response.status === 503) {
        const data = await response.json().catch(() => ({}));
        const errorMsg = (data as { error?: string }).error ?? t("audio.search.semanticUnavailable");
        if (onSwitchToSearchTab) {
          toast.info(t("audio.search.semanticUnavailableSwitch"));
          onSwitchToSearchTab(trimmed);
          return;
        }
        throw new Error(errorMsg);
      }

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error((data as { error?: string }).error ?? t("audio.search.searchFailed"));
      }

      const data = await response.json();
      if (searchId !== latestSearchIdRef.current) return;
      setResults((data.songs ?? []) as SemanticSearchResult[]);
      setResultMode(nextResultMode);
    } catch (err) {
      if (searchId !== latestSearchIdRef.current) return;
      setError(err instanceof Error ? err.message : t("audio.search.searchFailed"));
      setResults([]);
      setResultMode(null);
    } finally {
      if (searchId === latestSearchIdRef.current) {
        setIsLoading(false);
      }
    }
  }, [query, albums, keys, bpmRange, onSwitchToSearchTab, t]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter") {
        e.preventDefault();
        handleSearch();
      }
    },
    [handleSearch]
  );

  const isSongAdded = useCallback(
    (songId: string) => existingSongIds.includes(songId) || addedSongIds.has(songId),
    [existingSongIds, addedSongIds]
  );

  const isSongAdding = useCallback(
    (songId: string) => addingSongIds.has(songId),
    [addingSongIds]
  );

  const formatSimilarity = (score: number) =>
    `${Math.round(score * 100)}% ${t("audio.search.matchSuffix")}`;

  const toggleExpand = (songId: string) => {
    setExpandedSongId(expandedSongId === songId ? null : songId);
  };

  const controls = (
      <div className="space-y-2">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={t("audio.search.placeholder")}
          aria-label={t("audio.search.ariaLabel")}
          data-testid="semantic-search-input"
        />
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground" aria-hidden="true" data-testid="describe-help-text">
            {t("audio.search.helpTip")}
          </p>
          {showSearchButton && (
            <Button
              onClick={handleSearch}
              disabled={isLoading}
              className={cn("gap-1.5", searchButtonClassName)}
              data-testid="semantic-search-button"
              aria-label={isLoading ? t("audio.search.searching") : t("audio.search.searchSongsByDescription")}
            >
              {isLoading ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Sparkles className="size-4" />
              )}
              {t("audio.search.searchButton")}
            </Button>
          )}
        </div>
      </div>
  );

  const resultsContent = (
    <>
      {error && (
        <div
          role="alert"
          className="text-sm text-destructive p-3 rounded-md bg-destructive/10"
          data-testid="semantic-search-error"
        >
          {error}
        </div>
      )}

      {!error && isLoading && (
        <div className="flex flex-col items-center justify-center py-8 text-center">
          <Loader2 className="size-8 animate-spin text-muted-foreground mb-2" />
          <p className="text-sm text-muted-foreground">
            {query.trim() ? t("audio.search.searchingByMeaning") : t("audio.search.loadingSongs")}
          </p>
        </div>
      )}

      {!error && !isLoading && hasSearched && results.length === 0 && (
        <div className="flex flex-col items-center justify-center py-8 text-center">
          <Music className="size-8 text-muted-foreground mb-2" />
          <p className="text-muted-foreground text-sm">
            {resultMode === "browse" ? t("audio.search.noSongsMatchFilters") : t("audio.search.noMatchingSongs")}
          </p>
          <p className="text-xs text-muted-foreground mt-1">
            {resultMode === "browse"
              ? t("audio.search.tryRemovingFilters")
              : t("audio.search.tryDifferentDescription")}
          </p>
        </div>
      )}

      {!error && !isLoading && results.length > 0 && (
        <div className="space-y-2" data-testid="semantic-search-results" aria-live="polite" aria-atomic="true">
          <p className="text-xs text-muted-foreground" role="status">{results.length} {t("audio.search.songsFoundLabel")}</p>
          {results.map((song) => (
            <div key={song.id} className="relative">
              <SongCard
                song={song}
                onAdd={() => onAddSong(song)}
                onPlay={handlePlaySong}
                isAdded={isSongAdded(song.id)}
                isAdding={isSongAdding(song.id)}
                isPlaying={playingSongId === song.id}
                isPreviewLoading={previewLoadingSongId === song.id}
              />
              {resultMode === "semantic" && typeof song.similarity === "number" && (
                <Badge
                  variant="secondary"
                  className="absolute top-2 right-10 text-xs"
                  data-testid="similarity-badge"
                >
                  {formatSimilarity(song.similarity)}
                </Badge>
              )}
              {song.matchingSnippet && (
                <p
                  className="text-xs italic text-muted-foreground pl-3 -mt-1"
                  data-testid="matching-snippet"
                >
                  ▸ {song.matchingSnippet}
                </p>
              )}
              {(song.whyThisMatch?.length ?? 0) > 0 && (
                <button
                  className="flex items-center gap-1 text-xs text-muted-foreground pl-3 py-1 hover:text-foreground transition-colors"
                  onClick={() => toggleExpand(song.id)}
                  data-testid="why-this-match-toggle"
                  aria-expanded={expandedSongId === song.id}
                  aria-label={t("audio.search.whyThisMatch")}
                >
                  {expandedSongId === song.id ? (
                    <ChevronDown className="size-3" />
                  ) : (
                    <ChevronRight className="size-3" />
                  )}
                  {t("audio.search.whyThisMatch")}
                </button>
              )}
              {expandedSongId === song.id && (song.whyThisMatch?.length ?? 0) > 0 && (
                <div className="pl-6 space-y-0.5" data-testid="why-this-match-content">
                  {song.whyThisMatch?.map((line, i) => (
                    <p key={i} className="text-xs text-muted-foreground">
                      {t("audio.search.lyric")} {i + 1}: {line}
                    </p>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </>
  );

  return {
    controls,
    resultsContent,
    search: handleSearch,
    isLoading,
    reset,
  };
}

export function SemanticSearch({
  className,
  ...props
}: SemanticSearchProps) {
  const { controls, resultsContent } = useSemanticSearch(props);

  return (
    <div className={cn("flex flex-col gap-4", className)} data-testid="semantic-search">
      {controls}
      {resultsContent}
    </div>
  );
}
