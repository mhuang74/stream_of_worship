"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { SongSearch } from "./SongSearch";
import { SongCard, SongCardData } from "./SongCard";
import { SemanticSearch } from "@/components/search/SemanticSearch";
import type { StructuredSearchCriteria } from "./search/types";
import { Loader2, Music, AlertCircle, Search, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { useAudioPlayerContext } from "@/contexts/AudioPlayerContext";
import { getPublicAudioUrl } from "@/lib/r2/public-url";
import { SONGSET_MAX_SONGS } from "@/lib/constants";

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

export function BrowseSheet({
  isOpen,
  onOpenChange,
  onAddSong,
  existingSongIds = [],
  itemCount = 0,
  className,
}: BrowseSheetProps) {
  const [mode, setMode] = useState<SearchMode>("keyword");
  const [keywordQuery, setKeywordQuery] = useState("");
  const [initialSearchQuery, setInitialSearchQuery] = useState<string | undefined>();
  const [selectedAlbums, setSelectedAlbums] = useState<string[]>([]);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [selectedBpm, setSelectedBpm] = useState<StructuredSearchCriteria["bpmRange"]>();
  const [activeFilters, setActiveFilters] = useState<StructuredSearchCriteria | undefined>();
  const [results, setResults] = useState<SongCardData[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [albums, setAlbums] = useState<string[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isLoadingAlbums, setIsLoadingAlbums] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [addingSongIds, setAddingSongIds] = useState<Set<string>>(new Set());
  const [addedSongIds, setAddedSongIds] = useState<Set<string>>(new Set());
  const [playingSongId, setPlayingSongId] = useState<string | null>(null);
  const [previewLoadingSongId, setPreviewLoadingSongId] = useState<string | null>(null);
  const latestSearchIdRef = useRef(0);
  const { play, pause, currentTrack, state: playerState } = useAudioPlayerContext();

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
    async (
      searchQuery: string,
      albumFilters?: string[],
      advanced?: StructuredSearchCriteria
    ) => {
      const searchId = latestSearchIdRef.current + 1;
      latestSearchIdRef.current = searchId;
      const nextFilters: StructuredSearchCriteria = {
        query: searchQuery.trim() || undefined,
        albums: albumFilters && albumFilters.length > 0 ? albumFilters : undefined,
        keys: advanced?.keys,
        bpmRange: advanced?.bpmRange,
      };
      setKeywordQuery(searchQuery);
      setActiveFilters(nextFilters);
      setIsLoading(true);
      setError(null);

      try {
        const params = new URLSearchParams();
        if (searchQuery.trim()) {
          params.set("q", searchQuery.trim());
        }
        for (const album of albumFilters ?? []) {
          params.append("albumName", album);
        }
        if (nextFilters.keys?.length) {
          params.set("keys", nextFilters.keys.join(","));
        }
        if (nextFilters.bpmRange) {
          params.set("bpmRange", nextFilters.bpmRange);
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
        if (searchId !== latestSearchIdRef.current) return;
        setResults(data.songs || []);
        setTotalCount(data.total ?? 0);
      } catch (err) {
        if (searchId !== latestSearchIdRef.current) return;
        setError(err instanceof Error ? err.message : "Failed to search songs");
        setResults([]);
        setTotalCount(0);
      } finally {
        if (searchId === latestSearchIdRef.current) {
          setIsLoading(false);
        }
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
        setKeywordQuery("");
        setSelectedAlbums([]);
        setSelectedKeys([]);
        setSelectedBpm(undefined);
        setActiveFilters(undefined);
        setResults([]);
        setTotalCount(0);
        setError(null);
        setAddingSongIds(new Set());
        setAddedSongIds(new Set());
        setMode("keyword");
        setPlayingSongId(null);
        setPreviewLoadingSongId(null);
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
    async (songOrId: string | SongCardData) => {
      const songId = typeof songOrId === "string" ? songOrId : songOrId.id;
      if (addingSongIds.has(songId) || addedSongIds.has(songId)) return;

      const song = typeof songOrId === "string"
        ? results.find((result) => result.id === songId)
        : songOrId;
      if (!song) {
        toast.error("Song not found");
        return;
      }

      setAddingSongIds((prev) => new Set(prev).add(songId));

      try {
        await onAddSong(song);
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
    [onAddSong, addingSongIds, addedSongIds, results]
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

  const handleSwitchToSearchTab = useCallback((searchQuery: string) => {
    setInitialSearchQuery(searchQuery);
    setKeywordQuery(searchQuery);
    setMode("keyword");
  }, []);

  const handlePlaySong = useCallback(
    async (songId: string) => {
      const song = results.find((r) => r.id === songId);
      if (!song || song.recordings.length === 0) {
        toast.error("No audio available for this song");
        return;
      }

      if (playingSongId === songId && currentTrack?.id === `song-${songId}`) {
        if (playerState.isPlaying) {
          pause();
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
    [results, playingSongId, currentTrack, playerState.isPlaying, play, pause]
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

  return (
    <Sheet open={isOpen} onOpenChange={onOpenChange}>
      <SheetContent side="bottom" className={cn("data-[side=bottom]:!h-[85vh] sm:data-[side=bottom]:!h-[90vh] overflow-hidden", className)}>
        <SheetHeader className="pb-2">
          <SheetTitle>Search Songs</SheetTitle>
        </SheetHeader>

        <div className={cn("flex flex-col h-full min-h-0", currentTrack ? "pb-28 sm:pb-20" : "pb-8")}>
          {/* Mode tabs */}
          <div className="flex gap-1 pb-4 border-b mb-4" role="tablist" aria-label="Search mode">
            <Button
              role="tab"
              aria-selected={mode === "keyword"}
              variant={mode === "keyword" ? "default" : "ghost"}
              size="sm"
              onClick={() => setMode("keyword")}
              className="gap-1.5"
              data-testid="browse-mode-tab"
            >
              <Search className="size-3.5" />
              Keyword
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

          {mode === "keyword" && (
            <div role="tabpanel" aria-label="Keyword song search" className="flex flex-col min-h-0 flex-1">
              {/* Search section */}
              <div className="px-1 pb-4">
                <SongSearch
                  onSearch={handleSearch}
                  onAdvancedSearch={(criteria) =>
                    handleSearch(criteria.query ?? "", criteria.albums, criteria)
                  }
                  albums={albums}
                  isLoading={isLoading || isLoadingAlbums}
                  initialQuery={initialSearchQuery}
                  query={keywordQuery}
                  onQueryChange={setKeywordQuery}
                  selectedAlbums={selectedAlbums}
                  onSelectedAlbumsChange={setSelectedAlbums}
                  selectedKeys={selectedKeys}
                  onSelectedKeysChange={setSelectedKeys}
                  selectedBpm={selectedBpm}
                  onSelectedBpmChange={setSelectedBpm}
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
                      onClick={() => handleSearch(keywordQuery, selectedAlbums, activeFilters)}
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

                {!error && !isLoading && results.length === 0 && keywordQuery && (
                  <div className="flex flex-col items-center justify-center py-12 text-center">
                    <Music className="size-8 text-muted-foreground mb-2" />
                    <p className="text-muted-foreground">
                      No songs found for &quot;{keywordQuery}&quot;
                    </p>
                    <p className="text-sm text-muted-foreground mt-1">
                      {activeFilters?.keys?.length || activeFilters?.bpmRange
                        ? "Try adjusting your filters or search term"
                        : "Try a different search term"}
                    </p>
                  </div>
                )}

                {!error && !isLoading && results.length === 0 && !keywordQuery && (activeFilters?.albums?.length || activeFilters?.keys?.length || activeFilters?.bpmRange) && (
                  <div className="flex flex-col items-center justify-center py-12 text-center">
                    <Music className="size-8 text-muted-foreground mb-2" />
                    <p className="text-muted-foreground">No songs match your filters</p>
                    <p className="text-sm text-muted-foreground mt-1">
                      Try removing some filters to see more results
                    </p>
                  </div>
                )}

                {!error && !isLoading && results.length === 0 && !keywordQuery && (
                  <div className="flex flex-col items-center justify-center py-12 text-center">
                    <Music className="size-8 text-muted-foreground mb-2" />
                    <p className="text-muted-foreground">No songs available</p>
                    <p className="text-sm text-muted-foreground mt-1">
                      Start typing to search for songs
                    </p>
                  </div>
                )}

                {!error && results.length > 0 && (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 pb-4">
                    {results.map((song) => (
                      <SongCard
                        key={song.id}
                        song={song}
                        onAdd={handleAddSong}
                        onPlay={handlePlaySong}
                        isAdded={isSongAdded(song.id)}
                        isAdding={isSongAdding(song.id)}
                        disabled={isSongsetFull}
                        isPlaying={playingSongId === song.id}
                        isPreviewLoading={previewLoadingSongId === song.id}
                      />
                    ))}
                  </div>
                )}
              </div>
            </div>
          )}

          {mode === "describe" && (
            <div className="flex-1 min-h-0 overflow-y-auto px-1 -mx-1" role="tabpanel" aria-label="Describe songs">
              <SemanticSearch
                onAddSong={handleAddSong}
                existingSongIds={existingSongIds}
                addingSongIds={addingSongIds}
                addedSongIds={addedSongIds}
                onSwitchToSearchTab={handleSwitchToSearchTab}
                albums={selectedAlbums}
                keys={selectedKeys}
                bpmRange={selectedBpm}
              />
            </div>
          )}

          {/* Footer */}
          <div className="pt-4 border-t mt-4">
            <div className="flex items-center justify-between">
              <p className="text-sm text-muted-foreground">
                {isSongsetFull
                  ? "Songset full"
                  : mode === "keyword" && totalCount > 0
                    ? `${totalCount} songs`
                    : ""}
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
