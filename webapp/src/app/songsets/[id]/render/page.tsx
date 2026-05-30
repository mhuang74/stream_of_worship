"use client"

import { useState, useEffect, useCallback } from "react"
import { useParams, useRouter } from "next/navigation"
import dynamic from "next/dynamic"
import { Button } from "@/components/ui/button"
import { ArrowLeft } from "lucide-react"
import { toast } from "sonner"
import { Skeleton } from "@/components/ui/skeleton"
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
}

export default function RenderPage() {
  const params = useParams()
  const router = useRouter()
  const songsetId = params.id as string

  const [screenState, setScreenState] = useState<RenderScreenState>("form")
  const [songset, setSongset] = useState<SongsetData | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [estimatedMinutes, setEstimatedMinutes] = useState(0)
  const [isCancelling, setIsCancelling] = useState(false)
  const [initialData, setInitialData] = useState<Partial<RenderFormData> | undefined>(undefined)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Load songset data
  useEffect(() => {
    let cancelled = false

    async function loadSongset() {
      try {
        setIsLoading(true)
        setError(null)

        const response = await fetch(`/api/songsets/${songsetId}`)

        if (!response.ok) {
          if (response.status === 401) {
            router.push("/login")
            return
          }
          if (response.status === 404) {
            throw new Error("Songset not found")
          }
          throw new Error("Failed to load songset")
        }

        const data = await response.json()

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
        })

        // Check if there's an active render job
        if (data.latestRenderJobId) {
          const jobResponse = await fetch(`/api/render-jobs/${data.latestRenderJobId}`)
          if (jobResponse.ok) {
            const job = await jobResponse.json()
            if (job.status === "running" || job.status === "queued") {
              setJobId(job.id)
              const est = job.estimatedTotalSeconds ? Math.ceil(job.estimatedTotalSeconds / 60) : 5
              setEstimatedMinutes(est)
              setScreenState("submitted")
            } else if (job.status === "completed") {
              setInitialData({
                template: job.template as RenderFormData["template"],
                resolution: job.resolution as RenderFormData["resolution"],
                audioEnabled: job.audioEnabled,
                videoEnabled: job.videoEnabled,
                fontSizePreset: job.fontSizePreset as RenderFormData["fontSizePreset"],
                includeTitleCard: job.includeTitleCard,
                titleCardDurationSeconds: job.titleCardDurationSeconds,
                titleCardLines: job.titleCardLines ?? [],
              })
            }
          }
        }
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
          const errorData = await response.json()
          throw new Error(errorData.error || "Failed to create render job")
        }

        const job = await response.json()
        setJobId(job.id)
        const est = job.estimatedTotalSeconds ? Math.ceil(job.estimatedTotalSeconds / 60) : 5
        setEstimatedMinutes(est)
        setScreenState("submitted")
        toast.success("Render started")
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Failed to start render")
      }
    },
    [songsetId, router]
  )

  const handleCancel = useCallback(async () => {
    if (!jobId) {
      router.push(`/songsets/${songsetId}`)
      return
    }

    setIsCancelling(true)
    try {
      await fetch(`/api/render-jobs/${jobId}`, { method: "DELETE" })
    } catch {
      // silent fallback
    }
    router.push(`/songsets/${songsetId}`)
  }, [jobId, router, songsetId])

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
          />
        )}

        {screenState === "submitted" && jobId && (
          <RenderSubmitted
            estimatedMinutes={estimatedMinutes}
            onCancel={handleCancel}
            isCancelling={isCancelling}
          />
        )}
      </main>
    </div>
  )
}
