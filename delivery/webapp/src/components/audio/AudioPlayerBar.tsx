"use client";

import { useAudioPlayer } from "@/hooks/useAudioPlayer";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import {
  Play,
  Pause,
  SkipBack,
  SkipForward,
  Volume2,
  VolumeX,
  Repeat,
  X,
  Music,
} from "lucide-react";
import { cn } from "@/lib/utils";

export function AudioPlayerBar() {
  const {
    currentTrack,
    isPlaying,
    duration,
    volume,
    isMuted,
    isLooping,
    formattedCurrentTime,
    formattedDuration,
    progress,
    togglePlay,
    seek,
    setVolume,
    toggleMute,
    toggleLoop,
    stop,
    seekRelative,
  } = useAudioPlayer();

  // Don't show if no track is loaded
  if (!currentTrack) {
    return null;
  }

  const handleSeek = (value: number | readonly number[]) => {
    const v = Array.isArray(value) ? value[0] : value;
    const newTime = (v / 100) * duration;
    seek(newTime);
  };

  const handleVolumeChange = (value: number | readonly number[]) => {
    const v = Array.isArray(value) ? value[0] : value;
    setVolume(v / 100);
  };

  return (
    <div
      className={cn(
        "fixed bottom-0 left-0 right-0 z-[60]",
        "bg-background/95 backdrop-blur-sm border-t"
      )}
      data-testid="audio-player-bar"
    >
      {/* Seek bar (full width, taller for touch) */}
      <div className="w-full px-3 pt-2 lg:pt-3">
        <Slider
          value={[progress]}
          min={0}
          max={100}
          onValueChange={handleSeek}
          className="w-full h-6 [&_[data-slot=slider-track]]:h-1.5 [&_[data-slot=slider-track]]:hover:h-2 [&_[data-slot=slider-track]]:transition-all [&_[data-slot=slider-thumb]]:size-3.5 [&_[data-slot=slider-thumb]]:hover:size-4 [&_[data-slot=slider-thumb]]:transition-all"
          data-testid="seek-slider"
        />
      </div>

      <div className="flex items-center gap-2 px-3 pb-2 pt-1 lg:px-4 lg:pb-3">
        {/* Track info */}
        <div className="flex items-center gap-3 min-w-0 flex-1 lg:flex-none">
          {/* Album art placeholder */}
          <div className="shrink-0 w-10 h-10 lg:w-12 lg:h-12 rounded-md bg-muted flex items-center justify-center">
            <Music className="size-5 lg:size-6 text-muted-foreground" />
          </div>

          {/* Title and artist */}
          <div className="min-w-0 flex-1">
            <p
              className="font-medium text-sm truncate"
              data-testid="track-title"
            >
              {currentTrack.title}
            </p>
            <p
              className="text-xs text-muted-foreground truncate"
              data-testid="track-artist"
            >
              {currentTrack.artist}
              {currentTrack.type === "transition" && (
                <span className="ml-1 text-xs text-primary">(Preview)</span>
              )}
              {currentTrack.type === "lyrics-loop" && (
                <span className="ml-1 text-xs text-primary">(Loop)</span>
              )}
            </p>
          </div>
        </div>

        {/* Controls - centered on desktop */}
        <div className="flex items-center justify-center gap-1 lg:gap-2 flex-1">
          {/* Skip back 10s */}
          <Button
            variant="ghost"
            size="icon"
            className="size-8 lg:size-10 shrink-0"
            onClick={() => seekRelative(-10)}
            aria-label="Skip back 10 seconds"
            data-testid="skip-back-button"
          >
            <SkipBack className="size-4 lg:size-5" />
          </Button>

          {/* Play/Pause */}
          <Button
            variant="default"
            size="icon"
            className="size-10 lg:size-12 shrink-0 rounded-full"
            onClick={togglePlay}
            aria-label={isPlaying ? "Pause" : "Play"}
            data-testid="play-pause-button"
          >
            {isPlaying ? (
              <Pause className="size-5 lg:size-6" />
            ) : (
              <Play className="size-5 lg:size-6 ml-0.5" />
            )}
          </Button>

          {/* Skip forward 10s */}
          <Button
            variant="ghost"
            size="icon"
            className="size-8 lg:size-10 shrink-0"
            onClick={() => seekRelative(10)}
            aria-label="Skip forward 10 seconds"
            data-testid="skip-forward-button"
          >
            <SkipForward className="size-4 lg:size-5" />
          </Button>

          {/* Loop toggle (only for lyrics-loop type) */}
          {currentTrack.type === "lyrics-loop" && (
            <Button
              variant={isLooping ? "secondary" : "ghost"}
              size="icon"
              className="size-8 lg:size-10 shrink-0"
              onClick={toggleLoop}
              aria-label={isLooping ? "Disable loop" : "Enable loop"}
              data-testid="loop-toggle-button"
            >
              <Repeat
                className={cn(
                  "size-4 lg:size-5",
                  isLooping && "text-primary"
                )}
              />
            </Button>
          )}
        </div>

        {/* Time display and volume */}
        <div className="flex items-center gap-2 lg:gap-4 min-w-0 flex-1 lg:flex-none justify-end">
          {/* Time - visible on all screen sizes */}
          <span
            className="text-xs text-muted-foreground tabular-nums whitespace-nowrap"
            data-testid="time-display"
          >
            {formattedCurrentTime} / {formattedDuration}
          </span>

          {/* Volume - mute toggle on mobile, full slider on desktop */}
          <Button
            variant="ghost"
            size="icon"
            className="size-8 shrink-0 lg:hidden"
            onClick={toggleMute}
            aria-label={isMuted ? "Unmute" : "Mute"}
            data-testid="mute-button-mobile"
          >
            {isMuted || volume === 0 ? (
              <VolumeX className="size-4" />
            ) : (
              <Volume2 className="size-4" />
            )}
          </Button>

          {/* Volume slider - desktop only */}
          <div className="hidden lg:flex items-center gap-2">
            <Button
              variant="ghost"
              size="icon"
              className="size-8 shrink-0"
              onClick={toggleMute}
              aria-label={isMuted ? "Unmute" : "Mute"}
              data-testid="mute-button"
            >
              {isMuted || volume === 0 ? (
                <VolumeX className="size-4" />
              ) : (
                <Volume2 className="size-4" />
              )}
            </Button>
            <div className="w-20">
              <Slider
                value={[isMuted ? 0 : volume * 100]}
                min={0}
                max={100}
                onValueChange={handleVolumeChange}
                data-testid="volume-slider"
              />
            </div>
          </div>
        </div>

        {/* Close button */}
        <Button
          variant="ghost"
          size="icon"
          className="size-8 lg:size-10 shrink-0"
          onClick={stop}
          aria-label="Close player"
          data-testid="close-player-button"
        >
          <X className="size-4 lg:size-5" />
        </Button>
      </div>
    </div>
  );
}
