"use client"

import { useState, useCallback } from "react"
import { useRouter } from "next/navigation"
import dynamic from "next/dynamic"
import { Button } from "@/components/ui/button"
import { ArrowLeft } from "lucide-react"
import { toast } from "sonner"
import { Skeleton } from "@/components/ui/skeleton"
import { FontPreviewStylesheets } from "@/components/fonts/FontPreviewStylesheets"
import type { RenderFormData } from "@/components/render/RenderForm"
import { useLocale } from "@/hooks/useLocale"
import { useSongsetListBack } from "@/hooks/useSongsetListBack"

const RenderForm = dynamic(() => import("@/components/render/RenderForm").then((m) => ({ default: m.RenderForm })), {
  loading: () => <div className="space-y-4"><Skeleton className="h-48 w-full" /><Skeleton className="h-12 w-40" /></div>,
})

type RenderState = "unrendered" | "rendering" | "fresh" | "stale" | "failed"

interface SongsetData {
  id: string
  name: string
  description: string | null
  markedLineCount: number
  renderState: RenderState
  songTitles: string[]
  lastCompletedRenderJobId: string | null
  durationSeconds: number | null
}

interface RenderJobData {
  id: string
  status: string
  createdAt: string
  elapsedSeconds?: number | null
  estimatedTotalSeconds?: number | null
  template: string
  fontFamily: string
  fontSizePreset: string
  includeTitleCard: boolean
  titleCardDurationSeconds?: number | null
  resolution?: string
  totalDurationSeconds?: number | null
  songCount?: number | null
  songsetDurationSeconds?: number | null
  mp3R2Key: string | null
  mp4R2Key: string | null
  chaptersR2Key: string | null
}

interface RenderPageClientProps {
  songsetId: string
  initialSongset: SongsetData
  initialPreviousCompletedJob: RenderJobData | null
  initialRenderData: Partial<RenderFormData>
  currentSongCount?: number
  currentSongsetDurationSeconds?: number | null
}

/**
 * Render form host. Submitting hands the job to the editor page, which owns the
 * in-flight state (status line, cancel, completion toast); a job that is already
 * running never reaches this component — the page redirects it away.
 */
export function RenderPageClient({
  songsetId,
  initialSongset,
  initialPreviousCompletedJob,
  initialRenderData,
  currentSongCount,
  currentSongsetDurationSeconds,
}: RenderPageClientProps) {
  const router = useRouter()
  const backToList = useSongsetListBack()
  const { t } = useLocale()

  const [songset] = useState<SongsetData | null>(initialSongset)
  const [previousCompletedJob] = useState<RenderJobData | null>(initialPreviousCompletedJob)
  const [initialData] = useState<Partial<RenderFormData> | undefined>(initialRenderData)
  const isLoading = false
  const error: string | null = null
  const [isSubmitting, setIsSubmitting] = useState(false)

  // Both success and an already-in-progress 409 land on the editor: that is
  // where the job is tracked and cancelled.
  const handleSubmit = useCallback(
    async (formData: RenderFormData) => {
      setIsSubmitting(true)
      try {
        const response = await fetch("/api/render-jobs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            songsetId,
            template: formData.template,
            resolution: formData.resolution,
            audioEnabled: formData.audioEnabled,
            videoEnabled: formData.videoEnabled,
            fontSizePreset: formData.fontSizePreset,
            fontFamily: formData.fontFamily,
            includeTitleCard: formData.includeTitleCard,
            titleCardDurationSeconds: formData.titleCardDurationSeconds,
            titleCardLines: formData.titleCardLines.length > 0 ? formData.titleCardLines : undefined,
          }),
        })

        if (!response.ok) {
          if (response.status === 401) {
            router.push("/login")
            return
          }
          if (response.status === 409) {
            const data = await response.json()
            if (data.jobId) {
              const configSummary = []
              if (data.config?.audioEnabled) configSummary.push(t("render.toast.config.audio"))
              if (data.config?.videoEnabled) configSummary.push(t("render.toast.config.video"))
              toast.info(`${t("render.toast.alreadyInProgress")} (${configSummary.join(" + ")})`)
              router.replace(`/songsets/${songsetId}`)
            } else if (
              data.code === "ARRANGEMENT_ANALYSIS_UNAVAILABLE" ||
              data.code === "ARRANGEMENT_STALE" ||
              data.code === "ARRANGEMENT_INVALID"
            ) {
              // Saved arrangement no longer matches analysis: repair or reset
              // from the editor (translated in both locales).
              toast.error(t("render.error.arrangementRepairOrReset"))
            } else {
              toast.error(data.error || t("render.toast.alreadyInProgress"))
            }
            return
          }
          const errorData = await response.json()
          throw new Error(errorData.error || t("render.toast.failedToCreateJob"))
        }

        toast.success(t("render.toast.started"))
        // replace, not push: Back must not return to a form for a job that is
        // already away.
        router.replace(`/songsets/${songsetId}`)
      } catch (err) {
        toast.error(err instanceof Error ? err.message : t("render.toast.failedToStart"))
      } finally {
        setIsSubmitting(false)
      }
    },
    [songsetId, router, t]
  )

  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <div role="status" className="animate-spin rounded-full h-8 w-8 border-b-2 border-primary" />
      </div>
    )
  }

  if (error || !songset) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center p-4">
        <p className="text-center text-destructive">
          {error || t("render.songsetNotFound")}
        </p>
        <Button
          variant="ghost"
          className="mt-4"
          onClick={() => backToList()}
        >
          {t("render.backToSongsets")}
        </Button>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-background">
      <FontPreviewStylesheets />
      {/* Header */}
      <header className="sticky top-0 z-10 border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
        <div className="flex h-14 items-center gap-4 px-4">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => router.push(`/songsets/${songsetId}`)}
            aria-label={t("render.back.ariaLabel")}
          >
            <ArrowLeft className="size-5" />
          </Button>
          <div className="flex-1">
            <h1 className="font-semibold">{t("render.heading")}</h1>
            <p className="text-sm text-muted-foreground truncate">
              {songset.name}
            </p>
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="p-4 pb-24">
        <RenderForm
          songsetId={songsetId}
          markedLineCount={songset.markedLineCount}
          songsetName={songset.name}
          songTitles={songset.songTitles}
          initialData={initialData}
          onSubmit={handleSubmit}
          onCancel={() => router.push(`/songsets/${songsetId}`)}
          isSubmitting={isSubmitting}
          previousRenderJob={
            previousCompletedJob
              ? {
                  id: previousCompletedJob.id,
                  createdAt: previousCompletedJob.createdAt,
                  template: previousCompletedJob.template,
                  fontFamily: previousCompletedJob.fontFamily,
                  fontSizePreset: previousCompletedJob.fontSizePreset,
                  includeTitleCard: previousCompletedJob.includeTitleCard,
                  titleCardDurationSeconds:
                    previousCompletedJob.titleCardDurationSeconds ?? undefined,
                  resolution: previousCompletedJob.resolution,
                  totalDurationSeconds: previousCompletedJob.totalDurationSeconds,
                  songCount: previousCompletedJob.songCount,
                  songsetDurationSeconds: previousCompletedJob.songsetDurationSeconds,
                }
              : undefined
          }
          currentSongCount={currentSongCount ?? songset.songTitles.length}
          currentSongsetDurationSeconds={currentSongsetDurationSeconds ?? songset.durationSeconds}
        />
      </main>
    </div>
  )
}
