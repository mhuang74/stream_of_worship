import { describe, expect, it } from "vitest";
import {
  composedGainAt,
  envelopeValueAt,
  incomingEnvelope,
  outgoingEnvelope,
  scheduleEnvelope,
  type EnvelopePoint,
} from "@/lib/audio/envelopes";
import type { ArrangementTimelineItem } from "@/lib/arrangements";

function item(overrides: Partial<ArrangementTimelineItem> = {}): ArrangementTimelineItem {
  return {
    itemId: "item",
    recordingHashPrefix: "hash",
    startSeconds: 0,
    durationSeconds: 10,
    gapBeforeSeconds: 0,
    crossfadeSeconds: 0,
    clips: [],
    ...overrides,
  };
}

interface GainEvent {
  type: "set" | "ramp";
  value: number;
  time: number;
}

function fakeParam(): { param: AudioParam; events: GainEvent[] } {
  const events: GainEvent[] = [];
  const param = {
    setValueAtTime: (value: number, time: number) => {
      events.push({ type: "set", value, time });
    },
    linearRampToValueAtTime: (value: number, time: number) => {
      events.push({ type: "ramp", value, time });
    },
  } as unknown as AudioParam;
  return { param, events };
}

describe("incomingEnvelope", () => {
  it("steps to full gain at the item start when there is no crossfade", () => {
    expect(incomingEnvelope(item({ startSeconds: 4 }))).toEqual([{ t: 4, v: 1 }]);
  });

  it("ramps 0 to 1 across the item's own crossfade window", () => {
    expect(incomingEnvelope(item({ startSeconds: 4, crossfadeSeconds: 3 }))).toEqual([
      { t: 4, v: 0 },
      { t: 7, v: 1 },
    ]);
  });
});

describe("outgoingEnvelope", () => {
  it("ramps 1 to 0 driven by the successor's crossfade duration", () => {
    const current = item({ startSeconds: 10, durationSeconds: 4 });
    const next = item({
      itemId: "next",
      startSeconds: 14,
      durationSeconds: 10,
      crossfadeSeconds: 2,
    });

    expect(outgoingEnvelope(current, next)).toEqual([
      { t: 12, v: 1 },
      { t: 14, v: 0 },
    ]);
  });

  it("holds full gain when there is no successor or no successor crossfade", () => {
    const current = item({ startSeconds: 10, durationSeconds: 4, crossfadeSeconds: 2 });
    expect(outgoingEnvelope(current, null)).toEqual([{ t: 10, v: 1 }]);
    expect(outgoingEnvelope(current, item({ startSeconds: 14, crossfadeSeconds: 0 }))).toEqual([
      { t: 10, v: 1 },
    ]);
  });
});

describe("envelopeValueAt", () => {
  const ramp: EnvelopePoint[] = [
    { t: 2, v: 0 },
    { t: 6, v: 1 },
  ];

  it("returns the first and last values outside the envelope", () => {
    expect(envelopeValueAt(ramp, 0)).toBe(0);
    expect(envelopeValueAt(ramp, 9)).toBe(1);
  });

  it("interpolates linearly between points", () => {
    expect(envelopeValueAt(ramp, 4)).toBe(0.5);
    expect(envelopeValueAt(ramp, 3)).toBe(0.25);
  });

  it("treats an empty envelope as full gain", () => {
    expect(envelopeValueAt([], 5)).toBe(1);
  });
});

describe("composedGainAt", () => {
  it("multiplies the incoming and successor-owned outgoing envelopes", () => {
    const current = item({ startSeconds: 0, durationSeconds: 4, crossfadeSeconds: 3 });
    const next = item({ itemId: "next", startSeconds: 4, crossfadeSeconds: 3 });

    expect(composedGainAt(current, next, 0)).toBe(0);
    expect(composedGainAt(current, next, 2)).toBeCloseTo(4 / 9, 10);
    expect(composedGainAt(current, next, 4)).toBe(0);
  });

  it("equals the incoming envelope when no fade-out applies", () => {
    const current = item({ startSeconds: 0, durationSeconds: 4, crossfadeSeconds: 2 });
    expect(composedGainAt(current, null, 1)).toBe(0.5);
  });
});

describe("scheduleEnvelope", () => {
  it("sets the value at the position and ramps every later point", () => {
    const { param, events } = fakeParam();
    const points: EnvelopePoint[] = [
      { t: 0, v: 1 },
      { t: 2, v: 0 },
    ];

    scheduleEnvelope(param, points, 0, 100);

    expect(events).toEqual([
      { type: "set", value: 1, time: 100 },
      { type: "ramp", value: 0, time: 102 },
    ]);
  });

  it("holds before an item start, then ramps relative to the origin", () => {
    const { param, events } = fakeParam();
    const points: EnvelopePoint[] = [
      { t: 5, v: 0 },
      { t: 7, v: 1 },
    ];

    scheduleEnvelope(param, points, 2, 100);

    expect(events).toEqual([
      { type: "set", value: 0, time: 100 },
      { type: "set", value: 0, time: 103 },
      { type: "ramp", value: 1, time: 105 },
    ]);
  });

  it("writes no ramps when the position is past the envelope", () => {
    const { param, events } = fakeParam();
    const points: EnvelopePoint[] = [
      { t: 0, v: 1 },
      { t: 2, v: 0 },
    ];

    scheduleEnvelope(param, points, 5, 100);

    expect(events).toEqual([{ type: "set", value: 0, time: 100 }]);
  });
});
