"use client";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { BpmBandKey } from "@/lib/constants";
import { AlbumMultiSelect } from "./AlbumMultiSelect";
import { MusicalKeyMultiSelect } from "./MusicalKeyMultiSelect";
import { BpmRangeMultiSelect } from "./BpmRangeMultiSelect";
import type { AlbumFilter, AlbumOption } from "@/lib/search/album-filter";

interface SharedFiltersProps {
  albums: AlbumOption[];
  selectedAlbums: AlbumFilter[];
  onSelectedAlbumsChange: (albums: AlbumFilter[]) => void;
  selectedKeys: string[];
  onSelectedKeysChange: (keys: string[]) => void;
  selectedBpm?: BpmBandKey[];
  onSelectedBpmChange: (bpm: BpmBandKey[]) => void;
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
  selectedBpm = [],
  onSelectedBpmChange,
  onClearFilters,
  isLoading = false,
  className,
}: SharedFiltersProps) {
  const hasAnyFilters =
    selectedAlbums.length > 0 || selectedKeys.length > 0 || selectedBpm.length > 0;

  return (
    <div className={cn("space-y-2", className)} data-testid="shared-filters">
      <div className="flex flex-wrap items-center gap-2">
        {albums.length > 0 && (
          <AlbumMultiSelect
            albums={albums}
            selectedAlbums={selectedAlbums}
            onSelectedAlbumsChange={onSelectedAlbumsChange}
            disabled={isLoading}
          />
        )}

        <MusicalKeyMultiSelect
          selectedKeys={selectedKeys}
          onSelectedKeysChange={onSelectedKeysChange}
          disabled={isLoading}
        />

        <BpmRangeMultiSelect
          selectedBpm={selectedBpm}
          onSelectedBpmChange={onSelectedBpmChange}
          disabled={isLoading}
        />

        {hasAnyFilters && (
          <Button
            variant="ghost"
            size="sm"
            className="h-auto py-0 text-sm"
            onClick={onClearFilters}
            disabled={!hasAnyFilters}
            data-testid="clear-all-filters"
          >
            Clear all
          </Button>
        )}
      </div>
    </div>
  );
}
