"use client";

import { useState, useEffect } from "react";
import { useParams, useRouter } from "next/navigation";
import { ControllerPlayer } from "@/components/play/ControllerPlayer";
import type { Chapter } from "@/lib/render/chapters";
import { normalizeChaptersManifest } from "@/lib/render/chapters";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

export default function ShareControllerPage() {
  const params = useParams();
  const router = useRouter();
  const token = params.token as string;

  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadData() {
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

        setVideoUrl(data.playback.mp4Url);

        if (data.playback.chaptersUrl) {
          try {
            const chaptersRes = await fetch(data.playback.chaptersUrl);
            if (chaptersRes.ok) {
              const chaptersData = await chaptersRes.json();
              const manifest = normalizeChaptersManifest(chaptersData);
              if (!cancelled) {
                setChapters(manifest.chapters);
              }
            }
          } catch (e) {
            console.error("Failed to load chapters:", e);
          }
        }
      } catch (err) {
        if (!cancelled) {
          const message = err instanceof Error ? err.message : "Failed to load player";
          setError(message);
          toast.error(message);
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false);
        }
      }
    }

    if (token) {
      loadData();
    }

    return () => {
      cancelled = true;
    };
  }, [token]);

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

  if (error || !videoUrl) {
    return (
      <div className="fixed inset-0 bg-black flex items-center justify-center p-4">
        <div className="text-center">
          <p className="text-white mb-4">
            {error || "Failed to load player"}
          </p>
          <button
            onClick={() => router.push(`/share/${token}`)}
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
      playerId={token}
      videoSrc={videoUrl}
      chapters={chapters}
      exitRoute={`/share/${token}`}
      autoFullscreen={false}
    />
  );
}
