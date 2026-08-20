"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { AudioPlayerProvider, useAudioPlayerContext } from "@/contexts/AudioPlayerContext";
import { AudioPlayerBar } from "./AudioPlayerBar";

interface GlobalAudioPlayerProps {
  children: React.ReactNode;
}

function PlaybarRouteGuard({ isControllerPage }: { isControllerPage: boolean }) {
  const { stop } = useAudioPlayerContext();

  useEffect(() => {
    if (isControllerPage) {
      stop();
    }
  }, [isControllerPage, stop]);

  return null;
}

export function GlobalAudioPlayer({ children }: GlobalAudioPlayerProps) {
  const pathname = usePathname();
  const isControllerPage = pathname.includes("/play/controller");

  return (
    <AudioPlayerProvider>
      <PlaybarRouteGuard isControllerPage={isControllerPage} />
      {children}
      {!isControllerPage && <AudioPlayerBar />}
    </AudioPlayerProvider>
  );
}
