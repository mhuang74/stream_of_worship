"use client";

import { useCallback, useEffect, useState } from "react";
import { useAudioPlayerContext } from "@/contexts/AudioPlayerContext";
import { resolveSongAudioSrc } from "@/lib/audio/audio-bytes-cache";
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
 * Cached blob URL when the Recording's bytes are reachable through the
 * same-origin proxy; otherwise the streaming URL this hook always used
 * (public bucket, then a freshly signed URL). Never throws for a
 * cache-path failure: playback must not regress on a surface that used to
 * stream straight from the network.
 */
async function resolvePlaybackSrc(hashPrefix: string): Promise<string> {
  try {
    return await resolveSongAudioSrc(hashPrefix);
  } catch {
    const publicUrl = getPublicAudioUrl(hashPrefix);
    if (publicUrl) return publicUrl;
    const res = await fetch("/api/signed-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hashPrefix, fileType: "audio" }),
    });
    if (!res.ok) throw new Error("Failed to get audio URL");
    const data = (await res.json()) as { url: string };
    return data.url;
  }
}

/**
 * Shared song-preview playback logic used by song lists, browse sheets,
 * semantic search results, and the favorites page. Resolves a song id to a
 * playable recording, plays it from the shared audio byte cache (falling back
 * to the public R2 URL, then a signed URL), and tracks the currently playing
 * song so callers can highlight the active card and toggle play/pause on
 * re-click.
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

      // A second click while the bytes load must not start a second load.
      if (previewLoadingSongId === songId) return;

      const recording = song.recording;
      setPreviewLoadingSongId(songId);

      try {
        const src = await resolvePlaybackSrc(recording.hashPrefix);

        play({
          id: `song-${songId}`,
          title: song.title,
          artist: song.artist,
          src,
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
      previewLoadingSongId,
      play,
      pause,
      originSongsetId,
      noAudioMessage,
      failedToLoadMessage,
    ]
  );

  // Clear the now-playing highlight shortly after playback stops. The timer
  // is only scheduled while a highlight is actually pending, so a callback
  // queued before a play starts can never clear the new track's highlight.
  useEffect(() => {
    if (playingSongId === null) return;
    if (currentTrack && playerState.isPlaying) return;
    const timeout = setTimeout(() => {
      setPlayingSongId(null);
    }, 200);
    return () => clearTimeout(timeout);
  }, [currentTrack, playingSongId, playerState.isPlaying]);

  const reset = useCallback(() => {
    setPlayingSongId(null);
    setPreviewLoadingSongId(null);
  }, []);

  return { playingSongId, previewLoadingSongId, handlePlay, reset };
}
