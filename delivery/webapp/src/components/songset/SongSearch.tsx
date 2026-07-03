"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { Search, X, Loader2, SlidersHorizontal } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  PITCH_CLASSES,
  BPM_BANDS,
  BPM_BAND_KEYS,
  type BpmBandKey,
} from "@/lib/constants";
import type { StructuredSearchCriteria } from "./search/types";
import { AlbumMultiSelect } from "./AlbumMultiSelect";

interface SongSearchProps {
  onSearch: (query: string, albumFilters?: string[]) => void;
  onAdvancedSearch?: (criteria: StructuredSearchCriteria) => void;
  albums: string[];
  isLoading?: boolean;
  className?: string;
  placeholder?: string;
  debounceMs?: number;
  initialQuery?: string;
  query?: string;
  onQueryChange?: (query: string) => void;
  selectedAlbums?: string[];
  onSelectedAlbumsChange?: (albums: string[]) => void;
  selectedKeys?: string[];
  onSelectedKeysChange?: (keys: string[]) => void;
  selectedBpm?: BpmBandKey;
  onSelectedBpmChange?: (bpm: BpmBandKey | undefined) => void;
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
  query: controlledQuery,
  onQueryChange,
  selectedAlbums: controlledSelectedAlbums,
  onSelectedAlbumsChange,
  selectedKeys: controlledSelectedKeys,
  onSelectedKeysChange,
  selectedBpm: controlledSelectedBpm,
  onSelectedBpmChange,
}: SongSearchProps) {
  const [internalQuery, setInternalQuery] = useState(initialQuery ?? "");
  const [internalSelectedAlbums, setInternalSelectedAlbums] = useState<string[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [internalSelectedKeys, setInternalSelectedKeys] = useState<string[]>([]);
  const [internalSelectedBpm, setInternalSelectedBpm] = useState<BpmBandKey | undefined>();
  const debounceTimerRef = useRef<NodeJS.Timeout | null>(null);
  const initialSearchTriggered = useRef(false);

  const query = controlledQuery ?? internalQuery;
  const selectedAlbums = controlledSelectedAlbums ?? internalSelectedAlbums;
  const selectedKeys = controlledSelectedKeys ?? internalSelectedKeys;
  const selectedBpm = controlledSelectedBpm ?? internalSelectedBpm;

  const setQuery = useCallback(
    (value: string) => {
      if (onQueryChange) onQueryChange(value);
      else setInternalQuery(value);
    },
    [onQueryChange]
  );

  const setSelectedAlbums = useCallback(
    (value: string[]) => {
      if (onSelectedAlbumsChange) onSelectedAlbumsChange(value);
      else setInternalSelectedAlbums(value);
    },
    [onSelectedAlbumsChange]
  );

  const setSelectedKeys = useCallback(
    (updater: string[] | ((prev: string[]) => string[])) => {
      const next = typeof updater === "function" ? updater(selectedKeys) : updater;
      if (onSelectedKeysChange) onSelectedKeysChange(next);
      else setInternalSelectedKeys(next);
    },
    [onSelectedKeysChange, selectedKeys]
  );

  const setSelectedBpm = useCallback(
    (
      updater:
        | BpmBandKey
        | undefined
        | ((prev: BpmBandKey | undefined) => BpmBandKey | undefined)
    ) => {
      const next = typeof updater === "function" ? updater(selectedBpm) : updater;
      if (onSelectedBpmChange) onSelectedBpmChange(next);
      else setInternalSelectedBpm(next);
    },
    [onSelectedBpmChange, selectedBpm]
  );

  const hasAdvancedFilters =
    selectedAlbums.length > 0 || selectedKeys.length > 0 || selectedBpm !== undefined;

  const triggerSearch = useCallback(
    (searchQuery: string, albumFilters: string[] = selectedAlbums) => {
      const normalizedAlbums = albumFilters.length > 0 ? albumFilters : undefined;
      if (showAdvanced && hasAdvancedFilters && onAdvancedSearch) {
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
      showAdvanced,
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

  // Handle album filter change
  const handleAlbumChange = useCallback(
    (value: string[]) => {
      setSelectedAlbums(value);
      setIsSearching(true);
      debouncedSearch(query, value);
    },
    [debouncedSearch, query, setSelectedAlbums]
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

  const toggleKey = useCallback((key: string) => {
    setSelectedKeys((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    );
  }, [setSelectedKeys]);

  const toggleBpm = useCallback((band: BpmBandKey) => {
    setSelectedBpm((prev) => (prev === band ? undefined : band));
  }, [setSelectedBpm]);

  const handleApplyFilters = useCallback(() => {
    setIsSearching(true);
    triggerSearch(query, selectedAlbums);
    setIsSearching(false);
  }, [triggerSearch, query, selectedAlbums]);

  const handleClearFilters = useCallback(() => {
    setSelectedAlbums([]);
    setSelectedKeys([]);
    setSelectedBpm(undefined);
    setIsSearching(true);
    onSearch(query, undefined);
    setIsSearching(false);
  }, [query, onSearch, setSelectedAlbums, setSelectedBpm, setSelectedKeys]);

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

      {/* Album filter */}
      {albums.length > 0 && (
        <AlbumMultiSelect
          albums={albums}
          selectedAlbums={selectedAlbums}
          onSelectedAlbumsChange={handleAlbumChange}
          disabled={isLoading}
        />
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
              {selectedAlbums.length + selectedKeys.length + (selectedBpm ? 1 : 0)}
            </Badge>
          )}
        </Button>
      )}

      {/* Collapsible advanced filters panel */}
      {showAdvanced && onAdvancedSearch && (
        <div
          id="advanced-filters-panel"
          className="border rounded-md p-2.5 space-y-3"
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
