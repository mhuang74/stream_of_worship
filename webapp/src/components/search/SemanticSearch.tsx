"use client";

import { useState, useCallback } from "react";
import { Textarea } from "@/components/ui/textarea";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { SongCard, SongCardData } from "@/components/songset/SongCard";
import { Loader2, Sparkles, Music } from "lucide-react";
import { cn } from "@/lib/utils";

interface SemanticSearchResult extends SongCardData {
  similarity: number;
}

interface SemanticSearchProps {
  onAddSong: (songId: string) => Promise<void>;
  existingSongIds?: string[];
  addingSongIds?: Set<string>;
  addedSongIds?: Set<string>;
  className?: string;
}

export function SemanticSearch({
  onAddSong,
  existingSongIds = [],
  addingSongIds = new Set(),
  addedSongIds = new Set(),
  className,
}: SemanticSearchProps) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SemanticSearchResult[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasSearched, setHasSearched] = useState(false);

  const handleSearch = useCallback(async () => {
    const trimmed = query.trim();
    if (!trimmed) return;

    setIsLoading(true);
    setError(null);
    setHasSearched(true);

    try {
      const response = await fetch("/api/songs/search/semantic", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: trimmed, limit: 20 }),
      });

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
  }, [query]);

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

  const formatSimilarity = (score: number) =>
    `${Math.round(score * 100)}% match`;

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
          <p className="text-xs text-muted-foreground">Press Ctrl+Enter to search</p>
          <Button
            onClick={handleSearch}
            disabled={isLoading || !query.trim()}
            size="sm"
            data-testid="semantic-search-button"
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
        <div className="space-y-2" data-testid="semantic-search-results">
          <p className="text-xs text-muted-foreground">{results.length} songs found</p>
          {results.map((song) => (
            <div key={song.id} className="relative">
              <SongCard
                song={song}
                onAdd={onAddSong}
                isAdded={isSongAdded(song.id)}
                isAdding={isSongAdding(song.id)}
              />
              <Badge
                variant="secondary"
                className="absolute top-2 right-10 text-xs"
                data-testid="similarity-badge"
              >
                {formatSimilarity(song.similarity)}
              </Badge>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
