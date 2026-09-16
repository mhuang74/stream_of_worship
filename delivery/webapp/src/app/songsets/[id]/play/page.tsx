"use client";

import { useState, useEffect, useCallback } from "react";
import { useParams, useRouter } from "next/navigation";
import { useLocale } from "@/hooks/useLocale";
import { useSongsetListBack } from "@/hooks/useSongsetListBack";
import { PrePlayCard } from "@/components/play/PrePlayCard";
import { OfflineAvailableCard } from "@/components/play/OfflineAvailableCard";
import { getOfflineRecord, type OfflineSongsetRecord } from "@/lib/offline/offline-index";
import { ShareDialog } from "@/components/share/ShareDialog";
import { Button } from "@/components/ui/button";
import { ArrowLeft } from "lucide-react";

/** The songset fetch answered 404: the server is reachable, the set is gone.
 * Never a reason to offer the offline card. */
class SongsetNotFoundError extends Error {}

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
  const backToList = useSongsetListBack();
  const { t } = useLocale();
  const songsetId = params.id as string;

  const [songset, setSongset] = useState<SongsetData | null>(null);
  const [items, setItems] = useState<SongsetItem[]>([]);
  const [renderJob, setRenderJob] = useState<RenderJobData | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [shareDialogOpen, setShareDialogOpen] = useState(false);
  // Offline entry (issue #206): when the songset fetch fails with no network,
  // an offline index record turns the error screen into the offline card.
  const [offlineRecord, setOfflineRecord] = useState<OfflineSongsetRecord | null>(null);

  // Load songset data
  useEffect(() => {
    let cancelled = false;

    // Resolves true when the songset has an offline index record — the
    // offline-available card then replaces the error screen. getOfflineRecord
    // is no-throw (null when the index is unavailable or has no record).
    async function offerOfflineEntry(): Promise<boolean> {
      const record = await getOfflineRecord(songsetId);
      if (cancelled || !record) return false;
      setOfflineRecord(record);
      return true;
    }

    async function loadSongset() {
      try {
        setIsLoading(true);
        setError(null);
        // A previous run may have set the record; a fresh fetch (e.g. the
        // effect re-running) must not keep a stale offline card.
        setOfflineRecord(null);

        const response = await fetch(`/api/songsets/${songsetId}`);

        if (!response.ok) {
          if (response.status === 401) {
            router.push("/login");
            return;
          }
          if (response.status === 404) {
            throw new SongsetNotFoundError(t("play.notFound"));
          }
          // The offline service worker answers an uncached API route with
          // 503 {"error":"offline"} — the exact signal that a downloaded copy
          // may still play (issue #206). A genuine 5xx from a reachable
          // server is NOT that: an online user who hits a server error should
          // see the real message, not an "You are offline" card. Handled
          // here (not thrown) so the catch block below — which offers the
          // offline card — only ever sees genuine network rejections.
          if (response.status === 503 && (await response.json().catch(() => null))?.error === "offline") {
            if (await offerOfflineEntry()) return;
          }
          setError(t("play.loadFailed"));
          return;
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
          // A 404 means the server is reachable and the set is gone: the
          // offline card would be a lie.
          if (err instanceof SongsetNotFoundError) {
            setError(err.message);
            return;
          }
          // Network-level failure (airplane mode, server unreachable): the
          // downloaded copy may still play (issue #206).
          if (await offerOfflineEntry()) return;
          setError(err instanceof Error ? err.message : t("play.loadFailed"));
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
  }, [songsetId, router, t]);

  const handleStartWorship = useCallback(() => {
    // Offline: a full document navigation is the deterministic path — the
    // SW document route serves the pre-cached controller HTML. SPA
    // navigation to a never-visited route needs RSC fetches that cannot be
    // pre-cached reliably (issue #206).
    if (typeof navigator !== "undefined" && navigator.onLine === false) {
      window.location.assign(`/songsets/${songsetId}/play/controller`);
      return;
    }
    // Online: SPA navigation, unchanged.
    router.push(`/songsets/${songsetId}/play/controller`);
  }, [router, songsetId]);

  // The offline card only renders after the songset fetch already failed, so
  // its tap always takes the deterministic full-document path regardless of
  // what navigator.onLine reports.
  const handleOfflineCardStartWorship = useCallback(() => {
    window.location.assign(`/songsets/${songsetId}/play/controller`);
  }, [songsetId]);

  const handleReRender = useCallback(() => {
    router.push(`/songsets/${songsetId}/render`);
  }, [router, songsetId]);

  const handleShare = useCallback(() => {
    setShareDialogOpen(true);
  }, []);

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div role="status" className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    );
  }

  if (error || !songset) {
    // The songset fetch failed with no network and a downloaded copy exists:
    // surface it instead of the error screen (issue #206).
    if (offlineRecord) {
      return (
        <div className="min-h-screen bg-background">
          <OfflineAvailableCard
            songsetName={offlineRecord.songsetName}
            onStartWorship={handleOfflineCardStartWorship}
          />
        </div>
      );
    }
    return (
      <div className="flex min-h-screen flex-col items-center justify-center p-4">
        <p className="text-center text-destructive">
          {error || t("play.notFound")}
        </p>
        <Button
          variant="ghost"
          className="mt-4"
          onClick={() => backToList()}
        >
          {t("play.backToSongsets")}
        </Button>
      </div>
    );
  }

  const totalDurationSeconds = items.reduce(
    (sum, item) => sum + (item.recording?.durationSeconds || 0), 0
  );

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="sticky top-0 z-10 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="flex h-14 items-center gap-4 px-4">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => router.push(`/songsets/${songsetId}`)}
            aria-label={t("play.backAriaLabel")}
          >
            <ArrowLeft className="size-5" />
          </Button>
          <div className="flex-1">
            <h1 className="font-semibold">{t("play.title")}</h1>
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

      <ShareDialog
        open={shareDialogOpen}
        onOpenChange={setShareDialogOpen}
        songsetId={songset.id}
        songsetName={songset.name}
        durationSeconds={totalDurationSeconds || null}
        renderJobId={renderJob?.id}
      />
    </div>
  );
}
