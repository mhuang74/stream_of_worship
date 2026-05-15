"use client";

import { AudioPlayerProvider } from "@/contexts/AudioPlayerContext";
import { AudioPlayerBar } from "./AudioPlayerBar";

interface GlobalAudioPlayerProps {
  children: React.ReactNode;
}

export function GlobalAudioPlayer({ children }: GlobalAudioPlayerProps) {
  return (
    <AudioPlayerProvider>
      {children}
      <AudioPlayerBar />
    </AudioPlayerProvider>
  );
}
