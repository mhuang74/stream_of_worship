"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Search, X, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";

interface SongSearchProps {
  onSearch: (query: string, albumFilter?: string) => void;
  albums: string[];
  isLoading?: boolean;
  className?: string;
  placeholder?: string;
  debounceMs?: number;
  initialQuery?: string;
}

export function SongSearch({
  onSearch,
  albums,
  isLoading = false,
  className,
  placeholder = "Search songs by title, artist, or album...",
  debounceMs = 300,
  initialQuery,
}: SongSearchProps) {
  const [query, setQuery] = useState(initialQuery ?? "");
  const [selectedAlbum, setSelectedAlbum] = useState<string>("all");
  const [isSearching, setIsSearching] = useState(false);
  const debounceTimerRef = useRef<NodeJS.Timeout | null>(null);
  const initialSearchTriggered = useRef(false);

  useEffect(() => {
    if (initialQuery && initialQuery.trim() && !initialSearchTriggered.current) {
      initialSearchTriggered.current = true;
      setIsSearching(true);
      onSearch(initialQuery, undefined);
    }
  }, [initialQuery, onSearch]);

  // Debounced search handler
  const debouncedSearch = useCallback(
    (searchQuery: string, albumFilter?: string) => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }

      debounceTimerRef.current = setTimeout(() => {
        const album = albumFilter === "all" ? undefined : albumFilter;
        onSearch(searchQuery, album);
        setIsSearching(false);
      }, debounceMs);
    },
    [onSearch, debounceMs]
  );

  // Handle query change
  const handleQueryChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const newQuery = e.target.value;
      setQuery(newQuery);
      setIsSearching(true);
      debouncedSearch(newQuery, selectedAlbum);
    },
    [debouncedSearch, selectedAlbum]
  );

  // Handle album filter change
  const handleAlbumChange = useCallback(
    (value: string | null) => {
      setSelectedAlbum(value ?? "");
      setIsSearching(true);
      debouncedSearch(query, value ?? "");
    },
    [debouncedSearch, query]
  );

  // Handle clear
  const handleClear = useCallback(() => {
    setQuery("");
    setIsSearching(true);
    debouncedSearch("", selectedAlbum);
  }, [debouncedSearch, selectedAlbum]);

  // Cleanup debounce timer on unmount
  useEffect(() => {
    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
    };
  }, []);

  const showClearButton = query.length > 0;
  const showLoadingIndicator = isLoading || isSearching;

  return (
    <div className={cn("space-y-3", className)} data-testid="song-search">
      {/* Search input */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground pointer-events-none" />
        <Input
          type="text"
          value={query}
          onChange={handleQueryChange}
          placeholder={placeholder}
          className="pl-9 pr-10"
          aria-label="Search songs"
          data-testid="search-input"
        />
        {showClearButton && (
          <Button
            variant="ghost"
            size="icon-sm"
            className="absolute right-2 top-1/2 -translate-y-1/2"
            onClick={handleClear}
            aria-label="Clear search"
            data-testid="clear-search-button"
          >
            <X className="size-4" />
          </Button>
        )}
        {showLoadingIndicator && !showClearButton && (
          <Loader2
            className="absolute right-3 top-1/2 -translate-y-1/2 size-4 animate-spin text-muted-foreground"
            aria-hidden="true"
          />
        )}
        {showLoadingIndicator && (
          <span className="sr-only" role="status" aria-live="polite">Searching...</span>
        )}
      </div>

      {/* Album filter */}
      {albums.length > 0 && (
        <div className="flex items-center gap-2">
          <span className="text-sm text-muted-foreground whitespace-nowrap">
            Filter by album:
          </span>
          <Select value={selectedAlbum} onValueChange={handleAlbumChange}>
            <SelectTrigger className="w-full" data-testid="album-filter">
              <SelectValue placeholder="All albums" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">All albums</SelectItem>
              {albums.map((album) => (
                <SelectItem key={album} value={album}>
                  {album}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      )}
    </div>
  );
}
