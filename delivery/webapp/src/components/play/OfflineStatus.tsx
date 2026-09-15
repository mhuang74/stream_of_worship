"use client";

import { useState, useEffect, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Download, Check, WifiOff, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { useLocale } from "@/hooks/useLocale";
import {
  ARTIFACT_CACHE_NAME,
  getArtifactCacheStatus,
  isOfflineSupportedOnCurrentDevice,
  type CacheableArtifacts,
} from "@/lib/offline/artifact-cache";
import { downloadOfflineArtifacts, NoArtifactsError } from "@/lib/offline/download-offline";

export interface OfflineStatusProps {
  songsetId: string;
  songsetName: string;
  renderJobId: string | null;
  mp3R2Key?: string | null;
  mp4R2Key?: string | null;
  chaptersR2Key?: string | null;
  className?: string;
}

export function OfflineStatus({
  songsetId,
  songsetName,
  renderJobId,
  mp3R2Key,
  mp4R2Key,
  chaptersR2Key,
  className,
}: OfflineStatusProps) {
  const { t } = useLocale();
  const [isCached, setIsCached] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [cacheProgress, setCacheProgress] = useState(0);
  const [isSupported] = useState(isOfflineSupportedOnCurrentDevice);

  // Legacy cache entries (pre-stable /sow-artifact-cache/<renderJobId>/{...}
  // keys) could never be matched or deleted by name; remove them once.
  useEffect(() => {
    const cleanupStaleEntries = async () => {
      if (!("caches" in window)) return;
      try {
        const cache = await caches.open(ARTIFACT_CACHE_NAME);
        const keys = await cache.keys();
        for (const key of keys) {
          if (key.url.includes("/songsets/") && key.url.includes("/renders/")) {
            await cache.delete(key);
          }
        }
      } catch {}
    };
    cleanupStaleEntries();
  }, []);
  useEffect(() => {
    const checkCacheStatus = async () => {
      if (!renderJobId || !("caches" in window)) {
        setIsCached(false);
        return;
      }

      try {
        const artifacts: CacheableArtifacts = {
          mp3Url: mp3R2Key ? `/api/r2/artifact/${renderJobId}/output.mp3` : null,
          mp4Url: mp4R2Key ? `/api/r2/artifact/${renderJobId}/output.mp4` : null,
          chaptersUrl: chaptersR2Key ? `/api/r2/artifact/${renderJobId}/chapters.json` : null,
        };
        const status = await getArtifactCacheStatus(renderJobId, artifacts);
        setIsCached(status.isCached);
      } catch {
        setIsCached(false);
      }
    };

    checkCacheStatus();
  }, [renderJobId, mp3R2Key, mp4R2Key, chaptersR2Key]);

  const handleDownloadOffline = useCallback(async () => {
    if (!renderJobId || !("caches" in window)) {
      toast.error(t("audio.offline.cachingNotAvailable"));
      return;
    }

    setIsDownloading(true);
    setCacheProgress(0);

    try {
      await downloadOfflineArtifacts(
        { songsetId, songsetName, renderJobId },
        (percent) => {
          setCacheProgress(percent);
        }
      );

      setIsCached(true);
      toast.success(t("audio.offline.downloaded"));
    } catch (error) {
      if (error instanceof NoArtifactsError) {
        toast.error(t("audio.offline.noArtifacts"));
      } else {
        console.error("Cache error:", error);
        toast.error(t("audio.offline.downloadFailed"));
      }
    } finally {
      setIsDownloading(false);
      setCacheProgress(0);
    }
  }, [songsetId, songsetName, renderJobId, t]);

  const hasArtifacts = !!(mp3R2Key || mp4R2Key);

  if (!hasArtifacts) {
    return null;
  }

  if (!isSupported) {
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <div
              className={cn(
                "flex items-center gap-2 text-sm text-muted-foreground",
                className
              )}
            >
              <WifiOff className="size-4" />
              <span>{t("audio.offline.updateIos")}</span>
            </div>
          </TooltipTrigger>
          <TooltipContent>
            <p>{t("audio.offline.iosTooltip")}</p>
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  return (
    <div className={cn("flex items-center gap-2", className)}>
      {isCached ? (
        <Badge variant="secondary" className="gap-1">
          <Check className="size-3" />
          {t("audio.offline.ready")}
        </Badge>
      ) : (
        <Button
          variant="outline"
          size="sm"
          onClick={handleDownloadOffline}
          disabled={isDownloading}
          className="gap-2"
        >
          {isDownloading ? (
            <>
              <Loader2 className="size-4 animate-spin" />
              {cacheProgress > 0 ? `${cacheProgress}%` : t("audio.offline.downloading")}
            </>
          ) : (
            <>
              <Download className="size-4" />
              {t("audio.offline.downloadForOffline")}
            </>
          )}
        </Button>
      )}
    </div>
  );
}
