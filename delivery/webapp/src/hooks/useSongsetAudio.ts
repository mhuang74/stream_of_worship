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
  type ArrangementTimeline,
  type TimelineItemSource,
} from "@/lib/arrangements";
import {
  createSongsetAudioSession,
  glueAudioUrl,
  validateSongsetAudioSources,
  type SongsetAudioError,
  type SongsetAudioGlueFailure,
  type SongsetAudioProgress,
  type SongsetAudioSession,
} from "@/lib/audio/songset-audio";
import { useAudioPlayerContext } from "@/contexts/AudioPlayerContext";

// The Listen dialog and the editor sheet keep one import surface for the
// transport's error contract.
export type {
  SongsetAudioError,
  SongsetAudioErrorKind,
  SongsetAudioGlueFailure,
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

/** One locally unavailable Glue junction (playback already falls back). */
export interface SongsetGlueFailure {
  takeId: string;
  fromItemId: string | null;
  toItemId: string;
}

export interface UseSongsetAudioResult {
  status: SongsetAudioStatus;
  errorMessage: string | null;
  timelinePositionSeconds: number;
  totalSeconds: number;
  /** Effective timeline after any local Glue fallback. */
  timeline: ArrangementTimeline;
  /** Glue junctions whose audio failed locally; ordinary timing is restored. */
  glueFailures: SongsetGlueFailure[];
  /** Snapshot of the session's in-flight prepare state, or null. */
  progress: SongsetAudioProgress | null;
  play: () => Promise<void>;
  pause: () => void;
  stop: () => void;
  seek: (seconds: number) => void;
  startAtSeconds: (seconds: number) => Promise<void>;
  /** Decodes the item at `seconds` plus its successor's junction before playing. */
  prepareAt: (seconds: number) => Promise<void>;
  /** Recreates the session at the current position and replays. */
  retry: () => Promise<void>;
  /** Re-fetches the same immutable take; rebuilds only when it is reachable. */
  retryGlueAudio: (takeId: string) => Promise<void>;
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

  // A local Glue failure is remembered by immutable take id. Only that take's
  // glue fields are stripped; every other effective Glue keeps playing, and the
  // ordinary gap/crossfade is restored at the failed junction because the
  // single layout definition sees no take there.
  const [glueFailures, setGlueFailures] = useState<SongsetGlueFailure[]>([]);
  const failedGlueTakeIds = useMemo(
    () => new Set(glueFailures.map((failure) => failure.takeId)),
    [glueFailures],
  );

  // A failure belongs to the take that failed. When the caller swaps the
  // Songset/selection and that take is no longer referenced, drop the warning.
  // The prune must also forget the failure (not merely hide it): re-selecting
  // the same take later must not resurrect a stale warning, which a derived
  // filter would do.
  useEffect(() => {
    const present = new Set(
      items
        .flatMap((item) => [item.glueTakeId, item.intro?.id, item.outro?.id])
        .filter((takeId): takeId is string => typeof takeId === "string"),
    );
    // eslint-disable-next-line react-hooks/set-state-in-effect -- forgets failures for takes the items no longer reference
    setGlueFailures((previous) => {
      const kept = previous.filter((failure) => present.has(failure.takeId));
      return kept.length === previous.length ? previous : kept;
    });
  }, [items]);
  const effectiveItems = useMemo(
    () =>
      items.map((item) => ({
        ...item,
        ...(item.glueTakeId && failedGlueTakeIds.has(item.glueTakeId)
          ? { glueTakeId: null, glueSeconds: null } : {}),
        ...(item.intro && failedGlueTakeIds.has(item.intro.id) ? { intro: undefined } : {}),
        ...(item.outro && failedGlueTakeIds.has(item.outro.id) ? { outro: undefined } : {}),
      })),
    [items, failedGlueTakeIds],
  );

  // Content identity: reorder/repeat/omit changes this string, which must
  // stop the session even though callers may hand over a fresh array on
  // unrelated renders.
  const itemsKey = useMemo(() => JSON.stringify(effectiveItems), [effectiveItems]);
  const timeline = useMemo(
    () => buildArrangementTimeline(effectiveItems),
    [effectiveItems],
  );
  const validationError = useMemo(
    () => validateSongsetAudioSources(effectiveItems),
    [effectiveItems],
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

  // A local Glue failure rebuilds the visible timeline without that take. The
  // position and playing state are carried across so the audition continues
  // with ordinary timing instead of stopping.
  const glueRebuildRef = useRef<{ position: number; resume: boolean } | null>(null);
  const handleGlueFailure = useCallback((failure: SongsetAudioGlueFailure) => {
    console.error(
      `Glue audio failed (take ${failure.takeId}, ` +
        `${failure.fromItemId ?? "none"} -> ${failure.toItemId}): ${failure.message}`,
    );
    const session = sessionRef.current;
    glueRebuildRef.current = {
      position: session?.getTimelinePositionSeconds() ?? 0,
      resume: session?.isPlaying() ?? false,
    };
    setGlueFailures((previous) =>
      previous.some((entry) => entry.takeId === failure.takeId)
        ? previous
        : [
            ...previous,
            {
              takeId: failure.takeId,
              fromItemId: failure.fromItemId,
              toItemId: failure.toItemId,
            },
          ],
    );
  }, []);
  const handleGlueFailureRef = useRef(handleGlueFailure);
  useEffect(() => {
    handleGlueFailureRef.current = handleGlueFailure;
  }, [handleGlueFailure]);

  const play = useCallback(async () => {
    if (!active) return;
    if (playInFlightRef.current) return playInFlightRef.current;
    if (sessionRef.current?.isPlaying()) return;

    // Reject before playback: missing recording / invalid sibling
    // arrangement. Never create a session or start a fetch.
    const validation = validateSongsetAudioSources(effectiveItems);
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
      session = createSongsetAudioSession(effectiveItems, {
        onGlueFailure: (failure) => handleGlueFailureRef.current(failure),
      });
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
  }, [active, effectiveItems, itemsKey, player, attachSession, teardown, timeline.totalSeconds]);

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

  const prepareAt = useCallback(
    async (seconds: number) => {
      if (!active) return;
      // The first preview after opening the sheet has no session yet, and a
      // preview started without one only decodes the outgoing item. Build the
      // session here so the clip is ready before the transport starts, exactly
      // as a later preview finds it. Validation failures stay `play()`'s job.
      if (validationError !== null) return;
      const existing = sessionRef.current;
      // A session that failed is `play()`'s to replace: tearing it down here
      // would leave `stoppedRef` set and silence the fresh session until the
      // transport actually starts.
      if (existing !== null) {
        if (existing.getError()) return;
        await existing.prepareAt(seconds);
        return;
      }
      const session = createSongsetAudioSession(effectiveItems, {
        onGlueFailure: (failure) => handleGlueFailureRef.current(failure),
      });
      attachSession(session, itemsKey);
      await session.prepareAt(seconds);
    },
    [active, attachSession, effectiveItems, itemsKey, validationError],
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

  const playRef = useRef(play);
  useEffect(() => {
    playRef.current = play;
  });

  /**
   * Re-fetches the same immutable take. The local fallback is dropped only
   * after the take is reachable again; a failed retry leaves ordinary timing
   * and the warning untouched. Preview/Listen never trigger provider work.
   */
  const retryGlueAudio = useCallback(
    async (takeId: string) => {
      if (!glueFailures.some((failure) => failure.takeId === takeId)) return;
      try {
        // Probe with a one-byte range: the proxy forwards Range, so a reachable
        // take answers 206 without streaming the whole staged WAV.
        const response = await fetch(glueAudioUrl(takeId), {
          cache: "no-store",
          headers: { Range: "bytes=0-0" },
        });
        if (!response.ok) return;
        void response.body?.cancel().catch(() => undefined);
      } catch {
        return;
      }
      const session = sessionRef.current;
      glueRebuildRef.current = {
        position:
          session?.getTimelinePositionSeconds() ?? state.timelinePositionSeconds,
        resume: session?.isPlaying() ?? false,
      };
      setGlueFailures((previous) =>
        previous.filter((failure) => failure.takeId !== takeId),
      );
    },
    [glueFailures, state.timelinePositionSeconds],
  );

  // Timeline-input change, dialog close, and unmount are the full-cleanup
  // triggers: sources, ticker, buffers, and the session itself.
  useEffect(() => {
    teardown();
  }, [itemsKey, teardown]);

  // A local Glue failure or a successful retry changes `itemsKey`; continue
  // the audition at the same position with the rebuilt timeline.
  useEffect(() => {
    const rebuild = glueRebuildRef.current;
    if (!rebuild) return;
    glueRebuildRef.current = null;
    pendingSeekRef.current = rebuild.position;
    if (rebuild.resume) {
      void playRef.current();
      return;
    }
    // The rebuilt timeline owns this position; it cannot be derived from the
    // new items alone.
    setState((previous) => ({
      ...baseState(previous, itemsKey),
      timelinePositionSeconds: rebuild.position,
    }));
  }, [itemsKey]);

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
    timeline,
    glueFailures,
    progress: isLive ? state.progress : null,
    play,
    pause,
    stop,
    seek,
    startAtSeconds,
    prepareAt,
    retry,
    retryGlueAudio,
  };
}
