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
import {
  createOfflineBlobUrl,
  resolveOfflinePlayback,
  type OfflineMediaKind,
} from "@/lib/offline/offline-playback";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";
import { useLocale } from "@/hooks/useLocale";

interface SongsetData {
  id: string;
  name: string;
  renderState: "unrendered" | "rendering" | "fresh" | "stale" | "failed";
  latestRenderJobId: string | null;
}

/**
 * Media the controller boots with: the online chain's presigned URL, or an
 * offline artifact (service-worker-served proxy URL, or a blob URL over the
 * cached bytes).
 */
interface ControllerMedia {
  src: string;
  kind: OfflineMediaKind;
  /** Offline boot: the offline hint renders and Cast is hidden. */
  isOffline: boolean;
  /** True while `src` is the proxy URL the service worker serves from cache;
   * a blob fallback is still possible. */
  viaProxy: boolean;
  renderJobId: string;
}

/** items[].recording.contentHash keyed by item position for the
 * Lyrics Feedback affordance (issue #194). */
type ChapterRecordingHashes = (string | null)[];

/** Control-flow signal: the songset fetch answered 401 and the page is
 * navigating to /login. Never a reason to fall back to the offline index. */
class AuthRedirectError extends Error {}

/** True when the device reports no network — the controller then boots
 * straight from the offline index instead of hanging on API fetches. */
function isOfflineAtBoot(): boolean {
  return typeof navigator !== "undefined" && navigator.onLine === false;
}

export default function ControllerPage() {
  const params = useParams();
  const router = useRouter();
  const { t } = useLocale();
  const songsetId = params.id as string;

  const [songset, setSongset] = useState<SongsetData | null>(null);
  const [media, setMedia] = useState<ControllerMedia | null>(null);
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [presentationMediaStatus, setPresentationMediaStatus] =
    useState<PresentationMediaStatus | null>(null);
  const [chapterRecordingHashes, setChapterRecordingHashes] =
    useState<ChapterRecordingHashes>([]);

  // Load songset and render job data.
  //
  // Connectivity-aware three-branch boot (issue #205):
  //   1. online → the existing four-fetch chain verbatim (freshness and Cast
  //      preserved for a downloaded-but-online leader);
  //   2. chain fails while nominally online (mid-session drop: a rejected
  //      fetch, or the service worker's 503 for an uncached API route) →
  //      boot cache-first from the offline index, Cast hidden;
  //   3. offline at boot → cache-first directly, zero API fetches.
  // `isOffline` media only ever comes out of branches 2/3, so Cast gating on
  // it is correct: online never skips the API chain, and never silently plays
  // a stale download.
  useEffect(() => {
    let cancelled = false;

    // Boots the player from the offline index + artifact cache. Returns false
    // when this songset has no usable offline copy (the caller then surfaces
    // the chain's own error).
    async function loadOffline(): Promise<boolean> {
      const offline = await resolveOfflinePlayback(songsetId);
      if (cancelled || !offline) return false;

      // Cache-first boot needs a songset shape for the player props; the
      // record carries everything it renders (name + render job).
      setSongset({
        id: songsetId,
        name: offline.songsetName,
        renderState: "fresh",
        latestRenderJobId: offline.renderJobId,
      });
      setChapterRecordingHashes(offline.chapterRecordingHashes);
      setChapters(offline.chapters);
      setMedia({
        src: offline.src,
        kind: offline.kind,
        isOffline: true,
        viaProxy: offline.viaProxy,
        renderJobId: offline.renderJobId,
      });
      return true;
    }

    // Chapters are a per-artifact best-effort: the player is already booted on
    // its media source when this runs, and neither a failed fetch nor an
    // unparseable manifest may take the media down with it.
    async function loadChapters(renderJobId: string): Promise<void> {
      try {
        const response = await fetch(`/api/r2/artifact/${renderJobId}/chapters.json`);
        if (!response.ok || cancelled) return;
        const manifest = normalizeChaptersManifest(await response.json());
        if (cancelled) return;
        setChapters(manifest.chapters);
      } catch (e) {
        console.error("Failed to load chapters:", e);
      }
    }

    async function loadOnline(): Promise<void> {
      // Load songset
      const songsetResponse = await fetch(`/api/songsets/${songsetId}`);
      if (!songsetResponse.ok) {
        if (songsetResponse.status === 401) {
          router.push("/login");
          throw new AuthRedirectError();
        }
        if (songsetResponse.status === 404) {
          throw new Error(t("control.songsetNotFound"));
        }
        throw new Error(t("control.failedToLoadSongset"));
      }

      const songsetData = await songsetResponse.json();
      if (cancelled) return;

      setSongset({
        id: songsetData.id,
        name: songsetData.name,
        renderState: songsetData.renderState,
        latestRenderJobId: songsetData.latestRenderJobId,
      });

      // Position → Recording content hash for the Lyrics Feedback
      // affordance (issue #194). Songset items are sorted by position and
      // chapters render in that order.
      const items = Array.isArray(songsetData.items) ? songsetData.items : [];
      const sortedItems = [...items].sort(
        (a: { position: number }, b: { position: number }) => a.position - b.position
      );
      setChapterRecordingHashes(
        sortedItems.map((item: { recording?: { contentHash: string } | null }) =>
          item?.recording?.contentHash ?? null
        )
      );

      // Check if render artifacts exist
      if (!songsetData.latestRenderJobId) {
        throw new Error(t("control.notRenderedYet"));
      }

      // Load render job
      const jobResponse = await fetch(
        `/api/render-jobs/${songsetData.latestRenderJobId}`
      );
      if (!jobResponse.ok) {
        throw new Error(t("control.failedToLoadRenderJob"));
      }

      const jobData = await jobResponse.json();
      if (cancelled) return;

      if (!jobData.mp4R2Key) {
        throw new Error(t("control.noVideoForSongset"));
      }

      // Get signed URL for video. The logged-in phone mints the presigned
      // R2 URL with its own session and hands it to the TV receiver (the TV
      // only hits R2, never the webapp). `cast=true` mints the 4-hour
      // Cast-playback expiry so the URL survives a full service + setup.
      const signedUrlResponse = await fetch(
        `/api/signed-url?renderJobId=${encodeURIComponent(jobData.id)}&fileType=video&cast=true`
      );
      if (!signedUrlResponse.ok) {
        throw new Error(t("control.failedToGetVideoUrl"));
      }

      const { url } = await signedUrlResponse.json();
      if (cancelled) return;

      setMedia({
        src: url,
        kind: "video",
        isOffline: false,
        viaProxy: false,
        renderJobId: jobData.id,
      });

      // Load chapters via the proxy URL — independently of the media boot.
      if (jobData.chaptersR2Key) {
        void loadChapters(jobData.id);
      }
    }

    async function loadData() {
      try {
        setIsLoading(true);
        setError(null);

        // Offline at boot: no API fetch is even attempted — the offline index
        // and the artifact cache are the only sources.
        if (isOfflineAtBoot()) {
          if (await loadOffline()) return;
          throw new Error(t("control.offlineUnavailable"));
        }

        try {
          await loadOnline();
        } catch (err) {
          if (err instanceof AuthRedirectError) throw err;
          // Nominally online but the chain did not complete: prefer the
          // downloaded copy over the error screen.
          if (await loadOffline()) return;
          throw err;
        }
      } catch (err) {
        if (!cancelled) {
          if (err instanceof AuthRedirectError) return;
          const message =
            err instanceof Error ? err.message : t("control.failedToLoadPlayer");
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
  }, [songsetId, router, t]);

  // ── Media failure → blob fallback ───────────────────────────────────────
  // The offline proxy URL is served by the service worker from Cache Storage.
  // When it is not (worker no longer controlling the document, entry evicted
  // between boot and play), the media element errors — re-issue the load
  // against a blob URL of the cached artifact rather than going straight to
  // the failure overlay. One attempt per boot: a blob URL already holds the
  // whole artifact, there is no cheaper source to fall back to after it.
  const blobFallbackTriedRef = useRef(false);

  const handleMediaError = useCallback(async (): Promise<boolean> => {
    if (!media?.isOffline || !media.viaProxy || blobFallbackTriedRef.current) {
      return false;
    }
    blobFallbackTriedRef.current = true;

    const src = await createOfflineBlobUrl(media.renderJobId, media.kind);
    if (!src) return false;

    setMedia({ ...media, src, viaProxy: false });
    return true;
  }, [media]);

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
  // the title overlay. When no media is loaded yet the URL falls back to the
  // bare path; the controller's render guards prevent `handleSendToTV` from
  // running before data is ready.
  const songsetName = songset?.name;
  const mediaSrc = media?.src;
  const presentationUrl = useMemo(() => {
    const params = new URLSearchParams();
    if (mediaSrc) params.set("v", mediaSrc);
    if (songsetName) params.set("t", songsetName);
    const qs = params.toString();
    return qs
      ? `/songsets/${songsetId}/play/projection?${qs}`
      : `/songsets/${songsetId}/play/projection`;
  }, [songsetId, mediaSrc, songsetName]);
  const castMedia = useMemo<CastMedia>(
    () => ({
      videoUrl: mediaSrc ?? "",
      title: songset?.name ?? t("control.worshipSet"),
      source: { kind: "songset", idOrToken: songsetId },
      startSeconds: 0,
    }),
    [mediaSrc, songset?.name, songsetId, t],
  );

  const cast = useCastTransport({
    media: castMedia,
    onError: (m) => toast.error(m),
  });

  const sender = usePresentationSender({
    presentationUrl,
    onConnected: () => toast.success(t("control.connectedProjection")),
    onDisconnected: () => {
      setPresentationMediaStatus(null);
      toast.info(t("control.disconnectedProjection"));
    },
    onStartError: (m) => toast.error(m),
    onStatus: (status) => {
      if (status.type === "error") {
        toast.error(t("projection.tvFailed"));
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
      toast.success(
        `${t("control.connectedTo")} ${cast.deviceName || t("control.tv")}`
      );
    } else if (!cast.isConnected && wasConnected) {
      toast.info(t("control.disconnectedFromTV"));
    }
    prevCastConnectedRef.current = cast.isConnected;
  }, [cast.isConnected, cast.deviceName, t]);

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
          <p className="text-white/70">
            {isOfflineAtBoot()
              ? t("control.offlineBooting")
              : t("control.loadingPlayer")}
          </p>
        </div>
      </div>
    );
  }

  if (error || !songset || !media) {
    return (
      <div className="fixed inset-0 bg-black flex items-center justify-center p-4">
        <div className="text-center">
          <p className="text-white mb-4">
            {error || t("control.failedToLoadPlayer")}
          </p>
          <button
            onClick={() => router.push(`/songsets/${songsetId}/play`)}
            className="px-4 py-2 bg-primary text-white rounded-lg"
          >
            {t("control.goBack")}
          </button>
        </div>
      </div>
    );
  }

  return (
    <ControllerPlayer
      playerId={songsetId}
      {...(media.kind === "audio" ? { audioSrc: media.src } : { videoSrc: media.src })}
      chapters={chapters}
      chapterRecordingHashes={chapterRecordingHashes}
      isOfflineMedia={media.isOffline}
      onMediaError={handleMediaError}
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
