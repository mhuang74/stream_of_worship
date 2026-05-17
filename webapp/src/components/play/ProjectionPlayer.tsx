"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useWakeLock } from "@/hooks/useWakeLock";
import { usePresentationReceiver } from "@/hooks/usePresentation";

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

  const handlePlay = useCallback(() => {
    videoRef.current?.play().catch(() => {});
  }, []);

  const handlePause = useCallback(() => {
    videoRef.current?.pause();
  }, []);

  const handleSeek = useCallback((positionSeconds: number) => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = positionSeconds;
  }, []);

  const handleVolume = useCallback((level: number) => {
    const video = videoRef.current;
    if (!video) return;
    video.volume = Math.max(0, Math.min(1, level));
    video.muted = level === 0;
  }, []);

  const handleSongTitle = useCallback(
    (title: string) => {
      setSongTitle(title);
      showTitleTemporarily();
    },
    [showTitleTemporarily]
  );

  usePresentationReceiver({
    onPlay: handlePlay,
    onPause: handlePause,
    onSeek: handleSeek,
    onVolume: handleVolume,
    onSongTitle: handleSongTitle,
  });

  return (
    <div className="fixed inset-0 bg-black" data-testid="projection-player">
      {/* Video fills 100% viewport, object-fit: cover for landscape */}
      <video
        ref={videoRef}
        src={videoSrc}
        className="w-full h-full object-cover"
        playsInline
        aria-label="Projection video"
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
