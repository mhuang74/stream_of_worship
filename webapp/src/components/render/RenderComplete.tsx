"use client"

import { useState } from "react"
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import {
  CheckCircle2,
  Download,
  Share2,
  Music,
  Video,
  FileJson,
  Loader2,
} from "lucide-react"
import { toast } from "sonner"

interface RenderCompleteProps {
  jobId: string
  songsetId: string
  songsetName: string
  mp3Url?: string
  mp4Url?: string
  chaptersUrl?: string
  onDone: () => void
  onShare: () => void
}

export function RenderComplete({
  songsetId,
  songsetName,
  mp3Url,
  mp4Url,
  chaptersUrl,
  onDone,
  onShare,
}: RenderCompleteProps) {
  const [isDownloadingAudio, setIsDownloadingAudio] = useState(false)
  const [isDownloadingVideo, setIsDownloadingVideo] = useState(false)
  const [isDownloadingChapters, setIsDownloadingChapters] = useState(false)

  const handleDownload = async (
    url: string | undefined,
    filename: string,
    setLoading: (loading: boolean) => void
  ) => {
    if (!url) {
      toast.error("File not available")
      return
    }

    setLoading(true)
    try {
      const response = await fetch(url)
      if (!response.ok) {
        throw new Error("Failed to download file")
      }

      const blob = await response.blob()
      const downloadUrl = window.URL.createObjectURL(blob)
      const link = document.createElement("a")
      link.href = downloadUrl
      link.download = filename
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      window.URL.revokeObjectURL(downloadUrl)

      toast.success(`Downloaded ${filename}`)
    } catch (error) {
      toast.error("Download failed")
      console.error("Download error:", error)
    } finally {
      setLoading(false)
    }
  }

  const handleShare = async () => {
    // Check if Web Share API is available
    if (navigator.share && (mp3Url || mp4Url)) {
      try {
        await navigator.share({
          title: songsetName,
          text: `Check out "${songsetName}" on Stream of Worship`,
          url: `${window.location.origin}/songsets/${songsetId}`,
        })
      } catch (error) {
        // User cancelled or share failed
        if ((error as Error).name !== "AbortError") {
          console.error("Share failed:", error)
          onShare()
        }
      }
    } else {
      onShare()
    }
  }

  const hasAudio = !!mp3Url
  const hasVideo = !!mp4Url
  const hasChapters = !!chaptersUrl

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
              onClick={() =>
                handleDownload(
                  mp3Url,
                  `${songsetName.replace(/\s+/g, "_")}.mp3`,
                  setIsDownloadingAudio
                )
              }
              disabled={isDownloadingAudio}
            >
              {isDownloadingAudio ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Music className="size-4" />
              )}
              <span className="flex-1 text-left">Download Audio (MP3)</span>
              <Download className="size-4" />
            </Button>
          )}

          {hasVideo && (
            <Button
              variant="outline"
              className="w-full justify-start gap-3"
              onClick={() =>
                handleDownload(
                  mp4Url,
                  `${songsetName.replace(/\s+/g, "_")}.mp4`,
                  setIsDownloadingVideo
                )
              }
              disabled={isDownloadingVideo}
            >
              {isDownloadingVideo ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <Video className="size-4" />
              )}
              <span className="flex-1 text-left">Download Video (MP4)</span>
              <Download className="size-4" />
            </Button>
          )}

          {hasChapters && (
            <Button
              variant="outline"
              className="w-full justify-start gap-3"
              onClick={() =>
                handleDownload(
                  chaptersUrl,
                  `${songsetName.replace(/\s+/g, "_")}_chapters.json`,
                  setIsDownloadingChapters
                )
              }
              disabled={isDownloadingChapters}
            >
              {isDownloadingChapters ? (
                <Loader2 className="size-4 animate-spin" />
              ) : (
                <FileJson className="size-4" />
              )}
              <span className="flex-1 text-left">Download Chapters (JSON)</span>
              <Download className="size-4" />
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
