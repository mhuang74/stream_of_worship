"use client";

import { useState, useCallback, useEffect } from "react";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { SongSearch } from "./SongSearch";
import { SongCard, SongCardData } from "./SongCard";
import { SemanticSearch } from "@/components/search/SemanticSearch";
import { Loader2, Music, AlertCircle, Search, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

type SearchMode = "browse" | "describe";

interface BrowseSheetProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  onAddSongs: (songIds: string[]) => Promise<void>;
  existingSongIds?: string[];
  className?: string;
}

interface SearchResult {
  songs: SongCardData[];
  total: number;
}

export function BrowseSheet({
  isOpen,
  onOpenChange,
  onAddSongs,
  existingSongIds = [],
  className,
}: BrowseSheetProps) {
  const [mode, setMode] = useState<SearchMode>("browse");
  const [query, setQuery] = useState("");
  const [albumFilter, setAlbumFilter] = useState<string | undefined>();
  const [results, setResults] = useState<SongCardData[]>([]);
  const [albums, setAlbums] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingAlbums, setIsLoadingAlbums] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [addingSongIds, setAddingSongIds] = useState<Set<string>>(new Set());
  const [addedSongIds, setAddedSongIds] = useState<Set<string>>(new Set());

  // Load albums function
  const loadAlbums = useCallback(async () => {
    setIsLoadingAlbums(true);
    try {
      const response = await fetch("/api/songs/albums");
      if (!response.ok) {
        throw new Error("Failed to load albums");
      }
      const data = await response.json();
      setAlbums(data.albums || []);
    } catch (err) {
      console.error("Error loading albums:", err);
    } finally {
      setIsLoadingAlbums(false);
    }
  }, []);

  // Search function
  const handleSearch = useCallback(
    async (searchQuery: string, album?: string) => {
      setQuery(searchQuery);
      setAlbumFilter(album);
      setIsLoading(true);
      setError(null);

      try {
        const params = new URLSearchParams();
        if (searchQuery.trim()) {
          params.set("q", searchQuery.trim());
        }
        if (album && album !== "all") {
          params.set("albumName", album);
        }
        params.set("limit", "50");

        const url = searchQuery.trim()
          ? `/api/songs/search?${params.toString()}`
          : `/api/songs?${params.toString()}`;

        const response = await fetch(url);
        if (!response.ok) {
          throw new Error("Failed to search songs");
        }

        const data: SearchResult = await response.json();
        setResults(data.songs || []);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to search songs");
        setResults([]);
      } finally {
        setIsLoading(false);
      }
    },
    []
  );

  // Handle sheet open/close
  useEffect(() => {
    if (isOpen) {
      if (albums.length === 0) {
        const albumTimeoutId = setTimeout(() => {
          loadAlbums();
        }, 0);
        return () => clearTimeout(albumTimeoutId);
      }
    } else {
      const timeoutId = setTimeout(() => {
        setQuery("");
        setAlbumFilter(undefined);
        setResults([]);
        setError(null);
        setAddingSongIds(new Set());
        setAddedSongIds(new Set());
        setMode("browse");
      }, 300);
      return () => clearTimeout(timeoutId);
    }
  }, [isOpen, albums.length, loadAlbums]);

  // Load initial results when opened
  useEffect(() => {
    if (isOpen) {
      const timeoutId = setTimeout(() => {
        handleSearch("", undefined);
      }, 0);
      return () => clearTimeout(timeoutId);
    }
  }, [isOpen, handleSearch]);

  const handleAddSong = useCallback(
    async (songId: string) => {
      if (addingSongIds.has(songId) || addedSongIds.has(songId)) return;

      setAddingSongIds((prev) => new Set(prev).add(songId));

      try {
        await onAddSongs([songId]);
        setAddedSongIds((prev) => new Set(prev).add(songId));
        toast.success("Song added to songset");
      } catch (err) {
        toast.error("Failed to add song");
        console.error("Error adding song:", err);
      } finally {
        setAddingSongIds((prev) => {
          const next = new Set(prev);
          next.delete(songId);
          return next;
        });
      }
    },
    [onAddSongs, addingSongIds, addedSongIds]
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

  return (
    <Sheet open={isOpen} onOpenChange={onOpenChange}>
      <SheetContent side="bottom" className={cn("h-[85vh] sm:h-[75vh]", className)}>
        <SheetHeader className="pb-4">
          <SheetTitle>Browse Songs</SheetTitle>
          <SheetDescription>
            Search and add songs to your songset
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-col h-full pb-8">
          {/* Mode tabs */}
          <div className="flex gap-1 pb-4 border-b mb-4" role="tablist" aria-label="Search mode">
            <Button
              role="tab"
              aria-selected={mode === "browse"}
              variant={mode === "browse" ? "default" : "ghost"}
              size="sm"
              onClick={() => setMode("browse")}
              className="gap-1.5"
              data-testid="browse-mode-tab"
            >
              <Search className="size-3.5" />
              Browse
            </Button>
            <Button
              role="tab"
              aria-selected={mode === "describe"}
              variant={mode === "describe" ? "default" : "ghost"}
              size="sm"
              onClick={() => setMode("describe")}
              className="gap-1.5"
              data-testid="describe-mode-tab"
            >
              <Sparkles className="size-3.5" />
              Describe
            </Button>
          </div>

          {mode === "browse" && (
            <div role="tabpanel" aria-label="Browse songs">
              {/* Search section */}
              <div className="px-1 pb-4">
                <SongSearch
                  onSearch={handleSearch}
                  albums={albums}
                  isLoading={isLoading || isLoadingAlbums}
                />
              </div>

              {/* Results section */}
              <div className="flex-1 overflow-y-auto px-1 -mx-1">
                {error && (
                  <div className="flex flex-col items-center justify-center py-8 text-center">
                    <AlertCircle className="size-8 text-destructive mb-2" />
                    <p className="text-destructive text-sm">{error}</p>
                    <Button
                      variant="outline"
                      size="sm"
                      className="mt-4"
                      onClick={() => handleSearch(query, albumFilter)}
                    >
                      Retry
                    </Button>
                  </div>
                )}

                {!error && isLoading && results.length === 0 && (
                  <div className="flex flex-col items-center justify-center py-12" role="status" aria-live="polite">
                    <Loader2 className="size-8 animate-spin text-muted-foreground mb-2" aria-hidden="true" />
                    <p className="text-muted-foreground text-sm">Searching songs...</p>
                  </div>
                )}

                {!error && !isLoading && results.length === 0 && query && (
                  <div className="flex flex-col items-center justify-center py-12 text-center">
                    <Music className="size-8 text-muted-foreground mb-2" />
                    <p className="text-muted-foreground">
                      No songs found for &quot;{query}&quot;
                    </p>
                    <p className="text-sm text-muted-foreground mt-1">
                      Try a different search term
                    </p>
                  </div>
                )}

                {!error && !isLoading && results.length === 0 && !query && (
                  <div className="flex flex-col items-center justify-center py-12 text-center">
                    <Music className="size-8 text-muted-foreground mb-2" />
                    <p className="text-muted-foreground">No songs available</p>
                    <p className="text-sm text-muted-foreground mt-1">
                      Start typing to search for songs
                    </p>
                  </div>
                )}

                {!error && results.length > 0 && (
                  <div className="space-y-2 pb-4">
                    {results.map((song) => (
                      <SongCard
                        key={song.id}
                        song={song}
                        onAdd={handleAddSong}
                        isAdded={isSongAdded(song.id)}
                        isAdding={isSongAdding(song.id)}
                      />
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {mode === "describe" && (
            <div className="flex-1 overflow-y-auto px-1 -mx-1" role="tabpanel" aria-label="Describe songs">
              <SemanticSearch
                onAddSong={handleAddSong}
                existingSongIds={existingSongIds}
                addingSongIds={addingSongIds}
                addedSongIds={addedSongIds}
              />
            </div>
          )}

          {/* Footer */}
          <div className="pt-4 border-t mt-4">
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">
                {mode === "browse" && results.length > 0 && `${results.length} songs found`}
              </p>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Done
              </Button>
            </div>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
