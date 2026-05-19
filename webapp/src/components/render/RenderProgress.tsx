"use client"

import { useEffect, useState, useCallback, useRef } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Loader2, X, AlertCircle } from "lucide-react"

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

export function RenderProgress({
  jobId,
  onComplete,
  onCancel,
  onError,
}: RenderProgressProps) {
  const [progress, setProgress] = useState<RenderProgressData | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [isCancelling, setIsCancelling] = useState(false)
  const eventSourceRef = useRef<EventSource | null>(null)

  const handleCancel = useCallback(async () => {
    if (isCancelling) return
    
    setIsCancelling(true)
    try {
      // Close SSE connection first
      if (eventSourceRef.current) {
        eventSourceRef.current.close()
        eventSourceRef.current = null
      }

      // Send cancel request
      const response = await fetch(`/api/render-jobs/${jobId}`, {
        method: "DELETE",
      })

      if (!response.ok) {
        throw new Error("Failed to cancel render job")
      }

      onCancel()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to cancel")
      setIsCancelling(false)
    }
  }, [jobId, onCancel, isCancelling])

  useEffect(() => {
    let retryCount = 0
    const maxRetries = 3
    let retryTimeout: NodeJS.Timeout

    const connectSSE = () => {
      // Close existing connection
      if (eventSourceRef.current) {
        eventSourceRef.current.close()
      }

      const eventSource = new EventSource(`/api/render-jobs/${jobId}/events`)
      eventSourceRef.current = eventSource

      eventSource.onopen = () => {
        retryCount = 0 // Reset retry count on successful connection
      }

      eventSource.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as RenderProgressData
          setProgress(data)

          if (data.status === "completed") {
            eventSource.close()
            eventSourceRef.current = null
            onComplete()
          } else if (data.status === "failed") {
            eventSource.close()
            eventSourceRef.current = null
            const errMsg = data.errorMessage || "Render failed"
            setError(errMsg)
            onError(errMsg)
          } else if (data.status === "cancelled") {
            eventSource.close()
            eventSourceRef.current = null
            onCancel()
          }
        } catch (err) {
          console.error("Failed to parse SSE data:", err)
        }
      }

      eventSource.onerror = (err) => {
        console.error("SSE error:", err)
        eventSource.close()
        eventSourceRef.current = null

        // Retry connection if not completed and under max retries
        if (retryCount < maxRetries) {
          retryCount++
          retryTimeout = setTimeout(connectSSE, 2000 * retryCount)
        } else {
          setError("Lost connection to render server. Please check the status manually.")
          onError("Connection lost")
        }
      }
    }

    connectSSE()

    return () => {
      if (retryTimeout) {
        clearTimeout(retryTimeout)
      }
      if (eventSourceRef.current) {
        eventSourceRef.current.close()
        eventSourceRef.current = null
      }
    }
  }, [jobId, onComplete, onError])

  // Fetch initial status
  useEffect(() => {
    const fetchStatus = async () => {
      try {
        const response = await fetch(`/api/render-jobs/${jobId}`)
        if (!response.ok) {
          throw new Error("Failed to fetch job status")
        }
        const data = await response.json()
        
        if (data.status === "failed") {
          setError(data.errorMessage || "Render failed")
          onError(data.errorMessage || "Render failed")
        } else if (data.status === "completed") {
          onComplete()
        }
      } catch (err) {
        console.error("Failed to fetch job status:", err)
      }
    }

    fetchStatus()
  }, [jobId, onComplete, onError])

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
