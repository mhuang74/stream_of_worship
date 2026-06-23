"use client";

import { useState, useEffect } from "react";
import { useParams } from "next/navigation";
import { ProjectionPlayer } from "@/components/play/ProjectionPlayer";

export default function ShareProjectionPage() {
  const params = useParams();
  const token = params.token as string;

  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [songTitle, setSongTitle] = useState<string | undefined>();
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

        if (!data.playback?.mp4Url) {
          throw new Error("No video available for this share");
        }

        setSongTitle(data.songset?.name ?? undefined);
        setVideoUrl(data.playback.mp4Url);
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

    if (token) {
      loadShare();
    }

    return () => {
      cancelled = true;
    };
  }, [token]);

  if (isLoading) {
    return (
      <div
        className="fixed inset-0 bg-black flex items-center justify-center"
        role="status"
        aria-label="Loading projection"
      >
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

  return <ProjectionPlayer videoSrc={videoUrl} initialSongTitle={songTitle} />;
}
