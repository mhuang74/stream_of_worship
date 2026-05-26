"use client";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  Play,
  Pause,
  SkipBack,
  SkipForward,
  Volume2,
  VolumeX,
  Monitor,
} from "lucide-react";

export interface PlaybackControlsProps {
  isPlaying: boolean;
  currentTime: number;
  duration: number;
  volume: number;
  isMuted: boolean;
  currentSongIndex: number;
  totalSongs: number;
  isPresentationActive: boolean;
  onPlayPause: () => void;
  onSeek: (time: number) => void;
  onPrevSong: () => void;
  onNextSong: () => void;
  onVolumeChange: (volume: number) => void;
  onToggleMute: () => void;
  className?: string;
}

export function PlaybackControls({
  isPlaying,
  currentTime,
  duration,
  volume,
  isMuted,
  currentSongIndex,
  totalSongs,
  isPresentationActive,
  onPlayPause,
  onSeek,
  onPrevSong,
  onNextSong,
  onVolumeChange,
  onToggleMute,
  className,
}: PlaybackControlsProps) {
  const formatTime = (seconds: number): string => {
    if (!isFinite(seconds) || seconds < 0) return "0:00";
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  const progress = duration > 0 ? (currentTime / duration) * 100 : 0;

  const handleScrubClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const clickPosition = (e.clientX - rect.left) / rect.width;
    const newTime = Math.max(0, Math.min(duration, clickPosition * duration));
    onSeek(newTime);
  };

  return (
    <div
      className={cn(
        "flex flex-col gap-4 p-4 bg-gradient-to-t from-black/80 via-black/50 to-transparent",
        className
      )}
    >
      {/* Progress bar */}
      <div className="space-y-1">
        {/* Mobile: thin read-only progress bar */}
        <div className="md:hidden h-0.5 bg-white/20 rounded-full overflow-hidden">
          <div
            className="h-full bg-primary transition-all duration-100"
            style={{ width: `${progress}%` }}
          />
        </div>

        {/* Desktop: interactive scrub bar */}
        <div
          className="hidden md:block relative h-2 bg-white/20 rounded-full cursor-pointer touch-none group"
          onClick={handleScrubClick}
          role="slider"
          aria-label="Seek"
          aria-valuemin={0}
          aria-valuemax={duration}
          aria-valuenow={currentTime}
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "ArrowLeft") {
              onSeek(Math.max(0, currentTime - 10));
            } else if (e.key === "ArrowRight") {
              onSeek(Math.min(duration, currentTime + 10));
            }
          }}
        >
          <div
            className="absolute h-full bg-primary rounded-full"
            style={{ width: `${progress}%` }}
          />
          <div
            className="absolute top-1/2 -translate-y-1/2 w-4 h-4 bg-white rounded-full shadow-lg opacity-0 group-hover:opacity-100 transition-opacity"
            style={{ left: `calc(${progress}% - 8px)` }}
          />
        </div>

        {/* Time display - all screen sizes */}
        <div className="flex justify-between text-xs text-white/60 mt-1">
          <span>{formatTime(currentTime)}</span>
          <span>{formatTime(duration)}</span>
        </div>
      </div>

      {/* Main controls */}
      <div className="flex items-center justify-between gap-1 sm:gap-4">
        {/* Song navigation */}
        <div className="flex items-center gap-1 sm:gap-2">
          <Button
            variant="ghost"
            size="icon"
            className="size-10 sm:size-12 text-white hover:bg-white/20"
            onClick={onPrevSong}
            disabled={currentSongIndex <= 0}
            aria-label="Previous song"
          >
            <SkipBack className="size-5 sm:size-6" />
          </Button>
          <span className="text-xs sm:text-sm text-white/70 min-w-[2.5rem] sm:min-w-[3rem] text-center">
            {currentSongIndex + 1}/{totalSongs}
          </span>
          <Button
            variant="ghost"
            size="icon"
            className="size-10 sm:size-12 text-white hover:bg-white/20"
            onClick={onNextSong}
            disabled={currentSongIndex >= totalSongs - 1}
            aria-label="Next song"
          >
            <SkipForward className="size-5 sm:size-6" />
          </Button>
        </div>

        {/* Play/Pause */}
        <div className="flex items-center justify-center">
          <Button
            variant="default"
            size="icon"
            className="size-14 sm:size-16 rounded-full bg-white text-black hover:bg-white/90"
            onClick={onPlayPause}
            aria-label={isPlaying ? "Pause" : "Play"}
          >
            {isPlaying ? (
              <Pause className="size-7 sm:size-8" />
            ) : (
              <Play className="size-7 sm:size-8 ml-1" />
            )}
          </Button>
        </div>

        {/* Volume and presentation status */}
        <div className="flex items-center gap-1 sm:gap-2">
          <Button
            variant="ghost"
            size="icon"
            className="hidden md:flex size-10 text-white hover:bg-white/20"
            onClick={onToggleMute}
            aria-label={isMuted ? "Unmute" : "Mute"}
          >
            {isMuted || volume === 0 ? (
              <VolumeX className="size-5" />
            ) : (
              <Volume2 className="size-5" />
            )}
          </Button>

          {/* Volume slider */}
          <div className="w-20 hidden md:block">
            <input
              type="range"
              min={0}
              max={1}
              step={0.01}
              value={isMuted ? 0 : volume}
              onChange={(e) => onVolumeChange(parseFloat(e.target.value))}
              className="w-full h-1 bg-white/30 rounded-full appearance-none cursor-pointer [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:w-3 [&::-webkit-slider-thumb]:h-3 [&::-webkit-slider-thumb]:bg-white [&::-webkit-slider-thumb]:rounded-full"
              aria-label="Volume"
            />
          </div>

          {/* Presentation status indicator */}
          {isPresentationActive && (
            <div className="flex items-center gap-1 px-2 py-1 bg-green-500/20 text-green-400 rounded-full text-xs">
              <Monitor className="size-3" />
              <span className="hidden sm:inline">Connected</span>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
