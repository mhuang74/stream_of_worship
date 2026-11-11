"use client";

/**
 * Persistent bottom bar for the Songset audition: the same dock surface as the
 * Song Play bar, including an inline Lyrics pullup. Everything transport-shaped
 * lives in `SongsetListenContext`; this component only renders it.
 */

import { type KeyboardEvent, type PointerEvent, useRef } from "react";
import {
  AlignLeft,
  Headphones,
  Loader2,
  Pause,
  Play,
  SkipBack,
  SkipForward,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { useLocale } from "@/hooks/useLocale";
import { useSongsetListen } from "@/contexts/SongsetListenContext";
import { findTimelineItemIndex } from "@/lib/arrangements";
import { cn } from "@/lib/utils";
import { SongsetLyricsPanel } from "./SongsetLyricsPanel";
import { useLyricsPullup } from "./useLyricsPullup";

/** Boundary snap distance for pointer seeks, in CSS pixels. */
const SEEK_SNAP_PX = 8;

/** `m:ss` for the seek readout; minutes are unbounded. */
function formatTime(seconds: number): string {
  const whole = Number.isFinite(seconds) && seconds > 0 ? Math.floor(seconds) : 0;
  const minutes = Math.floor(whole / 60);
  const remainder = whole % 60;
  return `${minutes}:${remainder.toString().padStart(2, "0")}`;
}

export function SongsetPlayerBar() {
  const { t } = useLocale();
  const {
    request,
    loadState,
    loadErrorKey,
    items,
    deletedItemCount,
    timeline,
    validation,
    previewErrorKind,
    audio,
    close,
    retryLoad,
  } = useSongsetListen();

  const {
    play,
    pause,
    retry,
    status,
    progress,
    seek,
    timelinePositionSeconds,
    totalSeconds,
    errorMessage,
  } = audio;

  const { showLyrics, isMounted, toggle, handleTransitionEnd, panelId } = useLyricsPullup({
    available: items.length > 0,
    panelId: "songset-lyrics-panel",
  });
  const seekBarRef = useRef<HTMLDivElement>(null);

  const clampedPosition =
    totalSeconds > 0 ? Math.min(Math.max(timelinePositionSeconds, 0), totalSeconds) : 0;
  const currentIndex = findTimelineItemIndex(timeline, clampedPosition);
  const currentItem = items[currentIndex] ?? items[0] ?? null;
  const currentTitle = currentItem?.song?.title ?? t("songsets.listen.untitled");
  const currentClips = timeline.items[currentIndex]?.clips ?? [];
  const segments = timeline.items.map((item, index) => ({
    item,
    index,
    start: item.startSeconds,
    end:
      index + 1 < timeline.items.length
        ? timeline.items[index + 1]!.startSeconds
        : totalSeconds,
  }));

  const hardError =
    loadErrorKey === "notFound"
      ? t("songsets.listen.error.notFound")
      : loadErrorKey === "build"
        ? t("songsets.listen.error.build")
        : loadState === "ready" && items.length === 0
          ? t("songsets.listen.error.empty")
          : loadState === "ready" && deletedItemCount > 0
            ? t("songsets.listen.error.deletedRecording")
            : loadState === "ready" && validation
              ? t(
                  validation.kind === "no-recording"
                    ? "songsets.listen.error.noRecording"
                    : "songsets.listen.error.invalidArrangement",
                )
              : null;

  const sessionError =
    !hardError && status === "error"
      ? previewErrorKind === "playback-failed"
        ? t("songsets.listen.error.playbackFailed")
        : errorMessage
      : null;
  const errorText = hardError ?? sessionError;

  const totalBytes = progress?.totalBytes ?? null;
  const percent =
    totalBytes !== null && totalBytes > 0
      ? Math.min(100, Math.floor(((progress?.receivedBytes ?? 0) / totalBytes) * 100))
      : null;
  const loadingItem = items[progress?.itemIndex ?? 0] ?? items[0] ?? null;
  const loadingTitle = loadingItem?.song?.title ?? t("songsets.listen.untitled");
  const loadingLabel =
    percent !== null
      ? t("songsets.listen.loading")
          .replace("{title}", loadingTitle)
          .replace("{percent}", String(percent))
      : t("songsets.listen.loadingIndeterminate").replace("{title}", loadingTitle);

  const playingOrPaused = status === "playing" || status === "paused";
  const loadingCurrent =
    !errorText &&
    items.length > 0 &&
    playingOrPaused &&
    progress !== null &&
    progress.phase !== "ready" &&
    progress.itemIndex <= currentIndex;
  const preparingNext =
    !errorText && status === "playing" && progress !== null && progress.phase !== "ready" && progress.itemIndex > currentIndex;

  if (!request) return null;

  const canSeek = !errorText && loadState === "ready" && totalSeconds > 0;
  let subtitle: string;
  if (errorText) {
    subtitle = errorText;
  } else if (loadState !== "ready") {
    subtitle = t("songsets.listen.loadingSongset");
  } else if (loadingCurrent) {
    subtitle = loadingLabel;
  } else if (preparingNext) {
    subtitle = t("songsets.listen.preparingNext");
  } else {
    subtitle = `${t("songsets.listen.currentItem")} · ${currentTitle}`;
  }

  const resolveSeek = (clientX: number) => {
    const rect = seekBarRef.current?.getBoundingClientRect();
    if (!rect || rect.width <= 0) return;
    const x = Math.min(Math.max(clientX - rect.left, 0), rect.width);
    const seconds = (x / rect.width) * totalSeconds;
    const snapSeconds = (SEEK_SNAP_PX / rect.width) * totalSeconds;
    const boundary = timeline.items.find(
      (timelineItem) => Math.abs(timelineItem.startSeconds - seconds) <= snapSeconds,
    );
    seek(boundary ? boundary.startSeconds : seconds);
  };

  const handleSeekPointerDown = (event: PointerEvent<HTMLDivElement>) => {
    event.currentTarget.setPointerCapture(event.pointerId);
    resolveSeek(event.clientX);
  };

  const handleSeekPointerMove = (event: PointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      resolveSeek(event.clientX);
    }
  };

  const handleSeekPointerRelease = (event: PointerEvent<HTMLDivElement>) => {
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  };

  const handleSeekKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    let next: number;
    switch (event.key) {
      case "ArrowLeft":
        next = clampedPosition - 5;
        break;
      case "ArrowRight":
        next = clampedPosition + 5;
        break;
      case "ArrowDown":
        next = clampedPosition - 10;
        break;
      case "ArrowUp":
        next = clampedPosition + 10;
        break;
      case "PageDown":
        next = clampedPosition - 60;
        break;
      case "PageUp":
        next = clampedPosition + 60;
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = totalSeconds;
        break;
      default:
        return;
    }
    event.preventDefault();
    seek(Math.min(Math.max(next, 0), totalSeconds));
  };

  return (
    <div
      className={cn(
        "fixed bottom-0 left-0 right-0 z-[60]",
        "bg-background/95 backdrop-blur-sm border-t",
      )}
      data-testid="songset-player-bar"
      role="region"
      aria-label={request.name}
    >
      {/* Animated lyrics panel — always in DOM for transition */}
      <div
        onTransitionEnd={handleTransitionEnd}
        className={cn(
          "overflow-hidden transition-[max-height,opacity] duration-300 ease-in-out border-t bg-background/95 backdrop-blur-sm",
          showLyrics && items.length > 0
            ? "max-h-[40dvh] md:max-h-[400px] opacity-100"
            : "max-h-0 opacity-0 border-t-0",
        )}
      >
        {isMounted && items.length > 0 && (
          <div
            id={panelId}
            role="region"
            aria-label={`${t("audio.lyricsFor")} ${currentTitle}`}
            className="overflow-y-auto overscroll-y-contain h-full"
          >
            <SongsetLyricsPanel
              key={currentItem?.id ?? "empty"}
              contentHash={currentItem?.recording?.contentHash ?? null}
              clips={currentClips}
              sections={currentItem?.recording?.sections ?? null}
              positionSeconds={clampedPosition}
              onSeek={seek}
            />
          </div>
        )}
      </div>

      {/* Proportional seek bar: one segment per Songset Item */}
      <div className="w-full pt-2 lg:pt-3">
        <div
          ref={seekBarRef}
          role="slider"
          tabIndex={canSeek ? 0 : -1}
          data-testid="songset-seek"
          aria-label={t("songsets.listen.seekAria")}
          aria-valuemin={0}
          aria-valuemax={totalSeconds}
          aria-valuenow={clampedPosition}
          aria-valuetext={`${formatTime(clampedPosition)} / ${formatTime(totalSeconds)}`}
          aria-disabled={!canSeek}
          className={cn(
            "relative h-6 w-full touch-none select-none px-3",
            !canSeek && "opacity-50 cursor-not-allowed",
          )}
          onPointerDown={canSeek ? handleSeekPointerDown : undefined}
          onPointerMove={canSeek ? handleSeekPointerMove : undefined}
          onPointerUp={canSeek ? handleSeekPointerRelease : undefined}
          onPointerCancel={canSeek ? handleSeekPointerRelease : undefined}
          onKeyDown={canSeek ? handleSeekKeyDown : undefined}
        >
          <div className="relative h-2 w-full overflow-hidden rounded-full bg-muted">
            {totalSeconds > 0 &&
              segments.map(({ item, index, start, end }) => (
                <div
                  key={item.itemId}
                  data-testid="songset-seek-segment"
                  data-item-id={item.itemId}
                  data-active={index === currentIndex || undefined}
                  className={cn(
                    "absolute inset-y-0",
                    index === currentIndex ? "bg-primary" : "bg-primary/30",
                  )}
                  style={{
                    left: `${(start / totalSeconds) * 100}%`,
                    width: `${((end - start) / totalSeconds) * 100}%`,
                  }}
                >
                  {index > 0 && item.crossfadeSeconds > 0 && (
                    <div
                      data-testid="songset-seek-transition"
                      className="absolute inset-y-0 left-0 bg-foreground/20"
                      style={{
                        width: `${(item.crossfadeSeconds / totalSeconds) * 100}%`,
                      }}
                    />
                  )}
                </div>
              ))}
          </div>
        </div>
      </div>

      <div className="flex items-center gap-2 px-3 pb-2 pt-1 lg:px-4 lg:pb-3">
        {/* Songset identity */}
        <div className="flex items-center gap-3 min-w-0 flex-1 lg:flex-none">
          <div className="shrink-0 w-10 h-10 lg:w-12 lg:h-12 rounded-md bg-muted flex items-center justify-center">
            <Headphones className="size-5 lg:size-6 text-muted-foreground" />
          </div>
          <div className="min-w-0 flex-1">
            <p className="font-medium text-sm truncate" data-testid="songset-title">
              {request.name}
            </p>
            <p
              className={cn(
                "text-xs truncate",
                errorText ? "text-destructive" : "text-muted-foreground",
              )}
              data-testid="songset-subtitle"
              role={errorText ? "alert" : undefined}
            >
              {subtitle}
            </p>
          </div>
        </div>

        {/* Transport */}
        <div className="flex items-center justify-center gap-1 lg:gap-2 flex-1">
          <Button
            variant="ghost"
            size="icon"
            className="size-8 lg:size-10 shrink-0"
            onClick={() => seek(Math.max(0, clampedPosition - 10))}
            aria-label={t("audio.skipBack")}
            data-testid="songset-skip-back"
          >
            <SkipBack className="size-4 lg:size-5" />
          </Button>

          <Button
            variant="default"
            size="icon"
            className="size-10 lg:size-12 shrink-0 rounded-full"
            onClick={() => {
              if (status === "playing") {
                pause();
              } else {
                void play();
              }
            }}
            disabled={loadState !== "ready" || Boolean(errorText)}
            aria-label={status === "playing" ? t("audio.pause") : t("audio.play")}
            data-testid="songset-play"
          >
            {loadState !== "ready" ? (
              <Loader2 className="size-5 lg:size-6 animate-spin" />
            ) : status === "playing" ? (
              <Pause className="size-5 lg:size-6" />
            ) : (
              <Play className="size-5 lg:size-6 ml-0.5" />
            )}
          </Button>

          <Button
            variant="ghost"
            size="icon"
            className="size-8 lg:size-10 shrink-0"
            onClick={() => seek(Math.min(totalSeconds, clampedPosition + 10))}
            aria-label={t("audio.skipForward")}
            data-testid="songset-skip-forward"
          >
            <SkipForward className="size-4 lg:size-5" />
          </Button>
        </div>

        <div className="flex items-center gap-2 lg:gap-4 min-w-0 flex-1 lg:flex-none justify-end">
          <span
            className="text-xs text-muted-foreground tabular-nums whitespace-nowrap"
            data-testid="songset-time"
          >
            {formatTime(clampedPosition)} / {formatTime(totalSeconds)}
          </span>

          {(errorText && loadState === "ready") || loadErrorKey !== null ? (
            <Button
              variant="outline"
              size="sm"
              data-testid="songset-retry"
              onClick={() => {
                if (loadErrorKey !== null) {
                  retryLoad();
                } else {
                  void retry();
                }
              }}
            >
              {t("songsets.listen.action.retry")}
            </Button>
          ) : null}
        </div>

        {/* Lyrics toggle */}
        {items.length > 0 && (
          <Button
            variant={showLyrics ? "secondary" : "ghost"}
            size="icon"
            className="size-8 lg:size-10 shrink-0"
            onClick={toggle}
            aria-expanded={showLyrics}
            aria-controls={panelId}
            aria-label={showLyrics ? t("audio.hideLyrics") : t("audio.showLyrics")}
            title={t("audio.lyricsTitle")}
            data-testid="songset-lyrics-toggle"
          >
            <AlignLeft className="size-4 lg:size-5" />
          </Button>
        )}

        {/* Close */}
        <Button
          variant="ghost"
          size="icon"
          className="size-8 lg:size-10 shrink-0"
          onClick={close}
          aria-label={t("audio.closePlayer")}
          data-testid="songset-close"
        >
          <X className="size-4 lg:size-5" />
        </Button>
      </div>
    </div>
  );
}
