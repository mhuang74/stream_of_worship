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
import type { CastTransportResult } from "@/hooks/useCast";
import type { PresentationCommand } from "@/types/presentation-api";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { ArrowLeft, X, Info, Maximize, Monitor, Loader2 } from "lucide-react";

/**
 * Surface for the dev-only Presentation API sender fallback (used only when
 * Cast is unsupported, e.g. iOS). The controller page passes the sender hook
 * result here so the player can render the iPhone AirPlay fallback hint when
 * neither Cast nor the Presentation fallback is available.
 */
export interface PresentationFallback {
  isSupported: boolean;
  isConnected?: boolean;
}

export interface ControllerPlayerProps {
  playerId: string;
  videoSrc: string;
  chapters: Chapter[];
  isPresentationActive?: boolean;
  /**
   * Unified Cast transport surface. When the controller page mounts
   * `useCastTransport`, it passes the full result here so the player can
   * reconcile on-phone UI from the receiver media status (time, playing state,
   * volume, mute) while connected, and read `resumeProposal` on disconnect.
   */
  transport?: CastTransportResult;
  /** Dev-only Presentation API sender (AirPlay fallback hint source). */
  presentationFallback?: PresentationFallback;
  /** Whether the Cast Web Sender SDK is supported on this browser. */
  isCastSupported?: boolean;
  /** Cast device availability signal for the diagnostic bottom sheet UX. */
  castAvailability?: "unknown" | "available" | "unavailable";
  /** True while a Cast session request is in flight (spinner on the button). */
  isCastConnecting?: boolean;
  /** Launch the Cast (or Presentation fallback) device picker. */
  onSendToTV?: () => void;
  /** Forward a transport command to the active receiver. */
  onSendTransportCommand?: (command: PresentationCommand) => void;
  exitRoute?: string;
  autoFullscreen?: boolean;
  className?: string;
}

const IOS_INFO_KEY = "sow-ios-info-shown";
const SEEK_DEBOUNCE_MS = 200;
const BUFFERING_ACTIONABLE_MS = 15_000;

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

function clamp(v: number, min: number, max: number): number {
  if (max < min) return min;
  return Math.max(min, Math.min(v, max));
}

interface PendingResume {
  time: number;
  isStale: boolean;
}

export function ControllerPlayer({
  playerId,
  videoSrc,
  chapters,
  isPresentationActive = false,
  transport,
  presentationFallback,
  isCastSupported,
  castAvailability,
  isCastConnecting,
  onSendToTV,
  onSendTransportCommand,
  exitRoute,
  autoFullscreen = true,
  className,
}: ControllerPlayerProps) {
  const router = useRouter();
  const videoRef = useRef<HTMLVideoElement>(null);
  const controlsRef = useRef<HTMLDivElement>(null);
  const hideTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const seekDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wasActiveRef = useRef(false);

  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(1);
  const [isMuted, setIsMuted] = useState(false);
  const [currentSongIndex, setCurrentSongIndex] = useState(0);
  const [controlsVisible, setControlsVisible] = useState(true);
  const [showIosInfo, setShowIosInfo] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [showDiagnosticSheet, setShowDiagnosticSheet] = useState(false);
  const [pendingResume, setPendingResume] = useState<PendingResume | null>(null);

  // Refs to the latest transport forwarding props so effect/handler closures
  // never go stale without forcing re-renders.
  const onSendToTVRef = useRef(onSendToTV);
  useEffect(() => {
    onSendToTVRef.current = onSendToTV;
  }, [onSendToTV]);
  const onSendTransportCommandRef = useRef(onSendTransportCommand);
  useEffect(() => {
    onSendTransportCommandRef.current = onSendTransportCommand;
  }, [onSendTransportCommand]);

  // Wake lock hook
  const { isSupported: wakeLockSupported } = useWakeLock();

  // ── Reconcile on-phone UI from Cast status while connected ──────────────
  // When the transport is connected, the receiver media status is the source
  // of truth for time / playing / volume / mute. The local <video> stays
  // paused + muted (audio plays on the receiver); only the controller UI
  // mirrors the receiver so the worship leader sees the right state.
  const isTransportConnected = transport?.isConnected ?? false;
  const effectiveCurrentTime = isTransportConnected
    ? transport?.currentTime ?? currentTime
    : currentTime;
  const effectiveDuration = isTransportConnected
    ? transport?.duration || duration
    : duration;
  const effectiveIsPlaying = isTransportConnected
    ? transport?.playerState === "playing"
    : isPlaying;
  const effectiveVolume = isTransportConnected ? transport?.volume ?? volume : volume;
  const effectiveIsMuted = isTransportConnected ? transport?.isMuted ?? isMuted : isMuted;

  const bufferingSinceMs = isTransportConnected ? transport?.bufferingSinceMs ?? null : null;
  const isBuffering = isTransportConnected && transport?.playerState === "buffering";
  const showActionableBuffering =
    isBuffering &&
    bufferingSinceMs !== null &&
    Date.now() - bufferingSinceMs > BUFFERING_ACTIONABLE_MS;

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
      // While the transport is connected, the receiver is the source of
      // truth — don't let local timeupdate events fight the mirrored state.
      if (isPresentationActive) return;
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

    const handlePlay = () => {
      if (!isPresentationActive) setIsPlaying(true);
    };
    const handlePause = () => {
      if (!isPresentationActive) setIsPlaying(false);
    };
    const handleVolumeChange = () => {
      if (!isPresentationActive) {
        setVolume(video.volume);
        setIsMuted(video.muted);
      }
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
  }, [chapters, currentSongIndex, isPresentationActive]);

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

  // ── Intent forwarding ───────────────────────────────────────────────────
  // When the presentation is active, control intents are forwarded to the
  // receiver as transport commands (guarded by isPresentationActive,
  // latest-wins during buffering — the transport hook debounces on its side;
  // client-side seek is also debounced 200ms to batch rapid jumps).
  const handlePlayPause = useCallback(() => {
    if (isPresentationActive) {
      const cmd: PresentationCommand = effectiveIsPlaying
        ? { type: "pause" }
        : { type: "play" };
      onSendTransportCommandRef.current?.(cmd);
      return;
    }
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
  }, [isPresentationActive, effectiveIsPlaying, isPlaying]);

  const handleSeek = useCallback(
    (time: number) => {
      if (!Number.isFinite(time)) {
        console.warn("handleSeek called with non-finite time:", time);
        return;
      }
      const upper = effectiveDuration > 0 ? effectiveDuration : time;
      const clampedTime = clamp(time, 0, upper);

      if (isPresentationActive) {
        // Mirror the seek locally for immediate UI feedback, then forward the
        // command (debounced 200ms client-side, latest-wins).
        setCurrentTime(clampedTime);
        if (seekDebounceRef.current) {
          clearTimeout(seekDebounceRef.current);
        }
        seekDebounceRef.current = setTimeout(() => {
          onSendTransportCommandRef.current?.({
            type: "seek",
            positionSeconds: clampedTime,
          });
        }, SEEK_DEBOUNCE_MS);
        return;
      }

      const video = videoRef.current;
      if (!video) return;
      video.currentTime = clampedTime;
      setCurrentTime(clampedTime);
    },
    [isPresentationActive, effectiveDuration]
  );

  const handleSkipBack = useCallback(() => {
    handleSeek(effectiveCurrentTime - 10);
  }, [effectiveCurrentTime, handleSeek]);

  const handleSkipForward = useCallback(() => {
    handleSeek(effectiveCurrentTime + 10);
  }, [effectiveCurrentTime, handleSeek]);

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

  const handleVolumeChange = useCallback(
    (newVolume: number) => {
      const clamped = clamp(newVolume, 0, 1);
      if (isPresentationActive) {
        onSendTransportCommandRef.current?.({ type: "volume", level: clamped });
        return;
      }
      const video = videoRef.current;
      if (!video) return;
      video.volume = clamped;
      video.muted = clamped === 0;
    },
    [isPresentationActive]
  );

  const handleToggleMute = useCallback(() => {
    if (isPresentationActive) {
      // Mute is a distinct bit on the receiver — never route through volume.
      onSendTransportCommandRef.current?.({
        type: "mute",
        muted: !effectiveIsMuted,
      });
      return;
    }
    const video = videoRef.current;
    if (!video) return;
    video.muted = !video.muted;
  }, [isPresentationActive, effectiveIsMuted]);

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
    router.push(exitRoute ?? `/songsets/${playerId}/play`);
  }, [router, playerId, exitRoute]);

  const handleReenterFullscreen = useCallback(() => {
    document.documentElement.requestFullscreen().catch(() => {});
  }, []);

  // Cancel any pending debounced seek on unmount.
  useEffect(() => {
    return () => {
      if (seekDebounceRef.current) {
        clearTimeout(seekDebounceRef.current);
        seekDebounceRef.current = null;
      }
    };
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
    updatePlaybackState(effectiveIsPlaying ? "playing" : "paused");
  }, [effectiveIsPlaying, updatePlaybackState]);

  // Update media session position state
  useEffect(() => {
    if (effectiveDuration > 0) {
      updatePositionState({
        duration: effectiveDuration,
        position: effectiveCurrentTime,
        playbackRate: 1,
      });
    }
  }, [effectiveDuration, effectiveCurrentTime, updatePositionState]);

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
    if (!autoFullscreen) return;

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
  }, [autoFullscreen]);

  // Mute (+ pause) local video when presentation is active (audio plays on the
  // receiver). Composes with the disconnect→resume effect below.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;

    if (isPresentationActive) {
      video.muted = true;
      video.setAttribute("muted", "");
      video.pause();
    } else {
      video.muted = false;
      video.removeAttribute("muted");
    }
  }, [isPresentationActive]);

  // ── Disconnect → local resume (P0) ──────────────────────────────────────
  // When the presentation transitions active → inactive (transport was
  // previously connected), read transport.resumeProposal and either auto-resume
  // local playback from the extrapolated TV position, or — when the proposal
  // is stale — surface a tap-to-resume prompt without auto-resuming. Never
  // silent: a play() rejection renders a prominent inline tap-to-resume control
  // with the seek already applied.
  useEffect(() => {
    const wasActive = wasActiveRef.current;
    wasActiveRef.current = isPresentationActive;
    if (!wasActive || isPresentationActive) return;

    const proposal = transport?.resumeProposal;
    if (!proposal) return;
    const video = videoRef.current;
    if (!video) return;

    if (proposal.isStale) {
      setPendingResume({ time: proposal.time, isStale: true });
      return;
    }

    const dur = Number.isFinite(video.duration) && video.duration > 0
      ? video.duration
      : effectiveDuration;
    const t = clamp(proposal.time, 0, dur > 0 ? dur : proposal.time);
    try {
      video.currentTime = t;
    } catch {
      /* best-effort */
    }
    setCurrentTime(t);
    setPendingResume(null);

    video
      .play()
      .then(() => {
        setIsPlaying(true);
      })
      .catch(() => {
        setPendingResume({ time: t, isStale: false });
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPresentationActive, transport?.resumeProposal]);

  const handleTapToResume = useCallback(() => {
    if (!pendingResume) return;
    const video = videoRef.current;
    if (!video) return;
    const t = pendingResume.time;
    try {
      video.currentTime = t;
    } catch {
      /* best-effort */
    }
    setCurrentTime(t);
    video
      .play()
      .then(() => {
        setIsPlaying(true);
        setPendingResume(null);
      })
      .catch(() => {
        /* keep the prompt visible */
      });
  }, [pendingResume]);

  // ── Song-change effect (keyed on currentSongIndex while active) ─────────
  // Push the new song title to the receiver. No-op for Cast (the title is set
  // via MediaInfo.metadata at loadMedia); the Presentation fallback uses it.
  useEffect(() => {
    if (!isPresentationActive) return;
    const chapter = chapters[currentSongIndex];
    if (!chapter) return;
    onSendTransportCommandRef.current?.({
      type: "songTitle",
      title: chapter.songTitle,
    });
  }, [currentSongIndex, isPresentationActive, chapters]);

  // ── Top-bar derived state ───────────────────────────────────────────────
  const showCastButton = isCastSupported === true && !isPresentationActive;
  const castUnavailable = castAvailability === "unavailable";
  const showIphoneFallback =
    isCastSupported === false &&
    (presentationFallback?.isSupported ?? false) === false;

  const handleCastButtonClick = useCallback(() => {
    if (castUnavailable) {
      setShowDiagnosticSheet(true);
      return;
    }
    onSendToTVRef.current?.();
  }, [castUnavailable]);

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
          <div className="flex items-center justify-between gap-2">
            <Button
              variant="ghost"
              size="icon"
              className="size-10 text-white hover:bg-white/20"
              onClick={handleExit}
              aria-label="Back"
            >
              <ArrowLeft className="size-5" />
            </Button>

            <div className="flex items-center gap-2">
              {/* Presentation status */}
              {isPresentationActive && (
                <div className="flex items-center gap-2 px-3 py-1.5 bg-green-500/20 text-green-400 rounded-full text-sm">
                  <div className="w-2 h-2 bg-green-400 rounded-full animate-pulse" />
                  <span>
                    Connected to {transport?.deviceName ? transport.deviceName : "TV"}
                  </span>
                </div>
              )}

              {/* Buffering chip (non-blocking; controls stay enabled) */}
              {isBuffering && (
                <div
                  className="flex items-center gap-2 px-3 py-1.5 bg-amber-500/20 text-amber-300 rounded-full text-xs"
                  data-testid="buffering-chip"
                >
                  <Loader2 className="size-3 animate-spin" />
                  <span>
                    {showActionableBuffering
                      ? "TV is still loading — check Wi-Fi / MP4 reachability / retry Cast."
                      : "TV is loading…"}
                  </span>
                </div>
              )}

              {/* Cast / Send-to-TV button */}
              {showCastButton && (
                <Button
                  variant="ghost"
                  size="icon"
                  className={cn(
                    "size-10 text-white hover:bg-white/20",
                    castUnavailable && "opacity-60"
                  )}
                  onClick={handleCastButtonClick}
                  aria-label={castUnavailable ? "Cast unavailable" : "Send to TV"}
                  data-testid="cast-button"
                >
                  {isCastConnecting ? (
                    <Loader2 className="size-5 animate-spin" />
                  ) : (
                    <Monitor className="size-5" />
                  )}
                </Button>
              )}

              {/* iPhone fallback: Cast unsupported and Presentation unsupported */}
              {showIphoneFallback && (
                <a
                  href="/docs#airplay"
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-white/10 text-white/80 rounded-full text-xs hover:bg-white/20"
                  data-testid="airplay-fallback"
                >
                  <Monitor className="size-3" />
                  <span>
                    Use AirPlay to an Apple TV — native iOS app pending
                  </span>
                </a>
              )}
            </div>

            {/* Wake lock indicator */}
            {wakeLockSupported && (
              <div className="text-white/50 text-xs">
                Screen stays on
              </div>
            )}
          </div>
        </div>

        {/* Tap-to-resume / stale resume prompt (disconnect → local resume) */}
        {pendingResume && (
          <button
            type="button"
            onClick={handleTapToResume}
            className="absolute top-16 left-1/2 -translate-x-1/2 z-[85] flex items-center gap-2 px-4 py-3 bg-amber-500/90 text-black rounded-lg shadow-lg text-sm font-medium"
            data-testid="tap-to-resume"
          >
            <Info className="size-4 shrink-0" />
            <span>
              {pendingResume.isStale
                ? `Resume from TV position may be stale — tap to resume at ${formatTime(
                    pendingResume.time
                  )}`
                : `Tap to resume at ${formatTime(pendingResume.time)}`}
            </span>
          </button>
        )}

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
          isPlaying={effectiveIsPlaying}
          currentTime={effectiveCurrentTime}
          duration={effectiveDuration}
          volume={effectiveVolume}
          isMuted={effectiveIsMuted}
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

      {/* Lyric Jump List (hidden while presentation is active) */}
      {!isPresentationActive && (
        <LyricJumpList
          chapters={chapters}
          currentTime={effectiveCurrentTime}
          currentSongIndex={currentSongIndex}
          onJumpToChapter={handleJumpToChapter}
          onJumpToLine={handleJumpToLine}
        />
      )}

      {/* Diagnostic bottom sheet (Cast unavailable) */}
      <Sheet
        open={showDiagnosticSheet}
        onOpenChange={setShowDiagnosticSheet}
      >
        <SheetContent side="bottom" data-testid="diagnostic-sheet">
          <SheetHeader>
            <SheetTitle>Cast unavailable</SheetTitle>
            <SheetDescription>
              Chromecast couldn&apos;t be reached. Check the following:
            </SheetDescription>
          </SheetHeader>
          <ol className="list-decimal space-y-2 px-4 pb-6 text-sm text-muted-foreground">
            <li>Use Android Chrome over HTTPS (the Cast Web Sender SDK requires it).</li>
            <li>Phone and TV must be on the same Wi-Fi / VLAN (guest and captive-portal networks block discovery).</li>
            <li>Receiver must be powered on, and dev/staging devices must be whitelisted in the Google Cast SDK Developer Console.</li>
            <li>Try opening the MP4 URL from this network in a laptop browser to confirm R2 reachability and range-seek.</li>
          </ol>
        </SheetContent>
      </Sheet>
    </div>
  );
}
