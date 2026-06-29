"use client";

import { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import { ProjectionPlayer } from "@/components/play/ProjectionPlayer";

export default function ProjectionPage() {
  const params = useParams();
  const songsetId = params.id as string;

  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [initialTitle, setInitialTitle] = useState<string | undefined>();
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function loadData() {
      try {
        setIsLoading(true);
        setError(null);

        // Receiver context (opened via PresentationRequest) does not share the
        // sender's session cookies, so it cannot call the authenticated songset
        // / render-job / signed-url APIs. The sender (controller) mints a
        // 4-hour signed R2 URL and passes it via the `v` query param; `t`
        // carries the songset name for the title overlay. Fall back to the
        // authenticated fetch path only when the params are absent (direct
        // navigation).
        const searchParams = new URLSearchParams(window.location.search);
        const passedVideoUrl = searchParams.get("v");
        const passedTitle = searchParams.get("t") ?? undefined;

        if (passedVideoUrl) {
          if (cancelled) return;
          setVideoUrl(passedVideoUrl);
          setInitialTitle(passedTitle);
          return;
        }

        // --- authenticated fallback (direct navigation by a logged-in user) ---
        const songsetResponse = await fetch(`/api/songsets/${songsetId}`);
        if (!songsetResponse.ok) {
          if (songsetResponse.status === 401) {
            setError("Authentication required");
            return;
          }
          throw new Error("Failed to load songset");
        }

        const songsetData = await songsetResponse.json();
        if (cancelled) return;

        setInitialTitle(songsetData.name as string);

        if (!songsetData.latestRenderJobId) {
          throw new Error("No render artifacts available");
        }

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

        const signedUrlResponse = await fetch(
          `/api/signed-url?renderJobId=${encodeURIComponent(songsetData.latestRenderJobId)}&fileType=video`
        );
        if (!signedUrlResponse.ok) {
          throw new Error("Failed to get video URL");
        }

        const { url } = await signedUrlResponse.json();
        if (cancelled) return;

        setVideoUrl(url as string);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load projection");
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
  }, [songsetId]);

  if (isLoading) {
    return (
      <div className="fixed inset-0 bg-black flex items-center justify-center" role="status" aria-label="Loading projection">
        <div className="w-8 h-8 border-2 border-white/30 border-t-white rounded-full animate-spin" />
      </div>
    );
  }

  if (error || !videoUrl) {
    return (
      <div className="fixed inset-0 bg-black flex items-center justify-center p-4">
        <p className="text-white/70 text-sm text-center">
          {error ?? "Failed to load projection"}
        </p>
      </div>
    );
  }

  return <ProjectionPlayer videoSrc={videoUrl} initialSongTitle={initialTitle} />;
}
