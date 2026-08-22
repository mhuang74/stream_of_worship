"use client";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Music, Clock, Disc, Plus, Check, BadgeCheck, Play, Pause, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { useState } from "react";
import { FavoriteButton } from "./FavoriteButton";
import { ThemeLabel, toSongTheme } from "./ThemeLabel";
import { useLocale } from "@/hooks/useLocale";

export interface SongCardData {
  id: string;
  title: string;
  composer: string | null;
  lyricist: string | null;
  albumName: string | null;
  musicalKey: string | null;
  effectiveKey?: string | null;
  effectiveKeyStartRoot?: string | null;
  effectiveKeyEndRoot?: string | null;
  recordings: {
    contentHash: string;
    hashPrefix: string;
    durationSeconds: number | null;
    tempoBpm: number | null;
    musicalKey: string | null;
    effectiveKey?: string | null;
    effectiveKeyStartRoot?: string | null;
    effectiveKeyEndRoot?: string | null;
    visibilityStatus: string | null;
    theme: string | null;
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
  isFavorite?: boolean;
  onToggleFavorite?: (songId: string) => void | Promise<void>;
  favoriteCount?: number;
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
  isFavorite = false,
  onToggleFavorite,
  favoriteCount,
  className,
}: SongCardProps) {
  const { t } = useLocale();
  const [isHovered, setIsHovered] = useState(false);

  const formatDuration = (seconds?: number | null) => {
    if (!seconds) return "--:--";
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  // Prefer published recording as primary; fall back to first
  const publishedRecording = song.recordings.find(
    (r) => r.visibilityStatus === "published"
  );
  const primaryRecording = publishedRecording ?? song.recordings[0];
  const duration = primaryRecording?.durationSeconds;
  const tempo = primaryRecording?.tempoBpm;
  const primaryTheme = primaryRecording?.theme ? toSongTheme(primaryRecording.theme) : null;
  const effectiveKeyDisplay =
    primaryRecording?.effectiveKeyStartRoot &&
    primaryRecording?.effectiveKeyEndRoot &&
    primaryRecording?.effectiveKeyStartRoot !== primaryRecording?.effectiveKeyEndRoot
      ? `${primaryRecording?.effectiveKeyStartRoot} → ${primaryRecording?.effectiveKeyEndRoot}`
      : primaryRecording?.effectiveKey ?? song.effectiveKey;
  const recordingKey = effectiveKeyDisplay || primaryRecording?.musicalKey || song.musicalKey;
  const artist = song.composer || song.lyricist || t("browse.unknownArtist");
  const isVerified = song.recordings?.some(
    (r) => r.visibilityStatus === "published"
  ) ?? false;

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
      <CardContent className="p-2.5">
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
            aria-label={isPlaying ? t("browse.pausePreview") : t("browse.playPreview")}
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
                  aria-label={t("browse.verified")}
                />
              )}
              {favoriteCount != null && favoriteCount > 0 && (
                <span
                  className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[10px] text-muted-foreground inline-flex items-center gap-1"
                  data-testid="favorited-by-badge"
                >
                  {t("home.badge.favoritedBy").replace("${n}", String(favoriteCount))}
                </span>
              )}
            </h4>
            <div className="flex items-center gap-1 text-xs text-muted-foreground mt-0.5">
              <Music className="size-3" />
              <span className="truncate" data-testid="song-artist">
                {artist}
              </span>
              {song.albumName && (
                <span className="truncate hidden sm:inline" data-testid="song-album">
                  • {song.albumName}
                </span>
              )}
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
                <span data-testid="song-tempo">{Math.round(tempo)} {t("browse.bpm")}</span>
              )}
            </div>
            {primaryTheme && (
              <div className="mt-1">
                <ThemeLabel theme={primaryTheme} />
              </div>
            )}
          </div>

          {/* Favorite + Add buttons */}
          {(onToggleFavorite || onAdd) && (
            <div className="flex items-center gap-1 shrink-0">
              {onToggleFavorite && (
                <FavoriteButton
                  songId={song.id}
                  isFavorite={isFavorite}
                  onToggle={onToggleFavorite}
                />
              )}
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
                  aria-label={isAdded ? t("browse.alreadyAdded") : disabled ? t("browse.songsetFull") : t("browse.addToSongset")}
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
          )}
        </div>
      </CardContent>
    </Card>
  );
}
