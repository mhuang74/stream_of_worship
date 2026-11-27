import { describe, expect, it } from "vitest";

import { describeGlueDescriptorDrift } from "@/lib/glue/stale";

const HASH_A = "a".repeat(64);
const HASH_B = "b".repeat(64);

function descriptorJson(overrides: {
  beats?: number;
  toTempoBpm?: number;
  fromHash?: string;
  toHash?: string;
  fromSteps?: [number, number][] | null;
  toSteps?: [number, number][] | null;
} = {}): string {
  return JSON.stringify({
    v: 2,
    model: "music_v2_5",
    promptVersion: 2,
    excerptSeconds: 12,
    beats: overrides.beats ?? 4,
    toTempoBpm: overrides.toTempoBpm ?? 120,
    from: {
      contentHash: overrides.fromHash ?? HASH_A,
      steps: overrides.fromSteps === undefined ? [[0, 60]] : overrides.fromSteps,
    },
    to: {
      contentHash: overrides.toHash ?? HASH_B,
      steps: overrides.toSteps === undefined ? [[10, 70]] : overrides.toSteps,
    },
  });
}

describe("describeGlueDescriptorDrift", () => {
  it("reports a beats change with both values", () => {
    expect(describeGlueDescriptorDrift(descriptorJson({ beats: 4 }), descriptorJson({ beats: 8 })))
      .toEqual({ change: "beats", storedBeats: 4, currentBeats: 8 });
  });

  it("prefers the arrangement change over the beats change", () => {
    expect(
      describeGlueDescriptorDrift(
        descriptorJson({ beats: 4 }),
        descriptorJson({ beats: 8, toSteps: [[20, 80]] })
      )
    ).toEqual({ change: "to-arrangement", storedBeats: null, currentBeats: null });
  });

  it("orders recordings before arrangements before tempo before beats", () => {
    const base = descriptorJson();
    expect(describeGlueDescriptorDrift(base, descriptorJson({ fromHash: HASH_B }))?.change).toBe(
      "from-recording"
    );
    expect(describeGlueDescriptorDrift(base, descriptorJson({ toHash: HASH_A }))?.change).toBe(
      "to-recording"
    );
    expect(
      describeGlueDescriptorDrift(base, descriptorJson({ fromSteps: null, beats: 16 }))?.change
    ).toBe("from-arrangement");
    expect(describeGlueDescriptorDrift(base, descriptorJson({ toTempoBpm: 90 }))?.change).toBe(
      "incoming-tempo"
    );
  });

  it("ignores step key order and equal steps", () => {
    expect(
      describeGlueDescriptorDrift(
        descriptorJson({ toSteps: [[10, 70], [70, 120]] }),
        descriptorJson({ toSteps: [[10, 70], [70, 120]] })
      )
    ).toBeNull();
  });

  it("returns null for identical payloads", () => {
    expect(describeGlueDescriptorDrift(descriptorJson(), descriptorJson())).toBeNull();
  });

  it("returns null for unparsable or incomplete payloads", () => {
    expect(describeGlueDescriptorDrift("{not json", descriptorJson())).toBeNull();
    expect(describeGlueDescriptorDrift(descriptorJson(), "null")).toBeNull();
    expect(describeGlueDescriptorDrift(JSON.stringify({ beats: 4 }), descriptorJson())).toBeNull();
  });
});
