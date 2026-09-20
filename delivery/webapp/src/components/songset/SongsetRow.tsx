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
  RenderStatusBadge,
  RenderState,
} from "./RenderStatusBadge";
import { ThemeArcSpan, toSongTheme } from "./ThemeLabel";
import { cn } from "@/lib/utils";
import { useLocale } from "@/hooks/useLocale";
import { useConnectivity } from "@/hooks/useConnectivity";
import type { SongTheme } from "@/lib/constants";
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
  CloudOff,
  Music,
  Clock,
  Download,
  FileAudio,
  FileVideo,
} from "lucide-react";

/** Kebab-menu items a row may show; default is the full management menu. */
export type SongsetMenuAction =
  | "render"
  | "play"
  | "downloadOffline"
  | "removeOffline";
export interface SongsetRowProps {
  id: string;
  name: string;
  description?: string | null;
  itemCount: number;
  durationSeconds?: number;
  updatedAt: Date;
  renderState: RenderState;
  isOfflineAvailable?: boolean;
  isArtifactsStale?: boolean;
  latestRenderJobId: string | null;
  lastCompletedRenderJobId: string | null;
  renderErrorMessage?: string | null;
  failedAt?: Date | null;
  onRender?: () => void;
  onPlay?: () => void;
  onRetry?: () => void;
  onRename?: () => void;
  onDuplicate?: () => void;
  onShare?: () => void;
  onDownloadAudio?: () => void;
  onDownloadVideo?: () => void;
  /** Present ⇒ row shows the offline download/re-download menu item. */
  onDownloadOffline?: () => void;
  isOfflineDownloadInProgress?: boolean;
  onRemoveOffline?: () => void;
  onDelete?: () => void;
  className?: string;
  themes?: string[];
  /**
   * Whitelist of kebab items to render. Undefined ⇒ full management menu
   * (Rename/Duplicate/Render/Play/Share/Audio/Video/offline/Delete), the
   * /songsets behavior. Narrow lists (e.g. /worship's playback-focused menu)
   * pass only the actions they wire.
   */
  menuActions?: SongsetMenuAction[];
}

export function SongsetRow({
  id,
  name,
  description,
  itemCount,
  durationSeconds,
  updatedAt,
  renderState,
  isOfflineAvailable = false,
  isArtifactsStale = false,
  latestRenderJobId,
  lastCompletedRenderJobId,
  renderErrorMessage,
  failedAt,
  onRender,
  onPlay,
  onRename,
  onDuplicate,
  onShare,
  onDownloadAudio,
  onDownloadVideo,
  onDownloadOffline,
  isOfflineDownloadInProgress = false,
  onRemoveOffline,
  onDelete,
  className,
  themes,
  menuActions,
}: SongsetRowProps) {
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const { t, locale } = useLocale();
  const connectivity = useConnectivity();

  // Undefined whitelist ⇒ full management menu (default).
  const showMenuItem = (action: SongsetMenuAction) =>
    !menuActions || menuActions.includes(action);

  const canPlayFreshRender =
    renderState === "fresh" && Boolean(lastCompletedRenderJobId) && Boolean(onPlay);

  // Download for Offline (issue #212 follow-up): fresh rows without a copy
  // get the plain item; stale rows get the re-download variant. Hidden when
  // downloaded and fresh — "Remove from offline" occupies the slot. Gated on
  // a render job + connectivity: the download fetches from the network.
  const showDownloadOffline =
    Boolean(onDownloadOffline) && (!isOfflineAvailable || isArtifactsStale);
  const canDownloadOffline = showDownloadOffline
    && (Boolean(latestRenderJobId) || Boolean(lastCompletedRenderJobId))
    && connectivity === "online";

  const arcThemes = (themes ?? []).map(toSongTheme).filter((t): t is SongTheme => t !== null);

  const formatDuration = (seconds?: number) => {
    if (!seconds) return "--:--";
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  const formatDate = (date: Date) => {
    return new Intl.DateTimeFormat(locale, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(date));
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
          <div className="flex-1 min-w-0">
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

              <div className="shrink-0 flex items-center gap-1.5">
                {canPlayFreshRender && (
                  <Button
                    variant="default"
                    size="sm"
                    className="shrink-0 gap-1.5"
                    onClick={onPlay}
                  >
                    <Play className="size-4" />
                    {t("songsets.action.play")}
                  </Button>
                )}

                <DropdownMenu open={isMenuOpen} onOpenChange={setIsMenuOpen}>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      className="shrink-0 opacity-100 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-hover:opacity-100 focus:opacity-100 data-[state=open]:opacity-100 transition-opacity"
                      aria-label={t("songsets.aria.openMenu")}
                    >
                      <MoreVertical className="size-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-48">
                    {showMenuItem("render") && onRename && (
                      <DropdownMenuItem onClick={onRename}>
                        <Edit className="size-4 mr-2" />
                        {t("songsets.action.rename")}
                      </DropdownMenuItem>
                    )}
                    {showMenuItem("render") && onDuplicate && (
                      <DropdownMenuItem onClick={onDuplicate}>
                        <Copy className="size-4 mr-2" />
                        {t("songsets.action.duplicate")}
                      </DropdownMenuItem>
                    )}
                    <DropdownMenuSeparator />
                    {showMenuItem("render") && (
                      <DropdownMenuItem onClick={onRender}>
                        <RefreshCw className="size-4 mr-2" />
                        {t("songsets.action.render")}
                      </DropdownMenuItem>
                    )}
                    {showMenuItem("play") && (
                      <DropdownMenuItem onClick={onPlay}>
                        <Play className="size-4 mr-2" />
                        {t("songsets.action.play")}
                      </DropdownMenuItem>
                    )}
                    {showMenuItem("render") && (
                      <DropdownMenuItem onClick={onShare}>
                        <Share2 className="size-4 mr-2" />
                        {t("songsets.action.share")}
                      </DropdownMenuItem>
                    )}
                    {showMenuItem("render") && (
                      <DropdownMenuItem
                        onClick={onDownloadAudio}
                        disabled={!lastCompletedRenderJobId}
                      >
                        <FileAudio className="size-4 mr-2" />
                        {t("songsets.action.downloadAudio")}
                      </DropdownMenuItem>
                    )}
                    {showMenuItem("render") && (
                      <DropdownMenuItem
                        onClick={onDownloadVideo}
                        disabled={!lastCompletedRenderJobId}
                      >
                        <FileVideo className="size-4 mr-2" />
                        {t("songsets.action.downloadVideo")}
                      </DropdownMenuItem>
                    )}
                    {showDownloadOffline && (
                      <DropdownMenuItem
                        onClick={onDownloadOffline}
                        disabled={!canDownloadOffline || isOfflineDownloadInProgress}
                      >
                        <Download className="size-4 mr-2" />
                        {isOfflineDownloadInProgress
                          ? t("songsets.menu.downloadingOffline")
                          : isArtifactsStale
                            ? t("songsets.menu.redownloadOffline")
                            : t("songsets.menu.downloadOffline")}
                      </DropdownMenuItem>
                    )}
                    {showMenuItem("removeOffline") && isOfflineAvailable && onRemoveOffline && (
                      <DropdownMenuItem onClick={onRemoveOffline}>
                        <CloudOff className="size-4 mr-2" />
                        {t("songsets.menu.removeOffline")}
                      </DropdownMenuItem>
                    )}
                    {showMenuItem("render") && onDelete && (
                      <DropdownMenuItem
                        onClick={onDelete}
                        className="text-destructive focus:text-destructive"
                      >
                        <Trash2 className="size-4 mr-2" />
                        {t("songsets.action.delete")}
                      </DropdownMenuItem>
                    )}
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </div>

            <Link
              href={`/songsets/${id}`}
              className="block rounded-md p-1 -m-1 hover:bg-accent/50 transition-colors"
            >
              <div className="flex items-center gap-3 mt-2 text-sm text-muted-foreground">
                <span className="flex items-center gap-1">
                  <Music className="size-3.5" />
                  {itemCount} {t(itemCount === 1 ? "songsets.unit.song" : "songsets.unit.songs")}
                </span>
                <span className="flex items-center gap-1">
                  <Clock className="size-3.5" />
                  {formatDuration(durationSeconds)}
                </span>
                <span className="text-xs">
                  {t("songsets.updatedPrefix")}
                  {formatDate(updatedAt)}
                </span>
              </div>
            </Link>

              <div className="flex items-center gap-2 mt-2 flex-wrap">
                <RenderStatusBadge
                  state={renderState}
                  errorMessage={renderErrorMessage}
                  failedAt={failedAt}
                />
                {arcThemes.length > 0 && (
                  <ThemeArcSpan themes={arcThemes} />
                )}
                {isOfflineAvailable && (
                  <Badge
                    variant="secondary"
                    className={cn(
                      "text-xs gap-1",
                      isArtifactsStale && "text-amber-600 border-amber-500/50"
                    )}
                  >
                    <WifiOff className="size-3" />
                    {t("songsets.badge.offline")}
                  </Badge>
                )}
                {isArtifactsStale && (
                  <Badge variant="outline" className="text-xs gap-1 text-amber-600 border-amber-500/50">
                    <AlertTriangle className="size-3" />
                    {t("songsets.alert.artifactsStale")}
                  </Badge>
                )}
              </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
