"use client";

/**
 * React binding for the whole-Songset arrangement preview transport
 * (issue #286, spec specs/song_arrangement_editor.md §5.11).
 *
 * The Sheet owns translations and builds the `TimelineItemSource[]` inputs
 * (draft serialized for the selected item, raw persisted arrangement strings
 * for saved siblings). This hook owns:
 * - preview session lifecycle: created on the first Play (elements are created
 *   only then — never at sheet open), fully torn down on stop, on any
 *   timeline-input change (draft mutation), on active=false, and on unmount;
 * - the global AudioPlayerContext stop(): starting a preview explicitly stops
 *   the shared player so the two transports never mix. The preview never
 *   creates an AudioTrack, never calls the global play(), and therefore can
 *   never write Completion (markSongCompleted).
 * - error/fallback surfacing: `onPreviewError` for no-recording /
 *   invalid-arrangement / playback-failed, `onFallbackNotice` for the iOS
 *   silent-output crossfade fallback.
 *
 * State is keyed by the serialized timeline inputs; a teardown triggered by
 * an input change masks the previous session's view instead of writing state
 * from an effect (React: no cascading renders from effects). Closing the
 * sheet is the one exception: the transport state must actually become idle,
 * because the next open reuses the same key and would otherwise render the
 * torn-down session as still playing.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  buildArrangementTimeline,
  type TimelineItemSource,
} from "@/lib/arrangements";
import {
  createArrangementPreviewSession,
  validateArrangementPreviewSources,
  type ArrangementPreviewError,
  type ArrangementPreviewSession,
} from "@/lib/audio/arrangement-preview";
import { useAudioPlayerContext } from "@/contexts/AudioPlayerContext";

// The Sheet consumes the error contract from this hook module (single import
// surface for the editor sheet); it re-exports the transport's error types.
export type {
  ArrangementPreviewError,
  ArrangementPreviewErrorKind,
} from "@/lib/audio/arrangement-preview";

export type ArrangementPreviewStatus =
  | "idle"
  | "loading"
  | "playing"
  | "paused"
  | "error";

export interface UseArrangementPreviewOptions {
  /** Timeline inputs in Songset order; the selected item carries the draft. */
  items: TimelineItemSource[];
  /** False when the sheet is closed or preview is disabled; tears down. */
  active: boolean;
  onPreviewError: (error: ArrangementPreviewError) => void;
  onFallbackNotice: () => void;
}

export interface UseArrangementPreviewResult {
  status: ArrangementPreviewStatus;
  errorMessage: string | null;
  timelinePositionSeconds: number;
  totalSeconds: number;
  play: () => Promise<void>;
  pause: () => void;
  stop: () => void;
  seek: (seconds: number) => void;
  startAt: (itemId: string) => Promise<void>;
  isFallbackMode: boolean;
}

interface ArrangementPreviewHookState {
  /** Serialized `items` the state belongs to; stale keys render as idle. */
  key: string;
  status: ArrangementPreviewStatus;
  errorMessage: string | null;
  timelinePositionSeconds: number;
  isFallbackMode: boolean;
}

function idleState(key: string): ArrangementPreviewHookState {
  return {
    key,
    status: "idle",
    errorMessage: null,
    timelinePositionSeconds: 0,
    isFallbackMode: false,
  };
}

/** Carries keyboard state forward only while it belongs to the same inputs. */
function baseState(
  previous: ArrangementPreviewHookState,
  key: string,
): ArrangementPreviewHookState {
  return previous.key === key ? previous : idleState(key);
}

export function useArrangementPreview(
  options: UseArrangementPreviewOptions,
): UseArrangementPreviewResult {
  const { items, active } = options;
  const player = useAudioPlayerContext();

  // Latest-ref pattern: async transport callbacks must not capture stale
  // render closures.
  const onPreviewErrorRef = useRef(options.onPreviewError);
  const onFallbackNoticeRef = useRef(options.onFallbackNotice);
  useEffect(() => {
    onPreviewErrorRef.current = options.onPreviewError;
    onFallbackNoticeRef.current = options.onFallbackNotice;
  });

  // Content identity: reorder/repeat/omit changes this string, which must
  // stop the preview (spec: draft mutation stops preview) even though the
  // Sheet may hand over a fresh array on unrelated renders.
  const itemsKey = useMemo(() => JSON.stringify(items), [items]);
  const timeline = useMemo(() => buildArrangementTimeline(items), [items]);
  const validationError = useMemo(
    () => validateArrangementPreviewSources(items),
    [items],
  );

  const [state, setState] = useState<ArrangementPreviewHookState>(() =>
    idleState(itemsKey),
  );
  const sessionRef = useRef<ArrangementPreviewSession | null>(null);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const reportedErrorRef = useRef<ArrangementPreviewError | null>(null);
  const reportedFallbackRef = useRef(false);
  const pendingSeekRef = useRef<number | null>(null);
  const playInFlightRef = useRef<Promise<void> | null>(null);

  /** Resource teardown only — callable from effects (never writes state). */
  const teardown = useCallback(() => {
    unsubscribeRef.current?.();
    unsubscribeRef.current = null;
    sessionRef.current?.stop();
    sessionRef.current = null;
    reportedErrorRef.current = null;
    reportedFallbackRef.current = false;
    pendingSeekRef.current = null;
    playInFlightRef.current = null;
  }, []);

  const attachSession = useCallback(
    (session: ArrangementPreviewSession, key: string) => {
      sessionRef.current = session;
      reportedErrorRef.current = null;
      reportedFallbackRef.current = false;
      unsubscribeRef.current = session.onStateChange(() => {
        if (sessionRef.current !== session) return;
        const failure = session.getError();
        if (failure && reportedErrorRef.current !== failure) {
          reportedErrorRef.current = failure;
          onPreviewErrorRef.current(failure);
        }
        const fallback = session.isFallbackMode();
        if (fallback && !reportedFallbackRef.current) {
          reportedFallbackRef.current = true;
          onFallbackNoticeRef.current();
        }
        setState((previous) => {
          const base = baseState(previous, key);
          let status: ArrangementPreviewStatus;
          if (failure) status = "error";
          else if (base.status === "loading") status = "loading";
          else status = session.isPlaying() ? "playing" : "paused";
          return {
            key,
            status,
            errorMessage: failure ? failure.message : base.errorMessage,
            timelinePositionSeconds: session.getTimelinePositionSeconds(),
            isFallbackMode: fallback,
          };
        });
      });
    },
    [],
  );

  const play = useCallback(async () => {
    if (!active) return;
    if (playInFlightRef.current) return playInFlightRef.current;
    if (sessionRef.current?.isPlaying()) return;

    // Reject before playback: missing recording / invalid sibling
    // arrangement. Never substitute original audio, never create elements.
    const validation = validateArrangementPreviewSources(items);
    if (validation) {
      onPreviewErrorRef.current(validation);
      setState({
        ...idleState(itemsKey),
        status: "error",
        errorMessage: validation.message,
      });
      return;
    }

    // Starting preview explicitly stops the global player: the preview is not
    // a "song" track and must never share the global transport.
    player.stop();

    let session = sessionRef.current;
    if (session?.getError()) {
      // A failed session is unusable; start a fresh one from the pending seek
      // (or, absent one, from where the failed session stopped).
      const previousPosition = session.getTimelinePositionSeconds();
      const pending = pendingSeekRef.current;
      teardown();
      pendingSeekRef.current = pending ?? previousPosition;
      session = null;
    }
    if (!session) {
      session = createArrangementPreviewSession(items);
      attachSession(session, itemsKey);
      const pending = pendingSeekRef.current;
      pendingSeekRef.current = null;
      if (pending !== null) {
        session.seek(pending);
      }
    }
    const currentSession = session;
    setState((previous) => ({
      ...baseState(previous, itemsKey),
      status: "loading",
      errorMessage: null,
    }));

    const run = (async () => {
      try {
        await currentSession.play();
      } catch (err) {
        if (sessionRef.current !== currentSession) return;
        // Contained rejection: a throwing play() must surface as a reported
        // failure, never an unhandled rejection stuck on "loading".
        const failure: ArrangementPreviewError = currentSession.getError() ?? {
          kind: "playback-failed",
          itemId: null,
          message:
            err instanceof Error ? err.message : "Preview playback failed",
        };
        if (reportedErrorRef.current !== failure) {
          reportedErrorRef.current = failure;
          onPreviewErrorRef.current(failure);
        }
        setState((previous) => {
          const base = baseState(previous, itemsKey);
          return {
            key: itemsKey,
            status: "error",
            errorMessage: failure.message,
            timelinePositionSeconds: currentSession.getTimelinePositionSeconds(),
            isFallbackMode:
              currentSession.isFallbackMode() || base.isFallbackMode,
          };
        });
        return;
      }
      if (sessionRef.current !== currentSession) return;
      const failure = currentSession.getError();
      setState((previous) => {
        const base = baseState(previous, itemsKey);
        return {
          key: itemsKey,
          status: failure
            ? "error"
            : currentSession.isPlaying()
              ? "playing"
              : "paused",
          errorMessage: failure ? failure.message : null,
          timelinePositionSeconds: currentSession.getTimelinePositionSeconds(),
          isFallbackMode: currentSession.isFallbackMode() || base.isFallbackMode,
        };
      });
    })();
    playInFlightRef.current = run;
    try {
      await run;
    } finally {
      if (playInFlightRef.current === run) {
        playInFlightRef.current = null;
      }
    }
  }, [active, items, itemsKey, player, attachSession, teardown]);

  const pause = useCallback(() => {
    const session = sessionRef.current;
    if (!session) return;
    session.pause();
    const failure = session.getError();
    setState((previous) => {
      const base = baseState(previous, itemsKey);
      return {
        ...base,
        status: failure ? "error" : "paused",
        errorMessage: failure ? failure.message : base.errorMessage,
        timelinePositionSeconds: session.getTimelinePositionSeconds(),
      };
    });
  }, [itemsKey]);

  const stop = useCallback(() => {
    teardown();
    setState(idleState(itemsKey));
  }, [itemsKey, teardown]);

  const seek = useCallback(
    (seconds: number) => {
      const target = Math.min(
        Math.max(
          typeof seconds === "number" && Number.isFinite(seconds) ? seconds : 0,
          0,
        ),
        timeline.totalSeconds,
      );
      const session = sessionRef.current;
      if (session && !session.getError()) {
        session.seek(target);
        setState((previous) => ({
          ...baseState(previous, itemsKey),
          timelinePositionSeconds: session.getTimelinePositionSeconds(),
        }));
      } else {
        pendingSeekRef.current = target;
        setState((previous) => ({
          ...baseState(previous, itemsKey),
          timelinePositionSeconds: target,
        }));
      }
    },
    [itemsKey, timeline.totalSeconds],
  );

  const startAt = useCallback(
    async (itemId: string) => {
      const item = timeline.items.find((entry) => entry.itemId === itemId);
      if (!item) {
        onPreviewErrorRef.current({
          kind: "playback-failed",
          itemId,
          message: `Unknown arrangement item ${itemId}`,
        });
        return;
      }
      // "Start here": audition from that item's start, playing if idle/paused.
      seek(item.startSeconds);
      if (!sessionRef.current?.isPlaying()) {
        await play();
      }
    },
    [timeline, seek, play],
  );

  // Timeline-input change, sheet close, and unmount are the full-cleanup
  // triggers: timers, listeners, elements, graph, AudioContext.
  useEffect(() => {
    teardown();
  }, [itemsKey, teardown]);

  useEffect(() => {
    if (active) return;
    teardown();
    // Closing the sheet must leave a replayable idle view; a stale "playing"
    // status would make the reopened sheet claim audio it already tore down.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the reset is the point of reopening correctly; no render-derived alternative survives a prop flip back to active
    setState((previous) =>
      previous.key === itemsKey && previous.status !== "idle"
        ? idleState(itemsKey)
        : previous
    );
  }, [active, itemsKey, teardown]);

  useEffect(() => {
    return () => {
      teardown();
    };
  }, [teardown]);

  // Stale-key / inactive state renders as a fresh idle view; the close effect
  // additionally normalizes the stored state so a reopen starts idle.
  const isLive = active && state.key === itemsKey;
  return {
    status: isLive ? state.status : "idle",
    errorMessage: isLive ? state.errorMessage : null,
    timelinePositionSeconds: isLive ? state.timelinePositionSeconds : 0,
    totalSeconds: validationError ? 0 : timeline.totalSeconds,
    play,
    pause,
    stop,
    seek,
    startAt,
    isFallbackMode: isLive ? state.isFallbackMode : false,
  };
}
