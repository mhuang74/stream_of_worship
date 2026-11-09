/**
 * Whole-Songset arrangement preview transport (issue #286, spec
 * specs/song_arrangement_editor.md §5.11).
 *
 * Architecture constraints (why NOT decodeAudioData):
 * - 25 minutes of stereo 44.1 kHz PCM ≈ 529 MB; streaming media elements are
 *   mandatory. One `<audio preload="metadata">` + MediaElementAudioSourceNode
 *   + GainNode per item → one AudioContext destination.
 * - Elements are created ONLY when the preview session actually starts
 *   playback (never at sheet open), so a sheet open with five recordings
 *   downloads nothing beyond metadata.
 * - iOS autoplay: every element is gesture-unlocked inside the synchronous
 *   Play click handler (muted play→pause→currentTime reset→muted restored)
 *   before any timer-driven play().
 * - iOS AudioContext fallback: only items involved in a crossfade (their own
 *   incoming fade or the successor-owned outgoing fade) route through the
 *   graph. An AnalyserNode RMS check on the first graph-routed item detects
 *   silent output; on silence the graph is torn down and that item falls back
 *   to direct playback (fresh element, no source node) with crossfades
 *   approximated as hard boundaries. Transport stays element-driven, so
 *   pause/resume/seek keep working after the fallback.
 * - Crossfade ownership: the INCOMING item owns its fade-in
 *   (crossfadeSeconds); the outgoing fade of the predecessor must be derived
 *   from the SUCCESSOR's crossfadeSeconds, never the predecessor's own.
 * - Overlapping fade windows are composed by TWO series GainNodes per item
 *   (worker parity: the render worker applies two sequential afade filters,
 *   so the envelopes multiply). Never insert a full-gain event inside a
 *   running ramp: an item shorter than its own in+out windows would step to
 *   full volume mid-fade.
 * - Transport is monotonic on context time:
 *   timelinePosition = context.currentTime − playStartedAtContextTime
 *                    + playStartedAtTimelineSeconds (clamped to total).
 * - This module never touches the global AudioPlayerContext's play() path and
 *   never creates an AudioTrack of type "song", so the global provider's
 *   Completion gate (markSongCompleted) can never fire from a preview
 *   session. Stopping the global player before preview start is the hook's
 *   responsibility (an explicit player.stop() call).
 */

import {
  buildArrangementTimeline,
  parseSongArrangement,
  validateSongArrangement,
  type ArrangementTimeline,
  type ArrangementTimelineItem,
  type TimelineClip,
  type TimelineItemSource,
} from "@/lib/arrangements";

export type ArrangementPreviewErrorKind =
  | "no-recording"
  | "invalid-arrangement"
  | "playback-failed";

export interface ArrangementPreviewError {
  kind: ArrangementPreviewErrorKind;
  /** Offending item id, or null when no item can be blamed. */
  itemId: string | null;
  message: string;
}

export interface ArrangementPreviewSessionOptions {
  /** Same-origin proxy URL builder. Never a cross-origin R2 signed URL. */
  audioUrlFor?: (hashPrefix: string) => string;
  /** Injectable for tests. Default: `new (window.AudioContext)()`. */
  createAudioContext?: () => AudioContext;
  /** Injectable for tests. Default: document.createElement("audio"). */
  createAudioElement?: (src: string) => HTMLAudioElement;
  /** Scheduler tick, default 25 ms. */
  schedulerIntervalMs?: number;
  /** RMS below this on the first graph-routed item triggers the fallback. */
  rmsSilenceThreshold?: number;
  /** Delay after playback start before the RMS check samples. */
  rmsCheckDelayMs?: number;
}

export interface ArrangementPreviewSession {
  readonly timeline: ArrangementTimeline;
  readonly totalSeconds: number;
  /** Starts or resumes. Resolves once every newly-started element is audible. */
  play(): Promise<void>;
  pause(): void;
  /** Full teardown: timers, listeners, elements, graph nodes, AudioContext. */
  stop(): void;
  seek(seconds: number): void;
  startAt(seconds: number): void;
  getTimelinePositionSeconds(): number;
  isPlaying(): boolean;
  isFallbackMode(): boolean;
  getError(): ArrangementPreviewError | null;
  onStateChange(listener: () => void): () => void;
}

const DEFAULT_AUDIO_URL_FOR = (hashPrefix: string) =>
  `/api/recordings/${hashPrefix}/audio`;

/** Element-driven time drift beyond this against an ended element is a failure. */
const ENDED_TOLERANCE_SECONDS = 0.25;

/** Flat duration wins; SongsetItemDetail nests it under `recording`. */
function itemRecordingDurationSeconds(item: TimelineItemSource): number | null {
  const flat = item.recordingDurationSeconds;
  const value = typeof flat === "number" ? flat : item.recording?.durationSeconds;
  return typeof value === "number" && Number.isFinite(value) && value > 0
    ? value
    : null;
}

/**
 * Pre-playback rejection (issue: "Reject missing recording/invalid siblings
 * before playback, never substitute original audio"). Pure: creates no
 * elements, no AudioContext, performs no network activity.
 *
 * Every item carrying a saved arrangement is validated against that item's
 * CURRENT analysis, so a sibling whose steps no longer match its
 * sections/beats (or whose analysis is missing) blocks the preview instead of
 * auditioning source ranges the render metrics and the worker would reject.
 */
export function validateArrangementPreviewSources(
  items: TimelineItemSource[],
): ArrangementPreviewError | null {
  if (items.length === 0) {
    return {
      kind: "invalid-arrangement",
      itemId: null,
      message: "Songset has no items to preview",
    };
  }
  for (const item of items) {
    // Mirrors the worker's no-recording failure: never substitute original
    // audio or a sibling's recording.
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
      // No arrangement → the timeline needs the Recording duration for the
      // full-recording clip; without it the clip would be zero-length.
      if (duration === null) {
        return {
          kind: "no-recording",
          itemId: item.itemId,
          message: `Recording duration for item ${item.itemId} is unavailable; preview is unavailable`,
        };
      }
      continue;
    }
    // A present arrangement must still match the Recording's CURRENT analysis,
    // exactly as getSongsetRenderMetrics and the render worker enforce: stale
    // steps or missing analysis = repair-or-reset, never a stale audition.
    // Unknown duration passes 0 so buildSnappedSteps reports
    // ANALYSIS_UNAVAILABLE (normalizeBeats rejects a nonpositive duration).
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

interface ElementRecord {
  item: ArrangementTimelineItem;
  itemIndex: number;
  audio: HTMLAudioElement;
  /** Created exactly once per element; never re-created after fallback swap. */
  sourceNode: MediaElementAudioSourceNode | null;
  /** Incoming fade envelope (this item's own crossfadeSeconds). */
  gainIn: GainNode | null;
  /** Outgoing fade envelope (owned by the SUCCESSOR's crossfadeSeconds). */
  gainOut: GainNode | null;
  analyser: AnalyserNode | null;
  currentClip: TimelineClip | null;
  onEnded: EventListener;
  onError: EventListener;
}

function defaultCreateAudioElement(src: string): HTMLAudioElement {
  if (typeof document === "undefined") {
    throw new Error("Arrangement preview requires a DOM");
  }
  const element = document.createElement("audio");
  // metadata, never auto: five recordings must not download on sheet open,
  // and even at preview start only metadata loads before playback.
  element.preload = "metadata";
  element.src = src;
  return element;
}

interface LegacyAudioWindow {
  // Safari < 14.1 only exposes the prefixed constructor (not in lib.dom).
  webkitAudioContext?: typeof AudioContext;
}

function defaultCreateAudioContext(): AudioContext {
  if (typeof window === "undefined") {
    throw new Error("Arrangement preview requires a window");
  }
  const legacyWindow = window as unknown as LegacyAudioWindow;
  const Ctor = window.AudioContext ?? legacyWindow.webkitAudioContext;
  if (!Ctor) throw new Error("Web Audio is not supported in this browser");
  return new Ctor();
}

function clampToRange(value: number, max: number): number {
  return Math.min(Math.max(value, 0), max);
}

function clipAt(
  item: ArrangementTimelineItem,
  timelineSeconds: number,
): TimelineClip | null {
  return (
    item.clips.find(
      (clip) =>
        timelineSeconds >= clip.startSeconds &&
        timelineSeconds < clip.endSeconds,
    ) ?? null
  );
}

export function createArrangementPreviewSession(
  items: TimelineItemSource[],
  options: ArrangementPreviewSessionOptions = {},
): ArrangementPreviewSession {
  const timeline = buildArrangementTimeline(items);
  const totalSeconds = timeline.totalSeconds;
  const audioUrlFor = options.audioUrlFor ?? DEFAULT_AUDIO_URL_FOR;
  const createAudioElement = options.createAudioElement ?? defaultCreateAudioElement;
  const createAudioContext = options.createAudioContext ?? defaultCreateAudioContext;
  const schedulerIntervalMs = options.schedulerIntervalMs ?? 25;
  const rmsSilenceThreshold = options.rmsSilenceThreshold ?? 0.0005;
  const rmsCheckDelayMs = options.rmsCheckDelayMs ?? 1000;

  let records: ElementRecord[] = [];
  let elementsCreated = false;
  let graphWired = false;
  let audioContext: AudioContext | null = null;
  let contextClosed = false;
  let playing = false;
  // play() is holding the transport origin until the initial elements' play()
  // promises settle; elements are running while the clock is not.
  let startupPending = false;
  let stopped = false;
  let fallbackMode = false;
  let rmsChecked = false;
  let ticker: ReturnType<typeof setInterval> | null = null;
  let rmsTimer: ReturnType<typeof setTimeout> | null = null;
  let playStartedAtContextTime = 0;
  let playStartedAtTimelineSeconds = 0;
  let pausedPositionSeconds = 0;
  let error: ArrangementPreviewError | null = null;
  const listeners = new Set<() => void>();

  function createRecord(
    item: ArrangementTimelineItem,
    itemIndex: number,
  ): ElementRecord {
    // Elements are created here — at preview session start only. The session
    // is created lazily by the hook on first play(), so opening the sheet
    // performs no element creation and no fetch.
    const audio = createAudioElement(audioUrlFor(item.recordingHashPrefix as string));
    audio.preload = "metadata";
    const record: ElementRecord = {
      item,
      itemIndex,
      audio,
      sourceNode: null,
      gainIn: null,
      gainOut: null,
      analyser: null,
      currentClip: null,
      onEnded: () => handleEnded(record),
      onError: () => failPlayback(item.itemId),
    };
    audio.addEventListener("ended", record.onEnded);
    audio.addEventListener("error", record.onError);
    return record;
  }

  function ensureElements(): void {
    if (elementsCreated || stopped) return;
    elementsCreated = true;
    records = timeline.items.map((item, itemIndex) => createRecord(item, itemIndex));
  }

  /**
   * Routes ONLY crossfade-involved items through the graph: an item needs a
   * GainNode when it has its own incoming crossfade OR its successor owns an
   * outgoing fade that must land on it. Non-crossfade items play direct from
   * the element (iOS AudioContext fallback mitigation).
   */
  function wireGraph(): void {
    if (graphWired || fallbackMode || stopped) return;
    graphWired = true;
    const context = audioContext as AudioContext;
    for (const record of records) {
      const successorCrossfade =
        records[record.itemIndex + 1]?.item.crossfadeSeconds ?? 0;
      const needsGraph =
        record.item.crossfadeSeconds > 0 || successorCrossfade > 0;
      if (!needsGraph) continue;
      const sourceNode = context.createMediaElementSource(record.audio);
      const gainIn = context.createGain();
      const gainOut = context.createGain();
      // Two nodes in series MULTIPLY the envelopes, matching the worker's two
      // sequential afade filters: a short item can carry an incoming fade and
      // a successor-owned outgoing fade whose windows overlap.
      sourceNode.connect(gainIn);
      gainIn.connect(gainOut);
      // Every graph-routed element gets its own RMS tap: the silent-output
      // sample must follow whichever item is actually sounding.
      const analyser = context.createAnalyser();
      gainOut.connect(analyser); // RMS tap; analyser itself is not audible
      gainOut.connect(context.destination);
      record.sourceNode = sourceNode;
      record.gainIn = gainIn;
      record.gainOut = gainOut;
      record.analyser = analyser;
    }
  }

  function ensureContext(): AudioContext {
    if (!audioContext) {
      audioContext = createAudioContext();
    }
    return audioContext;
  }

  // ---------------------------------------------------------------------------
  // Gesture unlock (iOS autoplay policy)
  // ---------------------------------------------------------------------------

  /**
   * Runs synchronously inside the Play click handler, BEFORE any timer-driven
   * play(): per element — muted play, immediate pause, currentTime reset —
   * then muted=false restored after ALL elements. Rejected probe plays are
   * caught so a single stubborn element cannot break the session.
   */
  function unlockElements(): void {
    for (const record of records) {
      const audio = record.audio;
      audio.muted = true;
      try {
        const playPromise = audio.play();
        if (playPromise && typeof playPromise.then === "function") {
          playPromise.catch(() => {});
        }
      } catch {
        // Unlock probe is best-effort; real playback errors are reported later.
      }
      audio.pause();
      audio.currentTime = 0;
    }
    for (const record of records) {
      record.audio.muted = false;
    }
  }

  // ---------------------------------------------------------------------------
  // Gain automation
  // ---------------------------------------------------------------------------

  /**
   * (Re)schedules all gain automation relative to the new context origin,
   * after cancelling previous automation. Both envelopes live on separate
   * nodes, so they compose by multiplication exactly like the worker's two
   * sequential afade filters. Incoming fade: 0 → 1 across
   * [itemStart, itemStart + crossfadeSeconds]. Outgoing fade on the
   * predecessor: 1 → 0 across [itemEnd − successorCrossfade, itemEnd] — the
   * successor OWNS this fade, so its crossfadeSeconds drives the ramp.
   */
  function scheduleGains(originContextTime: number, position: number): void {
    if (fallbackMode) return;
    for (const record of records) {
      const item = record.item;
      if (!record.gainIn || !record.gainOut) continue;
      const itemStart = item.startSeconds;
      const itemEnd = itemStart + item.durationSeconds;
      const successorCrossfade =
        records[record.itemIndex + 1]?.item.crossfadeSeconds ?? 0;

      // Incoming envelope: 0 → 1 across [itemStart, itemStart + crossfadeSeconds].
      const incoming = record.gainIn.gain;
      incoming.cancelScheduledValues(originContextTime);
      if (item.crossfadeSeconds > 0) {
        const fadeInEnd = itemStart + item.crossfadeSeconds;
        if (position >= fadeInEnd) {
          incoming.setValueAtTime(1, originContextTime);
        } else if (position > itemStart) {
          // Seek/resume landed inside the fade: re-enter at the matching gain.
          incoming.setValueAtTime(
            (position - itemStart) / item.crossfadeSeconds,
            originContextTime,
          );
          incoming.linearRampToValueAtTime(
            1,
            originContextTime + (fadeInEnd - position),
          );
        } else {
          // Item has not started yet (element paused): stay silent, then fade
          // in across the item's OWN crossfade window [start, start + cf].
          incoming.setValueAtTime(0, originContextTime + (itemStart - position));
          incoming.linearRampToValueAtTime(
            1,
            originContextTime + (fadeInEnd - position),
          );
        }
      } else {
        incoming.setValueAtTime(1, originContextTime);
      }

      // Outgoing envelope on the predecessor: 1 → 0 across
      // [itemEnd − successorCrossfade, itemEnd]. The successor OWNS this fade.
      const outgoing = record.gainOut.gain;
      outgoing.cancelScheduledValues(originContextTime);
      if (successorCrossfade > 0 && itemEnd > itemStart) {
        const fadeOutStart = itemEnd - successorCrossfade;
        if (position < fadeOutStart) {
          // Hold at full until the outgoing window opens, then ramp to zero
          // exactly at item end (where overlap with the successor ends).
          outgoing.setValueAtTime(1, originContextTime);
          outgoing.setValueAtTime(1, originContextTime + (fadeOutStart - position));
          outgoing.linearRampToValueAtTime(
            0,
            originContextTime + (itemEnd - position),
          );
        } else if (position < itemEnd) {
          outgoing.setValueAtTime(
            clampToRange((itemEnd - position) / successorCrossfade, 1),
            originContextTime,
          );
          outgoing.linearRampToValueAtTime(
            0,
            originContextTime + (itemEnd - position),
          );
        } else {
          outgoing.setValueAtTime(0, originContextTime);
        }
      } else {
        outgoing.setValueAtTime(1, originContextTime);
      }
    }
  }

  // ---------------------------------------------------------------------------
  // Element scheduling
  // ---------------------------------------------------------------------------

  function safePlay(record: { audio: HTMLAudioElement; item: ArrangementTimelineItem }): Promise<void> {
    const audio = record.audio;
    try {
      const playPromise = audio.play();
      if (playPromise && typeof playPromise.then === "function") {
        return playPromise.catch(() => {
          failPlayback(record.item.itemId);
        });
      }
      return Promise.resolve();
    } catch {
      failPlayback(record.item.itemId);
      return Promise.resolve();
    }
  }

  /**
   * Resolves the active clip per item at `position` and aligns elements:
   * - active item with a clip: (re)position `currentTime` to
   *   sourceStart + max(0, position − clip.start); start paused elements when
   *   `audible`; reposition only at step-boundary crossings otherwise.
   * - item end: pause the element ("pause at item end").
   * - future/gap items: paused; the scheduler starts them at their item start.
   * Returns the play promises it initiated (for play() to await audibility).
   */
  function applyElementsAt(
    position: number,
    audible: boolean,
    force = false,
  ): Promise<void>[] {
    const playPromises: Promise<void>[] = [];
    for (const record of records) {
      const item = record.item;
      // Direct (fallback) playback has no overlap: hand over to the successor
      // at its crossfade start instead of letting both elements sound.
      const successorCrossfade = fallbackMode
        ? records[record.itemIndex + 1]?.item.crossfadeSeconds ?? 0
        : 0;
      const itemEnd =
        item.startSeconds + item.durationSeconds - successorCrossfade;
      const active = position >= item.startSeconds && position < itemEnd;
      const clip = active ? clipAt(item, position) : null;
      if (!active || !clip) {
        if (!record.audio.paused) {
          record.audio.pause();
        }
        record.currentClip = null;
        continue;
      }
      const offset =
        clip.sourceStartSeconds + Math.max(0, position - clip.startSeconds);
      if (record.audio.paused) {
        record.audio.currentTime = offset;
        record.currentClip = clip;
        if (audible) {
          playPromises.push(safePlay(record));
          // A graph-routed element that just started is the right RMS sample
          // source; an earlier inconclusive read retries here.
          if (record.analyser !== null) armRmsCheck();
        }
      } else if (force || record.currentClip !== clip) {
        record.audio.currentTime = offset;
        record.currentClip = clip;
      }
    }
    return playPromises;
  }

  function pauseAllElements(): void {
    for (const record of records) {
      try {
        if (!record.audio.paused) {
          record.audio.pause();
        }
      } catch {
        // Element may already be detached during teardown.
      }
      record.currentClip = null;
    }
  }

  // ---------------------------------------------------------------------------
  // Transport clock
  // ---------------------------------------------------------------------------

  /**
   * Monotonic context-time origin: the timeline position advances 1:1 with
   * context time from (playStartedAtContextTime → playStartedAtTimelineSeconds).
   */
  function startTransportAt(position: number): void {
    playStartedAtContextTime = (audioContext as AudioContext).currentTime;
    playStartedAtTimelineSeconds = position;
    playing = true;
    startTicker();
  }

  function getTimelinePositionSeconds(): number {
    if (stopped || !playing) {
      return clampToRange(pausedPositionSeconds, totalSeconds);
    }
    const contextTime = audioContext?.currentTime ?? 0;
    const position =
      contextTime - playStartedAtContextTime + playStartedAtTimelineSeconds;
    return clampToRange(position, totalSeconds);
  }

  function startTicker(): void {
    if (ticker !== null) return;
    ticker = setInterval(() => {
      if (!playing || stopped) return;
      const position = getTimelinePositionSeconds();
      applyElementsAt(position, true);
      if (position >= totalSeconds) {
        endOfTimeline();
        return;
      }
      notify();
    }, schedulerIntervalMs);
  }

  function stopTicker(): void {
    if (ticker !== null) {
      clearInterval(ticker);
      ticker = null;
    }
  }

  function endOfTimeline(): void {
    playing = false;
    stopTicker();
    cancelRmsCheck();
    pausedPositionSeconds = totalSeconds;
    pauseAllElements();
    notify();
  }

  function failPlayback(itemId: string): void {
    if (stopped) return;
    // Capture the live position BEFORE clearing `playing`, so a retry resumes
    // from where playback actually failed instead of the last pause/seek.
    pausedPositionSeconds = getTimelinePositionSeconds();
    error = {
      kind: "playback-failed",
      itemId,
      message: `Playback failed for item ${itemId}`,
    };
    playing = false;
    startupPending = false;
    stopTicker();
    cancelRmsCheck();
    pauseAllElements();
    notify();
  }

  function handleEnded(record: ElementRecord): void {
    if (stopped || !playing) return;
    const position = getTimelinePositionSeconds();
    const clip = record.currentClip;
    // Natural end at the active clip boundary is expected: the scheduler
    // advances (next clip or next item) at its next tick. An early end —
    // before the clip ends — means the file does not match the analysis.
    const clipEnd = clip
      ? clip.startSeconds + (clip.sourceEndSeconds - clip.sourceStartSeconds)
      : record.item.startSeconds + record.item.durationSeconds;
    if (position < clipEnd - ENDED_TOLERANCE_SECONDS) {
      failPlayback(record.item.itemId);
    }
  }

  // ---------------------------------------------------------------------------
  // RMS silent-output fallback (iOS)
  // ---------------------------------------------------------------------------

  function cancelRmsCheck(): void {
    if (rmsTimer !== null) {
      clearTimeout(rmsTimer);
      rmsTimer = null;
    }
  }

  function armRmsCheck(): void {
    if (rmsChecked || fallbackMode || stopped) return;
    if (!records.some((record) => record.analyser !== null)) return; // no graph path
    cancelRmsCheck();
    rmsTimer = setTimeout(() => {
      rmsTimer = null;
      if (stopped || fallbackMode) return;
      // The startup hold has already started elements even though the clock
      // has not been anchored yet.
      if (!playing && !startupPending) return;
      // Sample an element that is actually sounding AND has media data: a
      // buffering element reads as silence, so an inconclusive pass stays
      // unchecked and the next element start retries.
      const sampled = records.find(
        (record) =>
          record.analyser !== null &&
          !record.audio.paused &&
          record.audio.readyState >= 2
      );
      if (!sampled) return;
      rmsChecked = true;
      const analyser = sampled.analyser as AnalyserNode;
      const data = new Float32Array(analyser.fftSize);
      analyser.getFloatTimeDomainData(data);
      let sum = 0;
      for (let i = 0; i < data.length; i++) {
        sum += data[i] * data[i];
      }
      const rms = Math.sqrt(sum / data.length);
      if (rms < rmsSilenceThreshold) {
        engageFallback();
      }
    }, rmsCheckDelayMs);
  }

  /**
   * Silent output: tear the graph down and fall back to DIRECT playback for
   * every item. An element already captured by a MediaElementAudioSourceNode
   * cannot play direct again, so each graph-captured element is replaced with
   * a fresh element (no source node). Crossfades become hard boundaries; the
   * element-driven transport (seek/pause/resume) is untouched.
   */
  function engageFallback(): void {
    if (fallbackMode || stopped) return;
    fallbackMode = true;
    cancelRmsCheck();
    const position = playing
      ? getTimelinePositionSeconds()
      : pausedPositionSeconds;
    const oldRecords = records;
    const newRecords: ElementRecord[] = [];
    for (const oldRecord of oldRecords) {
      const wasPlaying = !oldRecord.audio.paused;
      detachElement(oldRecord);
      try {
        if (!oldRecord.audio.paused) oldRecord.audio.pause();
      } catch {
        // ignore
      }
      disconnectRecord(oldRecord);
      const record = createRecord(oldRecord.item, oldRecord.itemIndex);
      const clip = clipAt(record.item, position);
      if (clip) {
        record.audio.currentTime =
          clip.sourceStartSeconds + Math.max(0, position - clip.startSeconds);
        record.currentClip = clip;
      }
      if (wasPlaying && playing) {
        safePlay(record);
      }
      newRecords.push(record);
    }
    records = newRecords;
    notify();
  }

  function disconnectRecord(record: ElementRecord): void {
    try {
      record.sourceNode?.disconnect();
    } catch {
      // ignore
    }
    try {
      record.gainIn?.disconnect();
    } catch {
      // ignore
    }
    try {
      record.gainOut?.disconnect();
    } catch {
      // ignore
    }
    try {
      record.analyser?.disconnect();
    } catch {
      // ignore
    }
    record.sourceNode = null;
    record.gainIn = null;
    record.gainOut = null;
    record.analyser = null;
  }

  function detachElement(record: ElementRecord): void {
    record.audio.removeEventListener("ended", record.onEnded);
    record.audio.removeEventListener("error", record.onError);
  }

  // ---------------------------------------------------------------------------
  // Public transport
  // ---------------------------------------------------------------------------

  async function play(): Promise<void> {
    if (stopped || playing || startupPending) return;
    ensureElements();
    const context = ensureContext();
    // Crossfade items only; each element gets its source node exactly once.
    wireGraph();
    try {
      const resumePromise = context.resume();
      if (resumePromise && typeof resumePromise.then === "function") {
        resumePromise.catch(() => {});
      }
    } catch {
      // resume failures surface as playback failures on element play()
    }
    // Gesture unlock must run before ANY timer-driven play (iOS autoplay).
    unlockElements();
    const position = clampToRange(pausedPositionSeconds, totalSeconds);
    // Set the hold BEFORE starting anything: a synchronous play() throw routes
    // through safePlay → failPlayback, which clears the hold, and the tail must
    // then abort instead of anchoring a clock over a reported failure.
    startupPending = true;
    const playPromises = applyElementsAt(position, true);
    notify();
    // Resolve once every newly-started element is audible (metadata + play),
    // THEN anchor the clock: context time must not advance while media buffers.
    await Promise.all(playPromises.map((p) => p.catch(() => {})));
    if (stopped || !startupPending) {
      startupPending = false;
      return;
    }
    startupPending = false;
    startTransportAt(position);
    scheduleGains(playStartedAtContextTime, position);
    armRmsCheck();
    notify();
  }

  function pause(): void {
    if (stopped || (!playing && !startupPending)) return;
    // During the startup hold the clock never started, so
    // getTimelinePositionSeconds() returns the position play() was about to
    // begin at.
    pausedPositionSeconds = getTimelinePositionSeconds();
    startupPending = false;
    playing = false;
    stopTicker();
    cancelRmsCheck();
    pauseAllElements();
    notify();
  }

  function seek(seconds: number): void {
    if (stopped) return;
    const target = clampToRange(
      typeof seconds === "number" && Number.isFinite(seconds) ? seconds : 0,
      totalSeconds,
    );
    if (!elementsCreated) {
      // Pre-play seek: remember the start position; play() begins there.
      pausedPositionSeconds = target;
      notify();
      return;
    }
    if (playing || startupPending) {
      // Seek recompute: rebuild origin, active clips, offsets, and gain
      // automation relative to the new context origin. A seek during the
      // startup hold anchors immediately, so the awaited play() tail aborts
      // instead of re-anchoring a second time.
      startupPending = false;
      pausedPositionSeconds = target;
      startTransportAt(target);
      applyElementsAt(target, true, true);
      scheduleGains(playStartedAtContextTime, target);
      notify();
      return;
    }
    pausedPositionSeconds = target;
    // Pre-position the would-be-active element; gap seeks may have no active
    // element at all. Nothing plays while paused.
    applyElementsAt(target, false, true);
    notify();
  }

  function startAt(seconds: number): void {
    seek(seconds);
  }

  function stop(): void {
    if (stopped) return;
    stopped = true;
    playing = false;
    startupPending = false;
    stopTicker();
    cancelRmsCheck();
    for (const record of records) {
      detachElement(record);
      try {
        if (!record.audio.paused) record.audio.pause();
      } catch {
        // ignore
      }
      try {
        record.audio.removeAttribute("src");
        record.audio.load();
      } catch {
        // ignore: element may be a test double without load()
      }
      disconnectRecord(record);
    }
    records = [];
    if (audioContext && !contextClosed) {
      contextClosed = true;
      try {
        const closePromise = audioContext.close();
        if (closePromise && typeof closePromise.then === "function") {
          closePromise.catch(() => {});
        }
      } catch {
        // ignore
      }
    }
    notify();
  }

  function isPlaying(): boolean {
    return playing;
  }

  function isFallbackMode(): boolean {
    return fallbackMode;
  }

  function getError(): ArrangementPreviewError | null {
    return error;
  }

  function notify(): void {
    for (const listener of [...listeners]) {
      listener();
    }
  }

  function onStateChange(listener: () => void): () => void {
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
    seek,
    startAt,
    getTimelinePositionSeconds,
    isPlaying,
    isFallbackMode,
    getError,
    onStateChange,
  };
}
