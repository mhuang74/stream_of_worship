"use client"

import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { CheckCircle2, Loader2, AlertCircle, RefreshCw } from "lucide-react"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { getRenderFailureText } from "@/lib/render/error-message"

export type RenderState = "unrendered" | "rendering" | "fresh" | "stale" | "failed"

interface RenderStatusBadgeProps {
  state: RenderState
  errorMessage?: string | null
  failedAt?: Date | null
  className?: string
}

const STATE_CONFIG: Record<RenderState, {
  label: string
  variant: "default" | "secondary" | "destructive" | "outline"
  icon: React.ComponentType<{ className?: string }>
  iconClass: string
}> = {
  unrendered: {
    label: "Not rendered",
    variant: "outline",
    icon: RefreshCw,
    iconClass: "",
  },
  rendering: {
    label: "Rendering",
    variant: "secondary",
    icon: Loader2,
    iconClass: "animate-spin",
  },
  fresh: {
    label: "Rendered",
    variant: "default",
    icon: CheckCircle2,
    iconClass: "",
  },
  stale: {
    label: "Needs re-render",
    variant: "outline",
    icon: RefreshCw,
    iconClass: "",
  },
  failed: {
    label: "Render failed",
    variant: "destructive",
    icon: AlertCircle,
    iconClass: "",
  },
}

export function RenderStatusBadge({
  state,
  errorMessage,
  failedAt,
  className,
}: RenderStatusBadgeProps) {
  const config = STATE_CONFIG[state] || STATE_CONFIG.unrendered
  const Icon = config.icon

  const badge = (
    <Badge variant={config.variant} className={cn("gap-1", className)}>
      <Icon className={cn("size-3", config.iconClass)} />
      {config.label}
    </Badge>
  )

  if (state === "failed") {
    const failureText = getRenderFailureText(errorMessage, failedAt)
    if (failureText) {
      return (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <button type="button" className="inline-flex rounded-md focus:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2">
                {badge}
              </button>
            </TooltipTrigger>
            <TooltipContent className="max-w-80 whitespace-normal break-words">
              {failureText}
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      )
    }
  }

  return badge
}
