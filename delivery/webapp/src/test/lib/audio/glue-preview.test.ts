import { describe, expect, it } from "vitest";

import {
  buildGluePreviewWindow,
  contextSecondsForTempo,
} from "@/lib/audio/glue-preview";

describe("contextSecondsForTempo", () => {
  it("rounds 5 s up to whole bars of the song's tempo", () => {
    // 120 BPM: 5 s is 10 beats → 3 bars (12 beats) = 6 s.
    expect(contextSecondsForTempo(120)).toBe(6);
    // 72 BPM: 5 s is 6 beats → 2 bars (8 beats) ≈ 6.6667 s.
    expect(contextSecondsForTempo(72)).toBeCloseTo(6.6667, 4);
  });

  it("falls back to 1 s per beat when the tempo is unusable", () => {
    expect(contextSecondsForTempo(null)).toBe(8);
    expect(contextSecondsForTempo(0)).toBe(8);
  });
});

describe("buildGluePreviewWindow", () => {
  it("surrounds the transition with one bar of context on each side", () => {
    const clip = buildGluePreviewWindow({
      glue: { startSeconds: 100, endSeconds: 104 },
      outgoing: { startSeconds: 40, endSeconds: 100, tempoBpm: 120 },
      incoming: { startSeconds: 104, endSeconds: 200, tempoBpm: 100 },
    });
    // Lead-in from the outgoing tempo (6 s), lead-out from the incoming (5 s is
    // 8.33 beats → 3 bars = 7.2 s).
    expect(clip?.startSeconds).toBe(94);
    expect(clip?.endSeconds).toBeCloseTo(111.2, 4);
    expect(clip?.leadInSeconds).toBe(6);
  });

  it("clamps to the two songs that own the junction", () => {
    const clip = buildGluePreviewWindow({
      glue: { startSeconds: 45, endSeconds: 49 },
      outgoing: { startSeconds: 40, endSeconds: 45, tempoBpm: 120 },
      incoming: { startSeconds: 49, endSeconds: 52, tempoBpm: 120 },
    });
    expect(clip?.startSeconds).toBe(40);
    expect(clip?.endSeconds).toBe(52);
  });

  it("clamps the lead-out to the transport total", () => {
    // A junction at the very end of the Songset: the lead-out would run past the
    // transport, where the boundary could never fire.
    const clip = buildGluePreviewWindow({
      glue: { startSeconds: 100, endSeconds: 104 },
      outgoing: { startSeconds: 40, endSeconds: 100, tempoBpm: 120 },
      incoming: { startSeconds: 104, endSeconds: 105, tempoBpm: 100 },
      limitSeconds: 104.5,
    });
    expect(clip?.endSeconds).toBe(104.5);
  });

  it("returns null when the clamp leaves no audio", () => {
    expect(
      buildGluePreviewWindow({
        glue: { startSeconds: 100, endSeconds: 100 },
        outgoing: { startSeconds: 100, endSeconds: 100, tempoBpm: 120 },
        incoming: { startSeconds: 100, endSeconds: 100, tempoBpm: 120 },
      })
    ).toBeNull();
  });
});
