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

const RenderForm = dynamic(() => import("@/components/render/RenderForm").then((m) => ({ default: m.RenderForm })), {
  loading: () => <div className="space-y-4"><Skeleton className="h-48 w-full" /><Skeleton className="h-12 w-40" /></div>,
})
const RenderSubmitted = dynamic(() => import("@/components/render/RenderSubmitted").then((m) => ({ default: m.RenderSubmitted })), {
  loading: () => <Skeleton className="h-32 w-full" />,
})

type RenderScreenState = "form" | "submitted"

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
  initialLatestJob: RenderJobData | null
  initialPreviousCompletedJob: RenderJobData | null
  initialRenderData: Partial<RenderFormData>
  currentSongCount?: number
  currentSongsetDurationSeconds?: number | null
}

export function RenderPageClient({
  songsetId,
  initialSongset,
  initialLatestJob,
  initialPreviousCompletedJob,
  initialRenderData,
  currentSongCount,
  currentSongsetDurationSeconds,
}: RenderPageClientProps) {
  const router = useRouter()

  const hasActiveJob =
    initialLatestJob?.status === "running" || initialLatestJob?.status === "queued"
  const [screenState, setScreenState] = useState<RenderScreenState>(
    hasActiveJob ? "submitted" : "form"
  )
  const [songset] = useState<SongsetData | null>(initialSongset)
  const [jobId, setJobId] = useState<string | null>(initialLatestJob?.id ?? null)
  const [jobData] = useState<RenderJobData | null>(
    initialLatestJob?.status === "completed" ? initialLatestJob : null
  )
  const [previousCompletedJob] = useState<RenderJobData | null>(initialPreviousCompletedJob)
  const [initialData] = useState<Partial<RenderFormData> | undefined>(initialRenderData)
  const isLoading = false
  const error: string | null = null
  const [estimatedMinutes, setEstimatedMinutes] = useState(
    initialLatestJob?.estimatedTotalSeconds
      ? Math.ceil(initialLatestJob.estimatedTotalSeconds / 60)
      : 5
  )
  const [isCancelling, setIsCancelling] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)

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
              setJobId(data.jobId)
              if (data.estimatedTotalSeconds) {
                setEstimatedMinutes(Math.ceil(data.estimatedTotalSeconds / 60))
              }
              setScreenState("submitted")
              const configSummary = []
              if (data.config?.audioEnabled) configSummary.push("audio")
              if (data.config?.videoEnabled) configSummary.push("video")
              toast.info(`A render job is already in progress (${configSummary.join(" + ")})`)
            } else {
              toast.error(data.error || "A render job is already in progress")
            }
            return
          }
          const errorData = await response.json()
          throw new Error(errorData.error || "Failed to create render job")
        }

        const job = await response.json()
        setJobId(job.id)
        if (job.estimatedTotalSeconds) {
          setEstimatedMinutes(Math.ceil(job.estimatedTotalSeconds / 60))
        }
        setScreenState("submitted")
        toast.success("Render started")
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Failed to start render")
      } finally {
        setIsSubmitting(false)
      }
    },
    [songsetId, router]
  )

  const handleCancel = useCallback(async () => {
    if (!jobId || isCancelling) return
    setIsCancelling(true)
    try {
      const response = await fetch(`/api/render-jobs/${jobId}`, { method: "DELETE" })
      if (!response.ok) {
        throw new Error("Failed to cancel render job")
      }
      setScreenState("form")
      setJobId(null)
      toast.info("Render cancelled")
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to cancel")
    } finally {
      setIsCancelling(false)
    }
  }, [jobId, isCancelling])

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
          {error || "Songset not found"}
        </p>
        <Button
          variant="ghost"
          className="mt-4"
          onClick={() => router.push("/songsets")}
        >
          Back to songsets
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
            aria-label="Go back"
          >
            <ArrowLeft className="size-5" />
          </Button>
          <div className="flex-1">
            <h1 className="font-semibold">Render</h1>
            <p className="text-sm text-muted-foreground truncate">
              {songset.name}
            </p>
          </div>
        </div>
      </header>

      {/* Main content */}
      <main className="p-4 pb-24">
        {screenState === "form" && (
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
        )}

        {screenState === "submitted" && jobId && (
          <RenderSubmitted
            estimatedMinutes={estimatedMinutes}
            onCancel={handleCancel}
            isCancelling={isCancelling}
            submittedAt={jobData?.createdAt}
          />
        )}
      </main>
    </div>
  )
}
