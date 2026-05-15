"use client";

import { useCallback } from "react";
import {
  useAudioPlayerContext,
  AudioTrack,
  AudioTrackType,
} from "@/contexts/AudioPlayerContext";

interface PlaySongOptions {
  songId: string;
  title: string;
  artist: string;
  src: string;
  duration?: number;
}

interface PlayTransitionOptions {
  transitionId: string;
  fromSongTitle: string;
  toSongTitle: string;
  src: string;
  duration?: number;
}

interface PlayLyricsLoopOptions {
  songId: string;
  title: string;
  artist: string;
  src: string;
  loopStartSeconds: number;
  loopDurationSeconds: number;
}

export function useAudioPlayer() {
  const {
    currentTrack,
    state,
    play,
    pause,
    togglePlay,
    seek,
    setVolume,
    toggleMute,
    toggleLoop,
    setLoopWindow,
    clearLoopWindow,
    stop,
  } = useAudioPlayerContext();

  const playSong = useCallback(
    (options: PlaySongOptions) => {
      const track: AudioTrack = {
        id: `song-${options.songId}`,
        title: options.title,
        artist: options.artist,
        src: options.src,
        type: "song" as AudioTrackType,
        duration: options.duration,
      };
      play(track);
    },
    [play]
  );

  const playTransition = useCallback(
    (options: PlayTransitionOptions) => {
      const track: AudioTrack = {
        id: `transition-${options.transitionId}`,
        title: `${options.fromSongTitle} → ${options.toSongTitle}`,
        artist: "Transition Preview",
        src: options.src,
        type: "transition" as AudioTrackType,
        duration: options.duration,
      };
      play(track);
    },
    [play]
  );

  const playLyricsLoop = useCallback(
    (options: PlayLyricsLoopOptions) => {
      const track: AudioTrack = {
        id: `lyrics-loop-${options.songId}`,
        title: options.title,
        artist: options.artist,
        src: options.src,
        type: "lyrics-loop" as AudioTrackType,
        duration: options.duration,
        loopStart: options.loopStartSeconds,
        loopEnd: options.loopStartSeconds + options.loopDurationSeconds,
      };
      play(track);
    },
    [play]
  );

  const formatTime = useCallback((seconds: number): string => {
    if (!isFinite(seconds) || seconds < 0) return "0:00";
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  }, []);

  const seekRelative = useCallback(
    (deltaSeconds: number) => {
      const newTime = state.currentTime + deltaSeconds;
      seek(newTime);
    },
    [state.currentTime, seek]
  );

  return {
    // Current track info
    currentTrack,
    isPlaying: state.isPlaying,
    currentTime: state.currentTime,
    duration: state.duration,
    volume: state.volume,
    isMuted: state.isMuted,
    isLooping: state.isLooping,
    loopWindowStart: state.loopWindowStart,
    loopWindowEnd: state.loopWindowEnd,
    // Formatted time strings
    formattedCurrentTime: formatTime(state.currentTime),
    formattedDuration: formatTime(state.duration),
    formattedLoopStart: formatTime(state.loopWindowStart),
    formattedLoopEnd: formatTime(state.loopWindowEnd),
    // Progress percentage (0-100)
    progress: state.duration > 0 ? (state.currentTime / state.duration) * 100 : 0,
    // Actions
    playSong,
    playTransition,
    playLyricsLoop,
    play,
    pause,
    togglePlay,
    seek,
    seekRelative,
    setVolume,
    toggleMute,
    toggleLoop,
    setLoopWindow,
    clearLoopWindow,
    stop,
  };
}
