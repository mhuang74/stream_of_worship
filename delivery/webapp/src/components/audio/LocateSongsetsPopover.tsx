"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useAudioPlayer } from "@/hooks/useAudioPlayer";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { MapPin, Loader2, ListMusic, ArrowUpRight } from "lucide-react";

interface ContainingSongset {
  id: string;
  name: string;
  description: string | null;
  updatedAt: string;
  itemCount: number;
  songPosition: number;
  isOrigin: boolean;
  owner: { id: number; name: string };
}

export function LocateSongsetsPopover() {
  const { currentTrack } = useAudioPlayer();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [songsets, setSongsets] = useState<ContainingSongset[]>([]);
  const [error, setError] = useState<string | null>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const hasOpenedRef = useRef(false);

  const songId = currentTrack?.type === "song" ? currentTrack.songId : undefined;
  const originSongsetId = currentTrack?.originSongsetId;

  const handleOpenChange = useCallback((newOpen: boolean) => {
    setOpen(newOpen);
    if (newOpen) {
      setLoading(true);
      setError(null);
      setSongsets([]);
    }
  }, []);

  useEffect(() => {
    if (!open || !songId) return;

    const params = new URLSearchParams();
    if (originSongsetId) params.set("origin", originSongsetId);

    const controller = new AbortController();

    fetch(`/api/songs/${encodeURIComponent(songId)}/songsets?${params.toString()}`, {
      signal: controller.signal,
    })
      .then(async (res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        setSongsets(data.songsets ?? []);
      })
      .catch((err) => {
        if (err?.name === "AbortError") return;
        console.error("Locate songsets failed:", err);
        setError("Failed to load songsets");
      })
      .finally(() => setLoading(false));

    return () => controller.abort();
  }, [open, songId, originSongsetId]);

  const handleSelect = useCallback(
    (songsetId: string) => {
      if (!songId) return;
      router.push(
        `/songsets/${songsetId}?highlightSong=${encodeURIComponent(songId)}`
      );
      setOpen(false);
    },
    [router, songId]
  );

  // Return focus to trigger when closed (only after the popover has been opened).
  useEffect(() => {
    if (open) {
      hasOpenedRef.current = true;
    } else if (hasOpenedRef.current && triggerRef.current) {
      triggerRef.current.focus();
    }
  }, [open]);

  if (!songId) return null;

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <Button
          ref={triggerRef}
          variant="ghost"
          size="icon-sm"
          className="shrink-0"
          aria-label="Find containing songsets"
          title="Find in songsets"
        >
          <MapPin className="size-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-72 p-0" align="start">
        {loading ? (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="size-5 animate-spin text-muted-foreground" />
          </div>
        ) : error ? (
          <div className="px-3 py-4 text-sm text-destructive" role="alert">
            {error}
          </div>
        ) : songsets.length === 0 ? (
          <div className="px-3 py-4 text-sm text-muted-foreground">
            This song is not in any of your songsets.
          </div>
        ) : (
          <div
            role="list"
            aria-label="Songsets containing this song"
            className="flex flex-col"
          >
            {songsets.map((ss) => (
              <button
                key={`${ss.id}-${ss.songPosition}`}
                role="listitem"
                tabIndex={0}
                onClick={() => handleSelect(ss.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    handleSelect(ss.id);
                  }
                }}
                className={cn(
                  "flex items-center gap-3 px-3 py-2 text-left hover:bg-accent focus:bg-accent transition-colors border-b last:border-b-0"
                )}
              >
                <ListMusic className="size-4 text-muted-foreground shrink-0" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{ss.name}</p>
                  <p className="text-xs text-muted-foreground">
                    {ss.itemCount} songs • Position {ss.songPosition + 1}
                  </p>
                </div>
                {ss.isOrigin && (
                  <span className="text-xs text-primary shrink-0 font-medium">
                    Origin
                  </span>
                )}
                <ArrowUpRight className="size-3.5 text-muted-foreground shrink-0" />
              </button>
            ))}
          </div>
        )}
      </PopoverContent>
    </Popover>
  );
}
