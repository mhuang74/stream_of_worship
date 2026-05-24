"use client"

import { useEffect, useState, useCallback, useRef } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Loader2, X, AlertCircle } from "lucide-react"

const POLL_INTERVAL_MS = 2000
const SSE_FALLBACK_POLL_INTERVAL_MS = 5000
const MAX_POLL_INTERVAL_MS = 30000
const STALE_PROGRESS_THRESHOLD_MINUTES = 10

export type RenderPhase =
  | "preparing"
  | "mixing_audio"
  | "rendering_frames"
  | "encoding_video"
  | "uploading"
  | "completed"

export type RenderStatus = "queued" | "running" | "completed" | "failed" | "cancelled"

export interface RenderProgressData {
  phase: RenderPhase
  phaseIndex: number
  totalPhases: number
  estimatedTotalSeconds: number
  elapsedSeconds: number
  status: RenderStatus
  errorMessage?: string
}

interface RenderProgressProps {
  jobId: string
  onComplete: () => void
  onCancel: () => void
  onError: (error: string) => void
}

const PHASE_LABELS: Record<RenderPhase, string> = {
  preparing: "Preparing...",
  mixing_audio: "Mixing audio...",
  rendering_frames: "Rendering frames...",
  encoding_video: "Encoding video...",
  uploading: "Uploading...",
  completed: "Complete",
}

function formatDuration(seconds: number): string {
  if (seconds < 60) {
    return `${Math.round(seconds)}s`
  }
  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = Math.round(seconds % 60)
  return `${minutes}m ${remainingSeconds}s`
}

function checkStaleProgress(
  data: RenderProgressData,
  lastElapsedRef: React.MutableRefObject<number | null>,
  lastChangeTimeRef: React.MutableRefObject<number | null>,
  setStaleWarning: (msg: string | null) => void,
) {
  if (data.elapsedSeconds !== lastElapsedRef.current) {
    lastElapsedRef.current = data.elapsedSeconds
    lastChangeTimeRef.current = Date.now()
    setStaleWarning(null)
  } else if (lastChangeTimeRef.current !== null) {
    const minutesSinceChange = (Date.now() - lastChangeTimeRef.current) / 60000
    if (minutesSinceChange > STALE_PROGRESS_THRESHOLD_MINUTES) {
      setStaleWarning(
        `Progress hasn't updated in ${Math.round(minutesSinceChange)} minutes. ` +
        `The render may be stuck. You can cancel and try again.`
      )
    }
  }
}

export function RenderProgress({
  jobId,
  onComplete,
  onCancel,
  onError,
}: RenderProgressProps) {
  const [progress, setProgress] = useState<RenderProgressData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isCancelling, setIsCancelling] = useState(false)
  const [staleWarning, setStaleWarning] = useState<string | null>(null)
  const eventSourceRef = useRef<EventSource | null>(null)
  const lastElapsedRef = useRef<number | null>(null)
  const lastChangeTimeRef = useRef<number | null>(null)

  const onCompleteRef = useRef(onComplete)
  const onErrorRef = useRef(onError)
  const onCancelRef = useRef(onCancel)

  useEffect(() => {
    onCompleteRef.current = onComplete
    onErrorRef.current = onError
    onCancelRef.current = onCancel
  })

  const handleCancel = useCallback(async () => {
    if (isCancelling) return

    setIsCancelling(true)
    try {
      if (eventSourceRef.current) {
        eventSourceRef.current.close()
        eventSourceRef.current = null
      }

      const response = await fetch(`/api/render-jobs/${jobId}`, {
        method: "DELETE",
      })

      if (!response.ok) {
        throw new Error("Failed to cancel render job")
      }

      onCancelRef.current()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to cancel")
      setIsCancelling(false)
    }
  }, [jobId, isCancelling])

  useEffect(() => {
    let pollTimeout: ReturnType<typeof setTimeout>
    let consecutiveFailures = 0
    const abortController = new AbortController()
    let isSseActive = false

    const getPollInterval = () =>
      Math.min(POLL_INTERVAL_MS * Math.pow(2, consecutiveFailures), MAX_POLL_INTERVAL_MS)

    const poll = async () => {
      try {
        const response = await fetch(`/api/render-jobs/${jobId}`, {
          signal: abortController.signal,
        })
        if (!response.ok) throw new Error("Failed to fetch job status")
        const data = await response.json()

        consecutiveFailures = 0

        const progressData: RenderProgressData = {
          phase: data.phase ?? "preparing",
          phaseIndex: data.phaseIndex ?? 0,
          totalPhases: data.totalPhases ?? 5,
          estimatedTotalSeconds: data.estimatedTotalSeconds ?? 0,
          elapsedSeconds: data.elapsedSeconds ?? 0,
          status: data.status,
          errorMessage: data.errorMessage,
        }
        setProgress(progressData)

        checkStaleProgress(progressData, lastElapsedRef, lastChangeTimeRef, setStaleWarning)

        if (data.status === "completed") {
          onCompleteRef.current()
          return
        } else if (data.status === "failed") {
          const errMsg = data.errorMessage || "Render failed"
          setError(errMsg)
          onErrorRef.current(errMsg)
          return
        } else if (data.status === "cancelled") {
          onCancelRef.current()
          return
        }

        const delay = isSseActive ? SSE_FALLBACK_POLL_INTERVAL_MS : POLL_INTERVAL_MS
        pollTimeout = setTimeout(poll, delay)
      } catch (err) {
        if (abortController.signal.aborted) return
        consecutiveFailures++
        console.warn("Poll failed:", err instanceof Error ? err.message : err)
        pollTimeout = setTimeout(poll, getPollInterval())
      }
    }

    poll()

    const trySSE = () => {
      const eventSource = new EventSource(`/api/render-jobs/${jobId}/events`)
      eventSourceRef.current = eventSource

      eventSource.onopen = () => {
        isSseActive = true
      }

      eventSource.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as RenderProgressData
          setProgress(data)

          checkStaleProgress(data, lastElapsedRef, lastChangeTimeRef, setStaleWarning)

          if (data.status === "completed") {
            eventSource.close()
            eventSourceRef.current = null
            onCompleteRef.current()
          } else if (data.status === "failed") {
            eventSource.close()
            eventSourceRef.current = null
            const errMsg = data.errorMessage || "Render failed"
            setError(errMsg)
            onErrorRef.current(errMsg)
          } else if (data.status === "cancelled") {
            eventSource.close()
            eventSourceRef.current = null
            onCancelRef.current()
          }
        } catch (err) {
          console.warn("Failed to parse SSE data:", err)
        }
      }

      eventSource.onerror = () => {
        console.warn(
          `SSE connection dropped (readyState=${eventSource.readyState}). Falling back to polling.`
        )
        eventSource.close()
        eventSourceRef.current = null
        isSseActive = false
      }
    }

    trySSE()

    return () => {
      abortController.abort()
      clearTimeout(pollTimeout)
      if (eventSourceRef.current) {
        eventSourceRef.current.close()
        eventSourceRef.current = null
      }
    }
  }, [jobId])

  const currentPhase = progress?.phase ?? "preparing"
  const currentPhaseIndex = progress?.phaseIndex ?? 0
  const totalPhases = progress?.totalPhases ?? 5
  const estimatedTotalSeconds = progress?.estimatedTotalSeconds ?? 0
  const elapsedSeconds = progress?.elapsedSeconds ?? 0
  const status = progress?.status ?? "queued"

  let percentComplete: number
  let displayEstimatedTotal: number

  if (estimatedTotalSeconds <= 0) {
    percentComplete = 0
    displayEstimatedTotal = 0
  } else if (elapsedSeconds > estimatedTotalSeconds) {
    displayEstimatedTotal = elapsedSeconds * 1.1
    percentComplete = Math.min(99, (elapsedSeconds / displayEstimatedTotal) * 100)
  } else {
    displayEstimatedTotal = estimatedTotalSeconds
    percentComplete = (elapsedSeconds / estimatedTotalSeconds) * 100
  }

  if (status === "completed") {
    percentComplete = 100
  }

  return (
    <Card className="w-full">
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Rendering</CardTitle>
          <Button
            variant="ghost"
            size="icon"
            onClick={handleCancel}
            disabled={isCancelling}
            aria-label="Cancel render"
          >
            {isCancelling ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <X className="size-4" />
            )}
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-6">
        {error && (
          <Alert variant="destructive">
            <AlertCircle className="size-4" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        )}

        {staleWarning && (
          <Alert variant="destructive">
            <AlertCircle className="size-4" />
            <AlertDescription>{staleWarning}</AlertDescription>
          </Alert>
        )}

        {/* Phase indicator */}
        <div className="space-y-2">
          <div className="flex items-center justify-between text-sm">
            <span className="font-medium">{PHASE_LABELS[currentPhase]}</span>
            <span className="text-muted-foreground">
              Step {currentPhaseIndex + 1} of {totalPhases}
            </span>
          </div>

          {/* Phase dots */}
          <div className="flex gap-1">
            {Array.from({ length: totalPhases }).map((_, index) => (
              <div
                key={index}
                className={`h-1.5 flex-1 rounded-full transition-colors ${
                  index < currentPhaseIndex
                    ? "bg-primary"
                    : index === currentPhaseIndex
                    ? "bg-primary/60"
                    : "bg-muted"
                }`}
              />
            ))}
          </div>
        </div>

        {/* Progress bar */}
        <div className="space-y-2">
          <div className="h-2 w-full overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-all duration-500"
              style={{ width: `${percentComplete}%` }}
            />
          </div>
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>{formatDuration(elapsedSeconds)}</span>
            {displayEstimatedTotal > 0 && (
              <span>~{formatDuration(displayEstimatedTotal)}</span>
            )}
          </div>
        </div>

        {/* Cancel button */}
        <Button
          variant="outline"
          className="w-full"
          onClick={handleCancel}
          disabled={isCancelling}
        >
          {isCancelling ? (
            <>
              <Loader2 className="mr-2 size-4 animate-spin" />
              Cancelling...
            </>
          ) : (
            "Cancel Render"
          )}
        </Button>
      </CardContent>
    </Card>
  )
}
