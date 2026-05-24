"use client"

import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import {
  CheckCircle2,
  Share2,
  Music,
  Video,
  FileJson,
  Timer,
} from "lucide-react"
import { toast } from "sonner"
import { sanitizeFilename, downloadArtifact } from "@/lib/download"

interface RenderCompleteProps {
  jobId: string
  songsetId: string
  songsetName: string
  hasAudio: boolean
  hasVideo: boolean
  hasChapters: boolean
  elapsedSeconds?: number
  onDone: () => void
  onShare: () => void
}

function formatDuration(seconds: number): string {
  if (seconds < 60) {
    return `${Math.round(seconds)}s`
  }
  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = Math.round(seconds % 60)
  return `${minutes}m ${remainingSeconds}s`
}

export function RenderComplete({
  jobId,
  songsetId,
  songsetName,
  hasAudio,
  hasVideo,
  hasChapters,
  elapsedSeconds,
  onDone,
  onShare,
}: RenderCompleteProps) {
  const handleDownloadFile = async (
    fileType: "audio" | "video" | "json",
    extension: string,
  ) => {
    const toastId = toast.loading("Preparing download...");
    const controller = new AbortController();

    try {
      const filename = sanitizeFilename(songsetName);
      const disposition = `attachment; filename="${filename}.${extension}"`;
      const res = await fetch(
        `/api/signed-url?renderJobId=${encodeURIComponent(jobId)}` +
          `&fileType=${fileType}` +
          `&contentDisposition=${encodeURIComponent(disposition)}`,
        { signal: controller.signal }
      );
      if (!res.ok) throw new Error("Failed to get download URL");
      const { url } = await res.json();

      downloadArtifact(url);
      toast.success("Download started", { id: toastId });
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      toast.error("Download failed", { id: toastId });
    }
  };

  const handleShare = async () => {
    if (navigator.share) {
      try {
        await navigator.share({
          title: songsetName,
          text: `Check out "${songsetName}" on Stream of Worship`,
          url: `${window.location.origin}/songsets/${songsetId}`,
        });
      } catch (error) {
        if ((error as Error).name !== "AbortError") {
          console.error("Share failed:", error);
          onShare();
        }
      }
    } else {
      onShare();
    }
  }

  return (
    <Card className="w-full">
      <CardHeader className="text-center">
        <div className="mx-auto mb-4 flex size-16 items-center justify-center rounded-full bg-green-100 dark:bg-green-900">
          <CheckCircle2 className="size-8 text-green-600 dark:text-green-400" />
        </div>
        <CardTitle className="text-xl">Render Complete!</CardTitle>
        <CardDescription>
          &ldquo;{songsetName}&rdquo; is ready for playback
        </CardDescription>
        {elapsedSeconds != null && elapsedSeconds > 0 && (
          <div className="mt-2 flex items-center justify-center gap-1.5 text-sm text-muted-foreground">
            <Timer className="size-3.5" />
            <span>Total time: {formatDuration(elapsedSeconds)}</span>
          </div>
        )}
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Download Options */}
        <div className="space-y-3">
          <h3 className="text-sm font-medium text-muted-foreground">
            Download Files
          </h3>

          {hasAudio && (
            <Button
              variant="outline"
              className="w-full justify-start gap-3"
              onClick={() => handleDownloadFile("audio", "mp3")}
            >
              <Music className="size-4" />
              <span className="flex-1 text-left">Download Audio (MP3)</span>
            </Button>
          )}

          {hasVideo && (
            <Button
              variant="outline"
              className="w-full justify-start gap-3"
              onClick={() => handleDownloadFile("video", "mp4")}
            >
              <Video className="size-4" />
              <span className="flex-1 text-left">Download Video (MP4)</span>
            </Button>
          )}

          {hasChapters && (
            <Button
              variant="outline"
              className="w-full justify-start gap-3"
              onClick={() => handleDownloadFile("json", "json")}
            >
              <FileJson className="size-4" />
              <span className="flex-1 text-left">Download Chapters (JSON)</span>
            </Button>
          )}
        </div>

        <Separator />

        {/* Actions */}
        <div className="space-y-3">
          <Button
            className="w-full gap-2"
            onClick={handleShare}
          >
            <Share2 className="size-4" />
            Share Songset
          </Button>

          <Button
            variant="ghost"
            className="w-full"
            onClick={onDone}
          >
            Done
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
