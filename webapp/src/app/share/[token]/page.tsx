"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Play, Loader2, Monitor, AlertTriangle } from "lucide-react";

interface ShareData {
  token: string;
  songsetId: string;
  songsetName: string | null;
  renderJobId: string;
  allowDownload: boolean;
  mp3Url: string | null;
  mp4Url: string | null;
  chaptersUrl: string | null;
  createdAt: string;
}

export default function SharePage() {
  const params = useParams();
  const router = useRouter();
  const token = params.token as string;

  const [shareData, setShareData] = useState<ShareData | null>(null);
  const [error, setError] = useState<string | null>(null);
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
    if (!shareData?.mp4Url && !shareData?.mp3Url) return;
    setIsStarting(true);
    router.push(`/share/${token}/play/projection`);
  };

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <Loader2 className="size-8 animate-spin text-muted-foreground" role="status" aria-label="Loading" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center p-6 gap-4">
        <AlertTriangle className="size-12 text-muted-foreground" />
        <p className="text-center text-muted-foreground max-w-sm" role="alert">
          {error}
        </p>
      </div>
    );
  }

  if (!shareData) return null;

  const hasVideo = !!shareData.mp4Url;
  const hasAudio = !!shareData.mp3Url;
  const hasArtifacts = hasVideo || hasAudio;

  return (
    <div className="min-h-screen bg-background flex flex-col">
      {/* Header */}
      <header className="border-b px-4 py-3">
        <p className="text-sm text-muted-foreground font-medium">Stream of Worship</p>
      </header>

      {/* Main content */}
      <main className="flex-1 flex flex-col items-center justify-center p-6 gap-8">
        <div className="text-center space-y-2 max-w-sm">
          <h1 className="text-2xl font-bold" data-testid="songset-name">
            {shareData.songsetName ?? "Worship Set"}
          </h1>
          <p className="text-muted-foreground text-sm">
            Shared worship video
          </p>
        </div>

        {hasArtifacts ? (
          <div className="flex flex-col gap-3 w-full max-w-xs">
            {/* Play full-screen projection */}
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
                Play Full Screen
              </Button>
            )}

            {/* Audio-only play */}
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
                Play Audio
              </Button>
            )}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground text-center">
            No media available for this share
          </p>
        )}
      </main>
    </div>
  );
}
