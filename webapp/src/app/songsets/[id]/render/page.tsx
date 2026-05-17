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
const RenderProgress = dynamic(() => import("@/components/render/RenderProgress").then((m) => ({ default: m.RenderProgress })), {
  loading: () => <Skeleton className="h-32 w-full" />,
})
const RenderComplete = dynamic(() => import("@/components/render/RenderComplete").then((m) => ({ default: m.RenderComplete })), {
  loading: () => <Skeleton className="h-32 w-full" />,
})

type RenderScreenState = "form" | "progress" | "complete"

interface SongsetData {
  id: string
  name: string
  description: string | null
  markedLineCount: number
}

interface RenderJobData {
  id: string
  status: string
  mp3R2Key: string | null
  mp4R2Key: string | null
  chaptersR2Key: string | null
  mp3Url?: string
  mp4Url?: string
  chaptersUrl?: string
}

export default function RenderPage() {
  const params = useParams()
  const router = useRouter()
  const songsetId = params.id as string

  const [screenState, setScreenState] = useState<RenderScreenState>("form")
  const [songset, setSongset] = useState<SongsetData | null>(null)
  const [jobId, setJobId] = useState<string | null>(null)
  const [jobData, setJobData] = useState<RenderJobData | null>(null)
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

        setSongset({
          id: data.id,
          name: data.name,
          description: data.description,
          markedLineCount: data.items?.reduce(
            (sum: number, item: { markedLineCount?: number }) =>
              sum + (item.markedLineCount || 0),
            0
          ) || 0,
        })

        // Check if there's an active render job
        if (data.latestRenderJobId) {
          const jobResponse = await fetch(`/api/render-jobs/${data.latestRenderJobId}`)
          if (jobResponse.ok) {
            const job = await jobResponse.json()
            if (job.status === "running" || job.status === "queued") {
              setJobId(job.id)
              setScreenState("progress")
            } else if (job.status === "completed") {
              setJobId(job.id)
              const signedUrls: { mp3Url?: string; mp4Url?: string; chaptersUrl?: string } = {}
              const fetchSigned = async (key: string, type: string) => {
                const res = await fetch(`/api/signed-url?key=${encodeURIComponent(key)}&fileType=${type}`)
                return res.ok ? (await res.json()).url as string : undefined
              }
              if (job.mp3R2Key) signedUrls.mp3Url = await fetchSigned(job.mp3R2Key, "audio")
              if (job.mp4R2Key) signedUrls.mp4Url = await fetchSigned(job.mp4R2Key, "video")
              if (job.chaptersR2Key) signedUrls.chaptersUrl = await fetchSigned(job.chaptersR2Key, "json")
              setJobData({ ...job, ...signedUrls })
              setScreenState("complete")
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
        setScreenState("progress")
        toast.success("Render started")
      } catch (err) {
        toast.error(err instanceof Error ? err.message : "Failed to start render")
      }
    },
    [songsetId, router]
  )

  const handleCancel = useCallback(() => {
    setScreenState("form")
    setJobId(null)
    toast.info("Render cancelled")
  }, [])

  const handleComplete = useCallback(async () => {
    if (jobId) {
      try {
        const response = await fetch(`/api/render-jobs/${jobId}`)
        if (response.ok) {
          const job = await response.json()
          const signedUrls: { mp3Url?: string; mp4Url?: string; chaptersUrl?: string } = {}

          const fetchSignedUrl = async (r2Key: string, fileType: string) => {
            const res = await fetch(`/api/signed-url?key=${encodeURIComponent(r2Key)}&fileType=${fileType}`)
            if (res.ok) {
              const data = await res.json()
              return data.url as string
            }
            return undefined
          }

          if (job.mp3R2Key) signedUrls.mp3Url = await fetchSignedUrl(job.mp3R2Key, "audio")
          if (job.mp4R2Key) signedUrls.mp4Url = await fetchSignedUrl(job.mp4R2Key, "video")
          if (job.chaptersR2Key) signedUrls.chaptersUrl = await fetchSignedUrl(job.chaptersR2Key, "json")

          setJobData({ ...job, ...signedUrls })
        }
      } catch (err) {
        console.error("Failed to fetch job data:", err)
      }
    }
    setScreenState("complete")
    toast.success("Render complete!")
  }, [jobId])

  const handleError = useCallback((errorMessage: string) => {
    setError(errorMessage)
    setScreenState("form")
    toast.error(errorMessage)
  }, [])

  const handleDone = useCallback(() => {
    router.push(`/songsets/${songsetId}`)
  }, [router, songsetId])

  const handleShare = useCallback(() => {
    router.push(`/songsets/${songsetId}?share=true`)
  }, [router, songsetId])

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
            onSubmit={handleSubmit}
            onCancel={() => router.push(`/songsets/${songsetId}`)}
          />
        )}

        {screenState === "progress" && jobId && (
          <RenderProgress
            jobId={jobId}
            onComplete={handleComplete}
            onCancel={handleCancel}
            onError={handleError}
          />
        )}

        {screenState === "complete" && jobData && (
          <RenderComplete
            jobId={jobId!}
            songsetId={songsetId}
            songsetName={songset.name}
            mp3Url={jobData.mp3Url}
            mp4Url={jobData.mp4Url}
            chaptersUrl={jobData.chaptersUrl}
            onDone={handleDone}
            onShare={handleShare}
          />
        )}
      </main>
    </div>
  )
}
