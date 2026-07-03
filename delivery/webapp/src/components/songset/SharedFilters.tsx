"use client";

import { useState, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";
import { SlidersHorizontal } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  PITCH_CLASSES,
  BPM_BANDS,
  BPM_BAND_KEYS,
  type BpmBandKey,
} from "@/lib/constants";
import { AlbumMultiSelect } from "./AlbumMultiSelect";
import type { AlbumFilter, AlbumOption } from "@/lib/search/album-filter";

interface SharedFiltersProps {
  albums: AlbumOption[];
  selectedAlbums: AlbumFilter[];
  onSelectedAlbumsChange: (albums: AlbumFilter[]) => void;
  selectedKeys: string[];
  onSelectedKeysChange: (keys: string[]) => void;
  selectedBpm?: BpmBandKey;
  onSelectedBpmChange: (bpm: BpmBandKey | undefined) => void;
  onClearFilters: () => void;
  isLoading?: boolean;
  className?: string;
}

export function SharedFilters({
  albums,
  selectedAlbums,
  onSelectedAlbumsChange,
  selectedKeys,
  onSelectedKeysChange,
  selectedBpm,
  onSelectedBpmChange,
  onClearFilters,
  isLoading = false,
  className,
}: SharedFiltersProps) {
  const [showAdvanced, setShowAdvanced] = useState(false);

  const advancedFilterCount = selectedKeys.length + (selectedBpm ? 1 : 0);
  const hasAdvancedFilters = advancedFilterCount > 0;

  const toggleKey = useCallback(
    (key: string) => {
      onSelectedKeysChange(
        selectedKeys.includes(key)
          ? selectedKeys.filter((k) => k !== key)
          : [...selectedKeys, key]
      );
    },
    [onSelectedKeysChange, selectedKeys]
  );

  const toggleBpm = useCallback(
    (band: BpmBandKey) => {
      onSelectedBpmChange(selectedBpm === band ? undefined : band);
    },
    [onSelectedBpmChange, selectedBpm]
  );

  return (
    <div className={cn("space-y-2", className)} data-testid="shared-filters">
      {/* Album filter */}
      {albums.length > 0 && (
        <AlbumMultiSelect
          albums={albums}
          selectedAlbums={selectedAlbums}
          onSelectedAlbumsChange={onSelectedAlbumsChange}
          disabled={isLoading}
        />
      )}

      {/* Advanced filters toggle */}
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
            {advancedFilterCount}
          </Badge>
        )}
      </Button>

      {/* Collapsible advanced filters panel */}
      {showAdvanced && (
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
              variant="ghost"
              size="sm"
              onClick={onClearFilters}
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
