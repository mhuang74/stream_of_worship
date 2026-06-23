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
import {
  ARTIFACT_CACHE_NAME,
  cacheArtifacts,
  getArtifactCacheStatus,
  isOfflineSupportedOnCurrentDevice,
  requestPersistentStorage,
  type CacheableArtifacts,
} from "@/lib/offline/artifact-cache";

export interface OfflineStatusProps {
  renderJobId: string | null;
  mp3R2Key?: string | null;
  mp4R2Key?: string | null;
  chaptersR2Key?: string | null;
  className?: string;
}

export function OfflineStatus({
  renderJobId,
  mp3R2Key,
  mp4R2Key,
  chaptersR2Key,
  className,
}: OfflineStatusProps) {
  const [isCached, setIsCached] = useState(false);
  const [isDownloading, setIsDownloading] = useState(false);
  const [cacheProgress, setCacheProgress] = useState(0);
  const [isSupported] = useState(isOfflineSupportedOnCurrentDevice);

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
      toast.error("Offline caching not available");
      return;
    }

    await requestPersistentStorage();

    setIsDownloading(true);
    setCacheProgress(0);

    try {
      const apiUrl = `/api/offline/cache?renderJobId=${encodeURIComponent(renderJobId)}`;
      const response = await fetch(apiUrl);

      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.error || "Failed to get download URLs");
      }

      const proxyUrls = await response.json();
      const artifacts: CacheableArtifacts = {
        mp3Url: proxyUrls.mp3Url,
        mp4Url: proxyUrls.mp4Url,
        chaptersUrl: proxyUrls.chaptersUrl,
      };

      if (!artifacts.mp3Url && !artifacts.mp4Url && !artifacts.chaptersUrl) {
        toast.error("No artifacts available to cache");
        setIsDownloading(false);
        return;
      }

      await cacheArtifacts(renderJobId, artifacts, (percent) => {
        setCacheProgress(percent);
      });

      setIsCached(true);
      toast.success("Downloaded for offline playback");
    } catch (error) {
      console.error("Cache error:", error);
      toast.error("Failed to download for offline");
    } finally {
      setIsDownloading(false);
      setCacheProgress(0);
    }
  }, [renderJobId]);

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
              <span>Update iOS for offline</span>
            </div>
          </TooltipTrigger>
          <TooltipContent>
            <p>Offline caching requires iOS 17.4 or later</p>
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
          Offline ready
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
              {cacheProgress > 0 ? `${cacheProgress}%` : "Downloading..."}
            </>
          ) : (
            <>
              <Download className="size-4" />
              Download for offline
            </>
          )}
        </Button>
      )}
    </div>
  );
}
