"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { ControllerPlayer } from "@/components/play/ControllerPlayer";
import { Chapter } from "@/components/play/LyricJumpList";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

interface SongsetData {
  id: string;
  name: string;
  renderState: "unrendered" | "rendering" | "fresh" | "stale" | "failed";
  latestRenderJobId: string | null;
}

export default function ControllerPage() {
  const params = useParams();
  const router = useRouter();
  const songsetId = params.id as string;

  const [songset, setSongset] = useState<SongsetData | null>(null);
  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isPresentationActive, setIsPresentationActive] = useState(false);

  // Load songset and render job data
  useEffect(() => {
    let cancelled = false;

    async function loadData() {
      try {
        setIsLoading(true);
        setError(null);

        // Load songset
        const songsetResponse = await fetch(`/api/songsets/${songsetId}`);
        if (!songsetResponse.ok) {
          if (songsetResponse.status === 401) {
            router.push("/login");
            return;
          }
          if (songsetResponse.status === 404) {
            throw new Error("Songset not found");
          }
          throw new Error("Failed to load songset");
        }

        const songsetData = await songsetResponse.json();
        if (cancelled) return;

        setSongset({
          id: songsetData.id,
          name: songsetData.name,
          renderState: songsetData.renderState,
          latestRenderJobId: songsetData.latestRenderJobId,
        });

        // Check if render artifacts exist
        if (!songsetData.latestRenderJobId) {
          throw new Error("Songset has not been rendered yet");
        }

        // Load render job
        const jobResponse = await fetch(
          `/api/render-jobs/${songsetData.latestRenderJobId}`
        );
        if (!jobResponse.ok) {
          throw new Error("Failed to load render job");
        }

        const jobData = await jobResponse.json();
        if (cancelled) return;

        if (!jobData.mp4R2Key) {
          throw new Error("No video available for this songset");
        }

        // Get signed URL for video
        const signedUrlResponse = await fetch(
          `/api/signed-url?renderJobId=${encodeURIComponent(jobData.id)}&fileType=video`
        );
        if (!signedUrlResponse.ok) {
          throw new Error("Failed to get video URL");
        }

        const { url } = await signedUrlResponse.json();
        if (cancelled) return;

        setVideoUrl(url);

        // Load chapters if available
        if (jobData.chaptersR2Key) {
          const chaptersResponse = await fetch(
            `/api/signed-url?renderJobId=${encodeURIComponent(jobData.id)}&fileType=json`
          );
          if (chaptersResponse.ok) {
            const { url: chaptersUrl } = await chaptersResponse.json();
            const chaptersDataResponse = await fetch(chaptersUrl);
            if (chaptersDataResponse.ok) {
              const chaptersData = await chaptersDataResponse.json();
              if (chaptersData.chapters) {
                setChapters(chaptersData.chapters);
              }
            }
          }
        }
      } catch (err) {
        if (!cancelled) {
          const message =
            err instanceof Error ? err.message : "Failed to load player";
          setError(message);
          toast.error(message);
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    if (songsetId) {
      loadData();
    }

    return () => {
      cancelled = true;
    };
  }, [songsetId, router]);

  // Listen for Presentation API messages
  useEffect(() => {
    const handleMessage = (event: MessageEvent) => {
      if (event.data?.type === "presentation") {
        switch (event.data.action) {
          case "connected":
            setIsPresentationActive(true);
            toast.success("Connected to projection screen");
            break;
          case "disconnected":
            setIsPresentationActive(false);
            toast.info("Disconnected from projection screen");
            break;
        }
      }
    };

    window.addEventListener("message", handleMessage);
    return () => window.removeEventListener("message", handleMessage);
  }, []);

  const handlePresentationConnect = useCallback(() => {
    setIsPresentationActive(true);
  }, []);

  const handlePresentationDisconnect = useCallback(() => {
    setIsPresentationActive(false);
  }, []);

  if (isLoading) {
    return (
      <div className="fixed inset-0 bg-black flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <Loader2 className="size-8 animate-spin text-white" />
          <p className="text-white/70">Loading player...</p>
        </div>
      </div>
    );
  }

  if (error || !songset || !videoUrl) {
    return (
      <div className="fixed inset-0 bg-black flex items-center justify-center p-4">
        <div className="text-center">
          <p className="text-white mb-4">
            {error || "Failed to load player"}
          </p>
          <button
            onClick={() => router.push(`/songsets/${songsetId}/play`)}
            className="px-4 py-2 bg-primary text-white rounded-lg"
          >
            Go Back
          </button>
        </div>
      </div>
    );
  }

  return (
    <ControllerPlayer
      songsetId={songsetId}
      videoSrc={videoUrl}
      chapters={chapters}
      isPresentationActive={isPresentationActive}
      onPresentationConnect={handlePresentationConnect}
      onPresentationDisconnect={handlePresentationDisconnect}
    />
  );
}
