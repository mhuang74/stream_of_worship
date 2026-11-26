import { describe, it, expect, vi } from "vitest";

// Pure quote tests import the module that also exposes DB-backed loaders.
vi.mock("@/db", () => ({ db: {} }));

import {
  computeGlueQuote,
  deriveGlueJunction,
  isGpuFallbackEnabled,
  type GlueJunctionReady,
  type GlueResolverItem,
} from "@/lib/glue/quote";
import { buildGlueExcerptKey } from "@/lib/glue/excerpt-key";

const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);

function item(overrides: Partial<GlueResolverItem> = {}): GlueResolverItem {
  return {
    itemId: "item-a",
    position: 0,
    userId: 1,
    glueEnabled: true,
    glueBeats: 4,
    glueActiveTakeId: null,
    updatedAt: "2026-11-22T10:00:00.000Z",
    arrangementSteps: null,
    recording: {
      contentHash: HASH_A,
      durationSeconds: 200,
      tempoBpm: 120,
      sections: [{ label: "Verse", start: 0, end: 200 }],
      beats: [0, 1, 2, 3],
    },
    ...overrides,
  };
}

function incoming(overrides: Partial<GlueResolverItem> = {}): GlueResolverItem {
  return item({
    itemId: "item-b",
    position: 1,
    updatedAt: "2026-11-22T10:01:00.000Z",
    recording: {
      contentHash: HASH_B,
      durationSeconds: 180,
      tempoBpm: 120,
      sections: [{ label: "Chorus", start: 0, end: 180 }],
      beats: [0, 1, 2, 3],
    },
    ...overrides,
  });
}

function ready(overrides: Partial<GlueJunctionReady> = {}): GlueJunctionReady {
  return {
    ok: true,
    descriptorKey: "f".repeat(64),
    descriptorJson: "{}",
    fromSide: {
      contentHash: HASH_A,
      steps: null,
      arrangedDurationSeconds: 200,
      excerptSeconds: 12,
      excerptTooShort: false,
    },
    toSide: {
      contentHash: HASH_B,
      steps: null,
      arrangedDurationSeconds: 180,
      excerptSeconds: 12,
      excerptTooShort: false,
    },
    generationSeconds: 3,
    ...overrides,
  };
}

describe("deriveGlueJunction", () => {
  it("caps the excerpt at 12 s and uses the 3 s generation floor", () => {
    const derivation = deriveGlueJunction(item(), incoming());
    expect(derivation.ok).toBe(true);
    if (!derivation.ok) return;
    expect(derivation.fromSide.excerptSeconds).toBe(12);
    expect(derivation.toSide.excerptSeconds).toBe(12);
    expect(derivation.generationSeconds).toBe(3);
    expect(derivation.descriptorKey).toMatch(/^[0-9a-f]{64}$/);
  });

  it("uses the actual arranged duration when a side is shorter than 12 s", () => {
    const derivation = deriveGlueJunction(item(), incoming({
      recording: {
        contentHash: HASH_B,
        durationSeconds: 7.123456,
        tempoBpm: 120,
        sections: [{ label: "Chorus", start: 0, end: 7.123456 }],
        beats: [0, 1, 2, 3],
      },
    }));
    expect(derivation.ok).toBe(true);
    if (!derivation.ok) return;
    expect(derivation.toSide.excerptSeconds).toBe(7.123456);
  });

  it("computes generation seconds as beats × 60 / incoming tempo", () => {
    const derivation = deriveGlueJunction(item({ glueBeats: 16 }), incoming({
      recording: {
        contentHash: HASH_B,
        durationSeconds: 180,
        tempoBpm: 60,
        sections: [{ label: "Chorus", start: 0, end: 180 }],
        beats: [0, 1, 2, 3],
      },
    }));
    expect(derivation.ok).toBe(true);
    if (!derivation.ok) return;
    expect(derivation.generationSeconds).toBe(16);
  });

  it("returns no-tempo when the incoming tempo is absent", () => {
    const derivation = deriveGlueJunction(item(), incoming({
      recording: {
        contentHash: HASH_B,
        durationSeconds: 180,
        tempoBpm: null,
        sections: null,
        beats: [],
      },
    }));
    expect(derivation).toEqual({ ok: false, reason: "no-tempo" });
  });

  it("keeps canonical descriptor identity for untold arrangements", () => {
    const derivation = deriveGlueJunction(item(), incoming());
    expect(derivation.ok).toBe(true);
    if (!derivation.ok) return;
    expect(derivation.fromSide.steps).toBeNull();
    expect(derivation.toSide.steps).toBeNull();
  });
});

describe("computeGlueQuote", () => {
  it("charges 15 credits per uncached second plus the generation window", () => {
    const core = computeGlueQuote(ready(), new Set(), new Set());
    expect(core).toEqual({
      uncachedSides: [
        { side: "A", seconds: 12, credits: 180 },
        { side: "B", seconds: 12, credits: 180 },
      ],
      generationSeconds: 3,
      uploadCredits: 360,
      generationCredits: 45,
      estimatedNewCredits: 405,
      blockedSides: [],
    });
  });

  it("rounds upload credits half-up", () => {
    const core = computeGlueQuote(
      ready({
        fromSide: {
          contentHash: HASH_A,
          steps: null,
          arrangedDurationSeconds: 2.5,
          excerptSeconds: 2.5,
          excerptTooShort: false,
        },
      }),
      new Set(),
      new Set()
    );
    expect(core.uncachedSides[0]).toEqual({ side: "A", seconds: 2.5, credits: 38 });
  });

  it("omits cached sides and charges only the remaining side", () => {
    const derivation = deriveGlueJunction(item(), incoming());
    expect(derivation.ok).toBe(true);
    if (!derivation.ok) return;
    const core = computeGlueQuote(
      derivation,
      new Set(["unrelated-key"]),
      new Set()
    );
    expect(core.uncachedSides).toHaveLength(2);
    expect(core.uploadCredits).toBe(360);
  });

  it("reports rejected and too-short sides without charging upload credits", () => {
    const derivation = ready({
      fromSide: {
        contentHash: HASH_A,
        steps: null,
        arrangedDurationSeconds: 0.5,
        excerptSeconds: 0.5,
        excerptTooShort: true,
      },
    });
    const core = computeGlueQuote(derivation, new Set(), new Set());
    expect(core.blockedSides).toEqual([{ side: "A", code: "GLUE_EXCERPT_TOO_SHORT" }]);
    expect(core.uncachedSides).toEqual([{ side: "B", seconds: 12, credits: 180 }]);
    expect(core.uploadCredits).toBe(180);
  });

  it("allows a GPU quote for a rejected excerpt and still enforces minimum length", () => {
    const derivation = ready();
    const rejected = new Set([buildGlueExcerptKey({
      side: "A", seconds: derivation.fromSide.excerptSeconds,
      contentHash: derivation.fromSide.contentHash, steps: derivation.fromSide.steps,
    }).key]);
    expect(computeGlueQuote(derivation, new Set(), rejected).blockedSides)
      .toEqual([{ side: "A", code: "GLUE_COPYRIGHT_REJECTED" }]);
    expect(computeGlueQuote(derivation, new Set(), rejected, true)).toMatchObject({
      blockedSides: [],
      uncachedSides: [],
      estimatedNewCredits: 0,
    });
    expect(computeGlueQuote({
      ...derivation, fromSide: { ...derivation.fromSide, excerptTooShort: true },
    }, new Set(), rejected, true).blockedSides)
      .toEqual([{ side: "A", code: "GLUE_EXCERPT_TOO_SHORT" }]);
  });

  it("enables GPU fallback only for the cascade selector", () => {
    expect(isGpuFallbackEnabled("cascade")).toBe(true);
    expect(isGpuFallbackEnabled("elevenlabs")).toBe(false);
    expect(isGpuFallbackEnabled("simdps")).toBe(false);
    expect(isGpuFallbackEnabled(undefined)).toBe(false);
  });
});
