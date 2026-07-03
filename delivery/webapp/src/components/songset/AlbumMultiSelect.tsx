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
import { Badge } from "@/components/ui/badge";
import { ChevronDown, X } from "lucide-react";
import { cn } from "@/lib/utils";

interface AlbumMultiSelectProps {
  albums: string[];
  selectedAlbums: string[];
  onSelectedAlbumsChange: (albums: string[]) => void;
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
  const selectedSet = new Set(selectedAlbums);

  const toggleAlbum = (album: string) => {
    if (selectedSet.has(album)) {
      onSelectedAlbumsChange(selectedAlbums.filter((name) => name !== album));
    } else {
      onSelectedAlbumsChange([...selectedAlbums, album]);
    }
  };

  const clearAlbums = () => onSelectedAlbumsChange([]);
  const summary = selectedAlbums.slice(0, 2).join(", ");
  const overflowCount = Math.max(0, selectedAlbums.length - 2);

  return (
    <div className={cn("space-y-2", className)} data-testid="album-multi-select">
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-9 justify-between gap-2"
            disabled={disabled || albums.length === 0}
            data-testid="album-filter"
          >
            <span>Albums</span>
            {selectedAlbums.length > 0 && (
              <Badge variant="secondary" className="px-1.5 py-0 text-[10px]">
                {selectedAlbums.length}
              </Badge>
            )}
            <ChevronDown className="size-3.5 text-muted-foreground" />
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
            {albums.map((album) => (
              <DropdownMenuCheckboxItem
                key={album}
                checked={selectedSet.has(album)}
                onCheckedChange={() => toggleAlbum(album)}
                data-testid={`album-option-${album}`}
              >
                <span className="truncate">{album}</span>
              </DropdownMenuCheckboxItem>
            ))}
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>

      {selectedAlbums.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
          <span className="truncate max-w-[18rem]">{summary}</span>
          {overflowCount > 0 && <span>+{overflowCount} more</span>}
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            className="size-6"
            onClick={clearAlbums}
            aria-label="Clear selected albums"
            data-testid="album-summary-clear"
          >
            <X className="size-3.5" />
          </Button>
        </div>
      )}
    </div>
  );
}
