"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { SongCard, SongCardData } from "@/components/songset/SongCard";
import { Loader2, Sparkles, Music, ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAudioPlayerContext } from "@/contexts/AudioPlayerContext";
import { toast } from "sonner";
import { getPublicAudioUrl } from "@/lib/r2/public-url";
import type { StructuredSearchCriteria } from "@/components/songset/search/types";
import type { BpmBandKey } from "@/lib/constants";
import type { AlbumFilter } from "@/lib/search/album-filter";

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
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SemanticSearchResult[]>([]);
  const [resultMode, setResultMode] = useState<ResultMode | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasSearched, setHasSearched] = useState(false);
  const [playingSongId, setPlayingSongId] = useState<string | null>(null);
  const [previewLoadingSongId, setPreviewLoadingSongId] = useState<string | null>(null);
  const [expandedSongId, setExpandedSongId] = useState<string | null>(null);
  const latestSearchIdRef = useRef(0);
  const { play, currentTrack, state: playerState } = useAudioPlayerContext();

  const reset = useCallback(() => {
    setQuery("");
    setResults([]);
    setResultMode(null);
    setIsLoading(false);
    setError(null);
    setHasSearched(false);
    setPlayingSongId(null);
    setPreviewLoadingSongId(null);
    setExpandedSongId(null);
    latestSearchIdRef.current += 1;
  }, []);

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
        const errorMsg = (data as { error?: string }).error ?? "Semantic search unavailable";
        if (onSwitchToSearchTab) {
          toast.info("Semantic search unavailable, switching to text search");
          onSwitchToSearchTab(trimmed);
          return;
        }
        throw new Error(errorMsg);
      }

      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error((data as { error?: string }).error ?? "Search failed");
      }

      const data = await response.json();
      if (searchId !== latestSearchIdRef.current) return;
      setResults((data.songs ?? []) as SemanticSearchResult[]);
      setResultMode(nextResultMode);
    } catch (err) {
      if (searchId !== latestSearchIdRef.current) return;
      setError(err instanceof Error ? err.message : "Search failed");
      setResults([]);
      setResultMode(null);
    } finally {
      if (searchId === latestSearchIdRef.current) {
        setIsLoading(false);
      }
    }
  }, [query, albums, keys, bpmRange, onSwitchToSearchTab]);

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

  const handlePlaySong = useCallback(
    async (songId: string) => {
      const song = results.find((r) => r.id === songId);
      if (!song || song.recordings.length === 0) {
        toast.error("No audio available for this song");
        return;
      }

      if (playingSongId === songId && currentTrack?.id === `song-${songId}`) {
        if (playerState.isPlaying) {
          setPlayingSongId(null);
          return;
        }
      }

      const recording = song.recordings[0];
      const artist = song.composer || song.lyricist || "Unknown Artist";
      const publicUrl = getPublicAudioUrl(recording.hashPrefix);

      if (publicUrl) {
        play({
          id: `song-${songId}`,
          title: song.title,
          artist,
          src: publicUrl,
          type: "song",
          duration: recording.durationSeconds ?? undefined,
        });
        setPlayingSongId(songId);
        return;
      }

      setPreviewLoadingSongId(songId);

      try {
        const res = await fetch("/api/signed-url", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            hashPrefix: recording.hashPrefix,
            fileType: "audio",
          }),
        });

        if (!res.ok) {
          throw new Error("Failed to get audio URL");
        }

        const data = await res.json();

        play({
          id: `song-${songId}`,
          title: song.title,
          artist,
          src: data.url,
          type: "song",
          duration: recording.durationSeconds ?? undefined,
        });

        setPlayingSongId(songId);
      } catch {
        toast.error("Failed to load audio preview");
      } finally {
        setPreviewLoadingSongId(null);
      }
    },
    [results, playingSongId, currentTrack, playerState.isPlaying, play]
  );

  useEffect(() => {
    if (!currentTrack || !playerState.isPlaying) {
      const timeout = setTimeout(() => {
        if (!currentTrack || !playerState.isPlaying) {
          setPlayingSongId(null);
        }
      }, 200);
      return () => clearTimeout(timeout);
    }
  }, [currentTrack, playerState.isPlaying]);

  const formatSimilarity = (score: number) =>
    `${Math.round(score * 100)}% match`;

  const toggleExpand = (songId: string) => {
    setExpandedSongId(expandedSongId === songId ? null : songId);
  };

  const controls = (
      <div className="space-y-2">
        <Input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Describe songs by theme or feeling..."
          aria-label="Describe songs to search for"
          data-testid="semantic-search-input"
        />
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground" aria-hidden="true" data-testid="describe-help-text">
            Tip: describe by theme or feeling — e.g. &lsquo;關於神的恩典與憐憫的讚美&rsquo;, &lsquo;upbeat praise songs about grace&rsquo; · Press Enter to search
          </p>
          {showSearchButton && (
            <Button
              onClick={handleSearch}
              disabled={isLoading}
              className={cn("gap-1.5", searchButtonClassName)}
              data-testid="semantic-search-button"
              aria-label={isLoading ? "Searching..." : "Search songs by description"}
            >
              {isLoading ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Sparkles className="size-4" />
              )}
              Search
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
            {query.trim() ? "Searching by meaning..." : "Loading songs..."}
          </p>
        </div>
      )}

      {!error && !isLoading && hasSearched && results.length === 0 && (
        <div className="flex flex-col items-center justify-center py-8 text-center">
          <Music className="size-8 text-muted-foreground mb-2" />
          <p className="text-muted-foreground text-sm">
            {resultMode === "browse" ? "No songs match your filters" : "No matching songs found"}
          </p>
          <p className="text-xs text-muted-foreground mt-1">
            {resultMode === "browse"
              ? "Try removing some filters to see more results"
              : "Try a different description, or songs may not have embeddings yet"}
          </p>
        </div>
      )}

      {!error && !isLoading && results.length > 0 && (
        <div className="space-y-2" data-testid="semantic-search-results" aria-live="polite" aria-atomic="true">
          <p className="text-xs text-muted-foreground" role="status">{results.length} songs found</p>
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
                  aria-label="Why this match?"
                >
                  {expandedSongId === song.id ? (
                    <ChevronDown className="size-3" />
                  ) : (
                    <ChevronRight className="size-3" />
                  )}
                  Why this match?
                </button>
              )}
              {expandedSongId === song.id && (song.whyThisMatch?.length ?? 0) > 0 && (
                <div className="pl-6 space-y-0.5" data-testid="why-this-match-content">
                  {song.whyThisMatch?.map((line, i) => (
                    <p key={i} className="text-xs text-muted-foreground">
                      Lyric {i + 1}: {line}
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
