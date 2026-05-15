"use client";

import { useEffect, useRef } from "react";

export interface MediaSessionMetadata {
  title: string;
  artist?: string;
  album?: string;
  artwork?: MediaImage[];
}

export interface MediaSessionActions {
  onPlay?: () => void;
  onPause?: () => void;
  onPrevSong?: () => void;
  onNextSong?: () => void;
  onSeekBack?: () => void;
  onSeekForward?: () => void;
}

function isMediaSessionAvailable(): boolean {
  if (typeof navigator === "undefined") return false;
  if (!("mediaSession" in navigator)) return false;
  return navigator.mediaSession != null;
}

export function useMediaSession(
  metadata: MediaSessionMetadata | null,
  actions: MediaSessionActions
) {
  const actionsRef = useRef(actions);

  useEffect(() => {
    actionsRef.current = actions;
  });

  useEffect(() => {
    if (!isMediaSessionAvailable()) return;
    if (!metadata) return;

    const MediaMetadataCtor =
      typeof MediaMetadata !== "undefined" ? MediaMetadata : null;

    if (MediaMetadataCtor) {
      navigator.mediaSession.metadata = new MediaMetadataCtor({
        title: metadata.title,
        artist: metadata.artist || "",
        album: metadata.album || "",
        artwork: metadata.artwork || [],
      });
    }
  }, [metadata]);

  useEffect(() => {
    if (!isMediaSessionAvailable()) return;

    const wrapAction = (key: keyof MediaSessionActions) => {
      const action = actionsRef.current[key];
      return action ? () => actionsRef.current[key]?.() : null;
    };

    navigator.mediaSession.setActionHandler("play", wrapAction("onPlay"));
    navigator.mediaSession.setActionHandler("pause", wrapAction("onPause"));
    navigator.mediaSession.setActionHandler("previoustrack", wrapAction("onPrevSong"));
    navigator.mediaSession.setActionHandler("nexttrack", wrapAction("onNextSong"));
    navigator.mediaSession.setActionHandler("seekbackward", wrapAction("onSeekBack"));
    navigator.mediaSession.setActionHandler("seekforward", wrapAction("onSeekForward"));

    return () => {
      try {
        if (!isMediaSessionAvailable()) return;
        navigator.mediaSession.setActionHandler("play", null);
        navigator.mediaSession.setActionHandler("pause", null);
        navigator.mediaSession.setActionHandler("previoustrack", null);
        navigator.mediaSession.setActionHandler("nexttrack", null);
        navigator.mediaSession.setActionHandler("seekbackward", null);
        navigator.mediaSession.setActionHandler("seekforward", null);
      } catch {
        // Ignore errors during cleanup
      }
    };
  }, []);

  const updatePlaybackState = (state: MediaSessionPlaybackState) => {
    if (!isMediaSessionAvailable()) return;

    navigator.mediaSession.playbackState = state;
  };

  const updatePositionState = (options: {
    duration?: number;
    playbackRate?: number;
    position?: number;
  }) => {
    if (!isMediaSessionAvailable()) return;

    try {
      navigator.mediaSession.setPositionState(options);
    } catch {
      // Invalid position state (e.g., negative duration)
    }
  };

  return { updatePlaybackState, updatePositionState };
}
