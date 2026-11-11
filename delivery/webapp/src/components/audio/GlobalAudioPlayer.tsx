"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";
import { AudioPlayerProvider, useAudioPlayerContext } from "@/contexts/AudioPlayerContext";
import { SongsetListenProvider, useSongsetListen } from "@/contexts/SongsetListenContext";
import { AudioPlayerBar } from "./AudioPlayerBar";
import { SongsetPlayerBar } from "./SongsetPlayerBar";

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

/** One dock: a Songset audition replaces the Song transport's bar. */
function PlayerDock() {
  const { request } = useSongsetListen();
  return request ? <SongsetPlayerBar /> : <AudioPlayerBar />;
}

export function GlobalAudioPlayer({ children }: GlobalAudioPlayerProps) {
  const pathname = usePathname();
  const isControllerPage = pathname.includes("/play/controller") || pathname === "/admin/lyrics-review";

  return (
    <AudioPlayerProvider>
      <SongsetListenProvider enabled={!isControllerPage}>
        <PlaybarRouteGuard isControllerPage={isControllerPage} />
        {children}
        {!isControllerPage && <PlayerDock />}
      </SongsetListenProvider>
    </AudioPlayerProvider>
  );
}
