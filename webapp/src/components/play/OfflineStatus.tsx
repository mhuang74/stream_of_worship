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
  const [isIOS174Plus, setIsIOS174Plus] = useState(true);

  // Check if artifacts are already cached
  useEffect(() => {
    const checkCacheStatus = async () => {
      if (!renderJobId || !("caches" in window)) {
        setIsCached(false);
        return;
      }

      try {
        const cache = await caches.open("sow-artifacts");
        const mp3Cached = mp3R2Key ? await cache.match(mp3R2Key) : null;
        // Check other artifacts but only mp3 is required for "cached" status
        if (mp4R2Key) await cache.match(mp4R2Key);
        if (chaptersR2Key) await cache.match(chaptersR2Key);

        // Consider cached if at least the audio is cached
        setIsCached(!!mp3Cached);
      } catch {
        setIsCached(false);
      }
    };

    checkCacheStatus();
  }, [renderJobId, mp3R2Key, mp4R2Key, chaptersR2Key]);

  // Check iOS version for offline support
  useEffect(() => {
    const checkIOSVersion = () => {
      const userAgent = navigator.userAgent;
      const isIOS = /iPad|iPhone|iPod/.test(userAgent);

      if (!isIOS) {
        setIsIOS174Plus(true);
        return;
      }

      // Extract iOS version
      const match = userAgent.match(/OS (\d+)_(\d+)/);
      if (match) {
        const major = parseInt(match[1], 10);
        const minor = parseInt(match[2], 10);
        setIsIOS174Plus(major > 17 || (major === 17 && minor >= 4));
      }
    };

    checkIOSVersion();
  }, []);

  const handleDownloadOffline = useCallback(async () => {
    if (!renderJobId || !("caches" in window)) {
      toast.error("Offline caching not available");
      return;
    }

    // Request persistent storage on first cache action (iOS 17.4+)
    if ("storage" in navigator && "persist" in navigator.storage) {
      try {
        await navigator.storage.persist();
      } catch {
        // Continue even if persist fails
      }
    }

    setIsDownloading(true);
    setCacheProgress(0);

    try {
      const cache = await caches.open("sow-artifacts");
      const artifactsToCache: string[] = [];

      if (mp3R2Key) artifactsToCache.push(mp3R2Key);
      if (mp4R2Key) artifactsToCache.push(mp4R2Key);
      if (chaptersR2Key) artifactsToCache.push(chaptersR2Key);

      if (artifactsToCache.length === 0) {
        toast.error("No artifacts available to cache");
        setIsDownloading(false);
        return;
      }

      // Cache each artifact
      for (let i = 0; i < artifactsToCache.length; i++) {
        const url = artifactsToCache[i];
        const response = await fetch(url);

        if (!response.ok) {
          throw new Error(`Failed to fetch ${url}`);
        }

        await cache.put(url, response.clone());
        setCacheProgress(Math.round(((i + 1) / artifactsToCache.length) * 100));
      }

      setIsCached(true);
      toast.success("Downloaded for offline playback");
    } catch (error) {
      console.error("Cache error:", error);
      toast.error("Failed to download for offline");
    } finally {
      setIsDownloading(false);
      setCacheProgress(0);
    }
  }, [renderJobId, mp3R2Key, mp4R2Key, chaptersR2Key]);

  const hasArtifacts = !!(mp3R2Key || mp4R2Key);

  if (!hasArtifacts) {
    return null;
  }

  // iOS < 17.4 warning
  if (!isIOS174Plus) {
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
