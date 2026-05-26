"use client";

import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { PlaybackControls } from "./PlaybackControls";
import { LyricJumpList } from "./LyricJumpList";
import type { Chapter } from "@/lib/render/chapters";
import { useWakeLock } from "@/hooks/useWakeLock";
import { useKeyboardShortcuts } from "@/hooks/useKeyboardShortcuts";
import { useMediaSession } from "@/hooks/useMediaSession";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { ArrowLeft, X, Info, Maximize } from "lucide-react";

export interface ControllerPlayerProps {
  songsetId: string;
  videoSrc: string;
  chapters: Chapter[];
  isPresentationActive?: boolean;
  onPresentationConnect?: () => void;
  onPresentationDisconnect?: () => void;
  className?: string;
}

const IOS_INFO_KEY = "sow-ios-info-shown";

export function ControllerPlayer({
  songsetId,
  videoSrc,
  chapters,
  isPresentationActive = false,
  className,
}: ControllerPlayerProps) {
  const router = useRouter();
  const videoRef = useRef<HTMLVideoElement>(null);
  const controlsRef = useRef<HTMLDivElement>(null);
  const hideTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(1);
  const [isMuted, setIsMuted] = useState(false);
  const [currentSongIndex, setCurrentSongIndex] = useState(0);
  const [controlsVisible, setControlsVisible] = useState(true);
  const [showIosInfo, setShowIosInfo] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);

  // Wake lock hook
  const { isSupported: wakeLockSupported } = useWakeLock();

  // Check if iOS and if info toast was already shown
  useEffect(() => {
    if (typeof navigator === "undefined") return;

    const isIOS =
      /iPad|iPhone|iPod/.test(navigator.userAgent) && !(window as unknown as { MSStream: boolean }).MSStream;
    const infoShown = sessionStorage.getItem(IOS_INFO_KEY);

    if (isIOS && !isPresentationActive && !infoShown) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setShowIosInfo(true);
      sessionStorage.setItem(IOS_INFO_KEY, "true");
    }
  }, [isPresentationActive]);

  // Video event handlers
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    const handleTimeUpdate = () => {
      setCurrentTime(video.currentTime);

      // Update current song index based on time
      const newIndex = chapters.findIndex(
        (chapter, i) =>
          video.currentTime >= chapter.startSeconds &&
          (i === chapters.length - 1 ||
            video.currentTime < chapters[i + 1].startSeconds)
      );
      if (newIndex !== -1 && newIndex !== currentSongIndex) {
        setCurrentSongIndex(newIndex);
      }
    };

    const handleLoadedMetadata = () => {
      setDuration(video.duration);
    };

    const handlePlay = () => setIsPlaying(true);
    const handlePause = () => setIsPlaying(false);
    const handleVolumeChange = () => {
      setVolume(video.volume);
      setIsMuted(video.muted);
    };

    video.addEventListener("timeupdate", handleTimeUpdate);
    video.addEventListener("loadedmetadata", handleLoadedMetadata);
    video.addEventListener("play", handlePlay);
    video.addEventListener("pause", handlePause);
    video.addEventListener("volumechange", handleVolumeChange);

    return () => {
      video.removeEventListener("timeupdate", handleTimeUpdate);
      video.removeEventListener("loadedmetadata", handleLoadedMetadata);
      video.removeEventListener("play", handlePlay);
      video.removeEventListener("pause", handlePause);
      video.removeEventListener("volumechange", handleVolumeChange);
    };
  }, [chapters, currentSongIndex]);

  // Auto-hide controls in mirror mode
  const startHideTimer = useCallback(() => {
    if (isPresentationActive) return; // Don't auto-hide when presentation is active

    if (hideTimeoutRef.current) {
      clearTimeout(hideTimeoutRef.current);
    }

    hideTimeoutRef.current = setTimeout(() => {
      if (isPlaying) {
        setControlsVisible(false);
      }
    }, 2000);
  }, [isPresentationActive, isPlaying]);

  const showControls = useCallback(() => {
    setControlsVisible(true);
    startHideTimer();
  }, [startHideTimer]);

  const showControlsRef = useRef(showControls);
  useEffect(() => {
    showControlsRef.current = showControls;
  }, [showControls]);

  // Handle user interaction
  const handleInteraction = useCallback(() => {
    showControls();
  }, [showControls]);

  // Clear timer on unmount
  useEffect(() => {
    return () => {
      if (hideTimeoutRef.current) {
        clearTimeout(hideTimeoutRef.current);
      }
    };
  }, [showControls]);

  // Start hide timer when playing
  useEffect(() => {
    if (isPlaying && !isPresentationActive) {
      startHideTimer();
    }
  }, [isPlaying, isPresentationActive, startHideTimer]);

  // Control handlers
  const handlePlayPause = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;

    if (isPlaying) {
      video.pause();
      setIsPlaying(false);
    } else {
      setIsPlaying(true);
      video.play().catch((err) => {
        setIsPlaying(false);
        console.error("Play failed:", err);
        toast.error("Failed to start playback");
      });
    }
  }, [isPlaying]);

  const handleSeek = useCallback(
    (time: number) => {
      const video = videoRef.current;
      if (!video) return;

      if (!isFinite(time)) {
        console.warn("handleSeek called with non-finite time:", time);
        return;
      }

      const clampedTime = Math.max(0, Math.min(duration, time));
      video.currentTime = clampedTime;
      setCurrentTime(clampedTime);
    },
    [duration]
  );

  const handleSkipBack = useCallback(() => {
    handleSeek(currentTime - 10);
  }, [currentTime, handleSeek]);

  const handleSkipForward = useCallback(() => {
    handleSeek(currentTime + 10);
  }, [currentTime, handleSeek]);

  const handlePrevSong = useCallback(() => {
    if (currentSongIndex > 0) {
      const prevChapter = chapters[currentSongIndex - 1];
      if (prevChapter) {
        handleSeek(prevChapter.startSeconds);
      }
    }
  }, [currentSongIndex, chapters, handleSeek]);

  const handleNextSong = useCallback(() => {
    if (currentSongIndex < chapters.length - 1) {
      const nextChapter = chapters[currentSongIndex + 1];
      if (nextChapter) {
        handleSeek(nextChapter.startSeconds);
      }
    }
  }, [currentSongIndex, chapters, handleSeek]);

  const handleVolumeChange = useCallback((newVolume: number) => {
    const video = videoRef.current;
    if (!video) return;

    video.volume = newVolume;
    video.muted = newVolume === 0;
  }, []);

  const handleToggleMute = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;

    video.muted = !video.muted;
  }, []);

  const handleJumpToChapter = useCallback(
    (index: number) => {
      if (index >= 0 && index < chapters.length) {
        const chapter = chapters[index];
        if (chapter) {
          handleSeek(chapter.startSeconds);
        }
      }
    },
    [chapters, handleSeek]
  );

  const handleJumpToLine = useCallback(
    (chapterIndex: number, lineIndex: number) => {
      if (chapterIndex >= 0 && chapterIndex < chapters.length) {
        const chapter = chapters[chapterIndex];
        if (chapter && lineIndex >= 0 && lineIndex < chapter.lines.length) {
          const line = chapter.lines[lineIndex];
          if (line) {
            handleSeek(line.startSeconds);
          }
        }
      }
    },
    [chapters, handleSeek]
  );

  const handleExit = useCallback(() => {
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {
        // Ignore errors
      });
    }
    router.push(`/songsets/${songsetId}/play`);
  }, [router, songsetId]);

  const handleReenterFullscreen = useCallback(() => {
    document.documentElement.requestFullscreen().catch(() => {});
  }, []);

  // Keyboard shortcuts
  useKeyboardShortcuts({
    onTogglePlayback: handlePlayPause,
    onSeekBack: handleSkipBack,
    onSeekForward: handleSkipForward,
    onPrevSong: handlePrevSong,
    onNextSong: handleNextSong,
  });

  // Media Session API
  const currentChapter = chapters[currentSongIndex];
  const mediaSessionMetadata = useMemo(
    () =>
      currentChapter
        ? {
            title: currentChapter.songTitle,
            artist: "Stream of Worship",
            album: "Worship Set",
          }
        : null,
    [currentChapter]
  );

  const { updatePlaybackState, updatePositionState } = useMediaSession(
    mediaSessionMetadata,
    {
      onPlay: handlePlayPause,
      onPause: handlePlayPause,
      onPrevSong: handlePrevSong,
      onNextSong: handleNextSong,
      onSeekBack: handleSkipBack,
      onSeekForward: handleSkipForward,
    }
  );

  // Update media session playback state
  useEffect(() => {
    updatePlaybackState(isPlaying ? "playing" : "paused");
  }, [isPlaying, updatePlaybackState]);

  // Update media session position state
  useEffect(() => {
    if (duration > 0) {
      updatePositionState({
        duration,
        position: currentTime,
        playbackRate: 1,
      });
    }
  }, [duration, currentTime, updatePositionState]);

  useEffect(() => {
    const handleFullscreenChange = () => {
      setIsFullscreen(!!document.fullscreenElement);
    };

    document.addEventListener("fullscreenchange", handleFullscreenChange);

    return () => {
      document.removeEventListener("fullscreenchange", handleFullscreenChange);
    };
  }, []);

  useEffect(() => {
    const requestFullscreen = async () => {
      try {
        if (document.documentElement.requestFullscreen) {
          await document.documentElement.requestFullscreen();
        }
      } catch {
        // Fullscreen not supported or blocked
      }
    };

    requestFullscreen();

    const handleFullscreenChange = () => {
      if (!document.fullscreenElement) {
        showControlsRef.current();
      }
    };

    document.addEventListener("fullscreenchange", handleFullscreenChange);

    return () => {
      document.removeEventListener("fullscreenchange", handleFullscreenChange);
      if (document.fullscreenElement) {
        document.exitFullscreen().catch(() => {});
      }
    };
  }, []);

  // Mute video when presentation is active (audio plays on receiver)
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    if (isPresentationActive) {
      video.muted = true;
      video.setAttribute("muted", "");
    } else {
      video.muted = false;
      video.removeAttribute("muted");
    }
  }, [isPresentationActive]);

  return (
    <div
      className={cn(
        "fixed inset-0 z-[70] bg-black flex flex-col",
        className
      )}
      onClick={handleInteraction}
      onTouchStart={handleInteraction}
      onMouseMove={handleInteraction}
    >
      {/* Video */}
      <div className="flex-1 relative">
        <video
          ref={videoRef}
          src={videoSrc}
          className="w-full h-full object-contain"
          playsInline
          muted={isPresentationActive}
          onClick={(e) => {
            e.stopPropagation();
            handleInteraction();
          }}
          onDoubleClick={(e) => {
            e.preventDefault();
          }}
        />

        {!isFullscreen && (
          <Button
            variant="ghost"
            size="icon"
            className="fixed top-4 left-4 z-[80] size-10 text-white hover:bg-white/20"
            onClick={handleReenterFullscreen}
            aria-label="Re-enter fullscreen"
          >
            <Maximize className="size-5" />
          </Button>
        )}

        {/* Top bar */}
        <div
          className={cn(
            "absolute top-0 left-0 right-0 p-4 transition-opacity duration-300",
            controlsVisible || isPresentationActive ? "opacity-100" : "opacity-0"
          )}
        >
          <div className="flex items-center justify-between">
            <Button
              variant="ghost"
              size="icon"
              className="size-10 text-white hover:bg-white/20"
              onClick={handleExit}
              aria-label="Back"
            >
              <ArrowLeft className="size-5" />
            </Button>

            {/* Presentation status */}
            {isPresentationActive && (
              <div className="flex items-center gap-2 px-3 py-1.5 bg-green-500/20 text-green-400 rounded-full text-sm">
                <div className="w-2 h-2 bg-green-400 rounded-full animate-pulse" />
                <span>Connected to TV</span>
              </div>
            )}

            {/* Wake lock indicator */}
            {wakeLockSupported && (
              <div className="text-white/50 text-xs">
                Screen stays on
              </div>
            )}
          </div>
        </div>

        {/* iOS Info Toast */}
        {showIosInfo && (
          <div className="absolute top-16 left-4 right-4 bg-blue-500/90 text-white p-4 rounded-lg shadow-lg">
            <div className="flex items-start gap-3">
              <Info className="size-5 shrink-0 mt-0.5" />
              <div className="flex-1">
                <p className="font-medium">iOS Playback Tips</p>
                <p className="text-sm text-white/80 mt-1">
                  Tap the screen to show controls. Use the lyric list at the
                  bottom to jump between songs.
                </p>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="size-8 text-white hover:bg-white/20 shrink-0"
                onClick={() => setShowIosInfo(false)}
                aria-label="Dismiss info"
              >
                <X className="size-4" />
              </Button>
            </div>
          </div>
        )}

        {/* Desktop keyboard shortcuts hint - hidden on phone/tablet */}
        <div
          className={cn(
            "hidden lg:block absolute bottom-4 right-4 transition-opacity duration-300",
            controlsVisible || isPresentationActive ? "opacity-100" : "opacity-0"
          )}
          aria-label="Keyboard shortcuts"
          data-testid="keyboard-shortcuts-hint"
        >
          <div className="bg-black/60 text-white/75 rounded-lg px-3 py-2 text-xs">
            <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
              <span><kbd className="font-mono text-white/90">Space</kbd> Play/Pause</span>
              <span><kbd className="font-mono text-white/90">←</kbd>/<kbd className="font-mono text-white/90">→</kbd> Seek 10s</span>
              <span><kbd className="font-mono text-white/90">[</kbd> Prev song</span>
              <span><kbd className="font-mono text-white/90">]</kbd> Next song</span>
            </div>
          </div>
        </div>
      </div>

      {/* Controls */}
      <div
        ref={controlsRef}
        className={cn(
          "transition-opacity duration-300 pb-12",
          controlsVisible || isPresentationActive
            ? "opacity-100"
            : "opacity-0 pointer-events-none"
        )}
        onMouseEnter={() => {
          if (hideTimeoutRef.current) {
            clearTimeout(hideTimeoutRef.current);
          }
        }}
        onMouseLeave={startHideTimer}
      >
        <PlaybackControls
          isPlaying={isPlaying}
          currentTime={currentTime}
          duration={duration}
          volume={volume}
          isMuted={isMuted}
          currentSongIndex={currentSongIndex}
          totalSongs={chapters.length}
          isPresentationActive={isPresentationActive}
          onPlayPause={handlePlayPause}
          onSeek={handleSeek}
          onPrevSong={handlePrevSong}
          onNextSong={handleNextSong}
          onVolumeChange={handleVolumeChange}
          onToggleMute={handleToggleMute}
        />
      </div>

      {/* Lyric Jump List */}
      {!isPresentationActive && (
        <LyricJumpList
          chapters={chapters}
          currentTime={currentTime}
          currentSongIndex={currentSongIndex}
          onJumpToChapter={handleJumpToChapter}
          onJumpToLine={handleJumpToLine}
        />
      )}
    </div>
  );
}
