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

export default function ShareControllerPage() {
  const params = useParams();
  const router = useRouter();
  const token = params.token as string;

  const [videoUrl, setVideoUrl] = useState<string | null>(null);
  const [shareName, setShareName] = useState<string>("Shared Worship Set");
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [presentationMediaStatus, setPresentationMediaStatus] =
    useState<PresentationMediaStatus | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadData() {
      try {
        setIsLoading(true);
        setError(null);

        const res = await fetch(`/api/share/${token}`);
        if (!res.ok) {
          let errorMessage = "This link is no longer available";
          try {
            const data = await res.json();
            if (data?.error) {
              errorMessage = data.error;
            }
          } catch {
            // Fallback to default message if response is not valid JSON
          }
          throw new Error(errorMessage);
        }

        const data = await res.json();
        if (cancelled) return;

        if (!data?.playback?.mp4Url) {
          throw new Error("No video available for this share");
        }

        // The share-token route mints a presigned R2 URL (no auth on the TV);
        // the phone hands it to the receiver, which only hits R2.
        setVideoUrl(data.playback.mp4Url);
        if (data?.songset?.name) {
          setShareName(data.songset.name);
        }

        if (data.playback?.chaptersUrl) {
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

  // Cast + Presentation transport wiring (same shape as the songset
  // controller). Cast is preferred; the Presentation API fallback runs only
  // when `!cast.isSupported`.
  const presentationUrl = `/share/${token}/play/projection`;
  const media = useMemo<CastMedia>(
    () => ({
      videoUrl: videoUrl ?? "",
      title: shareName,
      source: { kind: "share", idOrToken: token },
      startSeconds: 0,
    }),
    [videoUrl, shareName, token],
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

  // Toasts only from transport lifecycle. Cast connection transitions are
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
      onSendTransportCommand={handleSendTransportCommand}
    />
  );
}
