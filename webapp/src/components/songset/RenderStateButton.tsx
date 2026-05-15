"use client";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { Play, RefreshCw, AlertCircle, Loader2 } from "lucide-react";

export type RenderState = "unrendered" | "rendering" | "fresh" | "stale" | "failed";

interface RenderStateButtonProps {
  state: RenderState;
  progress?: number;
  onRender?: () => void;
  onPlay?: () => void;
  onRetry?: () => void;
  disabled?: boolean;
  className?: string;
  size?: "default" | "sm" | "lg";
}

export function RenderStateButton({
  state,
  progress = 0,
  onRender,
  onPlay,
  onRetry,
  disabled = false,
  className,
  size = "default",
}: RenderStateButtonProps) {
  const handleClick = () => {
    switch (state) {
      case "unrendered":
        onRender?.();
        break;
      case "fresh":
        onPlay?.();
        break;
      case "stale":
        onRender?.();
        break;
      case "failed":
        onRetry?.();
        break;
      case "rendering":
        // No action during rendering
        break;
    }
  };

  const getButtonContent = () => {
    switch (state) {
      case "unrendered":
        return (
          <>
            <RefreshCw className="size-4" />
            <span>Render</span>
          </>
        );
      case "rendering":
        return (
          <>
            <Loader2 className="size-4 animate-spin" />
            <span>Rendering... {Math.round(progress)}%</span>
          </>
        );
      case "fresh":
        return (
          <>
            <Play className="size-4" />
            <span>Play</span>
          </>
        );
      case "stale":
        return (
          <>
            <RefreshCw className="size-4" />
            <span>Re-render</span>
          </>
        );
      case "failed":
        return (
          <>
            <AlertCircle className="size-4" />
            <span>Retry render</span>
          </>
        );
    }
  };

  const getVariant = () => {
    switch (state) {
      case "unrendered":
        return "default";
      case "rendering":
        return "secondary";
      case "fresh":
        return "default";
      case "stale":
        return "outline";
      case "failed":
        return "destructive";
    }
  };

  return (
    <Button
      variant={getVariant()}
      size={size}
      onClick={handleClick}
      disabled={disabled || state === "rendering"}
      className={cn("gap-1.5", className)}
      aria-label={
        state === "rendering"
          ? `Rendering ${progress}%`
          : state === "unrendered"
          ? "Render songset"
          : state === "fresh"
          ? "Play songset"
          : state === "stale"
          ? "Re-render songset"
          : "Retry render"
      }
    >
      {getButtonContent()}
    </Button>
  );
}
