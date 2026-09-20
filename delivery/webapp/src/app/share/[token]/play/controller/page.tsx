"use client";

import { useParams, useRouter } from "next/navigation";
import { ControllerPlayer } from "@/components/play/ControllerPlayer";
import { usePlaybackCore } from "@/hooks/usePlaybackCore";
import { useLocale } from "@/hooks/useLocale";
import { normalizeChaptersManifest } from "@/lib/render/chapters";
import { getOfflineRecord } from "@/lib/offline/offline-index";
import { resolveOfflinePlayback } from "@/lib/offline/offline-playback";
import { Loader2 } from "lucide-react";

/**
 * Anonymous share play controller (issue #218): a thin consumer of the
 * shared playback core. It differs from the songset controller only in its
 * anonymous token fetch chain, its offline resolver (share-namespace, PR2),
 * and its exit routes.
 */
export default function ShareControllerPage() {
  const params = useParams();
  const router = useRouter();
  const { t } = useLocale();
  const token = params.token as string;

  const core = usePlaybackCore({
    bootKey: token,
    defaultTitle: t("control.sharedWorshipSet"),
    castSourceKind: "share",
    projectionPath: `/share/${token}/play/projection`,
    offlineUnavailableMessage: t("control.offlineUnavailable"),
    // The anonymous token fetch chain. PR2 swaps this for the
    // share-namespace resolver (ADR-0009); until then the songset-namespace
    // index backs the shared core's media-error recovery, which is the only
    // consumer when no cache-first boot exists.
    resolveOffline: async () => {
      const record = await getOfflineRecord(token);
      if (!record) return null;
      const offline = await resolveOfflinePlayback(token);
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
    runOnlineChain: async (ctx) => {
      const res = await fetch(`/api/share/${token}`);
      if (!res.ok) {
        let errorMessage = t("control.linkNoLongerAvailable");
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

      interface ShareResponse {
        songset?: { name?: string } | null;
        playback?: {
          mediaKind?: "video" | "audio";
          selectedRenderJobId?: string | null;
          mp4Url?: string | null;
          mp3Url?: string | null;
          chapterRecordingHashes?: (string | null)[];
          chaptersData?: unknown;
        } | null;
      }
      const data: ShareResponse = await res.json();
      if (ctx.isCancelled()) return;

      const kind = data.playback?.mediaKind ?? (data.playback?.mp4Url ? "video" : "audio");
      const src = kind === "video" ? data.playback?.mp4Url : data.playback?.mp3Url;
      if (!src) {
        throw new Error(t("control.noPlaybackArtifacts"));
      }

      // The share-token route mints presigned R2 URLs (no auth on the TV);
      // the phone hands them to the receiver, which only hits R2.
      ctx.setTitle(data.songset?.name ?? t("control.sharedWorshipSet"));
      ctx.setChapterRecordingHashes(data.playback?.chapterRecordingHashes ?? []);
      ctx.setMedia({
        src,
        kind,
        isOffline: false,
        viaProxy: false,
        renderJobId: data.playback?.selectedRenderJobId ?? "",
      });

      if (data.playback?.chaptersData) {
        try {
          const manifest = normalizeChaptersManifest(data.playback.chaptersData);
          if (!ctx.isCancelled()) {
            ctx.setChapters(manifest.chapters);
          }
        } catch (e) {
          console.error("Failed to parse chapters:", e);
        }
      }
    },
  });

  const { media } = core;

  if (core.isLoading) {
    return (
      <div className="fixed inset-0 bg-black flex items-center justify-center">
        <div className="flex flex-col items-center gap-4">
          <Loader2 className="size-8 animate-spin text-white" />
          <p className="text-white/70">{t("control.loadingPlayer")}</p>
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
            onClick={() => router.push(`/share/${token}`)}
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
      playerId={token}
      exitRoute={`/share/${token}`}
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
