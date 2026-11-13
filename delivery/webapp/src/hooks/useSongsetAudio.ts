"use client";

/**
 * React binding for the browser Songset audio transport (spec §3.5).
 *
 * Lifecycle preserves the prior editor transport contract: the session is
 * created on the first `play`, torn down on
 * `itemsKey` change (`JSON.stringify(items)`), on `active=false`, and on
 * unmount; `player.stop()` runs before the session starts; errors flow through
 * a latest-ref and are reported once per distinct error object. `stop()` only
 * rewinds: the session keeps its decoded clips so replay needs neither
 * network nor decode. The new engine adds byte-level `progress`, which is
 * snapshotted on every state change.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  buildArrangementTimeline,
  type TimelineItemSource,
} from "@/lib/arrangements";
import {
  createSongsetAudioSession,
  validateSongsetAudioSources,
  type SongsetAudioError,
  type SongsetAudioProgress,
  type SongsetAudioSession,
} from "@/lib/audio/songset-audio";
import { useAudioPlayerContext } from "@/contexts/AudioPlayerContext";

// The Listen dialog and the editor sheet keep one import surface for the
// transport's error contract.
export type {
  SongsetAudioError,
  SongsetAudioErrorKind,
} from "@/lib/audio/songset-audio";

export type SongsetAudioStatus =
  | "idle"
  | "loading"
  | "playing"
  | "paused"
  | "error";

export interface UseSongsetAudioOptions {
  /** Timeline inputs in Songset order. */
  items: TimelineItemSource[];
  /** False when the dialog is closed; tears the session down. */
  active: boolean;
  onPreviewError: (error: SongsetAudioError) => void;
}

export interface UseSongsetAudioResult {
  status: SongsetAudioStatus;
  errorMessage: string | null;
  timelinePositionSeconds: number;
  totalSeconds: number;
  /** Snapshot of the session's in-flight prepare state, or null. */
  progress: SongsetAudioProgress | null;
  play: () => Promise<void>;
  pause: () => void;
  stop: () => void;
  seek: (seconds: number) => void;
  startAtSeconds: (seconds: number) => Promise<void>;
  /** Recreates the session at the current position and replays. */
  retry: () => Promise<void>;
}

interface SongsetAudioHookState {
  /** Serialized `items` the state belongs to; stale keys render as idle. */
  key: string;
  status: SongsetAudioStatus;
  errorMessage: string | null;
  timelinePositionSeconds: number;
  progress: SongsetAudioProgress | null;
}

function idleState(key: string): SongsetAudioHookState {
  return {
    key,
    status: "idle",
    errorMessage: null,
    timelinePositionSeconds: 0,
    progress: null,
  };
}

/** Carries playback state forward only while it belongs to the same inputs. */
function baseState(
  previous: SongsetAudioHookState,
  key: string,
): SongsetAudioHookState {
  return previous.key === key ? previous : idleState(key);
}

export function useSongsetAudio(
  options: UseSongsetAudioOptions,
): UseSongsetAudioResult {
  const { items, active } = options;
  const player = useAudioPlayerContext();

  // Latest-ref pattern: async transport callbacks must not capture stale
  // render closures.
  const onPreviewErrorRef = useRef(options.onPreviewError);
  useEffect(() => {
    onPreviewErrorRef.current = options.onPreviewError;
  });

  // Content identity: reorder/repeat/omit changes this string, which must
  // stop the session even though callers may hand over a fresh array on
  // unrelated renders.
  const itemsKey = useMemo(() => JSON.stringify(items), [items]);
  const timeline = useMemo(() => buildArrangementTimeline(items), [items]);
  const validationError = useMemo(
    () => validateSongsetAudioSources(items),
    [items],
  );

  const [state, setState] = useState<SongsetAudioHookState>(() =>
    idleState(itemsKey),
  );
  const sessionRef = useRef<SongsetAudioSession | null>(null);
  const unsubscribeRef = useRef<(() => void) | null>(null);
  const reportedErrorRef = useRef<SongsetAudioError | null>(null);
  const pendingSeekRef = useRef<number | null>(null);
  const playInFlightRef = useRef<Promise<void> | null>(null);
  const stoppedRef = useRef(false);

  /** Resource teardown only — callable from effects (never writes state). */
  const teardown = useCallback(() => {
    stoppedRef.current = true;
    unsubscribeRef.current?.();
    unsubscribeRef.current = null;
    sessionRef.current?.destroy();
    sessionRef.current = null;
    reportedErrorRef.current = null;
    pendingSeekRef.current = null;
    playInFlightRef.current = null;
  }, []);

  const attachSession = useCallback(
    (session: SongsetAudioSession, key: string) => {
      sessionRef.current = session;
      reportedErrorRef.current = null;
      unsubscribeRef.current = session.onStateChange(() => {
        if (sessionRef.current !== session) return;
        if (stoppedRef.current) return;
        const failure = session.getError();
        if (failure && reportedErrorRef.current !== failure) {
          reportedErrorRef.current = failure;
          onPreviewErrorRef.current(failure);
        }
        setState((previous) => {
          const base = baseState(previous, key);
          let status: SongsetAudioStatus;
          if (failure) status = "error";
          else if (base.status === "loading") status = "loading";
          else status = session.isPlaying() ? "playing" : "paused";
          return {
            key,
            status,
            errorMessage: failure ? failure.message : base.errorMessage,
            timelinePositionSeconds: session.getTimelinePositionSeconds(),
            progress: session.getProgress(),
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
    // arrangement. Never create a session or start a fetch.
    const validation = validateSongsetAudioSources(items);
    if (validation) {
      onPreviewErrorRef.current(validation);
      setState({
        ...idleState(itemsKey),
        status: "error",
        errorMessage: validation.message,
      });
      return;
    }

    // Starting the Listen transport explicitly stops the global player: the
    // two transports must never sound together.
    player.stop();

    let session = sessionRef.current;
    // Play at the timeline end restarts the Songset instead of rebuilding an
    // empty graph.
    const atEnd =
      timeline.totalSeconds > 0 &&
      session !== null &&
      session.getTimelinePositionSeconds() >= timeline.totalSeconds - 0.05;
    if (atEnd) {
      pendingSeekRef.current = 0;
      if (!session!.getError()) session!.seek(0);
    }
    if (session?.getError()) {
      // A failed session is unusable; start a fresh one from the pending seek
      // (or, absent one, from where the failed session stopped).
      const previousPosition = session.getTimelinePositionSeconds();
      const pending = pendingSeekRef.current;
      teardown();
      pendingSeekRef.current = pending ?? previousPosition;
      session = null;
    }
    // Re-arm: a previous Stop (or the teardown above) marked this stopped, so
    // this play's own notifications must count again.
    stoppedRef.current = false;
    if (!session) {
      session = createSongsetAudioSession(items);
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
      progress: currentSession.getProgress(),
    }));

    const run = (async () => {
      try {
        await currentSession.play();
      } catch (err) {
        if (sessionRef.current !== currentSession) return;
        if (stoppedRef.current) return;
        // Contained rejection: a throwing play() must surface as a reported
        // failure, never an unhandled rejection stuck on "loading".
        const failure: SongsetAudioError = currentSession.getError() ?? {
          kind: "playback-failed",
          itemId: null,
          message:
            err instanceof Error ? err.message : "Songset playback failed",
        };
        if (reportedErrorRef.current !== failure) {
          reportedErrorRef.current = failure;
          onPreviewErrorRef.current(failure);
        }
        setState({
          key: itemsKey,
          status: "error",
          errorMessage: failure.message,
          timelinePositionSeconds: currentSession.getTimelinePositionSeconds(),
          progress: currentSession.getProgress(),
        });
        throw err;
      }
      if (sessionRef.current !== currentSession) return;
      if (stoppedRef.current) return;
      const failure = currentSession.getError();
      setState({
        key: itemsKey,
        status: failure
          ? "error"
          : currentSession.isPlaying()
            ? "playing"
            : "paused",
        errorMessage: failure ? failure.message : null,
        timelinePositionSeconds: currentSession.getTimelinePositionSeconds(),
        progress: currentSession.getProgress(),
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
  }, [active, items, itemsKey, player, attachSession, teardown, timeline.totalSeconds]);

  const pause = useCallback(() => {
    const session = sessionRef.current;
    if (!session) return;
    // A paused session that was stopped stays idle: the retained session is
    // for replay, not for resurrecting a paused view.
    if (stoppedRef.current) return;
    session.pause();
    const failure = session.getError();
    setState((previous) => {
      const base = baseState(previous, itemsKey);
      return {
        ...base,
        status: failure ? "error" : "paused",
        errorMessage: failure ? failure.message : base.errorMessage,
        timelinePositionSeconds: session.getTimelinePositionSeconds(),
        progress: session.getProgress(),
      };
    });
  }, [itemsKey]);

  // Stop keeps the session (and its decoded clips) so the next play neither
  // refetches nor redecodes; only teardown releases it.
  const stop = useCallback(() => {
    stoppedRef.current = true;
    pendingSeekRef.current = null;
    playInFlightRef.current = null;
    sessionRef.current?.stop();
    setState(idleState(itemsKey));
  }, [itemsKey]);

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
          progress: session.getProgress(),
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

  const startAtSeconds = useCallback(
    async (seconds: number) => {
      seek(seconds);
      if (!sessionRef.current?.isPlaying()) {
        await play();
      }
    },
    [seek, play],
  );

  const retry = useCallback(async () => {
    if (!active) return;
    const position =
      sessionRef.current?.getTimelinePositionSeconds() ??
      state.timelinePositionSeconds;
    teardown();
    pendingSeekRef.current = position;
    await play();
  }, [active, play, state.timelinePositionSeconds, teardown]);

  // Timeline-input change, dialog close, and unmount are the full-cleanup
  // triggers: sources, ticker, buffers, and the session itself.
  useEffect(() => {
    teardown();
  }, [itemsKey, teardown]);

  useEffect(() => {
    if (active) return;
    teardown();
    // Closing the dialog must leave a replayable idle view; a stale "playing"
    // status would make the reopened dialog claim audio it already tore down.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the reset is the point of reopening correctly; no render-derived alternative survives a prop flip back to active
    setState((previous) =>
      previous.key === itemsKey && previous.status !== "idle"
        ? idleState(itemsKey)
        : previous,
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
    progress: isLive ? state.progress : null,
    play,
    pause,
    stop,
    seek,
    startAtSeconds,
    retry,
  };
}
