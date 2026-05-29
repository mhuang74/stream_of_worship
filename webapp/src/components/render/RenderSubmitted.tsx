"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Clock, X } from "lucide-react"

interface RenderSubmittedProps {
  estimatedMinutes: number
  onCancel: () => void
  isCancelling?: boolean
}

export function RenderSubmitted({
  estimatedMinutes,
  onCancel,
  isCancelling = false,
}: RenderSubmittedProps) {
  return (
    <Card className="w-full">
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Render Started</CardTitle>
          <Button
            variant="ghost"
            size="icon"
            onClick={onCancel}
            disabled={isCancelling}
            aria-label="Cancel render"
          >
            <X className="size-4" />
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-2 text-muted-foreground">
          <Clock className="size-4" />
          <span>Estimated time: ~{estimatedMinutes} minutes</span>
        </div>
        <p className="text-sm text-muted-foreground">
          You can leave this page. Check your songset later for the result.
        </p>
        <Button
          variant="outline"
          className="w-full"
          onClick={onCancel}
          disabled={isCancelling}
        >
          Cancel Render
        </Button>
      </CardContent>
    </Card>
  )
}
