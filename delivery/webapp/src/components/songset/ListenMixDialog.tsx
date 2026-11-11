"use client";

/**
 * Listen dialog for a whole Songset (issue #297, spec §3.6).
 *
 * The dialog reuses the existing Songset detail GET (no new route, no render,
 * no polling), maps it client-side, and drives the browser audio session from
 * `useSongsetAudio`. Every mount site passes `key={songsetId}`, so switching
 * rows remounts this component and tears the previous session down.
 */

import { useEffect, useId, useMemo, useRef, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { useLocale } from "@/hooks/useLocale";
import { useAudioPlayerContext } from "@/contexts/AudioPlayerContext";
import {
  useSongsetAudio,
  type SongsetAudioErrorKind,
} from "@/hooks/useSongsetAudio";
import {
  toListenTimelineSources,
  type ListenSongsetItemPayload,
} from "@/lib/arrangements-listen";
import { buildArrangementTimeline } from "@/lib/arrangements";
import { validateSongsetAudioSources } from "@/lib/audio/songset-audio";

interface ListenSongsetPayload {
  items?: ListenSongsetItemPayload[];
  deletedItemCount?: number;
}

type LoadState = "loading" | "ready" | "error";
type LoadErrorKey = "notFound" | "build";

/** `m:ss` for the seek readout; minutes are unbounded. */
function formatTime(seconds: number): string {
  const whole =
    Number.isFinite(seconds) && seconds > 0 ? Math.floor(seconds) : 0;
  const minutes = Math.floor(whole / 60);
  const remainder = whole % 60;
  return `${minutes}:${remainder.toString().padStart(2, "0")}`;
}

export interface ListenMixDialogProps {
  songsetId: string;
  songsetName: string;
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
}

export function ListenMixDialog({
  songsetId,
  songsetName,
  isOpen,
  onOpenChange,
}: ListenMixDialogProps) {
  const { t } = useLocale();
  const player = useAudioPlayerContext();

  // Keep the latest context value without making the one-shot open effect
  // refetch when the provider re-renders.
  const playerRef = useRef(player);
  useEffect(() => {
    playerRef.current = player;
  });

  const [payloadItems, setPayloadItems] = useState<ListenSongsetItemPayload[]>(
    [],
  );
  const [deletedItemCount, setDeletedItemCount] = useState(0);
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [loadErrorKey, setLoadErrorKey] = useState<LoadErrorKey | null>(null);
  const [previewErrorKind, setPreviewErrorKind] =
    useState<SongsetAudioErrorKind | null>(null);
  const [autoplayBlocked, setAutoplayBlocked] = useState(false);
  const autoplayAttemptedRef = useRef(false);
  // Distinguishes a payload loaded for THIS open from one left over from the
  // previous open (the dialog may stay mounted while `isOpen` toggles).
  const openGenerationRef = useRef(0);
  const loadedGenerationRef = useRef(-1);
  const [wasOpen, setWasOpen] = useState(isOpen);

  // Reset a reopened dialog during render, before its load/autoplay effects
  // run. Initial mount state already covers the first open; keyed remounts
  // cover row changes.
  if (isOpen !== wasOpen) {
    setWasOpen(isOpen);
    if (isOpen) {
      setAutoplayBlocked(false);
      setPreviewErrorKind(null);
      setLoadErrorKey(null);
      setPayloadItems([]);
      setDeletedItemCount(0);
      setLoadState("loading");
    }
  }

  const items = useMemo(
    () => toListenTimelineSources(payloadItems),
    [payloadItems],
  );
  const timeline = useMemo(() => buildArrangementTimeline(items), [items]);
  const validation = useMemo(
    () => validateSongsetAudioSources(items),
    [items],
  );
  const seekLabelId = useId();

  const audio = useSongsetAudio({
    items,
    active: isOpen,
    onPreviewError: (error) => {
      setPreviewErrorKind(error.kind);
    },
  });
  const {
    play,
    pause,
    retry,
    status,
    progress,
    seek,
    timelinePositionSeconds,
    totalSeconds,
  } = audio;

  // Open sequence: stop the global transport once, then issue exactly one
  // GET. The response is mapped and validated before any session work.
  useEffect(() => {
    if (!isOpen) return;

    playerRef.current.stop();
    openGenerationRef.current += 1;
    loadedGenerationRef.current = -1;
    autoplayAttemptedRef.current = false;

    const controller = new AbortController();
    let cancelled = false;

    void (async () => {
      try {
        const response = await fetch(`/api/songsets/${songsetId}`, {
          signal: controller.signal,
        });
        if (cancelled) return;
        if (response.status === 401 || response.status === 404) {
          setLoadErrorKey("notFound");
          setLoadState("error");
          return;
        }
        if (!response.ok) {
          setLoadErrorKey("build");
          setLoadState("error");
          return;
        }
        const payload = (await response.json()) as ListenSongsetPayload;
        if (cancelled) return;
        loadedGenerationRef.current = openGenerationRef.current;
        setPayloadItems(payload.items ?? []);
        setDeletedItemCount(payload.deletedItemCount ?? 0);
        setLoadState("ready");
      } catch {
        if (cancelled || controller.signal.aborted) return;
        setLoadErrorKey("build");
        setLoadState("error");
      }
    })();

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [isOpen, songsetId]);

  // Attempt playback once after the timeline loads, provided the payload is
  // playable (not deleted, valid arrangements). A browser autoplay rejection
  // surfaces the Play button instead.
  useEffect(() => {
    if (!isOpen || loadState !== "ready") return;
    if (loadedGenerationRef.current !== openGenerationRef.current) return;
    if (deletedItemCount > 0 || validation) return;
    if (autoplayAttemptedRef.current) return;
    autoplayAttemptedRef.current = true;
    void play().catch(() => {
      setAutoplayBlocked(true);
    });
  }, [isOpen, loadState, deletedItemCount, validation, play]);

  const payloadReady = loadState === "ready";
  const hardError = loadErrorKey
    ? t(
        loadErrorKey === "notFound"
          ? "songsets.listen.error.notFound"
          : "songsets.listen.error.build",
      )
    : payloadReady && deletedItemCount > 0
      ? t("songsets.listen.error.deletedRecording")
      : payloadReady && validation
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
        : audio.errorMessage
      : null;

  const ready = payloadReady && !hardError;
  const playingOrPaused = status === "playing" || status === "paused";
  const clampedPosition =
    totalSeconds > 0
      ? Math.min(Math.max(timelinePositionSeconds, 0), totalSeconds)
      : 0;
  const currentTimelineIndex = useMemo(() => {
    let index = 0;
    timeline.items.forEach((timelineItem, itemIndex) => {
      if (timelineItem.startSeconds <= clampedPosition) index = itemIndex;
    });
    return index;
  }, [timeline, clampedPosition]);

  // Spec §3.6: progress for the item at the current position is a loading
  // state (seek into an unprepared item); progress for a later item during
  // playback is mid-set preparation and keeps the playback view up.
  const progressItemIndex = progress?.itemIndex ?? -1;
  const loadingCurrent =
    ready &&
    playingOrPaused &&
    progress !== null &&
    progress.phase !== "ready" &&
    progressItemIndex <= currentTimelineIndex;
  const showPlayback = ready && playingOrPaused && !loadingCurrent;
  const loadingItem =
    payloadItems[progress?.itemIndex ?? 0] ?? payloadItems[0] ?? null;
  const loadingTitle =
    loadingItem?.song?.title ?? t("songsets.listen.untitled");
  const currentItem =
    payloadItems[currentTimelineIndex] ?? payloadItems[0] ?? null;
  const currentTitle =
    currentItem?.song?.title ?? t("songsets.listen.untitled");
  const totalBytes = progress?.totalBytes ?? null;
  const percent =
    totalBytes !== null && totalBytes > 0
      ? Math.min(
          100,
          Math.floor(((progress?.receivedBytes ?? 0) / totalBytes) * 100),
        )
      : null;
  const loadingLabel =
    percent !== null
      ? t("songsets.listen.loading")
          .replace("{title}", loadingTitle)
          .replace("{percent}", String(percent))
      : t("songsets.listen.loadingIndeterminate").replace(
          "{title}",
          loadingTitle,
        );

  const preparingNext =
    showPlayback && progress !== null && progress.phase !== "ready";

  return (
    <Dialog open={isOpen} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="listen-dialog"
        showCloseButton={false}
        className="sm:max-w-md"
      >
        <DialogHeader>
          <DialogTitle>{t("songsets.listen.title")}</DialogTitle>
          <DialogDescription>{songsetName}</DialogDescription>
        </DialogHeader>

        {hardError ? (
          <p role="alert" className="text-sm text-destructive">
            {hardError}
          </p>
        ) : sessionError ? (
          <div className="space-y-2">
            <p role="alert" className="text-sm text-destructive">
              {sessionError}
            </p>
            <p className="text-sm text-muted-foreground">
              {t("songsets.listen.error.renderHint")}
            </p>
            <Button
              type="button"
              data-testid="listen-retry"
              variant="outline"
              size="sm"
              onClick={() => {
                setPreviewErrorKind(null);
                setAutoplayBlocked(false);
                void retry();
              }}
            >
              {t("songsets.listen.action.retry")}
            </Button>
          </div>
        ) : !showPlayback ? (
          <div data-testid="listen-progress" className="space-y-2">
            <p className="text-sm text-muted-foreground">{loadingLabel}</p>
            {percent !== null && (
              <progress
                value={percent}
                max={100}
                aria-label={loadingLabel}
                className="w-full"
              />
            )}
            {autoplayBlocked && (
              <Button
                type="button"
                data-testid="listen-play"
                size="sm"
                onClick={() => {
                  setAutoplayBlocked(false);
                  void play().catch(() => {
                    setAutoplayBlocked(true);
                  });
                }}
              >
                {t("songsets.listen.action.play")}
              </Button>
            )}
          </div>
        ) : (
          <div className="space-y-2">
            <Button
              type="button"
              data-testid="listen-play"
              size="sm"
              onClick={() => {
                if (status === "playing") {
                  pause();
                } else {
                  void play();
                }
              }}
            >
              {status === "playing"
                ? t("songsets.listen.action.pause")
                : t("songsets.listen.action.play")}
            </Button>
            <div className="space-y-1">
              <span id={seekLabelId} className="sr-only">
                {t("songsets.listen.seekAria")}
              </span>
              <Slider
                value={[clampedPosition]}
                min={0}
                max={totalSeconds > 0 ? totalSeconds : 1}
                step={1}
                disabled={totalSeconds <= 0}
                aria-labelledby={seekLabelId}
                data-testid="listen-seek"
                className="w-full"
                onValueChange={(value) => {
                  const next = Array.isArray(value) ? value[0] : value;
                  if (Number.isFinite(next)) seek(next);
                }}
              />
              <div className="flex items-center justify-between gap-2 text-sm text-muted-foreground">
                <p data-testid="listen-time" className="tabular-nums">
                  {formatTime(clampedPosition)} / {formatTime(totalSeconds)}
                </p>
                <p data-testid="listen-current-item" className="truncate">
                  {t("songsets.listen.currentItem")}: {currentTitle}
                </p>
              </div>
            </div>
            {timeline.items.length > 0 && (
              <div
                role="group"
                aria-label={t("songsets.listen.markersAria")}
                data-testid="listen-markers"
                className="flex w-full items-center gap-1"
              >
                {timeline.items.map((timelineItem) => {
                  const markerTitle =
                    payloadItems.find(
                      (payloadItem) => payloadItem.id === timelineItem.itemId,
                    )?.song?.title ?? t("songsets.listen.untitled");
                  return (
                    <button
                      key={timelineItem.itemId}
                      type="button"
                      data-testid="listen-marker"
                      aria-label={markerTitle}
                      title={markerTitle}
                      onClick={() => seek(timelineItem.startSeconds)}
                      className="h-2 flex-1 rounded-full bg-primary/30 transition-colors hover:bg-primary/60 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-hidden"
                    />
                  );
                })}
              </div>
            )}
            <p className="text-sm text-muted-foreground">
              {t("songsets.listen.localNotice")}
            </p>
          </div>
        )}

        {preparingNext && (
          <p
            data-testid="listen-status"
            className="text-sm text-muted-foreground"
          >
            {t("songsets.listen.preparingNext")}
          </p>
        )}

        <div className="flex justify-end">
          <Button
            type="button"
            data-testid="listen-close"
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
          >
            {t("songsets.listen.action.close")}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
