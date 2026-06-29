"use client";

import { useState, useEffect, useRef, useMemo, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { ControllerPlayer } from "@/components/play/ControllerPlayer";
import { useCastTransport, type CastMedia } from "@/hooks/useCast";
import { usePresentationSender } from "@/hooks/usePresentation";
import { dispatchCast } from "@/lib/cast/dispatch";
import type { PresentationCommand, PresentationMediaStatus } from "@/types/presentation-api";
import type { Chapter } from "@/lib/render/chapters";
import { normalizeChaptersManifest } from "@/lib/render/chapters";
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
  const [presentationMediaStatus, setPresentationMediaStatus] =
    useState<PresentationMediaStatus | null>(null);

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

        // Get signed URL for video. The logged-in phone mints the presigned
        // R2 URL with its own session and hands it to the TV receiver (the TV
        // only hits R2, never the webapp). `cast=true` mints the 4-hour
        // Cast-playback expiry so the URL survives a full service + setup.
        const signedUrlResponse = await fetch(
          `/api/signed-url?renderJobId=${encodeURIComponent(jobData.id)}&fileType=video&cast=true`
        );
        if (!signedUrlResponse.ok) {
          throw new Error("Failed to get video URL");
        }

        const { url } = await signedUrlResponse.json();
        if (cancelled) return;

        setVideoUrl(url);

        // Load chapters if available via proxy URL
        if (jobData.chaptersR2Key) {
          const chaptersProxyUrl = `/api/r2/artifact/${jobData.id}/chapters.json`;
          const chaptersDataResponse = await fetch(chaptersProxyUrl);
          if (chaptersDataResponse.ok) {
            const chaptersData = await chaptersDataResponse.json();
            try {
              const manifest = normalizeChaptersManifest(chaptersData);
              setChapters(manifest.chapters);
            } catch (e) {
              console.error("Failed to parse chapters:", e);
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

  // Cast + Presentation transport wiring.
  //
  // The Cast Web Sender SDK is the production transport. The dev-only
  // Presentation API sender is retained as a fallback used only when the Cast
  // SDK is unavailable (e.g. iOS) — `sender.send` / `sender.start` are never
  // invoked when `cast.isSupported` is true.
  // Build the projection receiver URL. The controller mints a 4-hour signed
  // R2 URL with `cast=true` and passes it via the `v` query param so the
  // receiver (a Presentation-API context with no session cookies) can boot
  // without calling any authenticated API. `t` carries the songset name for
  // the title overlay. When `videoUrl` is not yet loaded the URL falls back
  // to the bare path; the controller's render guards prevent `handleSendToTV`
  // from running before data is ready.
  const presentationUrl = useMemo(() => {
    const params = new URLSearchParams();
    if (videoUrl) params.set("v", videoUrl);
    if (songset?.name) params.set("t", songset.name);
    const qs = params.toString();
    return qs
      ? `/songsets/${songsetId}/play/projection?${qs}`
      : `/songsets/${songsetId}/play/projection`;
  }, [songsetId, videoUrl, songset]);
  const media = useMemo<CastMedia>(
    () => ({
      videoUrl: videoUrl ?? "",
      title: songset?.name ?? "Worship Set",
      source: { kind: "songset", idOrToken: songsetId },
      startSeconds: 0,
    }),
    [videoUrl, songset?.name, songsetId],
  );

  const cast = useCastTransport({
    media,
    onError: (m) => toast.error(m),
  });

  const sender = usePresentationSender({
    presentationUrl,
    onConnected: () => toast.success("Connected to projection screen"),
    onDisconnected: () => {
      setPresentationMediaStatus(null);
      toast.info("Disconnected from projection screen");
    },
    onStartError: (m) => toast.error(m),
    onStatus: (status) => {
      if (status.type === "error") {
        toast.error("TV projection failed — check connection");
      } else if (status.type === "media") {
        setPresentationMediaStatus(status);
      }
    },
  });

  // Toasts only from transport lifecycle: cast connection transitions are
  // observed via state (the hook exposes `isConnected`, not a callback).
  const prevCastConnectedRef = useRef(false);
  useEffect(() => {
    const wasConnected = prevCastConnectedRef.current;
    if (cast.isConnected && !wasConnected) {
      toast.success(`Connected to ${cast.deviceName || "TV"}`);
    } else if (!cast.isConnected && wasConnected) {
      toast.info("Disconnected from TV");
    }
    prevCastConnectedRef.current = cast.isConnected;
  }, [cast.isConnected, cast.deviceName]);

  const isPresentationActive =
    cast.isConnected || (!cast.isSupported && sender.isConnected);

  // Unified intent handlers. Cast is preferred when supported; the
  // Presentation fallback only runs when `!cast.isSupported`.
  const handleSendToTV = useCallback(() => {
    if (cast.isSupported) {
      void cast.start();
    } else {
      void sender.start();
    }
  }, [cast, sender]);

  const handleSendTransportCommand = useCallback(
    (command: PresentationCommand) => {
      if (cast.isSupported) {
        dispatchCast(cast, command);
      } else {
        sender.send(command);
      }
    },
    [cast, sender],
  );

  const handleStopPresentation = useCallback(() => {
    if (cast.isConnected) {
      cast.stop();
    } else if (!cast.isSupported && sender.isConnected) {
      sender.stop();
    }
  }, [cast, sender]);

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
      playerId={songsetId}
      videoSrc={videoUrl}
      chapters={chapters}
      isPresentationActive={isPresentationActive}
      transport={cast}
      presentationFallback={{
        isSupported: sender.isSupported,
        isConnected: sender.isConnected,
      }}
      presentationMediaStatus={presentationMediaStatus}
      isCastSupported={cast.isSupported}
      castAvailability={cast.availability}
      isCastConnecting={cast.isConnecting}
      onSendToTV={handleSendToTV}
      onStopPresentation={handleStopPresentation}
      onSendTransportCommand={handleSendTransportCommand}
    />
  );
}
