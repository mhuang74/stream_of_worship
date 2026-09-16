"use client"

import { useEffect, useRef } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Clock } from "lucide-react"
import { useLocale } from "@/hooks/useLocale"

/** How often the submitted screen re-reads its render job. */
export const RENDER_JOB_POLL_INTERVAL_MS = 10_000

/** The slice of GET /api/render-jobs/[id] the render screen consumes. */
export interface RenderCompletionJob {
  id: string
  status: "queued" | "running" | "completed" | "failed" | "cancelled"
  mp3R2Key: string | null
  mp4R2Key: string | null
}

interface RenderSubmittedProps {
  estimatedMinutes: number
  jobId: string
  onComplete: (job: RenderCompletionJob) => void
  onFailed?: () => void
  onCancel: () => void
  isCancelling?: boolean
  submittedAt?: string
}

/**
 * Submitted-state screen: polls the job (the render worker writes the database
 * directly, so completion is only observable by asking) and reports the
 * terminal status to the page, which owns the toasts and the auto-cache.
 */
export function RenderSubmitted({
  estimatedMinutes,
  jobId,
  onComplete,
  onFailed,
  onCancel,
  isCancelling = false,
  submittedAt,
}: RenderSubmittedProps) {
  const { t } = useLocale()

  // Latest-callback refs: the poll is keyed on jobId alone, so a parent
  // re-render (toasts, auto-cache setting) never restarts the interval.
  const onCompleteRef = useRef(onComplete)
  const onFailedRef = useRef(onFailed)
  useEffect(() => {
    onCompleteRef.current = onComplete
    onFailedRef.current = onFailed
  })

  useEffect(() => {
    let timer: NodeJS.Timeout | undefined
    let disposed = false

    const stopPolling = () => {
      if (timer !== undefined) {
        clearInterval(timer)
        timer = undefined
      }
    }

    const poll = async () => {
      try {
        const response = await fetch(`/api/render-jobs/${jobId}`)

        // Transient (offline venue, dropped request): the next tick retries.
        // Permanent failures are terminal, or the screen would poll a job it
        // can never read forever.
        if (response.status === 401 || response.status === 403) {
          stopPolling()
          return
        }
        if (response.status === 404 || response.status === 410) {
          stopPolling()
          onFailedRef.current?.()
          return
        }
        if (!response.ok) return

        const job = (await response.json()) as RenderCompletionJob
        if (disposed) return

        if (job.status === "completed") {
          stopPolling()
          onCompleteRef.current(job)
        } else if (job.status === "failed" || job.status === "cancelled") {
          stopPolling()
          onFailedRef.current?.()
        }
      } catch {
        // Keep polling — a single failed request says nothing about the job.
      }
    }

    timer = setInterval(poll, RENDER_JOB_POLL_INTERVAL_MS)

    return () => {
      disposed = true
      stopPolling()
    }
  }, [jobId])

  return (
    <Card className="w-full">
      <CardHeader>
        <CardTitle>{t("render.submitted.title")}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-2 text-muted-foreground">
          <Clock className="size-4" />
          <span>
            {t("render.submitted.estimatedTime")}: {t("render.submitted.estimatedPrefix")}
            {estimatedMinutes} {t("render.submitted.estimatedMinutes")}
          </span>
        </div>
        <p className="text-sm text-muted-foreground">
          {t("render.submitted.leavePage")}
        </p>
        {submittedAt && (
          <p className="text-sm text-muted-foreground">
            {t("render.submitted.submittedAt")}{" "}
            {new Intl.DateTimeFormat(undefined, {
              dateStyle: "medium",
              timeStyle: "short",
            }).format(new Date(submittedAt))}
          </p>
        )}
        <Button
          variant="outline"
          className="w-full"
          onClick={onCancel}
          disabled={isCancelling}
        >
          {t("render.submitted.cancel")}
        </Button>
      </CardContent>
    </Card>
  )
}
