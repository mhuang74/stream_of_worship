"use client";

import { useParams, useRouter } from "next/navigation";
import { ControllerPlayer } from "@/components/play/ControllerPlayer";
import { usePlaybackCore, AuthRedirectError } from "@/hooks/usePlaybackCore";
import { useLocale } from "@/hooks/useLocale";
import { useConnectivity } from "@/hooks/useConnectivity";
import type { Chapter } from "@/lib/render/chapters";
import { normalizeChaptersManifest } from "@/lib/render/chapters";
import {
  resolveOfflinePlayback,
  type OfflineMediaKind,
} from "@/lib/offline/offline-playback";
import { getOfflineRecord } from "@/lib/offline/offline-index";
import { Loader2 } from "lucide-react";

interface SongsetData {
  id: string;
  name: string;
  renderState: "unrendered" | "rendering" | "fresh" | "stale" | "failed";
  latestRenderJobId: string | null;
}

// Connectivity for the boot hint is a render-time read of the shared
// Connectivity state machine (src/hooks/useConnectivity.ts, issue #211):
// the `online` server snapshot renders the online copy, and the client
// snapshot upgrades it in the same commit. The loading screen lives for
// milliseconds, so only definitive Offline flips the hint.
// Chapters are a per-artifact best-effort: the player is already booted on
// its media source when this runs, and neither a failed fetch nor an
// unparseable manifest may take the media down with it.
async function loadChapters(
  renderJobId: string,
  isCancelled: () => boolean,
  setChapters: (chapters: Chapter[]) => void
): Promise<void> {
  try {
    const response = await fetch(`/api/r2/artifact/${renderJobId}/chapters.json`);
    if (!response.ok || isCancelled()) return;
    const manifest = normalizeChaptersManifest(await response.json());
    if (isCancelled()) return;
    setChapters(manifest.chapters);
  } catch (e) {
    console.error("Failed to load chapters:", e);
  }
}

export default function ControllerPage() {
  const params = useParams();
  const router = useRouter();
  const { t } = useLocale();
  const songsetId = params.id as string;
  const connectivity = useConnectivity();
  const offlineNow = connectivity === "offline";

  const core = usePlaybackCore({
    bootKey: songsetId,
    defaultTitle: t("control.worshipSet"),
    castSourceKind: "songset",
    projectionPath: `/songsets/${songsetId}/play/projection`,
    offlineUnavailableMessage: t("control.offlineUnavailable"),
    // Cache-first boot reads the index first (so a corrupt/evicted artifact
    // falls through to the online chain — the resolver's "record exists, no
    // usable media" outcome), then resolves the playable source.
    resolveOffline: async () => {
      const record = await getOfflineRecord(songsetId);
      if (!record) return null;
      const offline = await resolveOfflinePlayback(songsetId);
      if (!offline) return null;
      return {
        title: offline.songsetName,
        chapters: offline.chapters,
        chapterRecordingHashes: offline.chapterRecordingHashes,
        media: {
          src: offline.src,
          kind: offline.kind,
          isOffline: true,
          viaProxy: offline.viaProxy,
          renderJobId: offline.renderJobId,
        },
      };
    },
    // The authenticated four-fetch chain (issue #205): songset → render job
    // → signed MP4 URL (Cast-expiry mint) → chapters via the proxy URL.
    runOnlineChain: async (ctx) => {
      const { isCancelled, setMedia, setChapters, setChapterRecordingHashes } = ctx;

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

      const songsetData: SongsetData & {
        items?: { position: number; recording?: { contentHash: string } | null }[];
      } = await songsetResponse.json();
      if (isCancelled()) return;

      ctx.setTitle(songsetData.name);

      // Position → Recording content hash for the Lyrics Feedback
      // affordance (issue #194). Songset items are sorted by position and
      // chapters render in that order.
      const items = Array.isArray(songsetData.items) ? songsetData.items : [];
      const sortedItems = [...items].sort(
        (a: { position: number }, b: { position: number }) => a.position - b.position
      );
      setChapterRecordingHashes(
        sortedItems.map((item) => item?.recording?.contentHash ?? null)
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
      if (isCancelled()) return;

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
      if (isCancelled()) return;

      const media: { src: string; kind: OfflineMediaKind; renderJobId: string } = {
        src: url,
        kind: "video",
        renderJobId: jobData.id,
      };
      setMedia({
        ...media,
        isOffline: false,
        viaProxy: false,
      });

      // Load chapters via the proxy URL — independently of the media boot.
      if (jobData.chaptersR2Key) {
        void loadChapters(jobData.id, isCancelled, setChapters);
      }
    },
  });

  const { media } = core;

  if (core.isLoading) {
    return (
      <div className="fixed inset-0 bg-black flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <Loader2 className="size-8 animate-spin text-white" />
          <p className="text-white/70">
            {offlineNow
              ? t("control.offlineBooting")
              : t("control.loadingPlayer")}
          </p>
        </div>
      </div>
    );
  }

  if (core.error || !media) {
    return (
      <div className="fixed inset-0 bg-black flex items-center justify-center p-4">
        <div className="text-center">
          <p className="text-white mb-4">
            {core.error || t("control.failedToLoadPlayer")}
          </p>
          <button
            type="button"
            onClick={() => router.push("/worship")}
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
      // Booted via the SW controller document the page cannot know its
      // origin; the offline list is the canonical exit (issue #211
      // follow-up, Q5).
      exitRoute="/worship"
      {...(media.kind === "audio" ? { audioSrc: media.src } : { videoSrc: media.src })}
      chapters={core.chapters}
      chapterRecordingHashes={core.chapterRecordingHashes}
      isOfflineMedia={media.isOffline}
      onMediaError={core.handleMediaError}
      isPresentationActive={core.isPresentationActive}
      transport={core.transport}
      presentationFallback={{
        isSupported: core.sender.isSupported,
        isConnected: core.sender.isConnected,
      }}
      presentationMediaStatus={core.presentationMediaStatus}
      isCastSupported={core.transport.isSupported}
      castAvailability={core.transport.availability}
      isCastConnecting={core.transport.isConnecting}
      onSendToTV={core.handleSendToTV}
      onStopPresentation={core.handleStopPresentation}
      onSendTransportCommand={core.handleSendTransportCommand}
    />
  );
}
