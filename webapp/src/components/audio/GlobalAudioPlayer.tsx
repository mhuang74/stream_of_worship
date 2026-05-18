"use client";

import { usePathname } from "next/navigation";
import { AudioPlayerProvider } from "@/contexts/AudioPlayerContext";
import { AudioPlayerBar } from "./AudioPlayerBar";

interface GlobalAudioPlayerProps {
  children: React.ReactNode;
}

export function GlobalAudioPlayer({ children }: GlobalAudioPlayerProps) {
  const pathname = usePathname();
  const isControllerPage = pathname.includes("/play/controller");

  return (
    <AudioPlayerProvider>
      {children}
      {!isControllerPage && <AudioPlayerBar />}
    </AudioPlayerProvider>
  );
}
