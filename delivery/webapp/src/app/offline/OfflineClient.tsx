"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { toast } from "sonner";
import { Loader2, Play, RefreshCw, Trash2, WifiOff, Download, Check } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { useLocale } from "@/hooks/useLocale";
import { useConnectivity } from "@/hooks/useConnectivity";
import {
  listOfflineRecords,
  removeOfflineSongset,
  type OfflineSongsetRecord,
} from "@/lib/offline/offline-index";
import { matchCachedArtifact } from "@/lib/offline/artifact-cache";
import { downloadOfflineArtifacts, NoArtifactsError } from "@/lib/offline/download-offline";

/**
 * /offline list (issue #211 follow-up): the offline redirect target and the
 * canonical playback entry. Rendered purely from the IndexedDB offline index
 * — no network fetch on boot. The document is pre-cached by the SW
 * (cacheOfflineListDocument), so this page boots with zero connectivity.
 *
 * Online extras are progressive enhancements layered over the same list:
 * per-row staleness comparison + Update action, byte verification downgrading
 * rows whose cached artifacts vanished.
 */

interface OfflineRow extends OfflineSongsetRecord {
  /** Bytes verified present; false → inert tap + re-download hint. */
  bytesOk: boolean;
  /** Server's latest renderJobId; null when not compared (offline) or equal. */
  latestRenderJobId: string | null;
}

export function OfflineClient() {
  const { t } = useLocale();
  const connectivity = useConnectivity();
  const [rows, setRows] = useState<OfflineRow[] | null>(null);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [progress, setProgress] = useState(0);

  const loadRecords = useCallback(async () => {
    const records = await listOfflineRecords();
    setRows(records.map((record) => ({ ...record, bytesOk: true, latestRenderJobId: null })));
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const records = await listOfflineRecords();
      if (!cancelled) {
        setRows(records.map((record) => ({ ...record, bytesOk: true, latestRenderJobId: null })));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadRecords]);

  // Byte verification (Q6): rows start tappable; one whose cached media is
  // gone downgrades to inert + "needs re-download". Runs post-render so the
  // list is never blocked on Cache Storage reads.
  useEffect(() => {
    if (!rows) return;
    let cancelled = false;
    for (const row of rows) {
      if (!row.bytesOk) continue;
      void (async () => {
        const kind = row.cachedMp4 ? "mp4" : "mp3";
        const cached = await matchCachedArtifact(row.renderJobId, kind);
        if (!cancelled && !cached) {
          setRows((prev) =>
            prev
              ? prev.map((r) =>
                  r.songsetId === row.songsetId ? { ...r, bytesOk: false } : r
                )
              : prev
          );
        }
      })();
    }
    return () => {
      cancelled = true;
    };
  }, [rows]);

  // Staleness comparison (Q3c/Q10): online only, one pass. A record whose
  // renderJobId trails the server's latest render gets an Update action.
  useEffect(() => {
    if (!rows || connectivity !== "online") return;
    let cancelled = false;
    void (async () => {
      const updates = await Promise.all(
        rows.map(async (row) => {
          try {
            const response = await fetch(`/api/songsets/${row.songsetId}`);
            if (!response.ok) return null;
            const data = await response.json();
            return data.latestRenderJobId &&
              data.latestRenderJobId !== row.renderJobId
              ? { songsetId: row.songsetId, latestRenderJobId: data.latestRenderJobId as string }
              : null;
          } catch {
            return null;
          }
        })
      );
      if (cancelled) return;
      setRows((prev) =>
        prev
          ? prev.map((r) => {
              const update = updates.find((u) => u?.songsetId === r.songsetId);
              return update ? { ...r, latestRenderJobId: update.latestRenderJobId } : r;
            })
          : prev
      );
    })();
    return () => {
      cancelled = true;
    };
  }, [rows, connectivity]);

  const handlePlay = useCallback((songsetId: string) => {
    // Full document navigation: the SW serves the pre-cached controller
    // document; SPA navigation would need an RSC fetch that dead-ends
    // offline (issue #206's trap). Cache-first boot (controller page)
    // plays the offline copy regardless of connectivity.
    window.location.assign(`/songsets/${songsetId}/play/controller`);
  }, []);

  const handleUpdate = useCallback(
    async (row: OfflineRow) => {
      if (!row.latestRenderJobId) return;
      setDownloadingId(row.songsetId);
      setProgress(0);
      try {
        await downloadOfflineArtifacts(
          {
            songsetId: row.songsetId,
            songsetName: row.songsetName,
            renderJobId: row.latestRenderJobId,
          },
          (percent) => setProgress(percent)
        );
        toast.success(t("offline.updated"));
        await loadRecords();
      } catch (error) {
        if (error instanceof NoArtifactsError) {
          toast.error(t("audio.offline.noArtifacts"));
        } else {
          toast.error(t("audio.offline.downloadFailed"));
        }
      } finally {
        setDownloadingId(null);
        setProgress(0);
      }
    },
    [loadRecords, t]
  );

  const handleRemove = useCallback(
    async (songsetId: string) => {
      try {
        await removeOfflineSongset(songsetId);
        setRows((prev) =>
          prev ? prev.filter((r) => r.songsetId !== songsetId) : prev
        );
        toast.success(t("songsets.toast.offlineRemoved"));
      } catch (err) {
        toast.error(
          err instanceof Error
            ? err.message
            : t("songsets.error.removeOfflineFailed")
        );
      }
    },
    [t]
  );

  if (rows === null) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 className="size-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="mx-auto max-w-2xl px-4 py-16 text-center space-y-4">
        <WifiOff className="size-10 mx-auto text-muted-foreground" />
        <p className="text-muted-foreground">{t("offline.empty")}</p>
        <Link
          href="/songsets"
          className="text-primary underline underline-offset-4 hover:text-primary/80"
        >
          {t("offline.emptyLink")}
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-2xl px-4 py-6 space-y-3">
      <h1 className="text-2xl font-bold">{t("offline.title")}</h1>
      {rows.map((row) => {
        const isDownloading = downloadingId === row.songsetId;
        const hasUpdate = row.latestRenderJobId != null;
        return (
          <Card key={row.songsetId} data-songset-id={row.songsetId}>
            <CardContent className="p-4 flex items-center justify-between gap-3">
              <div className="min-w-0">
                <p className="font-medium truncate" title={row.songsetName}>
                  {row.songsetName}
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  {t("offline.cachedPrefix")}{" "}
                  {new Date(row.cachedAt).toLocaleDateString()}
                </p>
                <div className="flex items-center gap-2 mt-2 flex-wrap">
                  {row.bytesOk ? (
                    <Badge variant="secondary" className="gap-1">
                      <Check className="size-3" />
                      {t("offline.ready")}
                    </Badge>
                  ) : (
                    <Badge variant="destructive" className="gap-1">
                      <WifiOff className="size-3" />
                      {t("offline.needsRedownload")}
                    </Badge>
                  )}
                  {hasUpdate && (
                    <Badge variant="outline" className="gap-1">
                      <RefreshCw className="size-3" />
                      {t("offline.updateAvailable")}
                    </Badge>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {hasUpdate && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => handleUpdate(row)}
                    disabled={isDownloading}
                    className="gap-2"
                  >
                    {isDownloading ? (
                      <>
                        <Loader2 className="size-4 animate-spin" />
                        {progress > 0 ? `${progress}%` : t("audio.offline.downloading")}
                      </>
                    ) : (
                      <>
                        <Download className="size-4" />
                        {t("offline.update")}
                      </>
                    )}
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => handleRemove(row.songsetId)}
                  disabled={isDownloading}
                  aria-label={t("offline.remove")}
                >
                  <Trash2 className="size-4" />
                </Button>
                <Button
                  variant="default"
                  size="sm"
                  onClick={() => handlePlay(row.songsetId)}
                  disabled={!row.bytesOk || isDownloading}
                  className="gap-2"
                >
                  <Play className="size-4" />
                  {t("offline.play")}
                </Button>
              </div>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
