import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import {
  createArrangementPreviewSession,
  validateArrangementPreviewSources,
  type ArrangementPreviewSessionOptions,
} from "@/lib/audio/arrangement-preview";
import type { TimelineItemSource } from "@/lib/arrangements";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function source(
  overrides: Partial<TimelineItemSource> & { itemId: string },
): TimelineItemSource {
  return {
    recordingHashPrefix: `hash-${overrides.itemId}`,
    recordingDurationSeconds: 100,
    arrangementSteps: null,
    gapBeats: 0,
    crossfadeEnabled: 0,
    crossfadeDurationSeconds: null,
    tempoBpm: null,
    ...overrides,
  };
}

function stepsJson(
  entries: Array<{ label: string; start: number; end: number }>,
): string {
  return JSON.stringify({
    version: 1,
    steps: entries.map((entry, index) => ({
      sectionIndex: index,
      label: entry.label,
      startSeconds: entry.start,
      endSeconds: entry.end,
    })),
  });
}

// ---------------------------------------------------------------------------
// Fake media elements / AudioContext
// ---------------------------------------------------------------------------

interface PlayCall {
  muted: boolean;
  currentTime: number;
}

interface FakeAudio {
  element: HTMLAudioElement;
  playCalls: PlayCall[];
  currentTimeSets: number[];
  removedListeners: string[];
  removedAttributes: string[];
  loadCalls: () => number;
  pauseCalls: () => number;
  setPlayMode: (mode: "resolve" | "reject" | "defer" | "throw") => void;
  resolvePlays: () => void;
  dispatch: (type: string) => void;
}

function makeFakeAudio(src: string): FakeAudio {
  let currentTime = 0;
  let paused = true;
  let muted = false;
  let preload = "";
  let pauseCount = 0;
  let loadCount = 0;
  let playMode: "resolve" | "reject" | "defer" | "throw" = "resolve";
  const pendingPlays: Array<() => void> = [];
  const playCalls: PlayCall[] = [];
  const currentTimeSets: number[] = [];
  const removedListeners: string[] = [];
  const removedAttributes: string[] = [];
  const listeners = new Map<string, Set<(event?: unknown) => void>>();

  const element: Record<string, unknown> = {
    src,
    load: vi.fn(() => {
      loadCount += 1;
    }),
    removeAttribute: vi.fn((name: string) => {
      removedAttributes.push(name);
      if (name === "src") element.src = "";
    }),
    addEventListener: vi.fn(
      (type: string, handler: (event?: unknown) => void) => {
        if (!listeners.has(type)) listeners.set(type, new Set());
        listeners.get(type)?.add(handler);
      },
    ),
    removeEventListener: vi.fn(
      (type: string, handler: (event?: unknown) => void) => {
        removedListeners.push(type);
        listeners.get(type)?.delete(handler);
      },
    ),
    play: vi.fn(() => {
      playCalls.push({ muted, currentTime });
      if (playMode === "throw") {
        // Synchronous throw: not every element returns a promise.
        throw new Error("play threw");
      }
      if (playMode === "reject") {
        return Promise.reject(new Error("play blocked"));
      }
      if (playMode === "defer") {
        // Cold audio request: the media is not audible until the promise
        // resolves (metadata fetch + buffer fill).
        const { promise, resolve } = Promise.withResolvers<void>();
        pendingPlays.push(() => {
          paused = false;
          resolve();
        });
        return promise;
      }
      paused = false;
      return Promise.resolve();
    }),
    pause: vi.fn(() => {
      paused = true;
      pauseCount += 1;
    }),
  };
  Object.defineProperty(element, "currentTime", {
    get: () => currentTime,
    set: (value: number) => {
      currentTime = value;
      currentTimeSets.push(value);
    },
    configurable: true,
  });
  Object.defineProperty(element, "paused", {
    get: () => paused,
    configurable: true,
  });
  Object.defineProperty(element, "muted", {
    get: () => muted,
    set: (value: boolean) => {
      muted = value;
    },
    configurable: true,
  });
  Object.defineProperty(element, "preload", {
    get: () => preload,
    set: (value: string) => {
      preload = value;
    },
    configurable: true,
  });

  return {
    element: element as unknown as HTMLAudioElement,
    playCalls,
    currentTimeSets,
    removedListeners,
    removedAttributes,
    loadCalls: () => loadCount,
    pauseCalls: () => pauseCount,
    setPlayMode: (mode) => {
      playMode = mode;
    },
    resolvePlays: () => {
      for (const resolve of pendingPlays.splice(0)) resolve();
    },
    dispatch: (type) => {
      // A real element leaves the playing state when it reaches its EOF.
      if (type === "ended") paused = true;
      for (const handler of [...(listeners.get(type) ?? [])]) handler();
    },
  };
}

interface FakeSourceNode {
  element: unknown;
  connect: Mock<(destination: unknown) => void>;
  disconnect: Mock<() => void>;
}

interface FakeAudioParam {
  value: number;
  events: Array<{ type: string; value?: number; time?: number }>;
  cancelScheduledValues: Mock<(time: number) => void>;
  setValueAtTime: Mock<(value: number, time: number) => void>;
  linearRampToValueAtTime: Mock<(value: number, time: number) => void>;
}

interface FakeGainNode {
  gain: FakeAudioParam;
  connect: Mock<(destination: unknown) => void>;
  disconnect: Mock<() => void>;
}

interface FakeAnalyserNode {
  fftSize: number;
  connect: Mock<(destination: unknown) => void>;
  disconnect: Mock<() => void>;
  getFloatTimeDomainData: (data: Float32Array) => void;
}

function makeFakeContext(rmsAmplitude = 0) {
  const sources: FakeSourceNode[] = [];
  const gains: FakeGainNode[] = [];
  const analysers: FakeAnalyserNode[] = [];

  const context = {
    currentTime: 0,
    state: "suspended",
    destination: {},
    resume: vi.fn(async () => {
      context.state = "running";
    }),
    close: vi.fn(async () => {}),
    createGain: vi.fn((): FakeGainNode => {
      const events: Array<{ type: string; value?: number; time?: number }> = [];
      const param: FakeAudioParam = {
        value: 1,
        events,
        cancelScheduledValues: vi.fn<(time: number) => void>((time) =>
          events.push({ type: "cancel", time }),
        ),
        setValueAtTime: vi.fn<(value: number, time: number) => void>(
          (value, time) => events.push({ type: "set", value, time }),
        ),
        linearRampToValueAtTime: vi.fn<(value: number, time: number) => void>(
          (value, time) => events.push({ type: "ramp", value, time }),
        ),
      };
      const node: FakeGainNode = {
        gain: param,
        connect: vi.fn<(destination: unknown) => void>(),
        disconnect: vi.fn<() => void>(),
      };
      gains.push(node);
      return node;
    }),
    createMediaElementSource: vi.fn(
      (element: unknown): FakeSourceNode => {
        const node: FakeSourceNode = {
          element,
          connect: vi.fn<(destination: unknown) => void>(),
          disconnect: vi.fn<() => void>(),
        };
        sources.push(node);
        return node;
      },
    ),
    createAnalyser: vi.fn((): FakeAnalyserNode => {
      const analyser: FakeAnalyserNode = {
        fftSize: 2048,
        connect: vi.fn<(destination: unknown) => void>(),
        disconnect: vi.fn<() => void>(),
        getFloatTimeDomainData: (data: Float32Array) => {
          data.fill(rmsAmplitude);
        },
      };
      analysers.push(analyser);
      return analyser;
    }),
  };
  return { context, sources, gains, analysers };
}

function makeSession(
  items: TimelineItemSource[],
  overrides: Partial<ArrangementPreviewSessionOptions> = {},
  rmsAmplitude = 0,
) {
  const created: FakeAudio[] = [];
  const fake = makeFakeContext(rmsAmplitude);
  const session = createArrangementPreviewSession(items, {
    createAudioContext: () => fake.context as unknown as AudioContext,
    createAudioElement: (src) => {
      const audio = makeFakeAudio(src);
      created.push(audio);
      return audio.element;
    },
    schedulerIntervalMs: 25,
    rmsCheckDelayMs: 1000,
    ...overrides,
  });
  return { session, created, ...fake };
}

function last<T>(values: T[]): T | undefined {
  return values[values.length - 1];
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("validateArrangementPreviewSources", () => {
  it("rejects an item without a selected Recording", () => {
    const error = validateArrangementPreviewSources([
      source({ itemId: "a" }),
      source({ itemId: "b", recordingHashPrefix: null }),
    ]);
    expect(error).toMatchObject({ kind: "no-recording", itemId: "b" });
  });

  it("rejects an invalid saved sibling arrangement (never original audio)", () => {
    const error = validateArrangementPreviewSources([
      source({ itemId: "a" }),
      source({ itemId: "b", arrangementSteps: "{ not json" }),
    ]);
    expect(error).toMatchObject({
      kind: "invalid-arrangement",
      itemId: "b",
    });
  });

  it("rejects an item whose recording duration is unknown", () => {
    const error = validateArrangementPreviewSources([
      source({ itemId: "a", recordingDurationSeconds: null }),
    ]);
    expect(error).toMatchObject({ kind: "no-recording", itemId: "a" });
  });

  it("accepts a valid draft + saved sibling mix", () => {
    const error = validateArrangementPreviewSources([
      source({
        itemId: "a",
        arrangementSteps: stepsJson([{ label: "Verse 1", start: 0, end: 0.5 }]),
      }),
      source({
        itemId: "b",
        crossfadeEnabled: 1,
        crossfadeDurationSeconds: 4,
      }),
    ]);
    expect(error).toBeNull();
  });
});

describe("createArrangementPreviewSession", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("creates no elements and no AudioContext before playback starts", () => {
    const createContext = vi.fn();
    const createElement = vi.fn();
    const session = createArrangementPreviewSession([source({ itemId: "a" })], {
      createAudioContext: createContext,
      createAudioElement: createElement,
    });
    expect(createContext).not.toHaveBeenCalled();
    expect(createElement).not.toHaveBeenCalled();
    expect(session.isPlaying()).toBe(false);
    expect(session.getTimelinePositionSeconds()).toBe(0);
  });

  it("creates one metadata-only element per item with same-origin proxy URLs", async () => {
    const { session, created, context } = makeSession([
      source({ itemId: "a" }),
      source({ itemId: "b" }),
    ]);
    await session.play();
    expect(created).toHaveLength(2);
    for (const audio of created) {
      expect(audio.element.preload).toBe("metadata");
      expect(audio.element.src.startsWith("/api/recordings/")).toBe(true);
      expect(audio.element.src).not.toContain("http");
    }
    expect(created[0].element.src).toBe("/api/recordings/hash-a/audio");
    expect(created[1].element.src).toBe("/api/recordings/hash-b/audio");
    expect(context.resume).toHaveBeenCalledTimes(1);
  });

  it("gesture-unlocks every element (muted play/pause, currentTime reset) before audible playback", async () => {
    const { session, created } = makeSession([
      source({ itemId: "a" }),
      source({ itemId: "b" }),
    ]);
    await session.play();
    for (const audio of created) {
      // Every element's first play is the muted unlock probe at position 0.
      expect(audio.playCalls[0]).toEqual({ muted: true, currentTime: 0 });
      expect(audio.pauseCalls()).toBeGreaterThanOrEqual(1);
      expect(audio.currentTimeSets[0]).toBe(0);
      // Muted is restored after ALL probes.
      expect(audio.element.muted).toBe(false);
    }
    // Only the active item is audible, after the unlock probe.
    expect(created[0].playCalls).toHaveLength(2);
    expect(created[0].playCalls[1]).toMatchObject({
      muted: false,
      currentTime: 0,
    });
    expect(created[1].playCalls).toHaveLength(1); // future item: probe only
  });

  it("plays non-crossfade items direct (no MediaElementAudioSourceNode)", async () => {
    const { session, sources, gains, analysers } = makeSession([
      source({ itemId: "a" }),
      source({ itemId: "b" }),
    ]);
    await session.play();
    expect(sources).toHaveLength(0);
    expect(gains).toHaveLength(0);
    expect(analysers).toHaveLength(0);
  });

  it("routes only crossfade items through the graph and schedules successor-owned fades", async () => {
    const { session, context, sources, gains, analysers } = makeSession([
      source({ itemId: "a", recordingDurationSeconds: 10 }),
      source({
        itemId: "b",
        recordingDurationSeconds: 10,
        crossfadeEnabled: 1,
        crossfadeDurationSeconds: 3,
      }),
    ]);
    await session.play();
    // a starts at 0 (dur 10); b overlaps by 3s → starts at 7, total 17.
    expect(session.totalSeconds).toBeCloseTo(17, 6);
    expect(sources).toHaveLength(2);
    expect(gains).toHaveLength(2);
    // Only the first graph-routed item carries the RMS analyser tap.
    expect(analysers).toHaveLength(1);

    const [gainA, gainB] = gains;
    // Outgoing fade on the predecessor is owned by the successor's 3s fade:
    // 1 until itemEnd − 3 = 7, then 1 → 0 across [7, 10] of item a.
    expect(gainA.gain.events).toContainEqual({ type: "set", value: 1, time: 0 });
    expect(gainA.gain.events).toContainEqual({ type: "set", value: 1, time: 7 });
    expect(gainA.gain.events).toContainEqual({
      type: "ramp",
      value: 0,
      time: 10,
    });
    // Incoming fade on b: silent until its start at 7, then 0 → 1 by 10.
    expect(gainB.gain.events).toContainEqual({ type: "set", value: 0, time: 7 });
    expect(gainB.gain.events).toContainEqual({
      type: "ramp",
      value: 1,
      time: 10,
    });
    expect(context.createMediaElementSource).toHaveBeenCalledTimes(2);
  });

  it("advances monotonically on context time and clamps to total", async () => {
    const { session, context } = makeSession([
      source({ itemId: "a", recordingDurationSeconds: 10 }),
    ]);
    await session.play();
    context.currentTime = 4;
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(4, 6);
    context.currentTime = 999;
    expect(session.getTimelinePositionSeconds()).toBe(10);
  });

  it("starts the transport clock only after element play promises resolve", async () => {
    const created: FakeAudio[] = [];
    const fake = makeFakeContext();
    const session = createArrangementPreviewSession(
      [source({ itemId: "a", recordingDurationSeconds: 10 })],
      {
        createAudioContext: () => fake.context as unknown as AudioContext,
        createAudioElement: (src) => {
          const audio = makeFakeAudio(src);
          audio.setPlayMode("defer");
          created.push(audio);
          return audio.element;
        },
        schedulerIntervalMs: 25,
        rmsCheckDelayMs: 1000,
      },
    );

    const playPromise = session.play();
    // Still buffering: the transport clock must not advance.
    expect(session.isPlaying()).toBe(false);
    fake.context.currentTime = 3;
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(0, 6);
    expect(created[0].element.paused).toBe(true);

    created[0].resolvePlays();
    await playPromise;
    expect(session.isPlaying()).toBe(true);
    // Anchor at context time 3 → no drift from the buffering window.
    fake.context.currentTime = 3.025;
    vi.advanceTimersByTime(25);
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(0.025, 6);
  });

  it("pause during the startup hold keeps the pre-start position and does not start the clock", async () => {
    const { session, created, context } = makeSession([
      source({ itemId: "a", recordingDurationSeconds: 10 }),
    ]);
    await session.play();
    context.currentTime = 3;
    session.pause();
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(3, 6);

    // Resume from 3 with cold media: the clock stays held at 3, not 9.
    created[0].setPlayMode("defer");
    const playPromise = session.play();
    context.currentTime = 9;
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(3, 6);

    session.pause();
    expect(session.isPlaying()).toBe(false);
    expect(created[0].element.paused).toBe(true);

    created[0].resolvePlays();
    await playPromise;
    // The held play() must not anchor a clock after the pause, and the pause
    // must not snap back to a stale origin (0) instead of the resume position.
    expect(session.isPlaying()).toBe(false);
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(3, 6);
  });

  it("reports a synchronous element play throw as playback-failed without starting the clock", async () => {
    const created: FakeAudio[] = [];
    const fake = makeFakeContext();
    const session = createArrangementPreviewSession(
      [source({ itemId: "a", recordingDurationSeconds: 10 })],
      {
        createAudioContext: () => fake.context as unknown as AudioContext,
        createAudioElement: (src) => {
          const audio = makeFakeAudio(src);
          audio.setPlayMode("throw");
          created.push(audio);
          return audio.element;
        },
        schedulerIntervalMs: 25,
        rmsCheckDelayMs: 1000,
      },
    );

    await session.play();
    expect(session.getError()).toMatchObject({
      kind: "playback-failed",
      itemId: "a",
    });
    // failPlayback runs synchronously inside applyElementsAt: the hold must
    // already be set so the play() tail aborts instead of anchoring a clock.
    expect(session.isPlaying()).toBe(false);
    expect(created[0].element.paused).toBe(true);
  });

  it("EOF at a clip boundary advances to the next clip instead of failing", async () => {
    // Outro (90→100) then a repeated verse (0→10): the element reaches the
    // recording's EOF at arranged position 10, mid-item, which is expected.
    const { session, created, context } = makeSession([
      source({
        itemId: "a",
        recordingDurationSeconds: 100,
        arrangementSteps: stepsJson([
          { label: "outro", start: 90, end: 100 },
          { label: "verse", start: 0, end: 10 },
        ]),
      }),
    ]);
    await session.play();
    expect(session.totalSeconds).toBeCloseTo(20, 6);
    context.currentTime = 9.9;
    vi.advanceTimersByTime(25);
    expect(created[0].element.paused).toBe(false);

    created[0].dispatch("ended");
    expect(session.getError()).toBeNull();

    context.currentTime = 10.05;
    vi.advanceTimersByTime(25);
    // Verse clip covers [10,20) from source 0 → 0 + (10.05 − 10).
    expect(last(created[0].currentTimeSets)).toBeCloseTo(0.05, 6);
    expect(created[0].element.paused).toBe(false);
    expect(session.getError()).toBeNull();
  });

  it("premature EOF before the active clip end still fails", async () => {
    const { session, created, context } = makeSession([
      source({ itemId: "a", recordingDurationSeconds: 100 }),
    ]);
    await session.play();
    context.currentTime = 5;
    vi.advanceTimersByTime(25);
    created[0].dispatch("ended");
    expect(session.getError()).toMatchObject({
      kind: "playback-failed",
      itemId: "a",
    });
    expect(session.isPlaying()).toBe(false);
  });

  it("crosses step boundaries by reassigning currentTime into the repeated clip", async () => {
    // Draft: Verse (10→20), Chorus (30→40), Verse (10→20) → arranged 30s.
    const { session, created, context } = makeSession([
      source({
        itemId: "a",
        recordingDurationSeconds: 100,
        arrangementSteps: stepsJson([
          { label: "Verse", start: 10, end: 20 },
          { label: "Chorus", start: 30, end: 40 },
          { label: "Verse", start: 10, end: 20 },
        ]),
      }),
    ]);
    await session.play();
    expect(session.totalSeconds).toBeCloseTo(30, 6);
    // Clip 0 covers [0,10) from source 10.
    expect(last(created[0].currentTimeSets)).toBeCloseTo(10, 6);

    context.currentTime = 12.5;
    vi.advanceTimersByTime(25);
    // Clip 1 covers [10,20) from source 30 → 30 + (12.5 − 10) = 32.5.
    expect(last(created[0].currentTimeSets)).toBeCloseTo(32.5, 6);
  });

  it("seek into a repeated clip sets the media currentTime to the looped source offset", async () => {
    const { session, created, context } = makeSession([
      source({
        itemId: "a",
        arrangementSteps: stepsJson([
          { label: "Verse", start: 10, end: 20 },
          { label: "Chorus", start: 30, end: 40 },
          { label: "Verse", start: 10, end: 20 },
        ]),
      }),
    ]);
    await session.play();
    context.currentTime = 25;
    session.seek(25);
    // Third occurrence: source 10 + (25 − 20) = 15.
    expect(last(created[0].currentTimeSets)).toBeCloseTo(15, 6);
    expect(created[0].element.currentTime).toBeCloseTo(15, 6);
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(25, 6);
  });

  it("pause keeps element state, stops the ticker, and resume rebuilds offsets/gains", async () => {
    const { session, created, context, gains } = makeSession([
      source({ itemId: "a", recordingDurationSeconds: 10 }),
      source({
        itemId: "b",
        recordingDurationSeconds: 10,
        crossfadeEnabled: 1,
        crossfadeDurationSeconds: 3,
      }),
    ]);
    await session.play();
    context.currentTime = 3;
    session.pause();
    expect(session.isPlaying()).toBe(false);
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(3, 6);
    expect(created[0].element.paused).toBe(true);

    const playsBefore = created[0].playCalls.length;
    vi.advanceTimersByTime(100);
    expect(created[0].playCalls.length).toBe(playsBefore); // ticker stopped

    const cancelCountBefore =
      gains[0].gain.cancelScheduledValues.mock.calls.length;
    await session.play();
    expect(session.isPlaying()).toBe(true);
    // Fresh origin at context time 3 → element resumes at source offset 3.
    expect(last(created[0].currentTimeSets)).toBeCloseTo(3, 6);
    expect(created[0].playCalls.length).toBeGreaterThan(playsBefore);
    expect(gains[0].gain.cancelScheduledValues.mock.calls.length).toBeGreaterThan(
      cancelCountBefore,
    );

    context.currentTime = 5;
    vi.advanceTimersByTime(25);
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(5, 6);
  });

  it("seek while paused pre-positions the active clip without playing", async () => {
    const { session, created, context } = makeSession([
      source({ itemId: "a", recordingDurationSeconds: 10 }),
    ]);
    await session.play();
    context.currentTime = 2;
    session.pause();
    const playsBefore = created[0].playCalls.length;
    const pauseBefore = created[0].pauseCalls();
    session.seek(6);
    expect(last(created[0].currentTimeSets)).toBeCloseTo(6, 6);
    expect(created[0].playCalls.length).toBe(playsBefore);
    expect(created[0].pauseCalls()).toBe(pauseBefore);
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(6, 6);
  });

  it("pre-play seek is remembered and play starts there", async () => {
    const { session, created } = makeSession([
      source({ itemId: "a", recordingDurationSeconds: 10 }),
    ]);
    session.seek(5);
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(5, 6);
    await session.play();
    expect(last(created[0].currentTimeSets)).toBeCloseTo(5, 6);
    expect(last(created[0].playCalls)?.currentTime).toBeCloseTo(5, 6);
  });

  it("startAt positions from that item's start", async () => {
    const { session, created } = makeSession([
      source({ itemId: "a", recordingDurationSeconds: 10 }),
      source({ itemId: "b", recordingDurationSeconds: 10 }),
    ]);
    await session.play();
    session.startAt(10);
    // Item b has its own element; it starts at source offset 0.
    expect(last(created[1].currentTimeSets)).toBeCloseTo(0, 6);
    expect(last(created[1].playCalls)?.muted).toBe(false);
    expect(created[0].element.paused).toBe(true);
  });

  it("starts future items at their item start from the scheduler", async () => {
    const { session, created, context } = makeSession([
      source({ itemId: "a", recordingDurationSeconds: 10 }),
      source({ itemId: "b", recordingDurationSeconds: 10 }),
    ]);
    await session.play();
    expect(created[1].playCalls).toHaveLength(1); // probe only; not audible yet
    context.currentTime = 10.5;
    vi.advanceTimersByTime(25);
    expect(created[0].element.paused).toBe(true);
    expect(created[1].element.paused).toBe(false);
    expect(last(created[1].currentTimeSets)).toBeCloseTo(0.5, 6);
  });

  it("ends the timeline at totalSeconds (pause at item end)", async () => {
    const { session, created, context } = makeSession([
      source({ itemId: "a", recordingDurationSeconds: 10 }),
    ]);
    await session.play();
    context.currentTime = 10;
    vi.advanceTimersByTime(25);
    expect(session.isPlaying()).toBe(false);
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(10, 6);
    expect(created[0].element.paused).toBe(true);
  });

  it("reports rejected element play promises as playback-failed", async () => {
    const created: FakeAudio[] = [];
    const fake = makeFakeContext();
    const session = createArrangementPreviewSession(
      [source({ itemId: "a" })],
      {
        createAudioContext: () => fake.context as unknown as AudioContext,
        createAudioElement: (src) => {
          const audio = makeFakeAudio(src);
          audio.setPlayMode("reject");
          created.push(audio);
          return audio.element;
        },
        schedulerIntervalMs: 25,
        rmsCheckDelayMs: 1000,
      },
    );
    await session.play();
    expect(session.getError()).toMatchObject({
      kind: "playback-failed",
      itemId: "a",
    });
    expect(session.isPlaying()).toBe(false);
    expect(created[0].element.paused).toBe(true);
  });

  it("reports media error events as playback-failed", async () => {
    const { session, created } = makeSession([source({ itemId: "a" })]);
    await session.play();
    created[0].dispatch("error");
    expect(session.getError()).toMatchObject({
      kind: "playback-failed",
      itemId: "a",
    });
    expect(session.isPlaying()).toBe(false);
  });

  it("detects silent graph output, tears the graph down, and keeps the element-driven transport", async () => {
    const { session, created, context, sources, gains, analysers } = makeSession(
      [
        source({ itemId: "a", recordingDurationSeconds: 10 }),
        source({
          itemId: "b",
          recordingDurationSeconds: 10,
          crossfadeEnabled: 1,
          crossfadeDurationSeconds: 3,
        }),
      ],
      {},
      0, // silent analyser
    );
    await session.play();
    expect(created).toHaveLength(2);
    expect(context.createMediaElementSource).toHaveBeenCalledTimes(2);

    vi.advanceTimersByTime(1000);
    expect(session.isFallbackMode()).toBe(true);
    // Graph torn down.
    for (const node of sources) expect(node.disconnect).toHaveBeenCalled();
    for (const node of gains) expect(node.disconnect).toHaveBeenCalled();
    for (const analyser of analysers) {
      expect(analyser.disconnect).toHaveBeenCalled();
    }
    // Old (captured) elements paused; fresh direct elements created without
    // new source nodes.
    expect(created[0].element.paused).toBe(true);
    expect(created).toHaveLength(4);
    expect(context.createMediaElementSource).toHaveBeenCalledTimes(2);
    // Transport survives: the scheduler still drives replacement elements.
    context.currentTime = 8;
    vi.advanceTimersByTime(25);
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(8, 6);
    expect(created[3].element.paused).toBe(false);
    // Fallback is one-way: no further element churn.
    vi.advanceTimersByTime(2000);
    expect(created).toHaveLength(4);
  });

  it("does not fall back when the analyser reports audible output", async () => {
    const { session, created } = makeSession(
      [
        source({ itemId: "a", recordingDurationSeconds: 10 }),
        source({
          itemId: "b",
          recordingDurationSeconds: 10,
          crossfadeEnabled: 1,
          crossfadeDurationSeconds: 3,
        }),
      ],
      {},
      0.25,
    );
    await session.play();
    vi.advanceTimersByTime(1000);
    expect(session.isFallbackMode()).toBe(false);
    expect(created).toHaveLength(2);
  });

  it("skips the RMS check while paused and re-arms on the next play", async () => {
    const { session, created, context, sources } = makeSession(
      [
        source({ itemId: "a", recordingDurationSeconds: 10 }),
        source({
          itemId: "b",
          recordingDurationSeconds: 10,
          crossfadeEnabled: 1,
          crossfadeDurationSeconds: 3,
        }),
      ],
      {},
      0,
    );
    await session.play();
    session.pause();
    vi.advanceTimersByTime(1000);
    expect(session.isFallbackMode()).toBe(false);
    expect(sources).toHaveLength(2);
    expect(context.createMediaElementSource).toHaveBeenCalledTimes(2);
    // Re-armed: resuming with the first graph element audible detects silence.
    await session.play();
    vi.advanceTimersByTime(1000);
    expect(session.isFallbackMode()).toBe(true);
    expect(created.length).toBeGreaterThan(2);
  });

  it("seek within the same clip forces a reposition", async () => {
    const { session, created, context } = makeSession([
      source({ itemId: "a", recordingDurationSeconds: 10 }),
    ]);
    await session.play();
    context.currentTime = 2;
    session.seek(5);
    expect(last(created[0].currentTimeSets)).toBeCloseTo(5, 6);
    expect(created[0].element.currentTime).toBeCloseTo(5, 6);
  });

  it("recomputes incoming and outgoing fades when seeking into a crossfade", async () => {
    const { session, created, context, gains } = makeSession([
      source({ itemId: "a", recordingDurationSeconds: 10 }),
      source({
        itemId: "b",
        recordingDurationSeconds: 10,
        crossfadeEnabled: 1,
        crossfadeDurationSeconds: 3,
      }),
    ]);
    await session.play();
    context.currentTime = 8;
    session.seek(8);
    const [gainA, gainB] = gains;
    // b is 1s into its 3s fade: gain 1/3 at ctx 8, ramping to 1 by ctx 10.
    expect(gainB.gain.events).toContainEqual({
      type: "set",
      value: 1 / 3,
      time: 8,
    });
    expect(gainB.gain.events).toContainEqual({
      type: "ramp",
      value: 1,
      time: 10,
    });
    // a is 1s into the successor-owned 3s fade-out: 2/3 → 0 by ctx 10.
    expect(gainA.gain.events).toContainEqual({
      type: "set",
      value: 2 / 3,
      time: 8,
    });
    expect(gainA.gain.events).toContainEqual({
      type: "ramp",
      value: 0,
      time: 10,
    });
    // The successor element starts inside its fade at the right offset.
    expect(created[1].element.paused).toBe(false);
    expect(last(created[1].currentTimeSets)).toBeCloseTo(1, 6);
  });

  it("cleans up everything on stop (timers, listeners, elements, graph, context)", async () => {
    const { session, created, context, sources, gains, analysers } = makeSession([
      source({ itemId: "a", recordingDurationSeconds: 10 }),
      source({
        itemId: "b",
        recordingDurationSeconds: 10,
        crossfadeEnabled: 1,
        crossfadeDurationSeconds: 3,
      }),
    ]);
    await session.play();
    const playsBefore = created[0].playCalls.length;
    session.stop();

    for (const audio of created) {
      expect(audio.element.paused).toBe(true);
      expect(audio.removedListeners).toEqual(
        expect.arrayContaining(["ended", "error"]),
      );
      expect(audio.removedAttributes).toContain("src");
      expect(audio.loadCalls()).toBeGreaterThanOrEqual(1);
    }
    for (const node of sources) expect(node.disconnect).toHaveBeenCalled();
    for (const node of gains) expect(node.disconnect).toHaveBeenCalled();
    for (const analyser of analysers) {
      expect(analyser.disconnect).toHaveBeenCalled();
    }
    expect(context.close).toHaveBeenCalledTimes(1);

    // Ticker is gone: no further element activity.
    vi.advanceTimersByTime(200);
    expect(created[0].playCalls.length).toBe(playsBefore);
    // stop() is idempotent.
    session.stop();
    expect(context.close).toHaveBeenCalledTimes(1);
  });

  it("stop before playback closes nothing it never opened", () => {
    const createContext = vi.fn();
    const session = createArrangementPreviewSession([source({ itemId: "a" })], {
      createAudioContext: createContext,
      createAudioElement: vi.fn(),
    });
    session.stop();
    expect(createContext).not.toHaveBeenCalled();
  });

  it("notifies state listeners on transport changes and unsubscribes", async () => {
    const { session, context } = makeSession([
      source({ itemId: "a", recordingDurationSeconds: 10 }),
    ]);
    const listener = vi.fn();
    const unsubscribe = session.onStateChange(listener);
    await session.play();
    const afterPlay = listener.mock.calls.length;
    expect(afterPlay).toBeGreaterThan(0);
    context.currentTime = 2;
    vi.advanceTimersByTime(25);
    expect(listener.mock.calls.length).toBeGreaterThan(afterPlay);
    unsubscribe();
    const afterUnsubscribe = listener.mock.calls.length;
    context.currentTime = 3;
    vi.advanceTimersByTime(25);
    expect(listener.mock.calls.length).toBe(afterUnsubscribe);
  });
});
