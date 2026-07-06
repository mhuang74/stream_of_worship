"use client";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ChevronDown, X } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  albumFilterKey,
  formatAlbumLabel,
  formatAlbumOptionLabel,
  type AlbumFilter,
  type AlbumOption,
} from "@/lib/search/album-filter";

interface AlbumMultiSelectProps {
  albums: AlbumOption[];
  selectedAlbums: AlbumFilter[];
  onSelectedAlbumsChange: (albums: AlbumFilter[]) => void;
  disabled?: boolean;
  className?: string;
}

export function AlbumMultiSelect({
  albums,
  selectedAlbums,
  onSelectedAlbumsChange,
  disabled = false,
  className,
}: AlbumMultiSelectProps) {
  const selectedSet = new Set(selectedAlbums.map(albumFilterKey));

  const toggleAlbum = (album: AlbumOption) => {
    const key = albumFilterKey(album);
    if (selectedSet.has(key)) {
      onSelectedAlbumsChange(selectedAlbums.filter((selected) => albumFilterKey(selected) !== key));
    } else {
      onSelectedAlbumsChange([
        ...selectedAlbums,
        { albumName: album.albumName, albumSeries: album.albumSeries },
      ]);
    }
  };

  const clearAlbums = () => onSelectedAlbumsChange([]);
  const triggerValue =
    selectedAlbums.length === 0
      ? `All ${albums.length}`
      : selectedAlbums.length === 1
        ? formatAlbumLabel(selectedAlbums[0])
        : `${selectedAlbums.length} Selected`;

  return (
    <div className={cn("space-y-2", className)} data-testid="album-multi-select">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="link"
            size="sm"
            className="h-auto px-0 py-0 text-sm font-medium underline-offset-4"
            disabled={disabled || albums.length === 0}
            data-testid="album-filter"
          >
            <span className="max-w-[18rem] truncate whitespace-nowrap">
              <span className="font-medium">Albums:</span>{" "}
              <span className="text-muted-foreground">{triggerValue}</span>
            </span>
            <ChevronDown className="size-3 text-muted-foreground/60" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent className="w-72 max-h-80">
          <DropdownMenuGroup>
            <DropdownMenuLabel>Albums</DropdownMenuLabel>
            {selectedAlbums.length > 0 && (
              <>
                <DropdownMenuItem onClick={clearAlbums} data-testid="album-clear-all">
                  <X className="size-3.5" />
                  Clear all
                </DropdownMenuItem>
                <DropdownMenuSeparator />
              </>
            )}
            {albums.map((album) => {
              const key = albumFilterKey(album);
              return (
              <DropdownMenuCheckboxItem
                key={key}
                checked={selectedSet.has(key)}
                onCheckedChange={() => toggleAlbum(album)}
                data-testid={`album-option-${encodeURIComponent(key)}`}
              >
                <span className="truncate">{formatAlbumOptionLabel(album)}</span>
              </DropdownMenuCheckboxItem>
              );
            })}
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}
