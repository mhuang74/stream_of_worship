"use client"

import { useCallback } from "react"
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
import { sanitizeFilename, fetchSignedUrlAndDownload } from "@/lib/download"
import { formatDuration } from "@/lib/format"
import { useLocale } from "@/hooks/useLocale"

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
  const { t } = useLocale()
  const handleDownloadFile = useCallback(async (
    fileType: "audio" | "video" | "json",
    extension: string,
  ) => {
    const toastId = toast.loading(t("render.download.preparing"));
    try {
      await fetchSignedUrlAndDownload(jobId, fileType, sanitizeFilename(songsetName), extension);
      toast.success(t("render.download.started"), { id: toastId });
    } catch (err) {
      toast.error(err instanceof Error ? err.message : t("render.download.failed"), { id: toastId });
    }
  }, [jobId, songsetName, t]);

  const handleShare = async () => {
    if (navigator.share) {
      try {
        await navigator.share({
          title: songsetName,
          text: `${t("render.complete.shareText")}${songsetName}${t("render.complete.shareTextSuffix")}`,
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
        <CardTitle className="text-xl">{t("render.complete.title")}</CardTitle>
        <CardDescription>
          &ldquo;{songsetName}&rdquo; {t("render.complete.description")}
        </CardDescription>
        {elapsedSeconds != null && elapsedSeconds > 0 && (
          <div className="mt-2 flex items-center justify-center gap-1.5 text-sm text-muted-foreground">
            <Timer className="size-3.5" />
            <span>{t("render.complete.totalTime")} {formatDuration(elapsedSeconds)}</span>
          </div>
        )}
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Download Options */}
        <div className="space-y-3">
          <h3 className="text-sm font-medium text-muted-foreground">
            {t("render.complete.downloadFiles")}
          </h3>

          {hasAudio && (
            <Button
              variant="outline"
              className="w-full justify-start gap-3"
              onClick={() => handleDownloadFile("audio", "mp3")}
            >
              <Music className="size-4" />
              <span className="flex-1 text-left">{t("render.complete.downloadAudio")}</span>
            </Button>
          )}

          {hasVideo && (
            <Button
              variant="outline"
              className="w-full justify-start gap-3"
              onClick={() => handleDownloadFile("video", "mp4")}
            >
              <Video className="size-4" />
              <span className="flex-1 text-left">{t("render.complete.downloadVideo")}</span>
            </Button>
          )}

          {hasChapters && (
            <Button
              variant="outline"
              className="w-full justify-start gap-3"
              onClick={() => handleDownloadFile("json", "json")}
            >
              <FileJson className="size-4" />
              <span className="flex-1 text-left">{t("render.complete.downloadChapters")}</span>
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
            {t("render.complete.share")}
          </Button>

          <Button
            variant="ghost"
            className="w-full"
            onClick={onDone}
          >
            {t("render.complete.done")}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
