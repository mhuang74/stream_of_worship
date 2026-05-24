"use client";

import { useState } from "react";
import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  RenderStateButton,
  RenderState,
} from "./RenderStateButton";
import { cn } from "@/lib/utils";
import {
  MoreVertical,
  Edit,
  Copy,
  Play,
  RefreshCw,
  Share2,
  Trash2,
  AlertTriangle,
  WifiOff,
  Music,
  Clock,
  FileAudio,
  FileVideo,
} from "lucide-react";

export interface SongsetRowProps {
  id: string;
  name: string;
  description?: string | null;
  itemCount: number;
  durationSeconds?: number;
  updatedAt: Date;
  renderState: RenderState;
  renderProgress?: number;
  isOfflineAvailable?: boolean;
  isArtifactsStale?: boolean;
  latestRenderJobId: string | null;
  onRender?: () => void;
  onPlay?: () => void;
  onRetry?: () => void;
  onRename?: () => void;
  onDuplicate?: () => void;
  onShare?: () => void;
  onDownloadAudio?: () => void;
  onDownloadVideo?: () => void;
  onDelete?: () => void;
  className?: string;
}

export function SongsetRow({
  id,
  name,
  description,
  itemCount,
  durationSeconds,
  updatedAt,
  renderState,
  renderProgress = 0,
  isOfflineAvailable = false,
  isArtifactsStale = false,
  latestRenderJobId,
  onRender,
  onPlay,
  onRetry,
  onRename,
  onDuplicate,
  onShare,
  onDownloadAudio,
  onDownloadVideo,
  onDelete,
  className,
}: SongsetRowProps) {
  const [isMenuOpen, setIsMenuOpen] = useState(false);

  const formatDuration = (seconds?: number) => {
    if (!seconds) return "--:--";
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  const formatDate = (date: Date) => {
    return new Intl.DateTimeFormat("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(date));
  };

  const handlePlayAnyway = () => {
    onPlay?.();
  };

  return (
    <Card
      className={cn(
        "group relative transition-all hover:shadow-md",
        isArtifactsStale && "border-amber-500/50",
        className
      )}
      data-songset-id={id}
    >
      <CardContent className="p-4">
        <div className="flex items-start gap-3">
          {/* Main content */}
          <div className="flex-1 min-w-0">
            {/* Header row */}
            <div className="flex items-start justify-between gap-2">
              <Link
                href={`/songsets/${id}`}
                className="min-w-0 flex-1 rounded-md p-1 -m-1 hover:bg-accent/50 transition-colors"
              >
                <h3 className="font-medium text-base truncate" title={name}>
                  {name}
                </h3>
                {description && (
                  <p className="text-sm text-muted-foreground truncate">
                    {description}
                  </p>
                )}
              </Link>

              {/* Context menu */}
              <DropdownMenu open={isMenuOpen} onOpenChange={setIsMenuOpen}>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    className="shrink-0 opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity"
                    aria-label="Open menu"
                  >
                    <MoreVertical className="size-4" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-48">
                  <DropdownMenuItem onClick={onRename}>
                    <Edit className="size-4 mr-2" />
                    Rename
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={onDuplicate}>
                    <Copy className="size-4 mr-2" />
                    Duplicate
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={onRender}>
                    <RefreshCw className="size-4 mr-2" />
                    Render
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={onPlay}>
                    <Play className="size-4 mr-2" />
                    Play
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={onShare}>
                    <Share2 className="size-4 mr-2" />
                    Share
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onClick={onDownloadAudio}
                    disabled={!latestRenderJobId}
                  >
                    <FileAudio className="size-4 mr-2" />
                    Download Audio
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onClick={onDownloadVideo}
                    disabled={!latestRenderJobId}
                  >
                    <FileVideo className="size-4 mr-2" />
                    Download Video
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    onClick={onDelete}
                    className="text-destructive focus:text-destructive"
                  >
                    <Trash2 className="size-4 mr-2" />
                    Delete
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>

            {/* Metadata row */}
            <Link
              href={`/songsets/${id}`}
              className="block rounded-md p-1 -m-1 hover:bg-accent/50 transition-colors"
            >
              <div className="flex items-center gap-3 mt-2 text-sm text-muted-foreground">
                <span className="flex items-center gap-1">
                  <Music className="size-3.5" />
                  {itemCount} {itemCount === 1 ? "song" : "songs"}
                </span>
                <span className="flex items-center gap-1">
                  <Clock className="size-3.5" />
                  {formatDuration(durationSeconds)}
                </span>
                <span className="text-xs">
                  Updated {formatDate(updatedAt)}
                </span>
              </div>

              {/* Status indicators */}
              <div className="flex items-center gap-2 mt-2 flex-wrap">
                {isOfflineAvailable && (
                  <Badge variant="secondary" className="text-xs gap-1">
                    <WifiOff className="size-3" />
                    Offline
                  </Badge>
                )}
                {isArtifactsStale && (
                  <Badge variant="outline" className="text-xs gap-1 text-amber-600 border-amber-500/50">
                    <AlertTriangle className="size-3" />
                    Artifacts out of date
                  </Badge>
                )}
              </div>
            </Link>

            {/* Action buttons */}
            <div className="flex items-center gap-2 mt-3">
              <RenderStateButton
                state={renderState}
                progress={renderProgress}
                onRender={onRender}
                onPlay={onPlay}
                onRetry={onRetry}
                size="sm"
              />
              {renderState === "stale" && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handlePlayAnyway}
                  className="text-muted-foreground"
                >
                  Play anyway
                </Button>
              )}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
