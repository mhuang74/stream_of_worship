"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { PrePlayCard } from "@/components/play/PrePlayCard";
import { Button } from "@/components/ui/button";
import { ArrowLeft } from "lucide-react";

interface SongsetData {
  id: string;
  name: string;
  description: string | null;
  renderState: "unrendered" | "rendering" | "fresh" | "stale" | "failed";
  latestRenderJobId: string | null;
  lastFailedRenderJobId: string | null;
}

interface SongsetItem {
  id: string;
  position: number;
  song: {
    id: string;
    title: string;
    composer: string | null;
    lyricist: string | null;
    albumName: string | null;
    musicalKey: string | null;
  } | null;
  recording: {
    contentHash: string;
    durationSeconds: number | null;
    tempoBpm: number | null;
    musicalKey: string | null;
  } | null;
}

interface RenderJobData {
  id: string;
  status: string;
  mp3R2Key: string | null;
  mp4R2Key: string | null;
  chaptersR2Key: string | null;
}

export default function PlayPage() {
  const params = useParams();
  const router = useRouter();
  const songsetId = params.id as string;

  const [songset, setSongset] = useState<SongsetData | null>(null);
  const [items, setItems] = useState<SongsetItem[]>([]);
  const [renderJob, setRenderJob] = useState<RenderJobData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Load songset data
  useEffect(() => {
    let cancelled = false;

    async function loadSongset() {
      try {
        setIsLoading(true);
        setError(null);

        const response = await fetch(`/api/songsets/${songsetId}`);

        if (!response.ok) {
          if (response.status === 401) {
            router.push("/login");
            return;
          }
          if (response.status === 404) {
            throw new Error("Songset not found");
          }
          throw new Error("Failed to load songset");
        }

        const data = await response.json();

        if (cancelled) return;

        setSongset({
          id: data.id,
          name: data.name,
          description: data.description,
          renderState: data.renderState,
          latestRenderJobId: data.latestRenderJobId,
          lastFailedRenderJobId: data.lastFailedRenderJobId,
        });

        setItems(data.items || []);

        // Load render job details if available
        if (data.latestRenderJobId) {
          const jobResponse = await fetch(`/api/render-jobs/${data.latestRenderJobId}`);
          if (jobResponse.ok) {
            const job = await jobResponse.json();
            setRenderJob(job);
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load songset");
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    if (songsetId) {
      loadSongset();
    }

    return () => {
      cancelled = true;
    };
  }, [songsetId, router]);

  const handleStartWorship = useCallback(() => {
    // Navigate to the controller player
    router.push(`/songsets/${songsetId}/play/controller`);
  }, [router, songsetId]);

  const handleReRender = useCallback(() => {
    router.push(`/songsets/${songsetId}/render`);
  }, [router, songsetId]);

  const handleShare = useCallback(() => {
    // Navigate to share dialog (or open share modal)
    router.push(`/songsets/${songsetId}?share=true`);
  }, [router, songsetId]);

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div role="status" className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    );
  }

  if (error || !songset) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center p-4">
        <p className="text-center text-destructive">
          {error || "Songset not found"}
        </p>
        <Button
          variant="ghost"
          className="mt-4"
          onClick={() => router.push("/songsets")}
        >
          Back to songsets
        </Button>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="sticky top-0 z-10 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="flex h-14 items-center gap-4 px-4">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => router.push(`/songsets/${songsetId}`)}
            aria-label="Go back"
          >
            <ArrowLeft className="size-5" />
          </Button>
          <div className="flex-1">
            <h1 className="font-semibold">Play</h1>
            <p className="text-sm text-muted-foreground truncate">
              {songset.name}
            </p>
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="p-4 pb-24 max-w-2xl mx-auto">
        <PrePlayCard
          songset={songset}
          items={items}
          renderJob={renderJob}
          onStartWorship={handleStartWorship}
          onReRender={handleReRender}
          onShare={handleShare}
        />
      </main>
    </div>
  );
}
