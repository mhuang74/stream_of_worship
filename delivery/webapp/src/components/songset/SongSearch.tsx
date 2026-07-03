"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Search, X, Loader2, SlidersHorizontal } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  PITCH_CLASSES,
  BPM_BANDS,
  BPM_BAND_KEYS,
  type BpmBandKey,
} from "@/lib/constants";
import type { StructuredSearchCriteria } from "./search/types";

interface SongSearchProps {
  onSearch: (query: string, albumFilter?: string) => void;
  onAdvancedSearch?: (criteria: StructuredSearchCriteria) => void;
  albums: string[];
  isLoading?: boolean;
  className?: string;
  placeholder?: string;
  debounceMs?: number;
  initialQuery?: string;
}

export function SongSearch({
  onSearch,
  onAdvancedSearch,
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
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [selectedKeys, setSelectedKeys] = useState<string[]>([]);
  const [selectedBpm, setSelectedBpm] = useState<BpmBandKey | undefined>();
  const debounceTimerRef = useRef<NodeJS.Timeout | null>(null);
  const initialSearchTriggered = useRef(false);

  const hasAdvancedFilters =
    selectedKeys.length > 0 || selectedBpm !== undefined;

  const triggerSearch = useCallback(
    (searchQuery: string, albumFilter?: string) => {
      const album = albumFilter === "all" ? undefined : albumFilter;
      if (showAdvanced && hasAdvancedFilters && onAdvancedSearch) {
        onAdvancedSearch({
          query: searchQuery.trim() || undefined,
          keys: selectedKeys.length > 0 ? selectedKeys : undefined,
          bpmRange: selectedBpm,
          album,
        });
      } else {
        onSearch(searchQuery, album);
      }
    },
    [showAdvanced, hasAdvancedFilters, onAdvancedSearch, selectedKeys, selectedBpm, onSearch]
  );

  useEffect(() => {
    if (initialQuery && initialQuery.trim() && !initialSearchTriggered.current) {
      initialSearchTriggered.current = true;
      setIsSearching(true);
      triggerSearch(initialQuery, undefined);
    }
  }, [initialQuery, triggerSearch]);

  // Debounced search handler
  const debouncedSearch = useCallback(
    (searchQuery: string, albumFilter?: string) => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }

      debounceTimerRef.current = setTimeout(() => {
        triggerSearch(searchQuery, albumFilter);
        setIsSearching(false);
      }, debounceMs);
    },
    [triggerSearch, debounceMs]
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

  const toggleKey = useCallback((key: string) => {
    setSelectedKeys((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    );
  }, []);

  const toggleBpm = useCallback((band: BpmBandKey) => {
    setSelectedBpm((prev) => (prev === band ? undefined : band));
  }, []);

  const handleApplyFilters = useCallback(() => {
    setIsSearching(true);
    triggerSearch(query, selectedAlbum);
    setIsSearching(false);
  }, [triggerSearch, query, selectedAlbum]);

  const handleClearFilters = useCallback(() => {
    setSelectedKeys([]);
    setSelectedBpm(undefined);
    setIsSearching(true);
    const album = selectedAlbum === "all" ? undefined : selectedAlbum;
    onSearch(query, album);
    setIsSearching(false);
  }, [query, selectedAlbum, onSearch]);

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

      {/* Advanced filters toggle */}
      {onAdvancedSearch && (
        <Button
          variant="ghost"
          size="sm"
          className="gap-1.5 w-fit"
          onClick={() => setShowAdvanced((prev) => !prev)}
          aria-expanded={showAdvanced}
          aria-controls="advanced-filters-panel"
          data-testid="advanced-filters-toggle"
        >
          <SlidersHorizontal className="size-3.5" />
          Advanced filters
          {hasAdvancedFilters && (
            <Badge variant="secondary" className="ml-1 px-1.5 py-0 text-[10px]">
              {selectedKeys.length + (selectedBpm ? 1 : 0)}
            </Badge>
          )}
        </Button>
      )}

      {/* Collapsible advanced filters panel */}
      {showAdvanced && onAdvancedSearch && (
        <div
          id="advanced-filters-panel"
          className="border rounded-md p-3 space-y-4"
          data-testid="advanced-filters-panel"
        >
          {/* Musical Key chips */}
          <div className="space-y-2">
            <Label className="text-xs text-muted-foreground">Musical Key</Label>
            <div
              className="flex flex-wrap gap-1.5"
              data-testid="advanced-key-chips"
              role="group"
              aria-label="Musical key filters"
            >
              {PITCH_CLASSES.map((key) => {
                const isSelected = selectedKeys.includes(key);
                return (
                  <Button
                    key={key}
                    type="button"
                    variant={isSelected ? "default" : "outline"}
                    size="xs"
                    className="rounded-full px-2.5"
                    aria-pressed={isSelected}
                    onClick={() => toggleKey(key)}
                    data-testid={`key-chip-${key.replace("#", "sharp")}`}
                  >
                    {key}
                  </Button>
                );
              })}
            </div>
          </div>

          {/* BPM Range chips */}
          <div className="space-y-2">
            <Label className="text-xs text-muted-foreground">BPM Range</Label>
            <div
              className="flex flex-wrap gap-1.5"
              data-testid="advanced-bpm-chips"
              role="group"
              aria-label="BPM range filters"
            >
              {BPM_BAND_KEYS.map((band) => {
                const isSelected = selectedBpm === band;
                const config = BPM_BANDS[band];
                let rangeText: string;
                if ("max" in config && !("min" in config)) {
                  rangeText = `< ${config.max}`;
                } else if ("min" in config && "max" in config) {
                  rangeText = `${config.min}–${config.max}`;
                } else {
                  rangeText = `≥ ${config.min}`;
                }
                return (
                  <Button
                    key={band}
                    type="button"
                    variant={isSelected ? "default" : "outline"}
                    size="xs"
                    className="rounded-full px-2.5"
                    aria-pressed={isSelected}
                    onClick={() => toggleBpm(band)}
                    data-testid={`bpm-chip-${band}`}
                  >
                    {config.label} ({rangeText})
                  </Button>
                );
              })}
            </div>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              onClick={handleApplyFilters}
              data-testid="advanced-apply-button"
            >
              Apply filters
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={handleClearFilters}
              disabled={!hasAdvancedFilters}
              data-testid="advanced-clear-button"
            >
              Clear all
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
