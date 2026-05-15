"use client";

import { useState, useEffect, useCallback } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { OfflineStatus } from "./OfflineStatus";
import { cn } from "@/lib/utils";
import { toast } from "sonner";
import {
  Play,
  Share2,
  Monitor,
  AlertTriangle,
  RefreshCw,
  Music,
  Clock,
  ChevronRight,
  Loader2,
} from "lucide-react";

export interface SongsetItem {
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

export interface PrePlayCardProps {
  songset: {
    id: string;
    name: string;
    description: string | null;
    renderState: "unrendered" | "rendering" | "fresh" | "stale" | "failed";
    latestRenderJobId: string | null;
    lastFailedRenderJobId: string | null;
  };
  items: SongsetItem[];
  renderJob: {
    id: string;
    status: string;
    mp3R2Key: string | null;
    mp4R2Key: string | null;
    chaptersR2Key: string | null;
  } | null;
  onStartWorship: () => void;
  onReRender: () => void;
  onShare: () => void;
  className?: string;
}

export function PrePlayCard({
  songset,
  items,
  renderJob,
  onStartWorship,
  onReRender,
  onShare,
  className,
}: PrePlayCardProps) {
  const [isPresentationAvailable, setIsPresentationAvailable] = useState(false);
  const [isCastAvailable, setIsCastAvailable] = useState(false);
  const [isStartingWorship, setIsStartingWorship] = useState(false);

  // Check for Presentation API and Cast availability
  useEffect(() => {
    const checkPresentationAvailability = async () => {
      if (typeof navigator === "undefined") return;

      // Check if Presentation API is available
      const hasPresentation = "presentation" in navigator;
      setIsPresentationAvailable(hasPresentation);

      if (hasPresentation) {
        try {
          // @ts-expect-error - PresentationRequest may not be in types
          const request = new PresentationRequest(["/songsets/${songset.id}/play/projection"]);
          // @ts-expect-error - getAvailability may not be in types
          const availability = await request.getAvailability();
          setIsCastAvailable(availability.value);

          availability.addEventListener("change", () => {
            setIsCastAvailable(availability.value);
          });
        } catch {
          setIsCastAvailable(false);
        }
      }
    };

    checkPresentationAvailability();
  }, [songset.id]);

  // Calculate total duration
  const totalDurationSeconds = items.reduce(
    (sum, item) => sum + (item.recording?.durationSeconds || 0),
    0
  );

  // Format duration
  const formatDuration = (seconds: number): string => {
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);

    if (hours > 0) {
      return `${hours}h ${minutes}m`;
    }
    return `${minutes} min`;
  };

  // Format individual song duration
  const formatSongDuration = (seconds: number | null): string => {
    if (!seconds) return "--:--";
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  const hasRenderArtifacts = !!(renderJob?.mp3R2Key || renderJob?.mp4R2Key);
  const isStale = songset.renderState === "stale";
  const isFailed = songset.renderState === "failed";
  const isUnrendered = songset.renderState === "unrendered";

  const handleStartWorship = useCallback(async () => {
    if (!hasRenderArtifacts) {
      toast.error("Please render this songset first");
      return;
    }

    setIsStartingWorship(true);
    try {
      await onStartWorship();
    } finally {
      setIsStartingWorship(false);
    }
  }, [hasRenderArtifacts, onStartWorship]);

  const handleSendToTV = useCallback(async () => {
    if (!isPresentationAvailable) {
      toast.error("Screen casting not available on this device");
      return;
    }

    try {
      // @ts-expect-error - PresentationRequest may not be in types
      const request = new PresentationRequest([`/songsets/${songset.id}/play/projection`]);
      // @ts-expect-error - start may not be in types
      await request.start();
      toast.success("Opening on second screen");
    } catch (error) {
      console.error("Presentation error:", error);
      toast.error("Failed to connect to second screen");
    }
  }, [isPresentationAvailable, songset.id]);

  const handleShare = useCallback(async () => {
    // Try Web Share API first
    if (navigator.share) {
      try {
        await navigator.share({
          title: songset.name,
          text: `Check out "${songset.name}" on Stream of Worship`,
          url: `${window.location.origin}/songsets/${songset.id}`,
        });
        return;
      } catch (error) {
        // User cancelled or share failed, fall through to custom handler
        if ((error as Error).name !== "AbortError") {
          console.error("Share failed:", error);
        }
      }
    }

    // Fall back to custom share handler
    onShare();
  }, [songset.name, songset.id, onShare]);

  return (
    <Card className={cn("w-full", className)}>
      <CardHeader>
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1 min-w-0">
            <CardTitle className="text-xl truncate">{songset.name}</CardTitle>
            {songset.description && (
              <CardDescription className="mt-1 line-clamp-2">
                {songset.description}
              </CardDescription>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <Badge variant="outline" className="gap-1">
              <Music className="size-3" />
              {items.length} {items.length === 1 ? "song" : "songs"}
            </Badge>
          </div>
        </div>
      </CardHeader>

      <CardContent className="space-y-6">
        {/* Render Status Alerts */}
        {isStale && (
          <Alert variant="default" className="bg-amber-50 dark:bg-amber-950/20 border-amber-200">
            <AlertTriangle className="size-4 text-amber-600" />
            <AlertTitle className="text-amber-800 dark:text-amber-200">
              Artifacts out of date
            </AlertTitle>
            <AlertDescription className="text-amber-700 dark:text-amber-300">
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <span>Songs have been modified since the last render.</span>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={onReRender}
                  className="shrink-0 gap-1 border-amber-500/50 hover:bg-amber-100"
                >
                  <RefreshCw className="size-3" />
                  Re-render
                </Button>
              </div>
            </AlertDescription>
          </Alert>
        )}

        {isFailed && (
          <Alert variant="destructive">
            <AlertTriangle className="size-4" />
            <AlertTitle>Render failed</AlertTitle>
            <AlertDescription>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <span>The last render attempt failed.</span>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={onReRender}
                  className="shrink-0 gap-1"
                >
                  <RefreshCw className="size-3" />
                  Retry render
                </Button>
              </div>
            </AlertDescription>
          </Alert>
        )}

        {isUnrendered && (
          <Alert>
            <AlertTriangle className="size-4" />
            <AlertTitle>Not rendered yet</AlertTitle>
            <AlertDescription>
              <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                <span>This songset needs to be rendered before playback.</span>
                <Button
                  size="sm"
                  onClick={onReRender}
                  className="shrink-0 gap-1"
                >
                  <RefreshCw className="size-3" />
                  Render now
                </Button>
              </div>
            </AlertDescription>
          </Alert>
        )}

        {/* Song List */}
        <div className="space-y-3">
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span className="font-medium">Song List</span>
            <span className="flex items-center gap-1">
              <Clock className="size-3" />
              Total: {formatDuration(totalDurationSeconds)}
            </span>
          </div>

          <div className="space-y-2">
            {items.map((item, index) => (
              <div
                key={item.id}
                className="flex items-center gap-3 p-3 rounded-lg bg-muted/50 hover:bg-muted transition-colors"
              >
                <span className="flex items-center justify-center w-6 h-6 rounded-full bg-primary/10 text-primary text-xs font-medium shrink-0">
                  {index + 1}
                </span>
                <div className="flex-1 min-w-0">
                  <p className="font-medium truncate">
                    {item.song?.title || "Unknown Song"}
                  </p>
                  <p className="text-xs text-muted-foreground truncate">
                    {item.song?.composer || item.song?.lyricist
                      ? [item.song.composer, item.song.lyricist]
                          .filter(Boolean)
                          .join(" • ")
                      : item.song?.albumName || "Unknown Artist"}
                  </p>
                </div>
                <div className="flex items-center gap-2 shrink-0 text-sm text-muted-foreground">
                  {item.recording?.durationSeconds && (
                    <span>{formatSongDuration(item.recording.durationSeconds)}</span>
                  )}
                  <ChevronRight className="size-4 opacity-50" />
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Offline Status */}
        {hasRenderArtifacts && (
          <OfflineStatus
            renderJobId={renderJob?.id || null}
            mp3R2Key={renderJob?.mp3R2Key}
            mp4R2Key={renderJob?.mp4R2Key}
            chaptersR2Key={renderJob?.chaptersR2Key}
          />
        )}

        {/* Action Buttons */}
        <div className="space-y-3 pt-2">
          {/* Start Worship Button */}
          <Button
            size="lg"
            className="w-full gap-2 h-14 text-lg"
            onClick={handleStartWorship}
            disabled={!hasRenderArtifacts || isStartingWorship}
          >
            {isStartingWorship ? (
              <>
                <Loader2 className="size-5 animate-spin" />
                Starting...
              </>
            ) : (
              <>
                <Play className="size-5" />
                Start Worship
              </>
            )}
          </Button>

          {/* Secondary Actions */}
          <div className="grid grid-cols-2 gap-3">
            {/* Send to TV Button - only show if Presentation API available */}
            {isPresentationAvailable && (
              <Button
                variant="outline"
                className="gap-2"
                onClick={handleSendToTV}
                disabled={!hasRenderArtifacts}
              >
                <Monitor className="size-4" />
                {isCastAvailable ? "Send to TV" : "Cast unavailable"}
              </Button>
            )}

            {/* Share Button */}
            <Button
              variant="outline"
              className="gap-2"
              onClick={handleShare}
            >
              <Share2 className="size-4" />
              Share
            </Button>
          </div>

          {/* Show message if no render artifacts */}
          {!hasRenderArtifacts && (
            <p className="text-sm text-center text-muted-foreground">
              Render this songset to enable playback
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
