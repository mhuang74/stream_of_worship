"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { AlertTriangle, Loader2 } from "lucide-react";

interface ShareData {
  songsetName: string | null;
  mp3Url: string | null;
}

export default function ShareAudioPage() {
  const params = useParams();
  const token = params.token as string;

  const [shareData, setShareData] = useState<ShareData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

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

        if (!data.mp3Url) {
          throw new Error("No audio available for this share");
        }

        setShareData({
          songsetName: data.songsetName ?? "Worship Set",
          mp3Url: data.mp3Url,
        });
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load audio");
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

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <Loader2 className="size-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (error || !shareData) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-4 p-6 bg-background">
        <AlertTriangle className="size-12 text-muted-foreground" />
        <p className="max-w-sm text-center text-muted-foreground">
          {error ?? "Failed to load audio"}
        </p>
      </div>
    );
  }

  return (
    <main className="min-h-screen bg-background px-6 py-10">
      <div className="mx-auto flex max-w-2xl flex-col gap-6">
        <div className="space-y-2 text-center">
          <p className="text-sm font-medium text-muted-foreground">
            Stream of Worship
          </p>
          <h1 className="text-2xl font-bold">
            {shareData.songsetName}
          </h1>
        </div>
        <audio
          className="w-full"
          controls
          autoPlay
          src={shareData.mp3Url ?? undefined}
        />
      </div>
    </main>
  );
}
