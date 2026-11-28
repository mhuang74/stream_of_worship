/**
 * Whole-Songset audition engine: decodes one Recording at a time, copies its
 * arranged source ranges into small clip buffers, and schedules those buffers
 * on the AudioContext clock through two series gain nodes per item.
 *
 * ADR-0012 pins the architecture: no media elements, no analyser/RMS
 * fallback, no server-side cache, no loudness normalization. Crossfade
 * ownership matches the render worker: the incoming item owns its fade-in and
 * the successor owns the outgoing fade, so the two series gains multiply
 * exactly like the worker's two sequential `afade` filters.
 *
 * Layout is never re-derived here: `buildArrangementTimeline` is the single
 * definition shared with the editor, the Listen dialog, and the render worker.
 */

import {
  buildArrangementTimeline,
  findTimelineItemIndex,
  parseSongArrangement,
  validateSongArrangement,
  type ArrangementTimeline,
  type ArrangementTimelineItem,
  type TimelineItemSource,
  type TimelineGlueWindow,
} from "@/lib/arrangements";
import { getAudioContext } from "@/lib/audio/audio-context";
import {
  fetchAudioBytes,
  recordingAudioUrl,
  sharedAudioBytesCache,
  type AudioBytesCache,
} from "@/lib/audio/audio-bytes-cache";
import {
  incomingEnvelope,
  outgoingEnvelope,
  scheduleEnvelope,
} from "@/lib/audio/envelopes";

export type SongsetAudioErrorKind =
  | "no-recording"
  | "invalid-arrangement"
  | "playback-failed";

export interface SongsetAudioError {
  kind: SongsetAudioErrorKind;
  itemId: string | null;
  message: string;
}

export interface SongsetAudioProgress {
  itemId: string;
  itemIndex: number;
  itemCount: number;
  phase: "fetching" | "decoding" | "ready";
  receivedBytes: number;
  totalBytes: number | null;
}

/** One local Glue load failure, reported without stopping the audition. */
export interface SongsetAudioGlueFailure {
  takeId: string;
  fromItemId: string | null;
  toItemId: string;
  message: string;
}

/** Same-origin proxy URL for one immutable AI-transition take. */
export function glueAudioUrl(takeId: string): string {
  return `/api/glues/${encodeURIComponent(takeId)}/audio`;
}

export interface SongsetAudioSessionOptions {
  endAtSeconds?: number;
  audioUrlFor?: (hashPrefix: string) => string;
  glueAudioUrlFor?: (takeId: string) => string;
  createAudioContext?: () => AudioContext;
  fetchArrayBuffer?: (
    url: string,
    onProgress: (receivedBytes: number, totalBytes: number | null) => void,
  ) => Promise<ArrayBuffer>;
  tickIntervalMs?: number;
  /** Soft target for retained clip PCM; default 480 s (issue #296). */
  retainedBudgetSeconds?: number;
  /** Byte cache shared across sessions; defaults to the process-wide cache. */
  audioBytesCache?: AudioBytesCache;
  /** Local Glue fetch/decode failure; the session keeps playing. */
  onGlueFailure?: (failure: SongsetAudioGlueFailure) => void;
}

export interface SongsetAudioSession {
  readonly timeline: ArrangementTimeline;
  readonly totalSeconds: number;
  play(): Promise<void>;
  pause(): void;
  /** Halts playback and rewinds, keeping decoded clips for replay. */
  stop(): void;
  /** Terminal teardown: releases decoded clips and listeners. */
  destroy(): void;
  seek(seconds: number): void;
  startAtSeconds(seconds: number): Promise<void>;
  /**
   * Decodes the item at `seconds` and the one after it, so a window that
   * starts before a junction has its AI take ready before the playhead
   * reaches it. Idempotent; failures propagate untouched.
   */
  prepareAt(seconds: number): Promise<void>;
  getTimelinePositionSeconds(): number;
  isPlaying(): boolean;
  getProgress(): SongsetAudioProgress | null;
  getError(): SongsetAudioError | null;
  onStateChange(listener: () => void): () => void;
}

const DEFAULT_TICK_INTERVAL_MS = 200;

const DEFAULT_RETAINED_BUDGET_SECONDS = 480;

const PROGRESS_NOTIFY_THROTTLE_MS = 100;

function itemRecordingDurationSeconds(item: TimelineItemSource): number | null {
  const flat = item.recordingDurationSeconds;
  const value = typeof flat === "number" ? flat : item.recording?.durationSeconds;
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : null;
}

/**
 * Pre-playback rejection, moved verbatim from `validateArrangementPreviewSources`.
 * Pure: no React, no Web Audio, no crypto, no network.
 */
export function validateSongsetAudioSources(
  items: TimelineItemSource[],
): SongsetAudioError | null {
  if (items.length === 0) {
    return {
      kind: "invalid-arrangement",
      itemId: null,
      message: "Songset has no items to preview",
    };
  }
  for (const item of items) {
    if (item.recordingHashPrefix == null) {
      return {
        kind: "no-recording",
        itemId: item.itemId,
        message: `Item ${item.itemId} has no Recording; preview is unavailable`,
      };
    }
    const duration = itemRecordingDurationSeconds(item);
    const parsed = parseSongArrangement(item.arrangementSteps ?? null);
    if (!parsed.present) {
      if (duration === null) {
        return {
          kind: "no-recording",
          itemId: item.itemId,
          message: `Recording duration for item ${item.itemId} is unavailable; preview is unavailable`,
        };
      }
      continue;
    }
    const validated = validateSongArrangement(
      item.arrangementSteps ?? null,
      item.recordingSections ?? null,
      item.recordingBeats ?? [],
      duration ?? 0,
    );
    if (!validated.ok) {
      return {
        kind: "invalid-arrangement",
        itemId: item.itemId,
        message: `Arrangement for item ${item.itemId} is invalid or unavailable; repair the analysis or reset the arrangement`,
      };
    }
  }
  const timeline = buildArrangementTimeline(items);
  for (const timelineItem of timeline.items) {
    if (timelineItem.clips.length === 0) {
      return {
        kind: "invalid-arrangement",
        itemId: timelineItem.itemId,
        message: `Arrangement for item ${timelineItem.itemId} has no playable sections`,
      };
    }
  }
  return null;
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return String(error);
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

interface StoredClip {
  buffer: AudioBuffer;
  startSeconds: number;
  endSeconds: number;
}

interface ActivePrepare {
  index: number;
  promise: Promise<void>;
}

export function createSongsetAudioSession(
  items: TimelineItemSource[],
  options: SongsetAudioSessionOptions = {},
): SongsetAudioSession {
  const timeline = buildArrangementTimeline(items);
  const totalSeconds = Math.min(timeline.totalSeconds, options.endAtSeconds ?? Infinity);
  const itemCount = timeline.items.length;
  const audioUrlFor = options.audioUrlFor ?? recordingAudioUrl;
  const glueUrlFor = options.glueAudioUrlFor ?? glueAudioUrl;
  const createAudioContext = options.createAudioContext ?? getAudioContext;
  const fetchArrayBuffer = options.fetchArrayBuffer ?? fetchAudioBytes;
  const audioBytesCache = options.audioBytesCache ?? sharedAudioBytesCache;
  const tickIntervalMs = options.tickIntervalMs ?? DEFAULT_TICK_INTERVAL_MS;
  const configuredBudgetSeconds = options.retainedBudgetSeconds;
  const retainedBudgetSeconds =
    typeof configuredBudgetSeconds === "number" &&
    Number.isFinite(configuredBudgetSeconds)
      ? Math.max(0, configuredBudgetSeconds)
      : DEFAULT_RETAINED_BUDGET_SECONDS;

  let context: AudioContext | null = null;
  function getContext(): AudioContext {
    if (!context) context = createAudioContext();
    return context;
  }

  // In-flight progress only, never a status field.
  let progress: SongsetAudioProgress | null = null;
  let lastProgressNotification = Number.NEGATIVE_INFINITY;
  let error: SongsetAudioError | null = null;

  const prepared = new Map<number, StoredClip[]>();
  let activePrepare: ActivePrepare | null = null;

  // Decoded Glue buffers keyed by immutable take id. `failedGlueTakes` stops
  // repeated fetches for a take that already failed locally; the hook rebuilds
  // the timeline without those takes.
  const preparedGlue = new Map<string, AudioBuffer>();
  const glueInFlight = new Map<string, Promise<void>>();
  const failedGlueTakes = new Set<string>();

  interface ScheduledGraph {
    sources: AudioBufferSourceNode[];
    gains: GainNode[];
  }
  const scheduledGraphs = new Map<number, ScheduledGraph>();

  let playing = false;
  let inert = false;
  let awaitingDecode = false;
  let position = 0;
  let startPosition = 0;
  let startedAtContextTime: number | null = null;
  let ticker: ReturnType<typeof setInterval> | null = null;
  let playbackGeneration = 0;
  let playPending: Promise<void> | null = null;

  const listeners = new Set<() => void>();

  function notify(): void {
    for (const listener of [...listeners]) {
      try {
        listener();
      } catch {
        // A subscriber must never corrupt transport state.
      }
    }
  }

  function notifyProgressThrottled(): void {
    const now = Date.now();
    if (now - lastProgressNotification < PROGRESS_NOTIFY_THROTTLE_MS) return;
    lastProgressNotification = now;
    notify();
  }

  function clockPosition(): number {
    if (awaitingDecode) return clamp(position, 0, totalSeconds);
    if (!playing || startedAtContextTime === null || context === null) {
      return clamp(position, 0, totalSeconds);
    }
    return clamp(
      startPosition + (context.currentTime - startedAtContextTime),
      0,
      totalSeconds,
    );
  }

  function stopTicker(): void {
    if (ticker !== null) {
      clearInterval(ticker);
      ticker = null;
    }
  }

  function startTicker(): void {
    stopTicker();
    ticker = setInterval(tick, tickIntervalMs);
  }

  function teardownItemGraph(graph: ScheduledGraph): void {
    for (const source of graph.sources) {
      try {
        source.stop();
      } catch {
        // Already stopped / never started; teardown stays total.
      }
      source.disconnect();
      source.buffer = null;
    }
    for (const gain of graph.gains) gain.disconnect();
  }

  function teardownGraph(): void {
    for (const graph of scheduledGraphs.values()) teardownItemGraph(graph);
    scheduledGraphs.clear();
  }

  function haltPlayback(): void {
    if (playing) position = clockPosition();
    playing = false;
    awaitingDecode = false;
    startedAtContextTime = null;
    playbackGeneration += 1;
    teardownGraph();
    stopTicker();
  }

  function reportError(
    kind: SongsetAudioErrorKind,
    itemId: string | null,
    message: string,
  ): void {
    haltPlayback();
    progress = null;
    error = { kind, itemId, message };
    notify();
  }

  function itemIndexAt(seconds: number): number {
    if (itemCount === 0) return 0;
    return Math.max(0, findTimelineItemIndex(timeline, seconds));
  }

  function storedSeconds(index: number): number {
    const clips = prepared.get(index);
    if (!clips) return 0;
    let total = 0;
    for (const clip of clips) total += clip.endSeconds - clip.startSeconds;
    return total;
  }

  function retainedSeconds(): number {
    let total = 0;
    for (const index of prepared.keys()) total += storedSeconds(index);
    return total;
  }

  /**
   * Issue #296: drop retained clips that can no longer sound before a prepare
   * adds item `index`. Only finished items (timeline end at or before
   * `atSeconds`) are eligible, oldest first, until the soft budget is met.
   * The item being prepared, the item at the position, and every later item
   * are never dropped (a later item ends after the position by definition),
   * and the last retained item always stays. The budget is a target, not a
   * guard: this single pass runs, then the caller prepares anyway.
   */
  function evictFinishedForPrepare(index: number, atSeconds: number): void {
    if (prepared.size <= 1) return;
    const requestedSeconds = timeline.items[index]?.durationSeconds ?? 0;
    let retained = retainedSeconds();
    if (retained + requestedSeconds <= retainedBudgetSeconds) return;
    const currentIndex = itemIndexAt(atSeconds);
    const candidates = [...prepared.keys()].sort((a, b) => a - b);
    for (const candidate of candidates) {
      if (retained + requestedSeconds <= retainedBudgetSeconds) break;
      if (prepared.size <= 1) break;
      if (candidate === index || candidate === currentIndex) continue;
      const candidateItem = timeline.items[candidate];
      if (!candidateItem) continue;
      if (
        candidateItem.startSeconds + candidateItem.durationSeconds > atSeconds
      ) {
        continue;
      }
      retained -= storedSeconds(candidate);
      prepared.delete(candidate);
      const graph = scheduledGraphs.get(candidate);
      if (graph) {
        teardownItemGraph(graph);
        scheduledGraphs.delete(candidate);
      }
    }
  }

  function scheduleItem(
    index: number,
    atSeconds: number,
    originContextTime: number,
    ctx: AudioContext,
  ): void {
    if (scheduledGraphs.has(index)) return;
    const item = timeline.items[index];
    const storedClips = prepared.get(index);
    if (!item || !storedClips) return;

    const gainIn = ctx.createGain();
    const gainOut = ctx.createGain();
    gainIn.connect(gainOut);
    gainOut.connect(ctx.destination);
    const nextItem: ArrangementTimelineItem | null =
      timeline.items[index + 1] ?? null;
    scheduleEnvelope(
      gainIn.gain,
      incomingEnvelope(item),
      atSeconds,
      originContextTime,
    );
    scheduleEnvelope(
      gainOut.gain,
      outgoingEnvelope(item, nextItem),
      atSeconds,
      originContextTime,
    );
    const graph: ScheduledGraph = { sources: [], gains: [gainIn, gainOut] };
    scheduledGraphs.set(index, graph);

    // The Glue plays on a neutral gain: its 20 ms fades and gain staging were
    // baked into the staged WAV, so no extra envelope is applied. A seek that
    // lands inside the window keeps the correct buffer offset.
    for (const glueWindow of [item.glueWindowBefore, item.glueWindowAfter]) {
      if (!glueWindow || glueWindow.endSeconds <= atSeconds) continue;
      const glueBuffer = preparedGlue.get(glueWindow.takeId);
      if (glueBuffer) {
        const glueGain = ctx.createGain();
        glueGain.gain.setValueAtTime(1, originContextTime);
        glueGain.connect(ctx.destination);
        const source = ctx.createBufferSource();
        source.buffer = glueBuffer;
        source.connect(glueGain);
        const windowStart = Math.max(atSeconds, glueWindow.startSeconds);
        const duration = Math.min(glueWindow.endSeconds, totalSeconds) - windowStart;
        if (duration > 0) {
          source.start(
            originContextTime +
              Math.max(0, glueWindow.startSeconds - atSeconds),
            clamp(atSeconds - glueWindow.startSeconds, 0, glueBuffer.duration),
            duration,
          );
          graph.sources.push(source);
          graph.gains.push(glueGain);
        } else {
          source.disconnect();
          glueGain.disconnect();
        }
      }
    }

    for (let clipIndex = 0; clipIndex < storedClips.length; clipIndex++) {
      const clip = item.clips[clipIndex];
      const stored = storedClips[clipIndex];
      if (!clip || !stored) continue;
      if (clip.endSeconds <= atSeconds) continue;
      const duration = Math.min(clip.endSeconds, totalSeconds) - Math.max(atSeconds, clip.startSeconds);
      if (duration <= 0) continue;

      const source = ctx.createBufferSource();
      source.buffer = stored.buffer;
      source.connect(gainIn);
      const when =
        originContextTime + Math.max(0, clip.startSeconds - atSeconds);
      // `stored.buffer` is a copy of this clip alone, so the start offset is
      // relative to the clip, not to the source Recording.
      const offset = clamp(
        Math.max(0, atSeconds - clip.startSeconds),
        0,
        stored.buffer.duration,
      );
      source.start(when, offset, duration);
      graph.sources.push(source);
    }
  }

  function fullRebuild(atSeconds: number): void {
    teardownGraph();
    const ctx = getContext();
    const originContextTime = ctx.currentTime;
    for (let index = 0; index < itemCount; index++) {
      const item = timeline.items[index]!;
      const itemEnd = item.startSeconds + item.durationSeconds;
      const glueEnd = item.glueWindowAfter?.endSeconds ?? item.glueWindowBefore?.endSeconds ?? itemEnd;
      if (Math.max(itemEnd, glueEnd) <= atSeconds) continue;
      if (!prepared.has(index)) continue;
      scheduleItem(index, atSeconds, originContextTime, ctx);
    }
  }

  function scheduleNewlyDecoded(index: number): void {
    if (inert || !playing || awaitingDecode) return;
    if (!prepared.has(index) || scheduledGraphs.has(index)) return;
    const item = timeline.items[index];
    if (!item) return;
    const atSeconds = clockPosition();
    if ((item.glueWindowAfter?.endSeconds ?? item.startSeconds + item.durationSeconds) <= atSeconds) return;
    const ctx = getContext();
    scheduleItem(index, atSeconds, ctx.currentTime, ctx);
  }

  function startClock(fromSeconds: number): void {
    const ctx = getContext();
    position = clamp(fromSeconds, 0, totalSeconds);
    startPosition = position;
    startedAtContextTime = ctx.currentTime;
    awaitingDecode = false;
    playing = true;
  }

  function startBackgroundPrepare(index: number): void {
    void prepare(index).then(
      () => {
        scheduleNewlyDecoded(index);
        notify();
      },
      () => {
        // reportError has already stopped playback and notified.
      },
    );
  }

  function tick(): void {
    if (inert || !playing) return;
    if (awaitingDecode) {
      // A seek is waiting on the target decode; the clock stays frozen and
      // that seek's completion owns the rebuild and clock restart.
      notifyProgressThrottled();
      return;
    }
    const atSeconds = clockPosition();
    position = atSeconds;

    if (atSeconds >= totalSeconds) {
      playing = false;
      startedAtContextTime = null;
      position = totalSeconds;
      teardownGraph();
      stopTicker();
      progress = null;
      notify();
      return;
    }

    const currentIndex = itemIndexAt(atSeconds);
    if (!prepared.has(currentIndex) && activePrepare === null) {
      startBackgroundPrepare(currentIndex);
    } else if (activePrepare === null) {
      const nextIndex = currentIndex + 1;
      if (nextIndex < itemCount && !prepared.has(nextIndex)) {
        startBackgroundPrepare(nextIndex);
      }
    }
    notifyProgressThrottled();
  }

  async function runPrepare(index: number): Promise<void> {
    const item = timeline.items[index];
    if (!item) return;
    if (item.recordingHashPrefix == null) {
      const message = `Item ${item.itemId} has no Recording; preview is unavailable`;
      reportError("no-recording", item.itemId, message);
      throw new Error(message);
    }

    const url = audioUrlFor(item.recordingHashPrefix);
    progress = {
      itemId: item.itemId,
      itemIndex: index,
      itemCount,
      phase: "fetching",
      receivedBytes: 0,
      totalBytes: null,
    };
    notify();

    try {
      const data = await audioBytesCache.load(
        item.recordingHashPrefix,
        (onProgress) => fetchArrayBuffer(url, onProgress),
        (receivedBytes, totalBytes) => {
          const current = getProgress();
          if (inert || current === null) return;
          progress = {
            ...current,
            receivedBytes,
            totalBytes: totalBytes ?? current.totalBytes,
          };
          notifyProgressThrottled();
        },
      );
      if (inert) return;
      const decodingProgress = getProgress();
      if (decodingProgress !== null) {
        progress = { ...decodingProgress, phase: "decoding" };
        notify();
      }

      const ctx = getContext();
      const decoded = await ctx.decodeAudioData(data);
      if (inert) return;

      const storedClips: StoredClip[] = item.clips.map((clip) => {
        const offset = clamp(
          Math.round(clip.sourceStartSeconds * decoded.sampleRate),
          0,
          decoded.length,
        );
        const length = Math.max(
          1,
          Math.min(
            Math.round(
              (clip.sourceEndSeconds - clip.sourceStartSeconds) *
                decoded.sampleRate,
            ),
            Math.max(1, decoded.length - offset),
          ),
        );
        const buffer = ctx.createBuffer(
          decoded.numberOfChannels,
          length,
          decoded.sampleRate,
        );
        for (let channel = 0; channel < decoded.numberOfChannels; channel++) {
          buffer.copyToChannel(
            decoded
              .getChannelData(channel)
              .subarray(offset, offset + length),
            channel,
          );
        }
        return {
          buffer,
          startSeconds: clip.startSeconds,
          endSeconds: clip.endSeconds,
        };
      });
      prepared.set(index, storedClips);
      // The Glue belongs to the incoming item's junction, so it is decoded
      // before the item becomes playable. Failure stays local.
      for (const window of [item.glueWindowBefore, item.glueWindowAfter]) {
        if (window) await prepareGlueFor(index, window);
      }

      const readyProgress = getProgress();
      if (readyProgress !== null) {
        progress = { ...readyProgress, phase: "ready" };
        notify();
      }
      progress = null;
    } catch (cause) {
      progress = null;
      if (inert) throw cause;
      reportError("playback-failed", item.itemId, errorMessage(cause));
      throw cause;
    }
  }

  /**
   * Fetches and decodes the Glue played into item `index`. Failure is local and
   * non-fatal: the take is remembered as failed, the subscriber is notified
   * (the hook rebuilds without it), and playback continues. Never called for a
   * junction without a server-resolved ready take, so an unfinished take is
   * never scheduled.
   */
  function prepareGlueFor(index: number, window: TimelineGlueWindow): Promise<void> {
    const item = timeline.items[index];
    const takeId = window.takeId;
    if (preparedGlue.has(takeId) || failedGlueTakes.has(takeId)) {
      return Promise.resolve();
    }
    const inFlight = glueInFlight.get(takeId);
    if (inFlight) return inFlight;

    const fromItemId = window.target === "outro" ? item.itemId : timeline.items[index - 1]?.itemId ?? null;
    const toItemId = item!.itemId;
    const promise = (async () => {
      try {
        const url = glueUrlFor(takeId);
        const data = await audioBytesCache.load(`glue:${takeId}`, (onProgress) =>
          fetchArrayBuffer(url, onProgress),
        );
        if (inert) return;
        const decoded = await getContext().decodeAudioData(data);
        if (inert) return;
        preparedGlue.set(takeId, decoded);
      } catch (cause) {
        if (inert) return;
        failedGlueTakes.add(takeId);
        const failure: SongsetAudioGlueFailure = {
          takeId,
          fromItemId,
          toItemId,
          message: errorMessage(cause),
        };
        try {
          options.onGlueFailure?.(failure);
        } catch {
          // A subscriber must never corrupt transport state.
        }
      } finally {
        glueInFlight.delete(takeId);
      }
    })();
    glueInFlight.set(takeId, promise);
    return promise;
  }

  function prepare(index: number): Promise<void> {
    if (inert) return Promise.resolve();
    if (prepared.has(index)) return Promise.resolve();
    if (activePrepare?.index === index) return activePrepare.promise;
    if (activePrepare) {
      // Exactly one fetch/decode at a time. A caller that asked for another
      // item waits for the active prepare, then retries its own request.
      return activePrepare.promise
        .catch(() => undefined)
        .then(() => prepare(index));
    }

    evictFinishedForPrepare(index, clockPosition());

    const record: ActivePrepare = {
      index,
      promise: Promise.resolve(),
    };
    const promise = runPrepare(index).finally(() => {
      if (activePrepare === record) activePrepare = null;
    });
    record.promise = promise;
    activePrepare = record;
    return promise;
  }

  async function doPlay(): Promise<void> {
    if (inert) return;

    const validation = validateSongsetAudioSources(items);
    if (validation) {
      reportError(validation.kind, validation.itemId, validation.message);
      throw new Error(validation.message);
    }

    const ctx = getContext();
    const playAttempt = playbackGeneration;
    try {
      await ctx.resume();
    } catch (cause) {
      const item = timeline.items[itemIndexAt(position)];
      reportError(
        "playback-failed",
        item?.itemId ?? null,
        errorMessage(cause),
      );
      throw cause;
    }
    if (inert || playAttempt !== playbackGeneration) return;

    error = null;
    playbackGeneration += 1;
    const generation = playbackGeneration;
    const index = itemIndexAt(position);
    try {
      await prepare(index);
    } catch (cause) {
      throw cause;
    }
    if (inert || generation !== playbackGeneration) return;
    const recordedError = getError();
    if (recordedError !== null) throw new Error(recordedError.message);

    fullRebuild(position);
    startClock(position);
    startTicker();
    notify();
  }

  function play(): Promise<void> {
    if (inert) return Promise.resolve();
    if (playing) return Promise.resolve();
    if (playPending) {
      // pause()/seek() invalidates a pending attempt. A new gesture starts a
      // fresh attempt once the invalidated promise has settled; its stale
      // rejection was already handled by the caller that owned it.
      return playPending.then(() => play(), () => play());
    }
    const pending = doPlay();
    playPending = pending;
    const clearPending = () => {
      if (playPending === pending) playPending = null;
    };
    void pending.then(clearPending, clearPending);
    return pending;
  }

  function pause(): void {
    if (inert) return;
    if (playing) position = clockPosition();
    playing = false;
    awaitingDecode = false;
    startedAtContextTime = null;
    playbackGeneration += 1;
    teardownGraph();
    stopTicker();
    progress = null;
    notify();
  }

  function seek(seconds: number): void {
    if (inert) return;
    const wasPlaying = playing;
    playbackGeneration += 1;
    const generation = playbackGeneration;
    const target = clamp(seconds, 0, totalSeconds);
    const index = itemIndexAt(target);
    position = target;
    awaitingDecode = false;

    if (!wasPlaying) {
      notify();
      void prepare(index).catch(() => {
        // reportError has already stopped playback and notified.
      });
      return;
    }

    if (prepared.has(index)) {
      startClock(target);
      fullRebuild(target);
      notify();
      return;
    }

    // Issue #296: evicted or never-fetched target. Silence the graph and hold
    // the clock at the requested position so the ticker cannot advance it;
    // the epoch guards below keep pause/another seek/stop from restarting.
    awaitingDecode = true;
    playing = true;
    startedAtContextTime = null;
    teardownGraph();
    void prepare(index).then(
      () => {
        if (
          inert ||
          !playing ||
          !awaitingDecode ||
          generation !== playbackGeneration
        ) {
          return;
        }
        awaitingDecode = false;
        startClock(target);
        fullRebuild(target);
        notify();
      },
      () => {
        // reportError has already stopped playback and notified.
      },
    );
    notify();
  }

  function stop(): void {
    if (inert) return;
    playing = false;
    awaitingDecode = false;
    startedAtContextTime = null;
    position = 0;
    startPosition = 0;
    playbackGeneration += 1;
    teardownGraph();
    stopTicker();
    progress = null;
    error = null;
    notify();
  }

  function destroy(): void {
    if (inert) return;
    inert = true;
    playing = false;
    awaitingDecode = false;
    startedAtContextTime = null;
    position = 0;
    startPosition = 0;
    playbackGeneration += 1;
    teardownGraph();
    stopTicker();
    prepared.clear();
    preparedGlue.clear();
    glueInFlight.clear();
    failedGlueTakes.clear();
    progress = null;
    error = null;
    notify();
    listeners.clear();
  }

  async function startAtSeconds(seconds: number): Promise<void> {
    if (inert) return;
    if (playing) pause();
    position = clamp(seconds, 0, totalSeconds);
    await play();
  }

  async function prepareAt(seconds: number): Promise<void> {
    if (inert) return;
    const index = itemIndexAt(clamp(seconds, 0, totalSeconds));
    await prepare(index);
    // `runPrepare(index + 1)` is what decodes the AI take of the junction
    // between the two items, so a lead-in never plays into a silent junction.
    if (index + 1 < itemCount) await prepare(index + 1);
  }

  function getTimelinePositionSeconds(): number {
    return playing ? clockPosition() : clamp(position, 0, totalSeconds);
  }

  function isPlaying(): boolean {
    return !inert && playing;
  }

  function getProgress(): SongsetAudioProgress | null {
    return progress;
  }

  function getError(): SongsetAudioError | null {
    return error;
  }

  function onStateChange(listener: () => void): () => void {
    if (inert) {
      return () => {
        // Inert sessions never notify.
      };
    }
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }

  return {
    timeline,
    totalSeconds,
    play,
    pause,
    stop,
    destroy,
    seek,
    startAtSeconds,
    prepareAt,
    getTimelinePositionSeconds,
    isPlaying,
    getProgress,
    getError,
    onStateChange,
  };
}
