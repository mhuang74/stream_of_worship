"use client"

import { useState, useEffect, useCallback } from "react"
import { useParams, useRouter } from "next/navigation"
import dynamic from "next/dynamic"
import { Button } from "@/components/ui/button"
import { ArrowLeft } from "lucide-react"
import { toast } from "sonner"
import { Skeleton } from "@/components/ui/skeleton"
import { FontPreviewStylesheets } from "@/components/fonts/FontPreviewStylesheets"
import { buildInitialRenderData, type UserSettingsData } from "@/lib/render/render-defaults"
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
}

interface RenderJobData {
  id: string
  status: string
  createdAt: string
  elapsedSeconds?: number
  template: string
  fontFamily: string
  fontSizePreset: string
  includeTitleCard: boolean
  titleCardDurationSeconds?: number
  mp3R2Key: string | null
  mp4R2Key: string | null
  chaptersR2Key: string | null
}

export default function RenderPage() {
  const params = useParams()
  const router = useRouter()
  const songsetId = params.id as string

  const [screenState, setScreenState] = useState<RenderScreenState>("form")
  const [songset, setSongset] = useState<SongsetData | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [jobData, setJobData] = useState<RenderJobData | null>(null)
  const [previousCompletedJob, setPreviousCompletedJob] = useState<RenderJobData | null>(null)
  const [initialData, setInitialData] = useState<Partial<RenderFormData> | undefined>(undefined)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [estimatedMinutes, setEstimatedMinutes] = useState(5)
  const [isCancelling, setIsCancelling] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)

  // Load songset data
  useEffect(() => {
    let cancelled = false

    async function loadSongset() {
      try {
        setIsLoading(true)
        setError(null)

        const [songsetResponse, settingsResponse] = await Promise.all([
          fetch(`/api/songsets/${songsetId}`),
          fetch(`/api/settings`),
        ])

        if (!songsetResponse.ok) {
          if (songsetResponse.status === 401) {
            router.push("/login")
            return
          }
          if (songsetResponse.status === 404) {
            throw new Error("Songset not found")
          }
          throw new Error("Failed to load songset")
        }

        const data = await songsetResponse.json()

        let userSettings: UserSettingsData | null = null
        if (settingsResponse.ok) {
          const settingsData = await settingsResponse.json()
          userSettings = settingsData.settings ?? null
        }

        if (cancelled) return

        const renderState = data.renderState as RenderState

        setSongset({
          id: data.id,
          name: data.name,
          description: data.description,
          markedLineCount: data.items?.reduce(
            (sum: number, item: { markedLineCount?: number }) =>
              sum + (item.markedLineCount || 0),
            0
          ) || 0,
          renderState,
          songTitles: data.items?.map((item: { song?: { title: string } | null }) =>
            item.song?.title ?? "Unknown Song"
          ) ?? [],
          lastCompletedRenderJobId: data.lastCompletedRenderJobId ?? null,
        })

        // Check if there's an active render job
        let latestJob: Record<string, unknown> | null = null
        if (data.latestRenderJobId) {
          const jobResponse = await fetch(`/api/render-jobs/${data.latestRenderJobId}`)
          if (jobResponse.ok) {
            const job = await jobResponse.json()
            latestJob = job

            if (job.status === "running" || job.status === "queued") {
              setJobId(job.id)
              if (job.estimatedTotalSeconds) {
                setEstimatedMinutes(Math.ceil(job.estimatedTotalSeconds / 60))
              }
              setScreenState("submitted")
            } else if (job.status === "completed") {
              setJobId(job.id)
              setJobData(job)
            }
          }
        }

        // Fetch previous completed job for the info banner
        if (data.lastCompletedRenderJobId) {
          const controller = new AbortController()
          const timeoutId = setTimeout(() => controller.abort(), 30000)
          try {
            const completedJobResponse = await fetch(
              `/api/render-jobs/${data.lastCompletedRenderJobId}`,
              { signal: controller.signal }
            )
            if (completedJobResponse.ok) {
              const completedJob = await completedJobResponse.json()
              setPreviousCompletedJob(completedJob)
            }
          } catch (err) {
            console.error("Failed to fetch previous completed job:", err)
          } finally {
            clearTimeout(timeoutId)
          }
        }

        if (cancelled) return

        const initial = buildInitialRenderData(latestJob, userSettings)
        setInitialData(initial)
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load songset")
        }
      } finally {
        if (!cancelled) {
          setIsLoading(false)
        }
      }
    }

    if (songsetId) {
      loadSongset()
    }

    return () => {
      cancelled = true
    }
  }, [songsetId, router])

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
                    titleCardDurationSeconds: previousCompletedJob.titleCardDurationSeconds,
                  }
                : undefined
            }
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
