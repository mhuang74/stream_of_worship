"use client";

import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { CatalogSearch } from "@/components/search/CatalogSearch";
import type { SongCardData } from "./SongCard";
import { cn } from "@/lib/utils";
import { useAudioPlayerContext } from "@/contexts/AudioPlayerContext";
import { useLocale } from "@/hooks/useLocale";
import { SONGSET_MAX_SONGS } from "@/lib/constants";

interface BrowseSheetProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  onAddSong: (song: SongCardData) => Promise<void>;
  existingSongIds?: string[];
  itemCount?: number;
  className?: string;
}

export function BrowseSheet({
  isOpen,
  onOpenChange,
  onAddSong,
  existingSongIds = [],
  itemCount = 0,
  className,
}: BrowseSheetProps) {
  const { t } = useLocale();
  const { currentTrack } = useAudioPlayerContext();

  const isSongsetFull = itemCount >= SONGSET_MAX_SONGS;

  return (
    <Sheet open={isOpen} onOpenChange={onOpenChange}>
      <SheetContent side="bottom" className={cn("data-[side=bottom]:!h-[85vh] sm:data-[side=bottom]:!h-[90vh] overflow-hidden", className)}>
        <SheetHeader className="pb-2">
          <SheetTitle>{t("browse.sheet.title")}</SheetTitle>
          <SheetDescription>{t("browse.sheet.description")}</SheetDescription>
        </SheetHeader>

        <div className={cn("flex flex-col h-full min-h-0", currentTrack ? "pb-28 sm:pb-20" : "pb-8")}>
          <CatalogSearch
            mode="browse"
            onAddSong={onAddSong}
            existingSongIds={existingSongIds}
            isSongsetFull={isSongsetFull}
            songNotFoundMessage={t("browse.songNotFound")}
            songAddedMessage={t("browse.songAddedToSongset")}
            failedToAddMessage={t("browse.failedToAddSong")}
            unknownArtistMessage={t("browse.unknownArtist")}
            noAudioMessage={t("browse.noAudioAvailable")}
            failedToLoadMessage={t("browse.failedToLoadPreview")}
            className="flex-1"
          />

          {/* Footer */}
          <div className="pt-4 border-t mt-4">
            <div className="flex items-center justify-between">
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                {t("browse.done")}
              </Button>
            </div>
          </div>
        </div>
      </SheetContent>
    </Sheet>
  );
}
