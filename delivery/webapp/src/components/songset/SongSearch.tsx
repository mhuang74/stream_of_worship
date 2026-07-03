"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Search, X, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { type BpmBandKey } from "@/lib/constants";
import type { StructuredSearchCriteria } from "./search/types";

interface SongSearchProps {
  onSearch: (query: string, albumFilters?: string[]) => void;
  onAdvancedSearch?: (criteria: StructuredSearchCriteria) => void;
  isLoading?: boolean;
  className?: string;
  placeholder?: string;
  debounceMs?: number;
  initialQuery?: string;
  query?: string;
  onQueryChange?: (query: string) => void;
  selectedAlbums?: string[];
  selectedKeys?: string[];
  selectedBpm?: BpmBandKey;
}

export function SongSearch({
  onSearch,
  onAdvancedSearch,
  isLoading = false,
  className,
  placeholder = "Search songs by title, artist, or album...",
  debounceMs = 300,
  initialQuery,
  query: controlledQuery,
  onQueryChange,
  selectedAlbums = [],
  selectedKeys = [],
  selectedBpm,
}: SongSearchProps) {
  const [internalQuery, setInternalQuery] = useState(initialQuery ?? "");
  const [isSearching, setIsSearching] = useState(false);
  const debounceTimerRef = useRef<NodeJS.Timeout | null>(null);
  const initialSearchTriggered = useRef(false);

  const query = controlledQuery ?? internalQuery;

  const setQuery = useCallback(
    (value: string) => {
      if (onQueryChange) onQueryChange(value);
      else setInternalQuery(value);
    },
    [onQueryChange]
  );

  const hasAdvancedFilters =
    selectedAlbums.length > 0 || selectedKeys.length > 0 || selectedBpm !== undefined;

  const triggerSearch = useCallback(
    (searchQuery: string, albumFilters: string[] = selectedAlbums) => {
      const normalizedAlbums = albumFilters.length > 0 ? albumFilters : undefined;
      if (hasAdvancedFilters && onAdvancedSearch) {
        onAdvancedSearch({
          query: searchQuery.trim() || undefined,
          keys: selectedKeys.length > 0 ? selectedKeys : undefined,
          bpmRange: selectedBpm,
          albums: normalizedAlbums,
        });
      } else {
        onSearch(searchQuery, normalizedAlbums);
      }
    },
    [
      selectedAlbums,
      hasAdvancedFilters,
      onAdvancedSearch,
      selectedKeys,
      selectedBpm,
      onSearch,
    ]
  );

  useEffect(() => {
    if (initialQuery && initialQuery.trim() && !initialSearchTriggered.current) {
      initialSearchTriggered.current = true;
      setIsSearching(true);
      triggerSearch(initialQuery, selectedAlbums);
    }
  }, [initialQuery, selectedAlbums, triggerSearch]);

  // Debounced search handler
  const debouncedSearch = useCallback(
    (searchQuery: string, albumFilters: string[] = selectedAlbums) => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }

      debounceTimerRef.current = setTimeout(() => {
        triggerSearch(searchQuery, albumFilters);
        setIsSearching(false);
      }, debounceMs);
    },
    [triggerSearch, debounceMs, selectedAlbums]
  );

  // Handle query change
  const handleQueryChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const newQuery = e.target.value;
      setQuery(newQuery);
      setIsSearching(true);
      debouncedSearch(newQuery, selectedAlbums);
    },
    [debouncedSearch, selectedAlbums, setQuery]
  );

  // Handle clear
  const handleClear = useCallback(() => {
    setQuery("");
    setIsSearching(true);
    debouncedSearch("", selectedAlbums);
  }, [debouncedSearch, selectedAlbums, setQuery]);

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
    <div className={cn("space-y-2", className)} data-testid="song-search">
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

      <p className="text-xs text-muted-foreground px-1" data-testid="keyword-help-text">
        Tip: search by title, pinyin, or composer — e.g. &lsquo;奇异恩典&rsquo;, &lsquo;Amazing Grace&rsquo;, &lsquo;约瑟夫&rsquo;
      </p>
    </div>
  );
}
