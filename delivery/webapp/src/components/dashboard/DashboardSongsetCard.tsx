"use client";

import Link from "next/link";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { RenderStatusBadge } from "@/components/songset/RenderStatusBadge";
import type { RenderState } from "@/components/songset/RenderStatusBadge";
import { ThemeArcSpan, toSongTheme } from "@/components/songset/ThemeLabel";
import { Clock, Play, Share2 } from "lucide-react";
import { useLocale } from "@/hooks/useLocale";
import type { SongTheme } from "@/lib/constants";

export interface DashboardSongset {
  id: string;
  name: string;
  itemCount: number;
  durationSeconds: number | null;
  updatedAt: string;
  renderState: RenderState;
  lastCompletedRenderJobId: string | null;
  themes: string[];
}

interface DashboardSongsetCardProps {
  songset: DashboardSongset;
  onPlay: (songsetId: string) => void;
  onShare: (songsetId: string, name: string) => void;
}

/**
 * Lightweight songset card for the dashboard: name link, render status, meta
 * row, and limited actions (Play when a fresh render exists, Share always).
 * Deliberately omits the full kebab menu (rename/duplicate/render/download/
 * delete) to keep the dashboard uncluttered.
 */
export function DashboardSongsetCard({
  songset,
  onPlay,
  onShare,
}: DashboardSongsetCardProps) {
  const { t, locale } = useLocale();

  const canPlayFreshRender =
    songset.renderState === "fresh" && Boolean(songset.lastCompletedRenderJobId);

  const formatDuration = (seconds: number | null) => {
    if (!seconds) return "--:--";
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  const formatDate = (date: string) => {
    return new Intl.DateTimeFormat(locale, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(date));
  };

  const arcThemes = songset.themes.map(toSongTheme).filter((t): t is SongTheme => t !== null);

  return (
    <Card className="transition-shadow hover:shadow-md" data-testid="dashboard-songset-card">
      <CardContent className="p-4">
        <Link
          href={`/songsets/${songset.id}`}
          className="block rounded-md p-1 -m-1 hover:bg-accent/50 transition-colors"
        >
          <h3 className="font-medium text-sm truncate" title={songset.name}>
            {songset.name}
          </h3>
        </Link>

        <div className="flex items-center gap-3 mt-2 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <Clock className="size-3" />
            {formatDuration(songset.durationSeconds)}
          </span>
          <span className="text-xs">
            {t("songsets.updatedPrefix")}
            {formatDate(songset.updatedAt)}
          </span>
        </div>

        <div className="flex items-center gap-2 mt-2 flex-wrap">
          <RenderStatusBadge state={songset.renderState} />
          {arcThemes.length > 0 && (
            <ThemeArcSpan themes={arcThemes} />
          )}
        </div>

        <div className="flex items-center gap-2 mt-3">
          {canPlayFreshRender && (
            <Button variant="default" size="sm" className="gap-1.5" onClick={() => onPlay(songset.id)}>
              <Play className="size-4" />
              {t("songsets.action.play")}
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            className="gap-1.5"
            onClick={() => onShare(songset.id, songset.name)}
          >
            <Share2 className="size-4" />
            {t("songsets.action.share")}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
