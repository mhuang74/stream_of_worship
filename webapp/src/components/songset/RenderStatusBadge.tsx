"use client"

import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"
import { CheckCircle2, Loader2, AlertCircle, RefreshCw } from "lucide-react"

export type RenderState = "unrendered" | "rendering" | "fresh" | "stale" | "failed"

interface RenderStatusBadgeProps {
  state: RenderState
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

export function RenderStatusBadge({ state, className }: RenderStatusBadgeProps) {
  const config = STATE_CONFIG[state]
  const Icon = config.icon

  return (
    <Badge variant={config.variant} className={cn("gap-1", className)}>
      <Icon className={cn("size-3", config.iconClass)} />
      {config.label}
    </Badge>
  )
}
