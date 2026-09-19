import {
  useState,
  useEffect,
  useRef,
  useCallback,
  useMemo,
  useSyncExternalStore,
} from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { PlaybackControls } from "./PlaybackControls";
import { LyricJumpList } from "./LyricJumpList";
import { useLocale } from "@/hooks/useLocale";
import { getMarketingUrl } from "@/lib/marketing-url";
import type { Chapter } from "@/lib/render/chapters";
import { useWakeLock } from "@/hooks/useWakeLock";
import { useKeyboardShortcuts } from "@/hooks/useKeyboardShortcuts";
import { useMediaSession } from "@/hooks/useMediaSession";
import type { CastTransportResult } from "@/hooks/useCast";
import type { PresentationCommand, PresentationMediaStatus } from "@/types/presentation-api";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import { ArrowLeft, X, Info, Maximize, Monitor, MonitorOff, Loader2, WifiOff, AlertTriangle } from "lucide-react";

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

interface ControllerPlayerBaseProps {
  playerId: string;
  chapters: Chapter[];
  /**
   * Offline boot (the controller's cache-first branches): renders the offline
   * hint and hides every transport entry point — with no network and no
   * session the receiver cannot reach the artifacts.
   */
  isOfflineMedia?: boolean;
  /**
   * Host hook for a media load failure. Resolve `true` when the host has taken
   * over the recovery (e.g. swapping the offline proxy URL for a blob URL of
   * the cached artifact) — the player then withholds its failure overlay.
   */
  onMediaError?: () => Promise<boolean>;
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
  /** Receiver media status from the dev-only Presentation API fallback. */
  presentationMediaStatus?: PresentationMediaStatus | null;
  /** Whether the Cast Web Sender SDK is supported on this browser. */
  isCastSupported?: boolean;
  /** Cast device availability signal for the diagnostic bottom sheet UX. */
  castAvailability?: "unknown" | "available" | "unavailable";
  /** True while a Cast session request is in flight (spinner on the button). */
  isCastConnecting?: boolean;
  /** Launch the Cast (or Presentation fallback) device picker. */
  onSendToTV?: () => void;
  /** Stop the active Cast or Presentation fallback session. */
  onStopPresentation?: () => void;
  /** Forward a transport command to the active receiver. */
  onSendTransportCommand?: (command: PresentationCommand) => void;
  exitRoute?: string;
  autoFullscreen?: boolean;
  /**
   * Content hash per chapter position (index 0 = first chapter), from the
   * songset detail API. Enables the Lyrics Feedback footer on the lyric
   * jump list for the current chapter (issue #194). Omitted by the
   * anonymous share-controller variant.
   */
  chapterRecordingHashes?: (string | null)[];
  className?: string;
}

/**
 * Exactly one media source. `audioSrc` is the offline audio-only boot (an
 * MP3-only render): the element is an <audio>, chapters and the custom
 * controls render as usual, and the visuals stay local.
 */
export type ControllerPlayerProps =
  | (ControllerPlayerBaseProps & { videoSrc: string; audioSrc?: never })
  | (ControllerPlayerBaseProps & { videoSrc?: never; audioSrc: string });

const IOS_INFO_KEY = "sow-ios-info-shown";

// iOS WebKit exposes native fullscreen on <video> (AVPlayer UI) even where the
// document Fullscreen API is unavailable (all WKWebView browsers, incl. Chrome
// iOS). Capability-detected via useSyncExternalStore; see
// canDocumentFullscreenSnapshot / canVideoFullscreenSnapshot and
// handleReenterFullscreen.
type VideoElementWithIOSFullscreen = HTMLVideoElement & {
  webkitEnterFullscreen?: () => void;
};

// These fullscreen capabilities never change for a browser session, so there
// is no store to subscribe to — the subscribe function is a no-op.
const subscribeCapabilitiesNever = () => () => {};

function canDocumentFullscreenSnapshot(): boolean {
  return typeof document.documentElement?.requestFullscreen === "function";
}

function canVideoFullscreenSnapshot(): boolean {
  // Capability lives on the prototype in WebKit, so this is instance- and
  // mount-timing independent — a client-side navigation to the play page
  // (no <video> in the DOM yet at first render) still detects it. The
  // prototype lookup needs the undefined guard for TS (webkitEnterFullscreen
  // is an optional member of VideoElementWithIOSFullscreen).
  return (
    typeof HTMLVideoElement !== "undefined" &&
    typeof (HTMLVideoElement.prototype as VideoElementWithIOSFullscreen)
      .webkitEnterFullscreen === "function"
  );
}

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
  audioSrc,
  chapters,
  isPresentationActive = false,
  transport,
  presentationFallback,
  presentationMediaStatus,
  isCastSupported,
  isOfflineMedia = false,
  onMediaError,
  exitRoute,
  autoFullscreen = true,
  chapterRecordingHashes,
  castAvailability,
  isCastConnecting,
  onSendToTV,
  onStopPresentation,
  onSendTransportCommand,
  className,
}: ControllerPlayerProps) {
  const router = useRouter();
  const { t, locale } = useLocale();
  // One element ref for both media elements: <video> online, <audio> on the
  // offline audio-only boot. Everything the player does with the element
  // (time, duration, volume, play/pause, load) lives on HTMLMediaElement; the
  // one video-only member (WebKit fullscreen) narrows at its call site.
  const mediaRef = useRef<HTMLMediaElement | null>(null);
  const setMediaElement = useCallback((element: HTMLMediaElement | null) => {
    mediaRef.current = element;
  }, []);
  const mediaSrc = videoSrc ?? audioSrc;
  const isAudioOnly = audioSrc !== undefined;
  const controlsRef = useRef<HTMLDivElement>(null);
  const hideTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const seekDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const wasActiveRef = useRef(false);
  const suppressNextResumeRef = useRef(false);

  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(1);
  // Fullscreen capability detection (SSR-safe): useSyncExternalStore with a
  // `false` server snapshot — SSR renders no fullscreen button, the client
  // snapshot supplies the real answer immediately after hydration with no
  // setState-in-effect cascading render. Capabilities never change during a
  // browser session, so there is no store to subscribe to.
  const canDocumentFullscreen = useSyncExternalStore(
    subscribeCapabilitiesNever,
    canDocumentFullscreenSnapshot,
    () => false
  );
  const canVideoFullscreen = useSyncExternalStore(
    subscribeCapabilitiesNever,
    canVideoFullscreenSnapshot,
    () => false
  );
  const [isMuted, setIsMuted] = useState(false);
  const [localSongIndex, setLocalSongIndex] = useState(0);
  const [controlsVisible, setControlsVisible] = useState(true);
  const [showIosInfo, setShowIosInfo] = useState(false);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [showDiagnosticSheet, setShowDiagnosticSheet] = useState(false);
  const [pendingResume, setPendingResume] = useState<PendingResume | null>(null);
  const [pendingSeek, setPendingSeek] = useState<number | null>(null);
  // Local media failure surface: a hard `error` event. Cleared as soon as
  // bytes flow again.
  const [mediaFailure, setMediaFailure] = useState<"error" | null>(null);
  // Host-takeover recovery (the controller swaps the failed src prop for the
  // offline copy): capture the failure position at error time — the swap
  // re-runs resource selection and can reset currentTime to 0 before the
  // host's promise resolves — and resume there once the new source is in.
  const recoveryResumeAtRef = useRef(0);
  const recoveryPendingRef = useRef(false);

  // Refs to the latest transport forwarding props so effect/handler closures
  // never go stale without forcing re-renders.
  const onSendToTVRef = useRef(onSendToTV);
  useEffect(() => {
    onSendToTVRef.current = onSendToTV;
  }, [onSendToTV]);
  const onStopPresentationRef = useRef(onStopPresentation);
  useEffect(() => {
    onStopPresentationRef.current = onStopPresentation;
  }, [onStopPresentation]);
  const onSendTransportCommandRef = useRef(onSendTransportCommand);
  useEffect(() => {
    onSendTransportCommandRef.current = onSendTransportCommand;
  }, [onSendTransportCommand]);
  const onMediaErrorRef = useRef(onMediaError);
  useEffect(() => {
    onMediaErrorRef.current = onMediaError;
  }, [onMediaError]);

  // Wake lock hook
  const { isSupported: wakeLockSupported } = useWakeLock();

  // ── Reconcile on-phone UI from Cast status while connected ──────────────
  // When the transport is connected, the receiver media status is the source
  // of truth for time / playing / volume / mute. The local <video> stays
  // paused + muted (audio plays on the receiver); only the controller UI
  // mirrors the receiver so the worship leader sees the right state.
  const isCastTransportConnected = transport?.isConnected ?? false;
  const isPresentationFallbackConnected =
    isPresentationActive && !isCastTransportConnected && (presentationFallback?.isConnected ?? true);
  const isRemotePlaybackActive = isCastTransportConnected || isPresentationFallbackConnected;
  const receiverCurrentTime = isCastTransportConnected
    ? transport?.currentTime
    : presentationMediaStatus?.currentTime;
  const effectiveCurrentTime = isRemotePlaybackActive
    ? pendingSeek ?? receiverCurrentTime ?? currentTime
    : currentTime;
  // Chapter index driven by local <video> timeupdate when offline, and by the
  // receiver's reported currentTime when a Cast transport is connected (the
  // local video is paused + muted and its timeupdate is suppressed while
  // active). Derived during render so the song-change effect + LyricJumpList
  // highlight stay in sync without a setState-in-effect. `fromPlayback`
  // distinguishes a chapter made current by actual playback position from
  // the initial local fallback (nothing is current before playback starts).
  const currentSong = useMemo(() => {
    if (isRemotePlaybackActive) {
      const t = effectiveCurrentTime;
      const idx = chapters.findIndex(
        (chapter, i) =>
          t >= chapter.startSeconds &&
          (i === chapters.length - 1 || t < chapters[i + 1].startSeconds)
      );
      if (idx !== -1) return { index: idx, fromPlayback: true };
    }
    return { index: localSongIndex, fromPlayback: false };
  }, [isRemotePlaybackActive, effectiveCurrentTime, chapters, localSongIndex]);
  const currentSongIndex = currentSong.index;
  const effectiveDuration = isCastTransportConnected
    ? transport?.duration || duration
    : isPresentationFallbackConnected
      ? presentationMediaStatus?.duration || duration
      : duration;
  const effectiveIsPlaying = isCastTransportConnected
    ? transport?.playerState === "playing"
    : isPresentationFallbackConnected
      ? presentationMediaStatus?.playerState === "playing"
      : isPlaying;
  const effectiveVolume = isCastTransportConnected
    ? transport?.volume ?? volume
    : isPresentationFallbackConnected
      ? presentationMediaStatus?.volume ?? volume
      : volume;
  const effectiveIsMuted = isCastTransportConnected
    ? transport?.isMuted ?? isMuted
    : isPresentationFallbackConnected
      ? presentationMediaStatus?.isMuted ?? isMuted
      : isMuted;

  const bufferingSinceMs = isCastTransportConnected ? transport?.bufferingSinceMs ?? null : null;
  const isBuffering = isCastTransportConnected && transport?.playerState === "buffering";
  // `nowMs` ticks once per second while the receiver is buffering so the
  // "actionable buffering" copy flips after BUFFERING_ACTIONABLE_MS without
  // calling Date.now() during render (which would violate component purity).
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (!isBuffering) return;
    const id = setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(id);
  }, [isBuffering]);
  const showActionableBuffering =
    isBuffering &&
    bufferingSinceMs !== null &&
    nowMs - bufferingSinceMs > BUFFERING_ACTIONABLE_MS;

  // Clear the pending seek once the receiver's currentTime catches up to the
  // target position (within 0.5s). This hands the slider back to the receiver
  // as the source of truth after a user-initiated seek while connected.
  useEffect(() => {
    if (pendingSeek === null || !isRemotePlaybackActive) return;
    const reported = receiverCurrentTime ?? 0;
    if (Math.abs(reported - pendingSeek) < 0.5) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setPendingSeek(null);
    }
  }, [pendingSeek, isRemotePlaybackActive, receiverCurrentTime]);

  // Also clear pending seek when disconnecting so the slider doesn't hold a
  // stale target across a disconnect→resume transition.
  useEffect(() => {
    if (!isRemotePlaybackActive) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setPendingSeek(null);
    }
  }, [isRemotePlaybackActive]);

  // Check if iOS and if info toast was already shown
  useEffect(() => {
    if (typeof navigator === "undefined") return;

    const isIOS =
      /iPad|iPhone|iPod/.test(navigator.userAgent) && !(window as unknown as { MSStream: boolean }).MSStream;

    // Safari private-browsing mode throws QuotaExceededError on sessionStorage
    // access — treat quota failures as "not shown" silently so the toast path
    // never surfaces an uncaught exception on iOS.
    let infoShown: string | null = null;
    try {
      infoShown = sessionStorage.getItem(IOS_INFO_KEY);
    } catch {
      /* private mode / disabled storage — treat as not shown */
    }

    if (isIOS && !isPresentationActive && !infoShown) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setShowIosInfo(true);
      try {
        sessionStorage.setItem(IOS_INFO_KEY, "true");
      } catch {
        /* best-effort: never throw from storage access */
      }
    }
  }, [isPresentationActive]);

  // Media element event handlers (works for <video> and the offline <audio>)
  useEffect(() => {
    const media = mediaRef.current;
    if (!media) return;

    const handleTimeUpdate = () => {
      // While the transport is connected, the receiver is the source of
      // truth — don't let local timeupdate events fight the mirrored state.
      if (isPresentationActive) return;
      setCurrentTime(media.currentTime);

      // Update current song index based on time
      const newIndex = chapters.findIndex(
        (chapter, i) =>
          media.currentTime >= chapter.startSeconds &&
          (i === chapters.length - 1 ||
            media.currentTime < chapters[i + 1].startSeconds)
      );
      if (newIndex !== -1 && newIndex !== currentSongIndex) {
        setLocalSongIndex(newIndex);
      }
    };

    const handleLoadedMetadata = () => {
      setDuration(media.duration);
    };

    const handlePlay = () => {
      if (!isPresentationActive) setIsPlaying(true);
    };
    const handlePause = () => {
      if (!isPresentationActive) setIsPlaying(false);
    };
    const handleVolumeChange = () => {
      if (!isPresentationActive) {
        setVolume(media.volume);
        setIsMuted(media.muted);
      }
    };

    // `playing`/`progress` mean bytes are flowing again, so they clear an
    // error overlay already on screen (which is also what makes Retry
    // recover visibly).
    const handleBytesFlowing = () => {
      if (isPresentationActive) return;
      setMediaFailure(null);
    };

    const handleError = () => {
      if (isPresentationActive) return;
      // The element exposes no failure reason, so log the source it failed on.
      console.error("Media element failed:", media.currentSrc || media.src);
      // Capture the failure position immediately: the host's recovery (a src
      // swap) re-runs resource selection and can reset currentTime to 0
      // before the host promise resolves.
      const resumeAt = Number.isFinite(media.currentTime) ? media.currentTime : 0;
      // The host may own the recovery (the controller swaps a failed offline
      // proxy URL for a blob URL of the cached artifact, or a failed online
      // presigned URL for the offline copy); when it declines, or its own
      // recovery rejects, the overlay is the answer.
      const handled = onMediaErrorRef.current?.() ?? Promise.resolve(false);
      void handled.then((isHandled) => {
        if (!isHandled) {
          setMediaFailure("error");
          toast.error(t("controller.mediaFailed"));
        } else {
          recoveryResumeAtRef.current = resumeAt;
          recoveryPendingRef.current = true;
        }
      }).catch(() => {
        setMediaFailure("error");
        toast.error(t("controller.mediaFailed"));
      });
    };

    media.addEventListener("timeupdate", handleTimeUpdate);
    media.addEventListener("loadedmetadata", handleLoadedMetadata);
    media.addEventListener("play", handlePlay);
    media.addEventListener("pause", handlePause);
    media.addEventListener("volumechange", handleVolumeChange);
    media.addEventListener("progress", handleBytesFlowing);
    media.addEventListener("playing", handleBytesFlowing);
    media.addEventListener("error", handleError);

    return () => {
      media.removeEventListener("timeupdate", handleTimeUpdate);
      media.removeEventListener("loadedmetadata", handleLoadedMetadata);
      media.removeEventListener("play", handlePlay);
      media.removeEventListener("pause", handlePause);
      media.removeEventListener("volumechange", handleVolumeChange);
      media.removeEventListener("progress", handleBytesFlowing);
      media.removeEventListener("playing", handleBytesFlowing);
      media.removeEventListener("error", handleError);
    };
  }, [chapters, currentSongIndex, isPresentationActive, t]);

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
    const video = mediaRef.current;
    if (!video) return;

    if (isPlaying) {
      video.pause();
      setIsPlaying(false);
    } else {
      setIsPlaying(true);
      video.play().catch((err) => {
        setIsPlaying(false);
        console.error("Play failed:", err);
        toast.error(t("controller.toastPlaybackFailed"));
      });
    }
  }, [isPresentationActive, effectiveIsPlaying, isPlaying, t]);

  const handleSeek = useCallback(
    (time: number) => {
      if (!Number.isFinite(time)) {
        console.warn("handleSeek called with non-finite time:", time);
        return;
      }

      if (isPresentationActive) {
        // While the receiver media is the source of truth, forward the seek
        // command (debounced 200ms client-side, latest-wins). When the
        // effective duration is 0 (receiver media not loaded yet), clamp
        // against the relevant chapter's endSeconds so an out-of-range
        // positionSeconds is not forwarded to the receiver before its duration
        // is known — the transport hook re-clamps using its own snapshot on
        // fire.
        let upper = effectiveDuration > 0 ? effectiveDuration : time;
        if (effectiveDuration <= 0) {
          // Derive a local upper bound from the chapter that contains `time`
          // so a chapter / lyric-line jump does not forward an unbounded
          // positionSeconds before the receiver reports its duration.
          const containingIdx = chapters.findIndex(
            (ch, i) =>
              time >= ch.startSeconds &&
              (i === chapters.length - 1 || time < chapters[i + 1].startSeconds),
          );
          const containingEnd =
            containingIdx >= 0 ? chapters[containingIdx]?.endSeconds : undefined;
          if (typeof containingEnd === "number" && containingEnd > 0) {
            upper = containingEnd;
          }
        }
        const clampedTime = clamp(time, 0, upper);
        // Track the pending seek so the slider mirrors the target position
        // immediately — without this, effectiveCurrentTime (derived from
        // transport.currentTime while connected) would show the stale receiver
        // position until the receiver reports the new time back. The pending
        // value is cleared once the receiver's currentTime catches up.
        setPendingSeek(clampedTime);
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

      const video = mediaRef.current;
      if (!video) return;
      const localUpper = effectiveDuration > 0 ? effectiveDuration : time;
      const localClamped = clamp(time, 0, localUpper);
      video.currentTime = localClamped;
      setCurrentTime(localClamped);
    },
    [isPresentationActive, effectiveDuration, chapters]
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
      const video = mediaRef.current;
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
    const video = mediaRef.current;
    if (!video) return;
    video.muted = !video.muted;
  }, [isPresentationActive, effectiveIsMuted]);

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

  const transportRef = useRef(transport);
  useEffect(() => {
    transportRef.current = transport;
  }, [transport]);

  const handleStopPresentation = useCallback(() => {
    suppressNextResumeRef.current = true;
    onStopPresentationRef.current?.();
  }, []);

  const handleExit = useCallback(() => {
    // Tear down any active remote session before navigating away so the TV
    // receiver does not keep playing audio with no controller attached.
    if (isPresentationActive && onStopPresentationRef.current) {
      try {
        handleStopPresentation();
      } catch {
        /* best-effort: never block navigation */
      }
    } else if (transportRef.current?.isConnected) {
      try {
        transportRef.current.stop();
      } catch {
        /* best-effort: never block navigation */
      }
    }
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {
        // Ignore errors
      });
    }
    router.push(exitRoute ?? "/songsets");
  }, [router, playerId, exitRoute, isPresentationActive, handleStopPresentation]);

  const handleReenterFullscreen = useCallback(() => {
    if (typeof document.documentElement.requestFullscreen === "function") {
      document.documentElement.requestFullscreen().catch(() => {});
      return;
    }
    // iOS WKWebView (Chrome iOS etc.): document fullscreen is unavailable.
    // Fall back to the <video> element's native WebKit fullscreen. Requires a
    // user gesture — satisfied because this runs from a button tap.
    try {
      (mediaRef.current as VideoElementWithIOSFullscreen | null)?.webkitEnterFullscreen?.();
    } catch {
      // Best-effort; capability detection hides this button when absent.
    }
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
            album: t("controller.mediaAlbum"),
          }
        : null,
    [currentChapter, t]
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
    const video = mediaRef.current;
    if (!video) return;

    if (isPresentationActive) {
      video.muted = true;
      video.setAttribute("muted", "");
      video.pause();
    } else {
      // On disconnect-resume, also re-sync the local <video>'s `.volume` /
      // `.muted` from React state. During Cast, the VolumeLevelChanged
      // listener reflected the receiver's volume into React state but NOT into
      // the local <video>'s `.volume` property (the volume-change handler
      // returns early while `isPresentationActive`), so the local element kept
      // its pre-Cast `.volume` value throughout the whole Cast session. After
      // disconnect, the on-screen volume slider shows the receiver's last
      // volume while the actual audio from the phone used the pre-Cast volume
      // — worship leader could hear unexpectedly loud/quiet audio after
      // disconnect. Mirror React state onto the element here so they match.
      video.muted = false;
      video.removeAttribute("muted");
      try {
        video.volume = volume;
        video.muted = isMuted;
      } catch {
        /* best-effort: reading volume can throw on some platforms */
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPresentationActive]);

  // ─‑ Disconnect → local resume (P0) ──────────────────────────────────────
  // When the presentation transitions active → inactive (transport was
  // previously connected), read transport.resumeProposal and either auto-resume
  // local playback from the extrapolated TV position, or — when the proposal
  // is stale — surface a tap-to-resume prompt without auto-resuming. Never
  // silent: a play() rejection renders a prominent inline tap-to-resume control
  // with the seek already applied.
  //
  // Clear any stale pendingResume prompt the moment presentation becomes active
  // again (reconnect) — otherwise a stale "Tap to resume" prompt rendered on
  // disconnect would persist on top of an active Cast session, and
  // handleTapToResume could seek the local (muted, paused) <video> to an
  // outdated extrapolated TV position while the receiver is the source of
  // truth.
  useEffect(() => {
    if (isPresentationActive) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setPendingResume(null);
    }
  }, [isPresentationActive]);

  useEffect(() => {
    const wasActive = wasActiveRef.current;
    wasActiveRef.current = isPresentationActive;
    if (!wasActive || isPresentationActive) return;
    if (suppressNextResumeRef.current) {
      suppressNextResumeRef.current = false;
      setPendingResume(null);
      return;
    }

    // The effect supports two transport sources:
    //   1. Cast: `transport.resumeProposal` populated by the `useCastTransport`
    //      `IS_CONNECTED_CHANGED → false` listener (extrapolated TV time + stale flag).
    //   2. Presentation API fallback (dev-only): the sender has no receiver
    //      status, so no Cast proposal exists. Synthesize one from the local
    //      `<video>`'s currentTime (frozen at the pre-presentation position
    //      while the local video was paused + muted during presentation) so
    //      the worship leader still gets the tap-to-resume prompt on the
    //      iOS / non-Cast path (P0 disconnect-resume must not be silently
    //      absent on the Presentation API path).
    const proposal = transport?.resumeProposal ?? null;
    const video = mediaRef.current;
    if (!video) return;

    // This effect synchronizes the local <video> element with the transport's
    // extrapolated resume proposal on disconnect — a documented external-system
    // sync. The setState calls mirror that external state into React.
    if (proposal && proposal.isStale) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setPendingResume({ time: proposal.time, isStale: true });
      return;
    }

    // Resolve the resume time: Cast proposal (non-stale), else local video's
    // frozen currentTime as the Presentation-API fallback.
    const proposalTime =
      proposal != null
        ? proposal.time
        : Number.isFinite(video.currentTime)
          ? video.currentTime
          : 0;
    const dur = Number.isFinite(video.duration) && video.duration > 0
      ? video.duration
      : effectiveDuration;
    const t = clamp(proposalTime, 0, dur > 0 ? dur : proposalTime);
    try {
      video.currentTime = t;
    } catch {
      /* best-effort */
    }
    setCurrentTime(t);
    // Mark as stale when synthesized from the local video (we have no idea
    // where the receiver actually was) so the user is prompted explicitly.
    const isStale = proposal != null ? proposal.isStale : true;
    setPendingResume(null);

    video
      .play()
      .then(() => {
        setIsPlaying(true);
      })
      .catch(() => {
        setPendingResume({ time: t, isStale });
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isPresentationActive, transport?.resumeProposal]);

  const handleTapToResume = useCallback(() => {
    if (!pendingResume) return;
    const video = mediaRef.current;
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

  // ── Media failure recovery ──────────────────────────────────────────────
  // Retry re-issues the load on the current source: `load()` re-runs the
  // resource selection algorithm (picking up a source the host swapped in
  // after the failure), then playback resumes. `load()` also rewinds the
  // element to 0:00, so the position is restored as soon as the new resource
  // has metadata — a mid-service stall must not restart the whole set.
  //
  // The restore listener is once-only AND explicitly removed in the error
  // path: a retry whose play() rejects leaves the overlay up, and an
  // orphaned listener would seek a LATER successful load back to this
  // failure's position (issue #210).
  const handleRetryMedia = useCallback((resumeAtOverride?: number) => {
    const element = mediaRef.current;
    if (!element) return;
    const resumeAt =
      resumeAtOverride ?? (Number.isFinite(element.currentTime) ? element.currentTime : 0);
    setMediaFailure(null);
    try {
      element.load();
    } catch {
      /* best-effort: reload can throw on a detached element */
    }
    if (resumeAt > 0) {
      const restorePosition = () => {
        element.removeEventListener("loadedmetadata", restorePosition);
        try {
          element.currentTime = resumeAt;
        } catch {
          /* best-effort: seeking can throw while the resource is unavailable */
        }
      };
      element.addEventListener("loadedmetadata", restorePosition, { once: true });
      // Clean up when this retry fails: the play rejection surfaces the
      // overlay again, and the restore listener must not survive to rewind
      // the next load.
      element
        .play()
        .then(() => {
          setIsPlaying(true);
        })
        .catch((err) => {
          element.removeEventListener("loadedmetadata", restorePosition);
          console.error("Play failed:", err);
          toast.error(t("controller.toastPlaybackFailed"));
        });
    } else {
      element
        .play()
        .then(() => {
          setIsPlaying(true);
        })
        .catch((err) => {
          console.error("Play failed:", err);
          toast.error(t("controller.toastPlaybackFailed"));
        });
    }
  }, [t]);

  // Host-takeover recovery: when the host resolved a media error with a src
  // swap, this effect fires on the new `mediaSrc` and re-issues the load +
  // play on it, resuming at the captured failure position via the same
  // once-only loadedmetadata restore path Retry uses. Refs are false/0 on
  // mount, so the effect is a no-op on boot — no autoplay.
  useEffect(() => {
    if (!recoveryPendingRef.current) return;
    recoveryPendingRef.current = false;
    handleRetryMedia(recoveryResumeAtRef.current);
  }, [mediaSrc, handleRetryMedia]);

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
  // The Cast button renders whenever Cast availability is known (rather than
  // only when `isCastSupported` is true). This is critical: `isSupported` is
  // only set to true on the full SDK-load-success path, but the diagnostic
  // bottom sheet must be reachable from the "unavailable" branch (iOS, missing
  // receiver app id, SDK globals absent, SDK script blocked) — otherwise the
  // disabled-but-tappable button never renders and the diagnostic UX is dead
  // code in production. When availability is still "unknown" (SDK load window),
  // no Cast UI renders to avoid premature taps.
  //
  // Offline media hides every transport entry point: the receiver fetches the
  // artifacts itself, and in an offline boot neither the network nor the
  // session that mints a signed URL is available — the player is local-only.
  const showCastButton =
    !isOfflineMedia && castAvailability !== "unknown" && !isPresentationActive;
  // Presentation API fallback launch button: rendered when Cast is confirmed
  // unsupported (not during the SDK load window, where `isCastSupported` is
  // false but `castAvailability` is still "unknown"). Gating on
  // `castAvailability !== "unknown"` prevents the fallback button from
  // rendering during the SDK load window on Android Chrome, where the
  // Presentation API is also available — which would otherwise let a tap
  // start a Presentation session that the Cast transport would later
  // shadow once `isSupported` flips to true.
  const showPresentationFallbackButton =
    !isOfflineMedia &&
    isCastSupported === false &&
    castAvailability !== "unknown" &&
    (presentationFallback?.isSupported ?? false) === true &&
    !isPresentationActive;
  const castUnavailable = castAvailability === "unavailable";
  const showIphoneFallback =
    !isOfflineMedia &&
    isCastSupported === false &&
    castAvailability !== "unknown" &&
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
      {/* Media: <video> normally, <audio> for an offline audio-only render
          (an MP3-only songset has no video track to show). */}
      <div className="flex-1 relative">
        {isAudioOnly ? (
          <audio
            ref={setMediaElement}
            src={mediaSrc}
            className="hidden"
            muted={isPresentationActive}
            data-testid="audio-element"
          />
        ) : (
          <video
            ref={setMediaElement}
            src={mediaSrc}
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
        )}

        {/* Top bar */}
        <div
          className={cn(
            "absolute top-0 left-0 right-0 p-4 transition-opacity duration-300",
            controlsVisible || isPresentationActive ? "opacity-100" : "opacity-0"
          )}
        >
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2" data-testid="playback-left-actions">
              <Button
                variant="ghost"
                size="icon"
                className="size-10 text-white hover:bg-white/20"
                onClick={handleExit}
                aria-label={t("controller.backAriaLabel")}
              >
                <ArrowLeft className="size-5" />
              </Button>

              {!isFullscreen && (canDocumentFullscreen || canVideoFullscreen) && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-10 text-white hover:bg-white/20"
                  onClick={handleReenterFullscreen}
                  aria-label={
                    canDocumentFullscreen
                      ? t("controller.reenterFullscreen")
                      : t("controller.enterFullscreen")
                  }
                >
                  <Maximize className="size-5" />
                </Button>
              )}
            </div>

            <div className="flex items-center gap-2">
              {/* Presentation status */}
              {isPresentationActive && (
                <div className="flex items-center gap-2 px-3 py-1.5 bg-green-500/20 text-green-400 rounded-full text-sm">
                  <div className="w-2 h-2 bg-green-400 rounded-full animate-pulse" />
                  <span>
                    {t("controller.connectedTo")} {transport?.deviceName ? transport.deviceName : t("controller.tv")}
                  </span>
                </div>
              )}

              {isPresentationActive && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-10 text-white hover:bg-white/20"
                  onClick={handleStopPresentation}
                  aria-label={t("controller.closeTvView")}
                  data-testid="presentation-close-button"
                >
                  <MonitorOff className="size-5" />
                </Button>
              )}

              {/* Offline hint: the controller booted from the downloaded copy */}
              {isOfflineMedia && (
                <div
                  className="flex items-center gap-2 px-3 py-1.5 bg-white/10 text-white/80 rounded-full text-xs"
                  data-testid="offline-hint"
                >
                  <WifiOff className="size-3" />
                  <span>{t("controller.offlinePlayback")}</span>
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
                      ? t("controller.bufferingActionable")
                      : t("controller.buffering")}
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
                  aria-label={castUnavailable ? t("controller.castUnavailable") : t("controller.sendToTV")}
                  data-testid="cast-button"
                >
                  {isCastConnecting ? (
                    <Loader2 className="size-5 animate-spin" />
                  ) : (
                    <Monitor className="size-5" />
                  )}
                </Button>
              )}

              {/* Presentation API fallback Send-to-TV button (dev-only,
                  iOS / non-Cast browsers). Routes to the controller page's
                  sender.start() via onSendToTV. */}
              {showPresentationFallbackButton && (
                <Button
                  variant="ghost"
                  size="icon"
                  className="size-10 text-white hover:bg-white/20"
                  onClick={() => onSendToTVRef.current?.()}
                  aria-label={t("controller.sendToTV")}
                  data-testid="presentation-send-to-tv-button"
                >
                  <Monitor className="size-5" />
                </Button>
              )}

              {/* iPhone fallback: Cast unsupported and Presentation unsupported */}
              {showIphoneFallback && (
                <a
                  href={`${getMarketingUrl(locale)}/docs#airplay`}
                  target="_blank"
                  rel="noreferrer"
                  className="flex items-center gap-1.5 px-3 py-1.5 bg-white/10 text-white/80 rounded-full text-xs hover:bg-white/20"
                  data-testid="airplay-fallback"
                >
                  <Monitor className="size-3" />
                  <span>
                    {t("controller.airplayFallback")}
                  </span>
                </a>
              )}
            </div>

            {/* Wake lock indicator */}
            {wakeLockSupported && (
              <div className="text-white/50 text-xs">
                {t("controller.screenStaysOn")}
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
                ? `${t("controller.resumeStale")} ${formatTime(
                    pendingResume.time
                  )}`
                : `${t("controller.tapToResume")} ${formatTime(pendingResume.time)}`}
            </span>
          </button>
        )}

        {/* Media failure overlay: a hard element error. Actionable — Retry
            re-issues the load on whatever source the host has in place by
            then. */}
        {mediaFailure && (
          <div
            role="alert"
            className="absolute inset-0 z-[85] flex items-center justify-center bg-black/85 p-6"
            data-testid="media-failure-overlay"
          >
            <div className="w-full max-w-sm rounded-lg bg-amber-500/95 text-black p-5 text-center shadow-lg">
              <div className="flex items-center justify-center gap-2 font-medium">
                <AlertTriangle className="size-5 shrink-0" />
                <span data-testid="media-failure-title">
                  {t("controller.mediaFailed")}
                </span>
              </div>
              <p className="mt-2 text-sm">
                {isOfflineMedia
                  ? t("controller.mediaFailedOfflineDesc")
                  : t("controller.mediaFailedDesc")}
              </p>
              <Button
                size="sm"
                className="mt-4 bg-black text-white hover:bg-black/80"
                onClick={() => handleRetryMedia()}
                data-testid="media-retry-button"
              >
                {t("controller.retry")}
              </Button>
            </div>
          </div>
        )}

        {/* iOS Info Toast */}
        {showIosInfo && (
          <div className="absolute top-16 left-4 right-4 bg-blue-500/90 text-white p-4 rounded-lg shadow-lg">
            <div className="flex items-start gap-3">
              <Info className="size-5 shrink-0 mt-0.5" />
              <div className="flex-1">
                <p className="font-medium">{t("controller.iosTitle")}</p>
                <p className="text-sm text-white/80 mt-1">
                  {t("controller.iosDesc")}
                </p>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="size-8 text-white hover:bg-white/20 shrink-0"
                onClick={() => setShowIosInfo(false)}
                aria-label={t("controller.dismissInfo")}
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
          aria-label={t("controller.keyboardShortcuts")}
          data-testid="keyboard-shortcuts-hint"
        >
          <div className="bg-black/60 text-white/75 rounded-lg px-3 py-2 text-xs">
            <div className="grid grid-cols-2 gap-x-4 gap-y-0.5">
              <span><kbd className="font-mono text-white/90">Space</kbd> {t("controller.kbSpacePlayPause")}</span>
              <span><kbd className="font-mono text-white/90">←</kbd>/<kbd className="font-mono text-white/90">→</kbd> {t("controller.kbSeek10s")}</span>
              <span><kbd className="font-mono text-white/90">[</kbd> {t("controller.kbPrevSong")}</span>
              <span><kbd className="font-mono text-white/90">]</kbd> {t("controller.kbNextSong")}</span>
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

      <LyricJumpList
        chapters={chapters}
        currentTime={effectiveCurrentTime}
        currentSongIndex={currentSongIndex}
        onJumpToLine={handleJumpToLine}
        currentRecordingContentHash={
          (currentSong.fromPlayback ? chapterRecordingHashes?.[currentSong.index] : null) ?? null
        }
      />

      {/* Diagnostic bottom sheet (Cast unavailable) */}
      <Sheet
        open={showDiagnosticSheet}
        onOpenChange={setShowDiagnosticSheet}
      >
        <SheetContent side="bottom" data-testid="diagnostic-sheet">
          <SheetHeader>
            <SheetTitle>{t("controller.diagTitle")}</SheetTitle>
            <SheetDescription>
              {t("controller.diagDesc")}
            </SheetDescription>
          </SheetHeader>
          <ol className="list-decimal space-y-2 px-4 pb-6 text-sm text-muted-foreground">
            <li>{t("controller.diag.1")}</li>
            <li>{t("controller.diag.2")}</li>
            <li>{t("controller.diag.3")}</li>
            <li>{t("controller.diag.4")}</li>
          </ol>
        </SheetContent>
      </Sheet>
    </div>
  );
}
