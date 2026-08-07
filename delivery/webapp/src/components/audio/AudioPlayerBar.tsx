"use client";

import { useState, useEffect, useRef } from "react";
import { usePathname } from "next/navigation";
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
  AlignLeft,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { PlayerLyricsPanel } from "./PlayerLyricsPanel";
import { LyricsErrorBoundary } from "./LyricsErrorBoundary";
import { LocateSongsetsPopover } from "./LocateSongsetsPopover";

function LyricsErrorFallback() {
  return (
    <div className="px-3 lg:px-4 py-2">
      <p className="text-sm text-muted-foreground">Lyrics unavailable</p>
    </div>
  );
}

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

  const [showLyrics, setShowLyrics] = useState(false);
  const [isLyricsMounted, setIsLyricsMounted] = useState(false);
  const pathname = usePathname();

  // Ref to track currentTrack without re-attaching listener
  const currentTrackRef = useRef(currentTrack);
  useEffect(() => {
    currentTrackRef.current = currentTrack;
  }, [currentTrack]);

  // Auto-collapse on track change / stop
  useEffect(() => {
    if (!currentTrack?.recordingContentHash) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setShowLyrics(false);
    }
  }, [currentTrack?.recordingContentHash]);

  // Mount content when expanding; keep mounted during collapse transition
  useEffect(() => {
    if (showLyrics && currentTrack?.recordingContentHash) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setIsLyricsMounted(true);
    }
  }, [showLyrics, currentTrack?.recordingContentHash]);

  // Unmount content after collapse transition completes
  const handleTransitionEnd = () => {
    if (!showLyrics) {
      setIsLyricsMounted(false);
    }
  };

  // Keyboard shortcut ('L') — v6: modal-aware, stable listener, no-op preventDefault
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      const track = currentTrackRef.current;
      if (!track) return;

      const target = e.target as HTMLElement;
      if (
        target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.isContentEditable
      ) {
        return;
      }

      if (
        document.querySelector('[role="dialog"]') ||
        document.querySelector('[data-slot="sheet"]')
      ) {
        return;
      }

      if (e.key === "l" || e.key === "L") {
        if (track.recordingContentHash) {
          e.preventDefault();
          setShowLyrics((prev) => !prev);
        }
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);

  // Auto-collapse on route change
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setShowLyrics(false);
  }, [pathname]);

  // Auto-collapse on modal/sheet open
  useEffect(() => {
    const observer = new MutationObserver(() => {
      const modalOpen =
        document.querySelector('[role="dialog"]') ||
        document.querySelector('[data-slot="sheet"]');
      if (modalOpen) {
        setShowLyrics(false);
      }
    });

    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, []);

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
      {/* Animated lyrics panel — always in DOM for transition */}
      <div
        onTransitionEnd={handleTransitionEnd}
        className={cn(
          "overflow-hidden transition-[max-height,opacity] duration-300 ease-in-out border-t bg-background/95 backdrop-blur-sm",
          showLyrics && currentTrack?.recordingContentHash
            ? "max-h-[40dvh] md:max-h-[400px] opacity-100"
            : "max-h-0 opacity-0 border-t-0"
        )}
      >
        {isLyricsMounted && currentTrack?.recordingContentHash && (
          <div
            id="player-lyrics-panel"
            role="region"
            aria-label={`Lyrics for ${currentTrack.title}`}
            className="overflow-y-auto overscroll-y-contain h-full"
          >
            <LyricsErrorBoundary fallback={<LyricsErrorFallback />}>
              <PlayerLyricsPanel recordingContentHash={currentTrack.recordingContentHash} />
            </LyricsErrorBoundary>
          </div>
        )}
      </div>

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
          <LocateSongsetsPopover />
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

        {/* Lyrics toggle button */}
        {currentTrack.recordingContentHash && (
          <Button
            variant={showLyrics ? "secondary" : "ghost"}
            size="icon"
            className="size-8 lg:size-10 shrink-0"
            onClick={() => setShowLyrics((prev) => !prev)}
            aria-expanded={showLyrics}
            aria-controls="player-lyrics-panel"
            aria-label={showLyrics ? "Hide lyrics" : "Show lyrics"}
            data-testid="lyrics-toggle-button"
            title="Lyrics (L)"
          >
            <AlignLeft className="size-4 lg:size-5" />
          </Button>
        )}

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
