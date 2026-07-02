"use client";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Music, Clock, Disc, Plus, Check, BadgeCheck, Play, Pause, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useState } from "react";

export interface SongCardData {
  id: string;
  title: string;
  composer: string | null;
  lyricist: string | null;
  albumName: string | null;
  musicalKey: string | null;
  recordings: {
    contentHash: string;
    hashPrefix: string;
    durationSeconds: number | null;
    tempoBpm: number | null;
    musicalKey: string | null;
    visibilityStatus: string | null;
  }[];
}

interface SongCardProps {
  song: SongCardData;
  onAdd?: (songId: string) => void | Promise<void>;
  onPlay?: (songId: string) => void;
  isAdded?: boolean;
  isAdding?: boolean;
  isPlaying?: boolean;
  isPreviewLoading?: boolean;
  disabled?: boolean;
  className?: string;
}

export function SongCard({
  song,
  onAdd,
  onPlay,
  isAdded = false,
  isAdding = false,
  isPlaying = false,
  isPreviewLoading = false,
  disabled = false,
  className,
}: SongCardProps) {
  const [isHovered, setIsHovered] = useState(false);

  const formatDuration = (seconds?: number | null) => {
    if (!seconds) return "--:--";
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  // Get the first recording for display
  const primaryRecording = song.recordings[0];
  const duration = primaryRecording?.durationSeconds;
  const tempo = primaryRecording?.tempoBpm;
  const recordingKey = primaryRecording?.musicalKey || song.musicalKey;
  const artist = song.composer || song.lyricist || "Unknown Artist";
  const isVerified = song.recordings.some(
    (r) => r.visibilityStatus === "published"
  );

  const handleAdd = async () => {
    if (isAdded || isAdding || disabled || !onAdd) return;
    await onAdd(song.id);
  };

  return (
    <Card
      className={cn(
        "border-border/50 hover:border-border transition-colors",
        className
      )}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      data-testid="song-card"
    >
      <CardContent className="p-3">
        <div className="flex items-start gap-3">
          {/* Album art placeholder */}
          <div
            className={cn(
              "shrink-0 w-12 h-12 rounded-md bg-muted flex items-center justify-center relative",
              onPlay && "cursor-pointer hover:bg-muted/80 transition-colors",
              isPlaying && "bg-primary/10"
            )}
            onClick={onPlay ? () => onPlay(song.id) : undefined}
            data-testid={onPlay ? "song-play-button" : "song-art-placeholder"}
            aria-label={isPlaying ? "Pause preview" : "Play preview"}
            role={onPlay ? "button" : undefined}
          >
            {isPreviewLoading ? (
              <Loader2 className="size-6 animate-spin text-muted-foreground" />
            ) : isPlaying ? (
              <Pause className="size-6 text-primary" />
            ) : isHovered && onPlay ? (
              <Play className="size-6 text-primary ml-0.5" />
            ) : (
              <Disc className="size-6 text-muted-foreground" />
            )}
          </div>

          {/* Song info */}
          <div className="flex-1 min-w-0">
            <h4 className="font-medium text-sm truncate flex items-center gap-1" data-testid="song-title">
              <span className="truncate">{song.title}</span>
              {isVerified && (
                <BadgeCheck
                  className="size-3.5 text-emerald-600 shrink-0"
                  data-testid="verified-badge"
                  aria-label="Verified"
                />
              )}
            </h4>
            <div className="flex items-center gap-1 text-xs text-muted-foreground mt-0.5">
              <Music className="size-3" />
              <span className="truncate" data-testid="song-artist">
                {artist}
              </span>
            </div>
            <div className="flex items-center gap-2 text-xs text-muted-foreground mt-1">
              {duration && (
                <span className="flex items-center gap-1" data-testid="song-duration">
                  <Clock className="size-3" />
                  {formatDuration(duration)}
                </span>
              )}
              {recordingKey && (
                <Badge variant="outline" className="text-xs px-1.5 py-0" data-testid="song-key">
                  {recordingKey}
                </Badge>
              )}
              {tempo && (
                <span data-testid="song-tempo">{Math.round(tempo)} BPM</span>
              )}
              {song.albumName && (
                <span className="truncate hidden sm:inline" data-testid="song-album">
                  • {song.albumName}
                </span>
              )}
            </div>
          </div>

          {/* Add button */}
          {onAdd && (
            <Button
              variant={isAdded ? "ghost" : "outline"}
              size="icon-sm"
              className={cn(
                "shrink-0 transition-opacity",
                !isHovered && !isAdded && "opacity-0 sm:opacity-100"
              )}
              onClick={handleAdd}
              disabled={isAdded || isAdding || disabled}
              aria-label={isAdded ? "Already added" : disabled ? "Songset full" : "Add to songset"}
              data-testid="add-song-button"
            >
              {isAdding ? (
                <span className="size-4 animate-spin border-2 border-current border-t-transparent rounded-full" />
              ) : isAdded ? (
                <Check className="size-4 text-green-500" />
              ) : (
                <Plus className="size-4" />
              )}
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
