"use client";

import {
  createContext,
  useContext,
  useState,
  useCallback,
  useRef,
  useEffect,
} from "react";

export type AudioTrackType = "song" | "transition" | "lyrics-loop";

export interface AudioTrack {
  id: string;
  title: string;
  artist: string;
  src: string;
  type: AudioTrackType;
  duration?: number;
  loopStart?: number;
  loopEnd?: number;
}

export interface AudioPlayerState {
  isPlaying: boolean;
  currentTime: number;
  duration: number;
  volume: number;
  isMuted: boolean;
  isLooping: boolean;
  loopWindowStart: number;
  loopWindowEnd: number;
}

interface AudioPlayerContextValue {
  // Current track
  currentTrack: AudioTrack | null;
  // Player state
  state: AudioPlayerState;
  // Actions
  play: (track: AudioTrack) => void;
  pause: () => void;
  togglePlay: () => void;
  seek: (time: number) => void;
  setVolume: (volume: number) => void;
  toggleMute: () => void;
  toggleLoop: () => void;
  setLoopWindow: (start: number, end: number) => void;
  clearLoopWindow: () => void;
  stop: () => void;
  // Audio element ref (for advanced use)
  audioRef: React.RefObject<HTMLAudioElement | null>;
}

const defaultState: AudioPlayerState = {
  isPlaying: false,
  currentTime: 0,
  duration: 0,
  volume: 1,
  isMuted: false,
  isLooping: false,
  loopWindowStart: 0,
  loopWindowEnd: 0,
};

const AudioPlayerContext = createContext<AudioPlayerContextValue | null>(null);

export function AudioPlayerProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [currentTrack, setCurrentTrack] = useState<AudioTrack | null>(null);
  const [state, setState] = useState<AudioPlayerState>(defaultState);

  const isLoopingRef = useRef(false);
  const loopWindowStartRef = useRef(0);
  const loopWindowEndRef = useRef(0);

  useEffect(() => {
    isLoopingRef.current = state.isLooping;
    loopWindowStartRef.current = state.loopWindowStart;
    loopWindowEndRef.current = state.loopWindowEnd;
  });

  const trackDurationRef = useRef<number>(0);
  
  // Update duration ref when track changes
  useEffect(() => {
    trackDurationRef.current = currentTrack?.duration || 0;
  }, [currentTrack]);

  // Handle audio events
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;

    const handleTimeUpdate = () => {
      setState((prev) => ({
        ...prev,
        currentTime: audio.currentTime,
      }));

      // Handle loop window
      if (isLoopingRef.current && loopWindowEndRef.current > 0) {
        if (audio.currentTime >= loopWindowEndRef.current) {
          audio.currentTime = loopWindowStartRef.current;
        }
      }
    };

    const handleLoadedMetadata = () => {
      setState((prev) => ({
        ...prev,
        duration: audio.duration || prev.duration,
      }));
    };

    const handleEnded = () => {
      setState((prev) => ({ ...prev, isPlaying: false, currentTime: 0 }));
    };

    const handleVolumeChange = () => {
      setState((prev) => ({
        ...prev,
        volume: audio.volume,
        isMuted: audio.muted,
      }));
    };

    audio.addEventListener("timeupdate", handleTimeUpdate);
    audio.addEventListener("loadedmetadata", handleLoadedMetadata);
    audio.addEventListener("ended", handleEnded);
    audio.addEventListener("volumechange", handleVolumeChange);

    return () => {
      audio.removeEventListener("timeupdate", handleTimeUpdate);
      audio.removeEventListener("loadedmetadata", handleLoadedMetadata);
      audio.removeEventListener("ended", handleEnded);
      audio.removeEventListener("volumechange", handleVolumeChange);
    };
  }, []);

  const play = useCallback((track: AudioTrack) => {
    setCurrentTrack(track);
    setState((prev) => ({
      ...prev,
      isPlaying: true,
      duration: track.duration || prev.duration,
      isLooping: track.type === "lyrics-loop",
      loopWindowStart: track.loopStart || 0,
      loopWindowEnd: track.loopEnd || 0,
    }));

    // Use setTimeout to ensure state update before playing
    setTimeout(() => {
      if (audioRef.current) {
        audioRef.current.src = track.src;
        const playPromise = audioRef.current.play();
        if (playPromise && typeof playPromise.catch === "function") {
          playPromise.catch(() => {
            // Auto-play blocked, user needs to interact
            setState((prev) => ({ ...prev, isPlaying: false }));
          });
        }
      }
    }, 0);
  }, []);

  const pause = useCallback(() => {
    setState((prev) => ({ ...prev, isPlaying: false }));
    audioRef.current?.pause();
  }, []);

  const togglePlay = useCallback(() => {
    if (state.isPlaying) {
      pause();
    } else if (currentTrack) {
      setState((prev) => ({ ...prev, isPlaying: true }));
      const playPromise = audioRef.current?.play();
      if (playPromise && typeof playPromise.catch === "function") {
        playPromise.catch(() => {
          setState((prev) => ({ ...prev, isPlaying: false }));
        });
      }
    }
  }, [state.isPlaying, currentTrack, pause]);

  const seek = useCallback((time: number) => {
    if (audioRef.current) {
      const clampedTime = Math.max(0, Math.min(time, state.duration || time));
      audioRef.current.currentTime = clampedTime;
      setState((prev) => ({ ...prev, currentTime: clampedTime }));
    }
  }, [state.duration]);

  const setVolume = useCallback((volume: number) => {
    const clampedVolume = Math.max(0, Math.min(1, volume));
    if (audioRef.current) {
      audioRef.current.volume = clampedVolume;
    }
    setState((prev) => ({ ...prev, volume: clampedVolume }));
  }, []);

  const toggleMute = useCallback(() => {
    setState((prev) => {
      const newMuted = !prev.isMuted;
      if (audioRef.current) {
        audioRef.current.muted = newMuted;
      }
      return { ...prev, isMuted: newMuted };
    });
  }, []);

  const toggleLoop = useCallback(() => {
    setState((prev) => ({ ...prev, isLooping: !prev.isLooping }));
  }, []);

  const setLoopWindow = useCallback((start: number, end: number) => {
    setState((prev) => ({
      ...prev,
      isLooping: true,
      loopWindowStart: start,
      loopWindowEnd: end,
    }));
  }, []);

  const clearLoopWindow = useCallback(() => {
    setState((prev) => ({
      ...prev,
      isLooping: false,
      loopWindowStart: 0,
      loopWindowEnd: 0,
    }));
  }, []);

  const stop = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      audioRef.current.src = "";
    }
    setCurrentTrack(null);
    setState((prev) => ({
      ...prev,
      isPlaying: false,
      currentTime: 0,
      isLooping: false,
      loopWindowStart: 0,
      loopWindowEnd: 0,
    }));
  }, []);

  const value: AudioPlayerContextValue = {
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
    audioRef,
  };

  return (
    <AudioPlayerContext.Provider value={value}>
      {children}
      {/* Hidden audio element */}
      <audio ref={audioRef} preload="metadata" />
    </AudioPlayerContext.Provider>
  );
}

export function useAudioPlayerContext(): AudioPlayerContextValue {
  const ctx = useContext(AudioPlayerContext);
  if (!ctx) {
    throw new Error(
      "useAudioPlayerContext must be used within an AudioPlayerProvider"
    );
  }
  return ctx;
}
