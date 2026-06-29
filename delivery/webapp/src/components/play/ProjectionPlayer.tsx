"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useWakeLock } from "@/hooks/useWakeLock";
import { usePresentationReceiver } from "@/hooks/usePresentation";
import type { PresentationStatus } from "@/types/presentation-api";

export interface ProjectionPlayerProps {
  videoSrc: string;
  initialSongTitle?: string;
}

export function ProjectionPlayer({ videoSrc, initialSongTitle }: ProjectionPlayerProps) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const titleTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const [songTitle, setSongTitle] = useState(initialSongTitle ?? "");
  const [titleVisible, setTitleVisible] = useState(!!initialSongTitle);

  useWakeLock();

  // Landscape orientation lock - fail gracefully
  useEffect(() => {
    const lockOrientation = async () => {
      try {
        const orientation = screen.orientation as ScreenOrientation & {
          lock?: (type: string) => Promise<void>;
        };
        if (
          typeof window !== "undefined" &&
          orientation &&
          typeof orientation.lock === "function"
        ) {
          await orientation.lock("landscape");
        }
      } catch {
        // Orientation lock not supported or permission denied
      }
    };

    lockOrientation();

    return () => {
      try {
        if (
          typeof window !== "undefined" &&
          screen.orientation &&
          typeof screen.orientation.unlock === "function"
        ) {
          screen.orientation.unlock();
        }
      } catch {
        // Ignore unlock errors
      }
    };
  }, []);

  // Show title temporarily with 2s fade timer
  const showTitleTemporarily = useCallback(() => {
    setTitleVisible(true);

    if (titleTimeoutRef.current) {
      clearTimeout(titleTimeoutRef.current);
    }

    titleTimeoutRef.current = setTimeout(() => {
      setTitleVisible(false);
    }, 2000);
  }, []);

  // Show initial title on mount
  useEffect(() => {
    if (initialSongTitle) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      showTitleTemporarily();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Cleanup timeout on unmount
  useEffect(() => {
    return () => {
      if (titleTimeoutRef.current) {
        clearTimeout(titleTimeoutRef.current);
      }
    };
  }, []);

  // `sendStatus` is populated by `usePresentationReceiver` (defined below)
  // but `handlePlay` — passed into that hook — needs to emit a transport-
  // relevant `error` status when `video.play()` rejects. Break the cycle with a
  // ref that the hook result writes to on mount.
  const sendStatusRef = useRef<((status: PresentationStatus) => void) | null>(null);

  const buildMediaStatus = useCallback((video: HTMLVideoElement): PresentationStatus => {
    const currentTime =
      Number.isFinite(video.currentTime) && video.currentTime >= 0 ? video.currentTime : 0;
    const duration = Number.isFinite(video.duration) && video.duration >= 0 ? video.duration : 0;
    return {
      type: "media",
      currentTime,
      duration,
      playerState: video.paused ? "paused" : "playing",
      volume: Math.max(0, Math.min(1, video.volume)),
      isMuted: video.muted,
    };
  }, []);

  const sendMediaStatus = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    sendStatusRef.current?.(buildMediaStatus(video));
  }, [buildMediaStatus]);

  const handlePlay = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    video.play().then(sendMediaStatus).catch(() => {
      sendStatusRef.current?.({
        type: "error",
        message: "TV projection failed — check connection",
      });
    });
  }, [sendMediaStatus]);

  const handlePause = useCallback(() => {
    videoRef.current?.pause();
    sendMediaStatus();
  }, [sendMediaStatus]);

  const handleSeek = useCallback((positionSeconds: number) => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = positionSeconds;
    sendMediaStatus();
  }, [sendMediaStatus]);

  const handleVolume = useCallback((level: number) => {
    const video = videoRef.current;
    if (!video) return;
    video.volume = Math.max(0, Math.min(1, level));
    video.muted = level === 0;
    sendMediaStatus();
  }, [sendMediaStatus]);

  // Mute is its own command on the wire (distinct from volume level). On the
  // Presentation fallback path the receiver `<video>` mute bit is toggled
  // directly; volume level is preserved for unmute.
  const handleMute = useCallback((muted: boolean) => {
    const video = videoRef.current;
    if (!video) return;
    video.muted = muted;
    sendMediaStatus();
  }, [sendMediaStatus]);

  const handleSongTitle = useCallback(
    (title: string) => {
      setSongTitle(title);
      showTitleTemporarily();
    },
    [showTitleTemporarily]
  );

  const { sendStatus } = usePresentationReceiver({
    onPlay: handlePlay,
    onPause: handlePause,
    onSeek: handleSeek,
    onVolume: handleVolume,
    onMute: handleMute,
    onSongTitle: handleSongTitle,
  });

  // Expose sendStatus to the handlePlay catch (defined above) now that the
  // hook has returned it.
  useEffect(() => {
    sendStatusRef.current = sendStatus;
  }, [sendStatus]);

  // Notify the controlling page when the receiver is ready to play. `ready`
  // fires on `loadedmetadata` / `canplay` (the receiver has enough data to
  // begin playback). Transport-relevant `error` is emitted from `handlePlay`
  // on `video.play()` rejection (autoplay block / decoder failure) — not on
  // every media error event — so the controller can surface an actionable
  // "TV projection failed — check connection" toast.
  const handleLoadedMetadata = useCallback(() => {
    sendStatus({ type: "ready" });
    sendMediaStatus();
  }, [sendStatus, sendMediaStatus]);

  const handleCanPlay = useCallback(() => {
    sendStatus({ type: "ready" });
    sendMediaStatus();
  }, [sendStatus, sendMediaStatus]);

  return (
    <div className="fixed inset-0 bg-black" data-testid="projection-player">
      {/* Video fills 100% viewport, object-fit: cover for landscape */}
      <video
        ref={videoRef}
        src={videoSrc}
        className="w-full h-full object-cover"
        playsInline
        aria-label="Projection video"
        onLoadedMetadata={handleLoadedMetadata}
        onCanPlay={handleCanPlay}
        onTimeUpdate={sendMediaStatus}
        onPlay={sendMediaStatus}
        onPause={sendMediaStatus}
        onSeeked={sendMediaStatus}
        onVolumeChange={sendMediaStatus}
      />

      {/* Song title overlay at top edge */}
      <div
        className="absolute top-0 left-0 right-0 px-4 py-2 transition-opacity duration-500"
        style={{ opacity: titleVisible ? 0.5 : 0 }}
        aria-live="polite"
        data-testid="song-title-overlay"
      >
        <p
          className="text-white truncate"
          style={{ fontSize: "14px", lineHeight: "1.4" }}
          data-testid="song-title-text"
        >
          {songTitle}
        </p>
      </div>
    </div>
  );
}
