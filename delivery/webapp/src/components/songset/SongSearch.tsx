"use client";

import { useState, useCallback } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Search, X, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { type BpmBandKey } from "@/lib/constants";
import type { StructuredSearchCriteria } from "./search/types";
import type { AlbumFilter } from "@/lib/search/album-filter";

interface SongSearchProps {
  onSearch: (query: string, albumFilters?: AlbumFilter[]) => void;
  onAdvancedSearch?: (criteria: StructuredSearchCriteria) => void;
  isLoading?: boolean;
  className?: string;
  placeholder?: string;
  initialQuery?: string;
  query?: string;
  onQueryChange?: (query: string) => void;
  selectedAlbums?: AlbumFilter[];
  selectedKeys?: string[];
  selectedBpm?: BpmBandKey[];
  searchButtonClassName?: string;
  showSearchButton?: boolean;
}

export function SongSearch({
  onSearch,
  onAdvancedSearch,
  isLoading = false,
  className,
  placeholder = "Search songs by title, artist, or album...",
  initialQuery,
  query: controlledQuery,
  onQueryChange,
  selectedAlbums = [],
  selectedKeys = [],
  selectedBpm,
  searchButtonClassName,
  showSearchButton = true,
}: SongSearchProps) {
  const [internalQuery, setInternalQuery] = useState(initialQuery ?? "");

  const query = controlledQuery ?? internalQuery;

  const setQuery = useCallback(
    (value: string) => {
      if (onQueryChange) onQueryChange(value);
      else setInternalQuery(value);
    },
    [onQueryChange]
  );

  const hasAdvancedFilters =
    selectedAlbums.length > 0 || selectedKeys.length > 0 || (selectedBpm?.length ?? 0) > 0;

  const triggerSearch = useCallback(
    () => {
      const normalizedAlbums = selectedAlbums.length > 0 ? selectedAlbums : undefined;
      if (hasAdvancedFilters && onAdvancedSearch) {
        onAdvancedSearch({
          query: query.trim() || undefined,
          keys: selectedKeys.length > 0 ? selectedKeys : undefined,
          bpmRange: selectedBpm && selectedBpm.length > 0 ? selectedBpm : undefined,
          albums: normalizedAlbums,
        });
      } else {
        onSearch(query, normalizedAlbums);
      }
    },
    [
      selectedAlbums,
      hasAdvancedFilters,
      onAdvancedSearch,
      selectedKeys,
      selectedBpm,
      onSearch,
      query,
    ]
  );

  // Handle query change
  const handleQueryChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const newQuery = e.target.value;
      setQuery(newQuery);
    },
    [setQuery]
  );

  // Handle clear
  const handleClear = useCallback(() => {
    setQuery("");
  }, [setQuery]);

  // Handle Enter key to trigger search
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (e.key === "Enter") {
        e.preventDefault();
        triggerSearch();
      }
    },
    [triggerSearch]
  );

  const showClearButton = query.length > 0;
  const showLoadingIndicator = isLoading;

  return (
    <div className={cn("space-y-2", className)} data-testid="song-search">
      {/* Search input */}
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground pointer-events-none" />
          <Input
            type="text"
            value={query}
            onChange={handleQueryChange}
            onKeyDown={handleKeyDown}
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
        {showSearchButton && (
          <Button
            type="button"
            onClick={triggerSearch}
            disabled={isLoading}
            className={cn("shrink-0 gap-1.5", searchButtonClassName)}
            data-testid="search-button"
            aria-label={isLoading ? "Searching songs" : "Run song search"}
          >
            {isLoading ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />}
            Search
          </Button>
        )}
      </div>

      <p className="text-xs text-muted-foreground px-1" data-testid="keyword-help-text">
        Tip: search by title, pinyin, or composer — e.g. &lsquo;歡喜&rsquo;, &lsquo;huan xi&rsquo;, &lsquo;曾祥怡&rsquo; · Press Enter to search
      </p>
    </div>
  );
}
