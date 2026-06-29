"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Play, Loader2, Monitor, AlertTriangle, Music, Clock } from "lucide-react";

interface PublicSongsetItem {
  id: string;
  position: number;
  songTitle: string | null;
  composer: string | null;
  lyricist: string | null;
  albumName: string | null;
  songMusicalKey: string | null;
  durationSeconds: number | null;
  tempoBpm: number | null;
  recordingMusicalKey: string | null;
}

interface ShareData {
  token: string;
  shareType: "songset" | "renderJob";
  songset: {
    id: string;
    name: string;
    description: string | null;
    totalDurationSeconds: number | null;
    renderState: "unrendered" | "rendering" | "fresh" | "stale" | "failed";
    latestRenderJobId: string | null;
    lastCompletedRenderJobId: string | null;
  };
  items: PublicSongsetItem[];
  playback: {
    selectedRenderJobId: string | null;
    isStale: boolean;
    staleStatus: string | null;
    mp3Url: string | null;
    mp4Url: string | null;
    chaptersUrl: string | null;
    chaptersData: unknown;
    mp3SizeBytes: number | null;
    mp4SizeBytes: number | null;
  };
  allowDownload: boolean;
  createdAt: string;
  expiresAt: string | null;
}

function formatDuration(seconds: number | null): string {
  if (!seconds) return "--:--";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

function formatTotalDuration(seconds: number | null): string {
  if (!seconds) return "N/A";
  const totalMinutes = Math.round(seconds / 60);
  if (totalMinutes < 60) return `${totalMinutes} min`;
  const hours = Math.floor(totalMinutes / 60);
  const mins = totalMinutes % 60;
  return `${hours}h ${String(mins).padStart(2, "0")}m`;
}

export default function SharePage() {
  const params = useParams();
  const router = useRouter();
  const token = params.token as string;

  const [shareData, setShareData] = useState<ShareData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorStatus, setErrorStatus] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isStarting, setIsStarting] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function loadShare() {
      try {
        setIsLoading(true);
        setError(null);

        const res = await fetch(`/api/share/${token}`);

        if (!res.ok) {
          const data = await res.json();
          setErrorStatus(res.status);
          throw new Error(data.error ?? "This link is no longer available");
        }

        const data = await res.json();
        if (cancelled) return;
        setShareData(data);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load share");
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    if (token) {
      loadShare();
    }

    return () => {
      cancelled = true;
    };
  }, [token]);

  const handlePlay = () => {
    if (!shareData?.playback.mp4Url && !shareData?.playback.mp3Url) return;
    setIsStarting(true);
    router.push(
      shareData.playback.mp4Url
        ? `/share/${token}/play/controller`
        : `/share/${token}/play/audio`
    );
  };

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Loader2 className="size-8 animate-spin text-muted-foreground" role="status" aria-label="Loading" />
      </div>
    );
  }

  if (error) {
    const isRevoked = errorStatus === 410 && error?.toLowerCase().includes("revoked");
    const isExpired = errorStatus === 410 && error?.toLowerCase().includes("expired");

    return (
      <div className="min-h-screen flex flex-col items-center justify-center p-6 gap-4">
        <AlertTriangle className="size-12 text-muted-foreground" />
        <p className="text-center text-muted-foreground max-w-sm" role="alert">
          {isRevoked
            ? "This share link has been revoked."
            : isExpired
              ? "This share link has expired."
              : error}
        </p>
      </div>
    );
  }

  if (!shareData) return null;

  const { songset, items, playback } = shareData;
  const hasVideo = !!playback.mp4Url;
  const hasAudio = !!playback.mp3Url;
  const hasArtifacts = hasVideo || hasAudio;

  const renderUnavailableMessage = () => {
    if (songset.renderState === "unrendered") {
      return "This songset hasn't been rendered yet. Worship Playback is not available.";
    }
    if (songset.renderState === "rendering") {
      return "This songset is currently being rendered. Check back soon.";
    }
    if (songset.renderState === "failed") {
      return "Rendering failed. Worship Playback is not available.";
    }
    if (!hasArtifacts) {
      return "No playback artifacts available yet.";
    }
    return null;
  };

  const unavailableMessage = renderUnavailableMessage();

  return (
    <div className="min-h-screen bg-background flex flex-col">
      <header className="border-b px-4 py-3">
        <p className="text-sm text-muted-foreground font-medium">Stream of Worship</p>
      </header>

      <main className="flex-1 p-6 max-w-2xl mx-auto w-full space-y-6">
        <div className="space-y-2">
          <h1 className="text-2xl font-bold" data-testid="songset-name">
            {songset.name}
          </h1>
          {songset.description && (
            <p className="text-muted-foreground text-sm">
              {songset.description}
            </p>
          )}
          <div className="flex items-center gap-1 text-sm text-muted-foreground">
            <Clock className="size-3.5" />
            Total: {formatTotalDuration(songset.totalDurationSeconds)}
          </div>
        </div>

        <div className="space-y-3">
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span className="font-medium">Song List</span>
            <span className="flex items-center gap-1">
              <Music className="size-3" />
              {items.length} {items.length === 1 ? "song" : "songs"}
            </span>
          </div>

          <div className="space-y-2">
            {items.map((item, index) => (
              <div
                key={item.id}
                className="flex items-center gap-3 p-3 rounded-lg bg-muted/50"
              >
                <span className="flex items-center justify-center w-6 h-6 rounded-full bg-primary/10 text-primary text-xs font-medium shrink-0">
                  {index + 1}
                </span>
                <div className="flex-1 min-w-0">
                  <p className="font-medium truncate">
                    {item.songTitle || "Unknown Song"}
                  </p>
                  <p className="text-xs text-muted-foreground truncate">
                    {[item.composer, item.lyricist].filter(Boolean).join(" • ") || item.albumName || ""}
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0 text-sm text-muted-foreground">
                  {item.songMusicalKey && (
                    <span className="text-xs">{item.songMusicalKey}</span>
                  )}
                  {item.durationSeconds != null && (
                    <span>{formatDuration(item.durationSeconds)}</span>
                  )}
                  {item.tempoBpm != null && (
                    <span className="text-xs">{Math.round(item.tempoBpm)} BPM</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        {playback.isStale && (
          <div className="flex items-start gap-2 p-3 rounded-lg bg-amber-50 dark:bg-amber-950/20 border border-amber-200">
            <AlertTriangle className="size-4 text-amber-600 shrink-0 mt-0.5" />
            <p className="text-sm text-amber-700 dark:text-amber-300">
              The song list above is current, but the playback may reflect an earlier render.
            </p>
          </div>
        )}

        {unavailableMessage ? (
          <p className="text-sm text-muted-foreground text-center">
            {unavailableMessage}
          </p>
        ) : (
          <div className="flex flex-col gap-3 w-full max-w-xs mx-auto">
            {hasVideo && (
              <Button
                size="lg"
                className="w-full h-14 gap-3 text-lg"
                onClick={handlePlay}
                disabled={isStarting}
                aria-label="Play worship video"
                data-testid="play-button"
              >
                {isStarting ? (
                  <Loader2 className="size-5 animate-spin" />
                ) : (
                  <Monitor className="size-5" />
                )}
                Start Worship
              </Button>
            )}

            {hasAudio && !hasVideo && (
              <Button
                size="lg"
                className="w-full h-14 gap-3 text-lg"
                onClick={handlePlay}
                disabled={isStarting}
                aria-label="Play audio"
              >
                {isStarting ? (
                  <Loader2 className="size-5 animate-spin" />
                ) : (
                  <Play className="size-5" />
                )}
                Start Worship
              </Button>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
