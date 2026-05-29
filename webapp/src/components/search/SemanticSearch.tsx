"use client";

import { useState, useCallback, useEffect } from "react";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { SongCard, SongCardData } from "@/components/songset/SongCard";
import { Loader2, Sparkles, Music, ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAudioPlayerContext } from "@/contexts/AudioPlayerContext";
import { toast } from "sonner";
import { getPublicAudioUrl } from "@/lib/r2/public-url";

interface SemanticSearchResult extends SongCardData {
  similarity: number;
  matchingSnippet: string | null;
  whyThisMatch: string[];
}

interface SemanticSearchProps {
  onAddSong: (songId: string) => Promise<void>;
  existingSongIds?: string[];
  addingSongIds?: Set<string>;
  addedSongIds?: Set<string>;
  onSwitchToSearchTab?: (query: string) => void;
  className?: string;
}

export function SemanticSearch({
  onAddSong,
  existingSongIds = [],
  addingSongIds = new Set(),
  addedSongIds = new Set(),
  onSwitchToSearchTab,
  className,
}: SemanticSearchProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SemanticSearchResult[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasSearched, setHasSearched] = useState(false);
  const [playingSongId, setPlayingSongId] = useState<string | null>(null);
  const [previewLoadingSongId, setPreviewLoadingSongId] = useState<string | null>(null);
  const [expandedSongId, setExpandedSongId] = useState<string | null>(null);
  const { play, currentTrack, state: playerState } = useAudioPlayerContext();

  const handleSearch = useCallback(async () => {
    const trimmed = query.trim();
    if (!trimmed) return;

    setIsLoading(true);
    setError(null);
    setHasSearched(true);
    setExpandedSongId(null);

    try {
      const response = await fetch("/api/songs/search/semantic", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: trimmed, limit: 20 }),
      });

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
      setResults((data.songs ?? []) as SemanticSearchResult[]);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Search failed");
      setResults([]);
    } finally {
      setIsLoading(false);
    }
  }, [query, onSwitchToSearchTab]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
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

  return (
    <div className={cn("flex flex-col gap-4", className)} data-testid="semantic-search">
      <div className="space-y-2">
        <Textarea
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Describe the songs you're looking for... (e.g. '关于神的恩典的赞美诗' or 'upbeat praise songs about grace')"
          className="min-h-[80px] resize-none"
          aria-label="Describe songs to search for"
          data-testid="semantic-search-input"
        />
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground" aria-hidden="true">Press Ctrl+Enter to search</p>
          <Button
            onClick={handleSearch}
            disabled={isLoading || !query.trim()}
            size="sm"
            data-testid="semantic-search-button"
            aria-label={isLoading ? "Searching..." : "Search songs by description"}
          >
            {isLoading ? (
              <Loader2 className="size-4 animate-spin mr-1" />
            ) : (
              <Sparkles className="size-4 mr-1" />
            )}
            Search
          </Button>
        </div>
      </div>

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
          <p className="text-sm text-muted-foreground">Searching by meaning...</p>
        </div>
      )}

      {!error && !isLoading && hasSearched && results.length === 0 && (
        <div className="flex flex-col items-center justify-center py-8 text-center">
          <Music className="size-8 text-muted-foreground mb-2" />
          <p className="text-muted-foreground text-sm">No matching songs found</p>
          <p className="text-xs text-muted-foreground mt-1">
            Try a different description, or songs may not have embeddings yet
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
                onAdd={onAddSong}
                onPlay={handlePlaySong}
                isAdded={isSongAdded(song.id)}
                isAdding={isSongAdding(song.id)}
                isPlaying={playingSongId === song.id}
                isPreviewLoading={previewLoadingSongId === song.id}
              />
              <Badge
                variant="secondary"
                className="absolute top-2 right-10 text-xs"
                data-testid="similarity-badge"
              >
                {formatSimilarity(song.similarity)}
              </Badge>
              {song.matchingSnippet && (
                <p
                  className="text-xs italic text-muted-foreground pl-3 -mt-1"
                  data-testid="matching-snippet"
                >
                  ▸ {song.matchingSnippet}
                </p>
              )}
              {song.whyThisMatch.length > 0 && (
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
              {expandedSongId === song.id && song.whyThisMatch.length > 0 && (
                <div className="pl-6 space-y-0.5" data-testid="why-this-match-content">
                  {song.whyThisMatch.map((line, i) => (
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
    </div>
  );
}
