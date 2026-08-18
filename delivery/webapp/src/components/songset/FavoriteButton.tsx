"use client";

import { useState, useEffect } from "react";
import { Heart } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { isSongCompleted, subscribeCompletion } from "@/lib/audio/completion";
import { COMPLETION_THRESHOLD } from "@/lib/constants";

interface FavoriteButtonProps {
  songId: string;
  isFavorite: boolean;
  onToggle?: (songId: string) => void | Promise<void>;
  className?: string;
}

/**
 * Heart toggle for a song. Favoriting requires the song to be Completed (heard
 * ≥90% (COMPLETION_THRESHOLD) of a full play, tracked client-side per ADR-0002); unfavoriting is
 * always allowed. Flips live the moment a song crosses the threshold.
 */
export function FavoriteButton({
  songId,
  isFavorite,
  onToggle,
  className,
}: FavoriteButtonProps) {
  const [completed, setCompleted] = useState<boolean>(() =>
    isSongCompleted(songId)
  );

  useEffect(() => {
    return subscribeCompletion(() => setCompleted(isSongCompleted(songId)));
  }, [songId]);

  const canFavorite = isFavorite || completed;
  const disabled = !onToggle || !canFavorite;

  const handleClick = () => {
    if (!onToggle || disabled) return;
    void onToggle(songId);
  };

  const button = (
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      className={cn(
        "shrink-0 text-muted-foreground hover:text-foreground",
        isFavorite && "text-red-500 hover:text-red-700",
        className
      )}
      onClick={handleClick}
      disabled={disabled}
      aria-label={isFavorite ? "Remove from favorites" : "Add to favorites"}
      aria-pressed={isFavorite}
      data-testid="favorite-button"
      data-favorite={isFavorite}
      data-eligible={completed}
    >
      <Heart className={cn("size-4", isFavorite && "fill-current")} />
    </Button>
  );

  if (!isFavorite && !completed) {
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            {/* Span keeps the tooltip reachable over the disabled button. */}
            <span className="inline-flex">{button}</span>
          </TooltipTrigger>
          <TooltipContent side="top">
            {`Listen to ${Math.round(COMPLETION_THRESHOLD * 100)}% of the song to favorite`}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  return button;
}
