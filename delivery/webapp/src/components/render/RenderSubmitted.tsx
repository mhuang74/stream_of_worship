"use client"

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Clock } from "lucide-react"
import { useLocale } from "@/hooks/useLocale"

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
  const { t } = useLocale()
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
