import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import {
  createSongsetAudioSession,
  validateSongsetAudioSources,
  type SongsetAudioProgress,
  type SongsetAudioSession,
  type SongsetAudioSessionOptions,
} from "@/lib/audio/songset-audio";
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
    recordingSections: null,
    recordingBeats: [],
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

/** Mirrors the persisted schema; parsed analysis matches the steps exactly. */
function arrangedSource(itemId: string): TimelineItemSource {
  const sections = [
    { label: "A", start: 0, end: 4 },
    { label: "B", start: 6, end: 9 },
  ];
  return source({
    itemId,
    recordingDurationSeconds: 10,
    arrangementSteps: stepsJson([
      { label: "A", start: 0, end: 4 },
      { label: "B", start: 6, end: 9 },
    ]),
    recordingSections: sections,
    recordingBeats: [0, 4, 6, 9],
  });
}

// ---------------------------------------------------------------------------
// Fake Web Audio
// ---------------------------------------------------------------------------

interface StartCall {
  when: number;
  offset: number;
  duration: number | null;
}

interface ParamEvent {
  kind: "set" | "ramp";
  value: number;
  time: number;
}

interface FakeSourceRecord {
  node: AudioBufferSourceNode;
  buffer: AudioBuffer | null;
  starts: StartCall[];
  stops: number;
  connections: unknown[];
}

interface FakeGainRecord {
  node: GainNode;
  events: ParamEvent[];
  connections: unknown[];
}

interface FakeBuffer extends AudioBuffer {
  copyCalls: Array<{ channel: number; data: Float32Array }>;
}

interface FakeAudioContext {
  context: AudioContext;
  currentTime: number;
  sources: FakeSourceRecord[];
  gains: FakeGainRecord[];
  buffers: FakeBuffer[];
  resume: Mock;
  suspend: Mock;
  close: Mock;
  decodeAudioData: Mock;
}

function decodedBuffer(
  length = 1000,
  sampleRate = 100,
  channels = 2,
): AudioBuffer {
  const channelData = Array.from({ length: channels }, (_, channel) => {
    const data = new Float32Array(length);
    for (let i = 0; i < length; i++) data[i] = channel * 1000 + i;
    return data;
  });
  return {
    length,
    sampleRate,
    numberOfChannels: channels,
    duration: length / sampleRate,
    getChannelData: (channel: number) => channelData[channel]!,
  } as unknown as AudioBuffer;
}

function makeFakeContext(): FakeAudioContext {
  const fake = {
    currentTime: 0,
    sources: [] as FakeSourceRecord[],
    gains: [] as FakeGainRecord[],
    buffers: [] as FakeBuffer[],
    resume: vi.fn(async () => {}),
    suspend: vi.fn(async () => {}),
    close: vi.fn(async () => {}),
    decodeAudioData: vi.fn(async () => decodedBuffer()),
  } as FakeAudioContext;

  const context = {
    get currentTime() {
      return fake.currentTime;
    },
    destination: {} as AudioDestinationNode,
    resume: fake.resume,
    suspend: fake.suspend,
    close: fake.close,
    createBuffer: vi.fn(
      (numberOfChannels: number, length: number, sampleRate: number) => {
        const channelData = Array.from(
          { length: numberOfChannels },
          () => new Float32Array(length),
        );
        const copyCalls: Array<{ channel: number; data: Float32Array }> = [];
        const buffer = {
          numberOfChannels,
          length,
          sampleRate,
          duration: length / sampleRate,
          getChannelData: (channel: number) => channelData[channel]!,
          copyToChannel: (data: Float32Array, channel: number) => {
            copyCalls.push({ channel, data });
            channelData[channel]!.set(data);
          },
          copyCalls,
        } as FakeBuffer;
        fake.buffers.push(buffer);
        return buffer;
      },
    ),
    createBufferSource: vi.fn(() => {
      const record: FakeSourceRecord = {
        node: {} as AudioBufferSourceNode,
        buffer: null,
        starts: [],
        stops: 0,
        connections: [],
      };
      const node = {
        get buffer() {
          return record.buffer;
        },
        set buffer(value: AudioBuffer | null) {
          record.buffer = value;
        },
        connect: vi.fn((target: unknown) => {
          record.connections.push(target);
        }),
        disconnect: vi.fn(),
        start: vi.fn(
          (when?: number, offset?: number, duration?: number) => {
            record.starts.push({
              when: when ?? 0,
              offset: offset ?? 0,
              duration: duration ?? null,
            });
          },
        ),
        stop: vi.fn(() => {
          record.stops += 1;
        }),
      };
      record.node = node as unknown as AudioBufferSourceNode;
      fake.sources.push(record);
      return record.node;
    }),
    createGain: vi.fn(() => {
      const events: ParamEvent[] = [];
      const record: FakeGainRecord = {
        node: {} as GainNode,
        events,
        connections: [],
      };
      const param = {
        setValueAtTime: (value: number, time: number) => {
          events.push({ kind: "set", value, time });
        },
        linearRampToValueAtTime: (value: number, time: number) => {
          events.push({ kind: "ramp", value, time });
        },
        cancelScheduledValues: vi.fn(),
        setTargetAtTime: vi.fn(),
      };
      const node = {
        gain: param as unknown as AudioParam,
        connect: vi.fn((target: unknown) => {
          record.connections.push(target);
        }),
        disconnect: vi.fn(),
      };
      record.node = node as unknown as GainNode;
      fake.gains.push(record);
      return record.node;
    }),
    decodeAudioData: fake.decodeAudioData,
  } as unknown as AudioContext;

  fake.context = context;
  return fake;
}

function valueAt(events: ParamEvent[], time: number): number {
  const first = events[0];
  if (!first) return 1;
  let value = first.value;
  let previousTime = first.time;
  for (let i = 1; i < events.length; i++) {
    const event = events[i]!;
    if (event.time <= time) {
      value = event.value;
      previousTime = event.time;
      continue;
    }
    if (event.kind !== "ramp") return value;
    const span = event.time - previousTime;
    if (span <= 0) return event.value;
    return value + ((event.value - value) * (time - previousTime)) / span;
  }
  return value;
}

const flushMicrotasks = async (): Promise<void> => {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
};

type FakeOptions = Partial<SongsetAudioSessionOptions> & {
  context?: FakeAudioContext;
  fetchArrayBuffer?: SongsetAudioSessionOptions["fetchArrayBuffer"];
};

let sessions: SongsetAudioSession[] = [];

function buildSession(
  items: TimelineItemSource[],
  options: FakeOptions = {},
): { session: SongsetAudioSession; context: FakeAudioContext } {
  const context = options.context ?? makeFakeContext();
  const fetchArrayBuffer =
    options.fetchArrayBuffer ??
    vi.fn(async (_url: string, onProgress: (received: number, total: number | null) => void) => {
      onProgress(4, 4);
      return new ArrayBuffer(8);
    });
  const session = createSongsetAudioSession(items, {
    createAudioContext: () => context.context,
    fetchArrayBuffer,
    ...options,
  });
  sessions.push(session);
  return { session, context };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  for (const session of sessions) session.stop();
  sessions = [];
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

// ---------------------------------------------------------------------------
// Clip offsets and scheduling
// ---------------------------------------------------------------------------

describe("songset audio scheduling", () => {
  it("starts the first clip at the source offset and schedules later clips on the context clock", async () => {
    const { session, context } = buildSession([arrangedSource("a")]);
    const decoded = decodedBuffer(1000);
    context.decodeAudioData.mockResolvedValueOnce(decoded);

    await session.play();

    // Two arranged clips: [0,4) from source 0 and [4,7) from source 6.
    expect(context.sources).toHaveLength(2);
    expect(context.sources[0]!.starts[0]).toMatchObject({
      when: 0,
      offset: 0,
      duration: 4,
    });
    expect(context.sources[1]!.starts[0]!.when).toBeCloseTo(4, 6);
    // The second clip's buffer starts at its own source offset 6, so playing
    // it from its first sample is clip-relative offset 0.
    expect(context.sources[1]!.starts[0]!.offset).toBeCloseTo(0, 6);
    expect(context.sources[1]!.starts[0]!.duration).toBeCloseTo(3, 6);

    // Clip buffers are small copies, not the decoded Recording.
    expect(context.buffers).toHaveLength(2);
    expect(context.buffers[0]).not.toBe(decoded);
    expect(context.buffers[0]!.length).toBe(400);
    expect(context.buffers[1]!.length).toBe(300);
    expect(context.buffers[0]!.copyCalls[0]!.data[0]).toBe(0);
    expect(context.buffers[0]!.copyCalls[0]!.data[3]).toBe(3);
    expect(context.buffers[0]!.copyCalls[1]!.data[0]).toBe(1000);

    // Seeking inside the first clip preserves the source offset.
    const beforeReSeek = context.sources.length;
    context.currentTime = 2;
    session.seek(2);
    const rebuilt = context.sources.slice(beforeReSeek);
    expect(rebuilt[0]!.starts[0]).toMatchObject({
      when: 2,
      offset: 2,
      duration: 2,
    });
    expect(rebuilt[1]!.starts[0]!.when).toBeCloseTo(4, 6);
    expect(rebuilt[1]!.starts[0]!.offset).toBeCloseTo(0, 6);
    expect(rebuilt[1]!.starts[0]!.duration).toBeCloseTo(3, 6);
  });

  it("composes overlapping incoming and successor-owned outgoing ramps through two series gain nodes", async () => {
    // item1's own crossfade is 1 s while its successor's is 2 s, so the
    // incoming [9,10] and successor-owned outgoing [9,11] windows overlap.
    const items = [
      source({ itemId: "a", recordingDurationSeconds: 10 }),
      source({
        itemId: "b",
        recordingDurationSeconds: 2,
        crossfadeEnabled: 1,
        crossfadeDurationSeconds: 1,
      }),
      source({
        itemId: "c",
        recordingDurationSeconds: 10,
        crossfadeEnabled: 1,
        crossfadeDurationSeconds: 2,
      }),
    ];
    const { session, context } = buildSession(items);

    // Play opens item a; the ticker prepare-aheads item b before the seek.
    await session.play();
    await vi.advanceTimersByTimeAsync(200);
    await flushMicrotasks();
    context.currentTime = 9;
    session.seek(9);

    // The last-created gain pair belongs to item b.
    expect(context.gains.length).toBeGreaterThanOrEqual(4);
    const [gainIn, gainOut] = context.gains.slice(-2) as [
      FakeGainRecord,
      FakeGainRecord,
    ];
    expect(gainIn.node.connect).toHaveBeenCalledWith(gainOut.node);
    expect(gainOut.node.connect).toHaveBeenCalledWith(context.context.destination);

    // Incoming owns [9,10]: 0 -> 1 over one second.
    expect(gainIn.events[0]).toMatchObject({ kind: "set", value: 0, time: 9 });
    expect(gainIn.events.at(-1)).toMatchObject({ kind: "ramp", value: 1, time: 10 });

    // Outgoing is owned by the SUCCESSOR'S 2 s fade: [9,11], 1 -> 0.
    expect(gainOut.events[0]).toMatchObject({ kind: "set", value: 1, time: 9 });
    expect(gainOut.events.at(-1)).toMatchObject({ kind: "ramp", value: 0, time: 11 });

    // Mid-overlap the listener hears the product of both series envelopes.
    const composed = valueAt(gainIn.events, 9.5) * valueAt(gainOut.events, 9.5);
    expect(valueAt(gainIn.events, 9.5)).toBeCloseTo(0.5, 6);
    expect(valueAt(gainOut.events, 9.5)).toBeCloseTo(0.75, 6);
    expect(composed).toBeCloseTo(0.375, 6);
  });
});

// ---------------------------------------------------------------------------
// Startup, prepare-ahead, and late decodes
// ---------------------------------------------------------------------------

describe("songset audio prepare lifecycle", () => {
  it("prepares only the play-position item, keeps one prepare in flight, and schedules a late decode additively", async () => {
    const items = ["a", "b", "c"].map((itemId) =>
      source({ itemId, recordingDurationSeconds: 10 }),
    );
    const pending = new Map<
      string,
      { resolve: (buffer: ArrayBuffer) => void; reject: (error: unknown) => void }
    >();
    const decodedByData = new Map<ArrayBuffer, AudioBuffer>();
    let inFlight = 0;
    let maxInFlight = 0;
    const fetchArrayBuffer = vi.fn((url: string) => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      const { promise, resolve, reject } =
        Promise.withResolvers<ArrayBuffer>();
      pending.set(url, {
        resolve: (buffer) => {
          inFlight -= 1;
          resolve(buffer);
        },
        reject: (error) => {
          inFlight -= 1;
          reject(error);
        },
      });
      return promise;
    });
    const { session, context } = buildSession(items, { fetchArrayBuffer });
    context.decodeAudioData.mockImplementation(
      async (data: ArrayBuffer) => decodedByData.get(data)!,
    );

    const resolveFetch = (itemId: string, decoded: AudioBuffer): void => {
      const data = new ArrayBuffer(8);
      decodedByData.set(data, decoded);
      pending.get(`/api/recordings/hash-${itemId}/audio`)!.resolve(data);
    };

    const firstPlay = session.play();
    await flushMicrotasks();
    // A concurrent play joins the in-flight start instead of fetching again.
    const secondPlay = session.play();
    await flushMicrotasks();
    expect(fetchArrayBuffer).toHaveBeenCalledTimes(1);
    expect(session.getProgress()).toMatchObject({ itemId: "a", itemIndex: 0 });

    resolveFetch("a", decodedBuffer(1000));
    await Promise.all([firstPlay, secondPlay]);
    expect(fetchArrayBuffer).toHaveBeenCalledTimes(1);
    expect(context.sources).toHaveLength(1);
    expect(inFlight).toBe(0);

    // The ticker prepare-aheads item b only after item a settled.
    await vi.advanceTimersByTimeAsync(200);
    expect(fetchArrayBuffer).toHaveBeenCalledTimes(2);
    expect(inFlight).toBe(1);
    await vi.advanceTimersByTimeAsync(200);
    expect(fetchArrayBuffer).toHaveBeenCalledTimes(2);

    const soundingStarts = context.sources[0]!.starts.length;
    resolveFetch("b", decodedBuffer(1000));
    await vi.advanceTimersByTimeAsync(0);
    await flushMicrotasks();

    // The late decode adds item b without rebuilding the graph.
    expect(context.sources).toHaveLength(2);
    expect(context.sources[0]!.starts.length).toBe(soundingStarts);
    expect(context.sources[1]!.starts[0]).toMatchObject({
      when: 10,
      offset: 0,
      duration: 10,
    });
    expect(maxInFlight).toBe(1);
  });

  it("reports progress only while a prepare is in flight and forces a ready snapshot inside its notification", async () => {
    let resolveFetch!: (buffer: ArrayBuffer) => void;
    let rejectDecode!: (error: unknown) => void;
    let reportProgress!: (received: number, total: number | null) => void;
    const fetchArrayBuffer = vi.fn(
      (_url: string, onProgress: (received: number, total: number | null) => void) => {
        reportProgress = onProgress;
        const { promise, resolve } = Promise.withResolvers<ArrayBuffer>();
        resolveFetch = resolve;
        return promise;
      },
    );
    const { session, context } = buildSession([source({ itemId: "a" })], {
      fetchArrayBuffer,
    });
    context.decodeAudioData.mockImplementation(() => {
      const { promise, reject } = Promise.withResolvers<AudioBuffer>();
      rejectDecode = reject;
      return promise;
    });

    const snapshots: Array<SongsetAudioProgress | null> = [];
    session.onStateChange(() => {
      snapshots.push(session.getProgress());
    });

    const playPromise = session.play();
    await flushMicrotasks();
    expect(session.getProgress()).toMatchObject({
      itemId: "a",
      itemIndex: 0,
      itemCount: 1,
      phase: "fetching",
      receivedBytes: 0,
      totalBytes: null,
    });

    reportProgress(5, 10);
    expect(session.getProgress()).toMatchObject({
      phase: "fetching",
      receivedBytes: 5,
      totalBytes: 10,
    });

    resolveFetch(new ArrayBuffer(8));
    await flushMicrotasks();
    expect(session.getProgress()).toMatchObject({ phase: "decoding" });

    rejectDecode(new Error("bad decode"));
    await expect(playPromise).rejects.toThrow("bad decode");
    expect(session.getProgress()).toBeNull();
    expect(session.getError()).toMatchObject({
      kind: "playback-failed",
      itemId: "a",
      message: "bad decode",
    });
    expect(snapshots.some((progress) => progress?.phase === "decoding")).toBe(true);
    // A settled prepare is never observable through a later getProgress().
    expect(session.getProgress()).toBeNull();
  });

  it("observes the settled ready phase only inside the prepare notification", async () => {
    const { session } = buildSession([source({ itemId: "a" })]);
    const readySnapshots: Array<SongsetAudioProgress | null> = [];
    session.onStateChange(() => {
      const progress = session.getProgress();
      if (progress?.phase === "ready") readySnapshots.push(progress);
    });

    await session.play();
    expect(readySnapshots).toHaveLength(1);
    expect(readySnapshots[0]).toMatchObject({
      itemId: "a",
      itemIndex: 0,
      itemCount: 1,
      phase: "ready",
    });
    expect(session.getProgress()).toBeNull();
    session.pause();
    expect(session.getProgress()).toBeNull();
    session.stop();
    expect(session.getProgress()).toBeNull();
  });

  it("retries a rejected prepare and reuses successful clip buffers", async () => {
    const fetchArrayBuffer = vi.fn(
      async (_url: string, onProgress: (received: number, total: number | null) => void) => {
        onProgress(4, 4);
        return new ArrayBuffer(8);
      },
    );
    const { session, context } = buildSession([source({ itemId: "a" })], {
      fetchArrayBuffer,
    });
    context.decodeAudioData
      .mockRejectedValueOnce(new Error("transient decode"))
      .mockResolvedValue(decodedBuffer());

    await expect(session.play()).rejects.toThrow("transient decode");
    expect(session.getError()).toMatchObject({
      kind: "playback-failed",
      itemId: "a",
      message: "transient decode",
    });
    expect(fetchArrayBuffer).toHaveBeenCalledTimes(1);

    await session.play();
    expect(session.getError()).toBeNull();
    expect(fetchArrayBuffer).toHaveBeenCalledTimes(2);
    expect(context.buffers).toHaveLength(1);

    session.pause();
    await session.play();
    // The successful prepare is memoized: no refetch, no re-decode, no new copy.
    expect(fetchArrayBuffer).toHaveBeenCalledTimes(2);
    expect(context.decodeAudioData).toHaveBeenCalledTimes(2);
    expect(context.buffers).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// Clock, transport, and inert stop
// ---------------------------------------------------------------------------

describe("songset audio transport", () => {
  it("takes position from the AudioContext clock, clamps, and never suspends or closes the context", async () => {
    const { session, context } = buildSession([
      source({ itemId: "a", recordingDurationSeconds: 100 }),
    ]);

    await session.play();
    expect(session.isPlaying()).toBe(true);
    expect(context.resume).toHaveBeenCalledTimes(1);

    context.currentTime = 3;
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(3, 6);
    context.currentTime = 500;
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(100, 6);

    context.currentTime = 7;
    session.pause();
    expect(session.isPlaying()).toBe(false);
    expect(context.suspend).not.toHaveBeenCalled();
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(7, 6);

    context.currentTime = 20;
    await session.play();
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(7, 6);

    session.stop();
    expect(context.close).not.toHaveBeenCalled();
    expect(context.suspend).not.toHaveBeenCalled();
    expect(session.getTimelinePositionSeconds()).toBe(0);
    expect(session.getError()).toBeNull();

    const sourceCount = context.sources.length;
    await session.play();
    session.seek(50);
    await session.startAtSeconds(80);
    session.pause();
    session.stop();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(context.resume).toHaveBeenCalledTimes(2);
    expect(context.sources).toHaveLength(sourceCount);
  });

  it("stops at the end of the timeline", async () => {
    const { session, context } = buildSession([
      source({ itemId: "a", recordingDurationSeconds: 10 }),
    ]);
    await session.play();

    context.currentTime = 10;
    await vi.advanceTimersByTimeAsync(200);
    expect(session.isPlaying()).toBe(false);
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(10, 6);
  });
});

// ---------------------------------------------------------------------------
// Retained PCM budget (issue #296)
// ---------------------------------------------------------------------------

describe("songset audio retained budget", () => {
  const hashFromUrl = (url: string): string => url.split("/")[3]!;

  function countingFetch(): {
    fetchArrayBuffer: SongsetAudioSessionOptions["fetchArrayBuffer"];
    urls: string[];
  } {
    const urls: string[] = [];
    const fetchArrayBuffer = vi.fn(
      async (
        url: string,
        onProgress: (received: number, total: number | null) => void,
      ) => {
        urls.push(url);
        onProgress(4, 4);
        return new ArrayBuffer(8);
      },
    );
    return { fetchArrayBuffer, urls };
  }

  const tenSecondItems = (ids: string[]): TimelineItemSource[] =>
    ids.map((itemId) => source({ itemId, recordingDurationSeconds: 10 }));

  it("evicts finished clips oldest-first and stops once the budget is met", async () => {
    const { fetchArrayBuffer, urls } = countingFetch();
    const { session, context } = buildSession(
      tenSecondItems(["a", "b", "c", "d"]),
      { fetchArrayBuffer, retainedBudgetSeconds: 30 },
    );

    await session.play();
    await vi.advanceTimersByTimeAsync(200); // prepare-ahead b
    context.currentTime = 10;
    await vi.advanceTimersByTimeAsync(200); // prepare-ahead c
    context.currentTime = 20;
    await vi.advanceTimersByTimeAsync(200); // prepare-ahead d evicts a

    // Eviction must also release the clip held by its scheduled source node;
    // deleting only the prepare memo would leave PCM strongly reachable.
    expect(context.sources[0]!.buffer).toBeNull();
    expect(context.sources[1]!.buffer).not.toBeNull();
    expect(context.sources[1]!.stops).toBe(0);

    expect(urls.map(hashFromUrl)).toEqual([
      "hash-a",
      "hash-b",
      "hash-c",
      "hash-d",
    ]);

    // a and b are finished at 20, but the 30 s target needs only one eviction:
    // the oldest (a) is dropped and b stays retained.
    session.pause();
    session.seek(15);
    await vi.advanceTimersByTimeAsync(0);
    await flushMicrotasks();
    expect(urls).toHaveLength(4);

    session.seek(5);
    await vi.advanceTimersByTimeAsync(0);
    await flushMicrotasks();
    expect(urls.map(hashFromUrl)).toEqual([
      "hash-a",
      "hash-b",
      "hash-c",
      "hash-d",
      "hash-a",
    ]);
    session.stop();
  });

  it("never evicts the current or later items and tolerates an over-budget target", async () => {
    const { fetchArrayBuffer, urls } = countingFetch();
    const { session, context } = buildSession(tenSecondItems(["a", "b", "c"]), {
      fetchArrayBuffer,
      retainedBudgetSeconds: 5,
    });

    await session.play();
    await vi.advanceTimersByTimeAsync(200); // b exceeds the target alone; still prepared
    context.currentTime = 10;
    await vi.advanceTimersByTimeAsync(200); // a evicted for c; b is current

    expect(urls.map(hashFromUrl)).toEqual(["hash-a", "hash-b", "hash-c"]);
    expect(session.getError()).toBeNull();

    session.pause();
    session.seek(15); // b contained the position: retained
    await vi.advanceTimersByTimeAsync(0);
    await flushMicrotasks();
    expect(urls).toHaveLength(3);

    session.seek(25); // c is later than the position: retained
    await vi.advanceTimersByTimeAsync(0);
    await flushMicrotasks();
    expect(urls).toHaveLength(3);
    session.stop();
  });

  it("prepares and plays an item longer than the budget without looping or rejecting", async () => {
    const { fetchArrayBuffer, urls } = countingFetch();
    const { session, context } = buildSession(
      [source({ itemId: "long", recordingDurationSeconds: 600 })],
      { fetchArrayBuffer, retainedBudgetSeconds: 5 },
    );

    await session.play();
    expect(session.isPlaying()).toBe(true);
    expect(session.getError()).toBeNull();
    expect(urls).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(600);
    expect(urls).toHaveLength(1);
    expect(session.getError()).toBeNull();

    context.currentTime = 600;
    await vi.advanceTimersByTimeAsync(200);
    expect(session.isPlaying()).toBe(false);
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(600, 6);
    session.stop();
  });
});

// ---------------------------------------------------------------------------
// Seek while playing or paused (issue #296)
// ---------------------------------------------------------------------------

describe("songset audio seek hardening", () => {
  const hashFromUrl = (url: string): string => url.split("/")[3]!;
  const tenSecondPair = (): TimelineItemSource[] => [
    source({ itemId: "a", recordingDurationSeconds: 10 }),
    source({ itemId: "b", recordingDurationSeconds: 10 }),
  ];

  function pendingSecondItemFetch(): {
    fetchArrayBuffer: SongsetAudioSessionOptions["fetchArrayBuffer"];
    pending: Map<string, (buffer: ArrayBuffer) => void>;
  } {
    const pending = new Map<string, (buffer: ArrayBuffer) => void>();
    const fetchArrayBuffer = vi.fn(
      (
        url: string,
        onProgress: (received: number, total: number | null) => void,
      ) => {
        onProgress(4, 4);
        if (url.endsWith("/hash-a/audio")) {
          return Promise.resolve(new ArrayBuffer(8));
        }
        const { promise, resolve } = Promise.withResolvers<ArrayBuffer>();
        pending.set(url, resolve);
        return promise;
      },
    );
    return { fetchArrayBuffer, pending };
  }

  it("freezes the clock while a seek awaits decode, then resumes at the requested second", async () => {
    const { fetchArrayBuffer, pending } = pendingSecondItemFetch();
    const { session, context } = buildSession(tenSecondPair(), {
      fetchArrayBuffer,
    });

    await session.play();
    session.seek(15);
    await vi.advanceTimersByTimeAsync(0);
    await flushMicrotasks();

    expect(session.isPlaying()).toBe(true);
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(15, 6);
    expect(session.getProgress()).toMatchObject({
      itemId: "b",
      itemIndex: 1,
      phase: "fetching",
    });

    // Time passes while decoding; the ticker must not advance the position.
    context.currentTime = 5;
    await vi.advanceTimersByTimeAsync(600);
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(15, 6);

    pending.get("/api/recordings/hash-b/audio")!(new ArrayBuffer(8));
    await vi.advanceTimersByTimeAsync(0);
    await flushMicrotasks();

    expect(session.isPlaying()).toBe(true);
    expect(session.getProgress()).toBeNull();
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(15, 6);

    // The rebuild restarts the clock at the requested offset on the context
    // clock: b's clip is [10, 20), so the requested 15 s is source offset 5.
    const resumed = context.sources.at(-1)!;
    expect(resumed.starts[0]).toMatchObject({
      when: 5,
      offset: 5,
      duration: 5,
    });

    context.currentTime = 7;
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(17, 6);
    session.stop();
  });

  it("restarts when play follows a pause during startup preparation", async () => {
    const { fetchArrayBuffer, pending } = pendingSecondItemFetch();
    const { session } = buildSession(tenSecondPair(), { fetchArrayBuffer });

    const firstPlay = session.play();
    await flushMicrotasks();
    session.pause();
    const secondPlay = session.play();
    await flushMicrotasks();
    await Promise.all([firstPlay, secondPlay]);

    expect(session.isPlaying()).toBe(true);
    expect(session.getError()).toBeNull();
    session.stop();
  });

  it("does not additively schedule a prepared item during a frozen seek", async () => {
    const { fetchArrayBuffer, pending } = pendingSecondItemFetch();
    const { session, context } = buildSession(tenSecondPair(), {
      fetchArrayBuffer,
    });

    await session.play();
    await vi.advanceTimersByTimeAsync(200); // start b's background prepare
    await flushMicrotasks();
    context.currentTime = 10;
    session.seek(18);
    await flushMicrotasks();
    const sourceCount = context.sources.length;

    // The seek completion clears awaiting and schedules b's remaining clip;
    // no additive duplicate is created.
    pending.get("/api/recordings/hash-b/audio")!(new ArrayBuffer(8));
    await vi.advanceTimersByTimeAsync(0);
    await flushMicrotasks();
    expect(session.getTimelinePositionSeconds()).toBe(18);
    expect(context.sources).toHaveLength(sourceCount + 1);
    expect(context.sources.at(-1)!.starts).toHaveLength(1);
    expect(context.sources.at(-1)!.starts[0]).toMatchObject({
      when: 10,
      duration: 2,
    });
    session.stop();
  });

  it("ignores a stale seek decode after another seek", async () => {
    const { fetchArrayBuffer, pending } = pendingSecondItemFetch();
    const { session, context } = buildSession(tenSecondPair(), {
      fetchArrayBuffer,
    });

    await session.play();
    session.seek(15);
    await vi.advanceTimersByTimeAsync(0);
    await flushMicrotasks();
    session.seek(4);
    const sourcesAfterSecondSeek = context.sources.length;
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(4, 6);

    pending.get("/api/recordings/hash-b/audio")!(new ArrayBuffer(8));
    await vi.advanceTimersByTimeAsync(0);
    await flushMicrotasks();

    expect(session.getTimelinePositionSeconds()).toBeCloseTo(4, 6);
    expect(context.sources).toHaveLength(sourcesAfterSecondSeek);
    context.currentTime = 1;
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(5, 6);
    session.stop();
  });

  it("ignores a seek decode that settles after pause", async () => {
    const { fetchArrayBuffer, pending } = pendingSecondItemFetch();
    const { session, context } = buildSession(tenSecondPair(), {
      fetchArrayBuffer,
    });

    await session.play();
    session.seek(15);
    await vi.advanceTimersByTimeAsync(0);
    await flushMicrotasks();
    session.pause();
    expect(session.isPlaying()).toBe(false);
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(15, 6);
    const sourcesAfterPause = context.sources.length;

    pending.get("/api/recordings/hash-b/audio")!(new ArrayBuffer(8));
    await vi.advanceTimersByTimeAsync(0);
    await flushMicrotasks();

    expect(session.isPlaying()).toBe(false);
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(15, 6);
    expect(context.sources).toHaveLength(sourcesAfterPause);
    session.stop();
  });

  it("prepares the target in the background on a paused seek without playing", async () => {
    const { fetchArrayBuffer, pending } = pendingSecondItemFetch();
    const { session, context } = buildSession(tenSecondPair(), {
      fetchArrayBuffer,
    });

    await session.play();
    session.pause();
    const sourcesBeforeSeek = context.sources.length;

    session.seek(15);
    await vi.advanceTimersByTimeAsync(0);
    await flushMicrotasks();
    expect(session.isPlaying()).toBe(false);
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(15, 6);
    expect(session.getProgress()).toMatchObject({
      itemId: "b",
      itemIndex: 1,
      phase: "fetching",
    });

    pending.get("/api/recordings/hash-b/audio")!(new ArrayBuffer(8));
    await vi.advanceTimersByTimeAsync(0);
    await flushMicrotasks();
    expect(session.isPlaying()).toBe(false);
    expect(session.getProgress()).toBeNull();
    expect(context.sources).toHaveLength(sourcesBeforeSeek);

    await session.play();
    expect(session.isPlaying()).toBe(true);
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(15, 6);
    expect(fetchArrayBuffer).toHaveBeenCalledTimes(2);
    session.stop();
  });

  it("retries a seek prepare after a decode rejection without a terminal failure state", async () => {
    const urls: string[] = [];
    const fetchArrayBuffer = vi.fn(async (url: string) => {
      urls.push(url);
      return new ArrayBuffer(8);
    });
    const { session, context } = buildSession(tenSecondPair(), {
      fetchArrayBuffer,
    });
    let decodeCalls = 0;
    context.decodeAudioData.mockImplementation(async () => {
      decodeCalls += 1;
      if (decodeCalls === 2) throw new Error("seek decode failed");
      return decodedBuffer();
    });

    await session.play();
    session.seek(15);
    await vi.advanceTimersByTimeAsync(0);
    await flushMicrotasks();
    expect(session.getError()).toMatchObject({
      kind: "playback-failed",
      itemId: "b",
      message: "seek decode failed",
    });
    expect(session.isPlaying()).toBe(false);
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(15, 6);
    expect(session.getProgress()).toBeNull();

    await session.play();
    expect(session.getError()).toBeNull();
    expect(session.isPlaying()).toBe(true);
    expect(session.getTimelinePositionSeconds()).toBeCloseTo(15, 6);
    expect(context.sources.at(-1)!.starts[0]).toMatchObject({
      offset: 5,
      duration: 5,
    });
    expect(urls).toHaveLength(3); // a, failed b, retried b
    session.stop();
  });

  it("releases retained clips on stop and ignores late decodes", async () => {
    const { fetchArrayBuffer, pending } = pendingSecondItemFetch();
    const { session, context } = buildSession(tenSecondPair(), {
      fetchArrayBuffer,
    });

    await session.play();
    session.seek(15);
    await vi.advanceTimersByTimeAsync(0);
    await flushMicrotasks();
    const buffersBeforeStop = context.buffers.length;
    const sourcesBeforeStop = context.sources.length;

    session.stop();
    expect(session.getTimelinePositionSeconds()).toBe(0);
    expect(session.isPlaying()).toBe(false);
    expect(session.getProgress()).toBeNull();
    expect(session.getError()).toBeNull();
    expect(context.suspend).not.toHaveBeenCalled();
    expect(context.close).not.toHaveBeenCalled();

    pending.get("/api/recordings/hash-b/audio")!(new ArrayBuffer(8));
    await vi.advanceTimersByTimeAsync(0);
    await flushMicrotasks();
    expect(context.buffers).toHaveLength(buffersBeforeStop);
    expect(context.sources).toHaveLength(sourcesBeforeStop);

    // Every later call is inert, including the ticker.
    await session.play();
    session.seek(5);
    session.pause();
    session.stop();
    await vi.advanceTimersByTimeAsync(1_000);
    expect(context.buffers).toHaveLength(buffersBeforeStop);
    expect(context.sources).toHaveLength(sourcesBeforeStop);
    expect(context.resume).toHaveBeenCalledTimes(1);
  });
});

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

describe("songset audio errors", () => {
  it("records playback-failed for an HTTP failure and stops after notifying", async () => {
    const fetchArrayBuffer = vi.fn(async () => {
      throw new Error("HTTP 500");
    });
    const { session } = buildSession([source({ itemId: "a" })], {
      fetchArrayBuffer,
    });
    let notified = false;
    session.onStateChange(() => {
      if (session.getError() !== null) notified = true;
    });

    await expect(session.play()).rejects.toThrow("HTTP 500");
    expect(session.getError()).toEqual({
      kind: "playback-failed",
      itemId: "a",
      message: "HTTP 500",
    });
    expect(session.isPlaying()).toBe(false);
    expect(session.getProgress()).toBeNull();
    expect(notified).toBe(true);
  });

  it("records no-recording for an item without a recording prefix", async () => {
    const { session, context } = buildSession([
      source({ itemId: "a", recordingHashPrefix: null }),
    ]);

    await expect(session.play()).rejects.toThrow(
      "Item a has no Recording; preview is unavailable",
    );
    expect(session.getError()).toEqual({
      kind: "no-recording",
      itemId: "a",
      message: "Item a has no Recording; preview is unavailable",
    });
    expect(context.resume).not.toHaveBeenCalled();
  });

  it("returns the same error object so consumers can dedupe by identity", async () => {
    const fetchArrayBuffer = vi.fn(async () => {
      throw new Error("HTTP 500");
    });
    const { session } = buildSession([source({ itemId: "a" })], {
      fetchArrayBuffer,
    });

    await expect(session.play()).rejects.toThrow("HTTP 500");
    const failure = session.getError();
    expect(failure).not.toBeNull();
    expect(session.getError()).toBe(failure);
  });

  it("uses the production streaming fetch and reports byte progress", async () => {
    const chunks = [new Uint8Array([1, 2]), new Uint8Array([3])];
    let readIndex = 0;
    const response = {
      ok: true,
      status: 200,
      headers: { get: (name: string) => (name === "content-length" ? "3" : null) },
      body: {
        getReader: () => ({
          read: async () =>
            readIndex < chunks.length
              ? { done: false, value: chunks[readIndex++] }
              : { done: true, value: undefined },
        }),
      },
    };
    const fetchMock = vi.fn(async () => response);
    vi.stubGlobal("fetch", fetchMock);

    const context = makeFakeContext();
    const session = createSongsetAudioSession([source({ itemId: "a" })], {
      createAudioContext: () => context.context,
    });
    sessions.push(session);
    const progress: Array<SongsetAudioProgress | null> = [];
    session.onStateChange(() => progress.push(session.getProgress()));

    await session.play();

    expect(fetchMock).toHaveBeenCalledWith("/api/recordings/hash-a/audio");
    expect(progress.some((entry) => entry?.totalBytes === 3 && entry.receivedBytes === 3)).toBe(true);
    const decodedInput = context.decodeAudioData.mock.calls[0]![0] as ArrayBuffer;
    expect(decodedInput.byteLength).toBe(3);
  });
});

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

describe("validateSongsetAudioSources", () => {
  it("rejects an empty Songset", () => {
    expect(validateSongsetAudioSources([])).toEqual({
      kind: "invalid-arrangement",
      itemId: null,
      message: "Songset has no items to preview",
    });
  });

  it("rejects an item without a Recording", () => {
    expect(
      validateSongsetAudioSources([
        source({ itemId: "a", recordingHashPrefix: null }),
      ]),
    ).toEqual({
      kind: "no-recording",
      itemId: "a",
      message: "Item a has no Recording; preview is unavailable",
    });
  });

  it("rejects an unknown duration when no arrangement exists", () => {
    expect(
      validateSongsetAudioSources([
        source({ itemId: "a", recordingDurationSeconds: null }),
      ]),
    ).toEqual({
      kind: "no-recording",
      itemId: "a",
      message:
        "Recording duration for item a is unavailable; preview is unavailable",
    });
  });

  it("rejects a saved arrangement that no longer matches analysis", () => {
    expect(
      validateSongsetAudioSources([
        source({
          itemId: "a",
          arrangementSteps: stepsJson([{ label: "A", start: 0, end: 4 }]),
          recordingSections: null,
          recordingBeats: [],
        }),
      ]),
    ).toEqual({
      kind: "invalid-arrangement",
      itemId: "a",
      message:
        "Arrangement for item a is invalid or unavailable; repair the analysis or reset the arrangement",
    });
  });

  it("accepts a valid untouched and a valid arranged item", () => {
    expect(
      validateSongsetAudioSources([
        arrangedSource("a"),
        source({ itemId: "b", recordingDurationSeconds: 20 }),
      ]),
    ).toBeNull();
  });
});
