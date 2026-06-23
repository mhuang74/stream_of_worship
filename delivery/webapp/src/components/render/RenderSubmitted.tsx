"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Clock } from "lucide-react"

interface RenderSubmittedProps {
  estimatedMinutes: number
  onCancel: () => void
  isCancelling?: boolean
  submittedAt?: string
}

export function RenderSubmitted({
  estimatedMinutes,
  onCancel,
  isCancelling = false,
  submittedAt,
}: RenderSubmittedProps) {
  return (
    <Card className="w-full">
      <CardHeader>
        <CardTitle>Render Started</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center gap-2 text-muted-foreground">
          <Clock className="size-4" />
          <span>Estimated time: ~{estimatedMinutes} minutes</span>
        </div>
        <p className="text-sm text-muted-foreground">
          You can leave this page. Check your songset later for the result.
        </p>
        {submittedAt && (
          <p className="text-sm text-muted-foreground">
            Submitted at{" "}
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
          Cancel Render
        </Button>
      </CardContent>
    </Card>
  )
}
