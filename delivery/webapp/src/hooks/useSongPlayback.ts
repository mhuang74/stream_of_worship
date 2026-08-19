"use client";

import { useCallback, useEffect, useState } from "react";
import { useAudioPlayerContext } from "@/contexts/AudioPlayerContext";
import { getPublicAudioUrl } from "@/lib/r2/public-url";
import { toast } from "sonner";

export interface PlayableSong {
  id: string;
  title: string;
  artist: string;
  recording: {
    hashPrefix: string;
    contentHash: string;
    durationSeconds: number | null;
  } | null;
}

interface UseSongPlaybackOptions {
  resolveSong: (songId: string) => PlayableSong | null;
  originSongsetId?: string;
  noAudioMessage: string;
  failedToLoadMessage: string;
}

/**
 * Shared song-preview playback logic used by song lists, browse sheets,
 * semantic search results, and the favorites page. Resolves a song id to a
 * playable recording, prefers the public R2 URL, and falls back to a signed
 * URL from `/api/signed-url`. Tracks the currently playing song so callers can
 * highlight the active card and toggle play/pause on re-click.
 */
export function useSongPlayback({
  resolveSong,
  originSongsetId,
  noAudioMessage,
  failedToLoadMessage,
}: UseSongPlaybackOptions) {
  const { currentTrack, state: playerState, play, pause } = useAudioPlayerContext();
  const [playingSongId, setPlayingSongId] = useState<string | null>(null);
  const [previewLoadingSongId, setPreviewLoadingSongId] = useState<string | null>(null);

  const handlePlay = useCallback(
    async (songId: string) => {
      const song = resolveSong(songId);
      if (!song?.recording) {
        toast.error(noAudioMessage);
        return;
      }

      if (playingSongId === songId && currentTrack?.id === `song-${songId}`) {
        if (playerState.isPlaying) {
          pause();
          setPlayingSongId(null);
          return;
        }
      }

      const recording = song.recording;
      const publicUrl = getPublicAudioUrl(recording.hashPrefix);

      if (publicUrl) {
        play({
          id: `song-${songId}`,
          title: song.title,
          artist: song.artist,
          src: publicUrl,
          type: "song",
          duration: recording.durationSeconds ?? undefined,
          recordingContentHash: recording.contentHash,
          songId,
          originSongsetId,
        });
        setPlayingSongId(songId);
        return;
      }

      setPreviewLoadingSongId(songId);

      try {
        const res = await fetch("/api/signed-url", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            hashPrefix: recording.hashPrefix,
            fileType: "audio",
          }),
        });

        if (!res.ok) throw new Error("Failed to get audio URL");

        const data = await res.json();

        play({
          id: `song-${songId}`,
          title: song.title,
          artist: song.artist,
          src: data.url,
          type: "song",
          duration: recording.durationSeconds ?? undefined,
          recordingContentHash: recording.contentHash,
          songId,
          originSongsetId,
        });

        setPlayingSongId(songId);
      } catch {
        toast.error(failedToLoadMessage);
      } finally {
        setPreviewLoadingSongId(null);
      }
    },
    [
      resolveSong,
      playingSongId,
      currentTrack,
      playerState.isPlaying,
      play,
      pause,
      originSongsetId,
      noAudioMessage,
      failedToLoadMessage,
    ]
  );

  // Clear the now-playing highlight shortly after playback stops.
  useEffect(() => {
    if (!currentTrack || !playerState.isPlaying) {
      const timeout = setTimeout(() => {
        if (!currentTrack || !playerState.isPlaying) {
          setPlayingSongId(null);
        }
      }, 200);
      return () => clearTimeout(timeout);
    }
  }, [currentTrack, playerState.isPlaying]);

  const reset = useCallback(() => {
    setPlayingSongId(null);
    setPreviewLoadingSongId(null);
  }, []);

  return { playingSongId, previewLoadingSongId, handlePlay, reset };
}
