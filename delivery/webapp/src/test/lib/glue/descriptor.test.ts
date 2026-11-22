import { describe, it, expect } from "vitest";

import {
  canonicalJson,
  canonicalNumberText,
  canonicalSixDp,
  type CanonicalValue,
} from "@/lib/glue/canonical";
import {
  buildGlueDescriptor,
  glueDescriptorKey,
  type GlueDescriptorInput,
} from "@/lib/glue/descriptor";
import fixtureJson from "../../fixtures/glue-canonical.cross_language.json";

// Cross-language fixture: delivery/render-worker/tests/test_glue_descriptor.py
// loads the very same file and asserts the same canonical string and key.
interface CrossLanguageFixture {
  descriptors: Array<{
    name: string;
    input: GlueDescriptorInput;
    canonicalJson: string;
    key: string;
  }>;
  excerpts: unknown[];
}

const fixture = fixtureJson as unknown as CrossLanguageFixture;

const HASH_A = "0f1e2d3c4b5a69788796a5b4c3d2e1f00f1e2d3c4b5a69788796a5b4c3d2e1f0";
const HASH_B = "abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789";

// The contract's pinned case: integral tempo, a single 12 s step, and numeric
// 12.0 that must serialize as `12` (never `12.0` or `1.2e1`).
const PINNED_INPUT: GlueDescriptorInput = {
  beats: 4,
  toTempoBpm: 120,
  from: { contentHash: HASH_A, steps: [[0, 12.0]] },
  to: { contentHash: HASH_B, steps: [[0, 120]] },
};

const PINNED_CANONICAL_JSON =
  `{"v":2,"model":"music_v2_5","promptVersion":2,"excerptSeconds":12,"beats":4,` +
  `"toTempoBpm":120,` +
  `"from":{"contentHash":"${HASH_A}","steps":[[0,12]]},` +
  `"to":{"contentHash":"${HASH_B}","steps":[[0,120]]}}`;

const PINNED_KEY = "5e0d13ac268778bf5ec5d619834562b5706027ab82da141cc3aad241b8987651";

function withInput(patch: Partial<GlueDescriptorInput>): GlueDescriptorInput {
  return { ...PINNED_INPUT, ...patch };
}

describe("canonicalSixDp", () => {
  it("keeps at most six decimal places", () => {
    expect(canonicalSixDp(4.125)).toBe(4.125);
    expect(canonicalSixDp(127.35)).toBe(127.35);
    expect(canonicalSixDp(12)).toBe(12);
  });

  it("rounds the sixth decimal half up", () => {
    expect(canonicalSixDp(1.0000005)).toBe(1.000001);
    expect(canonicalSixDp(0.0000005)).toBe(0.000001);
    expect(canonicalSixDp(2.3456785)).toBe(2.345679);
    expect(canonicalSixDp(1.0000004)).toBe(1);
    expect(canonicalSixDp(0.9999995)).toBe(1);
  });

  it("rejects non-finite values", () => {
    expect(() => canonicalSixDp(Number.NaN)).toThrow(TypeError);
    expect(() => canonicalSixDp(Number.POSITIVE_INFINITY)).toThrow(TypeError);
    expect(() => canonicalSixDp(Number.NEGATIVE_INFINITY)).toThrow(TypeError);
  });
});

describe("canonicalNumberText", () => {
  it("prints integers without a fraction part", () => {
    expect(canonicalNumberText(0)).toBe("0");
    expect(canonicalNumberText(-0)).toBe("0");
    expect(canonicalNumberText(12)).toBe("12");
    expect(canonicalNumberText(12.0)).toBe("12");
    expect(canonicalNumberText(120)).toBe("120");
  });

  it("strips trailing fraction zeros and never emits an exponent", () => {
    expect(canonicalNumberText(0.1)).toBe("0.1");
    expect(canonicalNumberText(4.125)).toBe("4.125");
    expect(canonicalNumberText(127.35)).toBe("127.35");
    expect(canonicalNumberText(123456789.5)).toBe("123456789.5");
    expect(canonicalNumberText(1e-7)).toBe("0");
    expect(() => canonicalNumberText(1e12)).toThrow(RangeError);
  });

  it("rounds the sixth decimal half up from integer micro-units", () => {
    expect(canonicalNumberText(1.0000005)).toBe("1.000001");
    expect(canonicalNumberText(0.0000005)).toBe("0.000001");
    expect(canonicalNumberText(2.3456785)).toBe("2.345679");
    expect(canonicalNumberText(1.0000004)).toBe("1");
  });

  it("keeps negative values and drops negative zero", () => {
    expect(canonicalNumberText(-1.5)).toBe("-1.5");
    expect(canonicalNumberText(-0.0000004)).toBe("0");
  });

  it("rejects non-finite values", () => {
    expect(() => canonicalNumberText(Number.NaN)).toThrow(TypeError);
    expect(() => canonicalNumberText(Number.POSITIVE_INFINITY)).toThrow(TypeError);
  });
});

describe("canonicalJson", () => {
  it("serializes strings without ASCII escaping", () => {
    expect(canonicalJson("漸進")).toBe('"漸進"');
    expect(canonicalJson({ name: "漸進" })).toBe('{"name":"漸進"}');
  });

  it("keeps insertion order and uses compact separators", () => {
    const value: CanonicalValue = { b: [1, 2], a: null, c: true, d: "x" };
    expect(canonicalJson(value)).toBe('{"b":[1,2],"a":null,"c":true,"d":"x"}');
  });

  it("rejects unsupported values", () => {
    expect(() => canonicalJson(undefined as unknown as CanonicalValue)).toThrow(TypeError);
    expect(() => canonicalJson(Number.NaN)).toThrow(TypeError);
  });
});

describe("buildGlueDescriptor", () => {
  it("pins the exact canonical string and key", () => {
    const built = buildGlueDescriptor(PINNED_INPUT);
    expect(built.canonicalJson).toBe(PINNED_CANONICAL_JSON);
    expect(built.key).toBe(PINNED_KEY);
    expect(built.key).toMatch(/^[0-9a-f]{64}$/);
    expect(JSON.stringify(built.descriptor)).toBe(PINNED_CANONICAL_JSON);
  });

  it("emits the payload in the fixed contract order", () => {
    const { descriptor } = buildGlueDescriptor(PINNED_INPUT);
    expect(Object.keys(descriptor)).toEqual([
      "v",
      "model",
      "promptVersion",
      "excerptSeconds",
      "beats",
      "toTempoBpm",
      "from",
      "to",
    ]);
    expect(Object.keys(descriptor.from)).toEqual(["contentHash", "steps"]);
    expect(Object.keys(descriptor.to)).toEqual(["contentHash", "steps"]);
    expect(descriptor.v).toBe(2);
    expect(descriptor.model).toBe("music_v2_5");
    expect(descriptor.promptVersion).toBe(2);
    expect(descriptor.excerptSeconds).toBe(12);
  });

  it("matches every case in the shared cross-language fixture", () => {
    expect(fixture.descriptors.length).toBeGreaterThan(0);
    for (const testCase of fixture.descriptors) {
      const built = buildGlueDescriptor(testCase.input);
      expect(built.canonicalJson, testCase.name).toBe(testCase.canonicalJson);
      expect(built.key, testCase.name).toBe(testCase.key);
      expect(glueDescriptorKey(testCase.input), testCase.name).toBe(testCase.key);
      expect(JSON.stringify(built.descriptor), testCase.name).toBe(testCase.canonicalJson);
    }
  });

  it("keeps steps:null distinct from a full-range step list", () => {
    const nullSteps = buildGlueDescriptor(
      withInput({ to: { contentHash: HASH_B, steps: null } })
    );
    const fullRange = buildGlueDescriptor(PINNED_INPUT);
    expect(nullSteps.canonicalJson).not.toBe(fullRange.canonicalJson);
    expect(nullSteps.key).not.toBe(fullRange.key);
    expect(nullSteps.descriptor.to.steps).toBeNull();
  });

  it("changes the key when steps are reordered", () => {
    const forward = buildGlueDescriptor(
      withInput({
        from: { contentHash: HASH_A, steps: [[0, 12], [12, 3]] },
        to: { contentHash: HASH_A, steps: [[0, 20], [20, 4]] },
      })
    );
    const reversed = buildGlueDescriptor(
      withInput({
        from: { contentHash: HASH_A, steps: [[12, 3], [0, 12]] },
        to: { contentHash: HASH_A, steps: [[20, 4], [0, 20]] },
      })
    );
    expect(reversed.canonicalJson).not.toBe(forward.canonicalJson);
    expect(reversed.key).not.toBe(forward.key);
  });

  it.each<[string, GlueDescriptorInput]>([
    ["beats", withInput({ beats: 8 })],
    ["toTempoBpm", withInput({ toTempoBpm: 121 })],
    ["from.contentHash", withInput({ from: { contentHash: HASH_B, steps: [[0, 12]] } })],
    ["from.steps", withInput({ from: { contentHash: HASH_A, steps: [[0, 6]] } })],
    ["to.contentHash", withInput({ to: { contentHash: HASH_A, steps: [[0, 120]] } })],
    ["to.steps", withInput({ to: { contentHash: HASH_B, steps: null } })],
  ])("changes the key when %s changes", (_field, input) => {
    expect(glueDescriptorKey(input)).not.toBe(PINNED_KEY);
  });

  it("canonicalizes numbers before hashing", () => {
    const roundedDown = buildGlueDescriptor(
      withInput({
        toTempoBpm: 120.0000004,
        from: { contentHash: HASH_A, steps: [[0, 12.0000004]] },
      })
    );
    expect(roundedDown.canonicalJson).toBe(PINNED_CANONICAL_JSON);
    expect(roundedDown.key).toBe(PINNED_KEY);

    const roundedUp = buildGlueDescriptor(withInput({ toTempoBpm: 120.0000005 }));
    expect(roundedUp.canonicalJson).toContain('"toTempoBpm":120.000001');
    expect(roundedUp.key).not.toBe(PINNED_KEY);
  });

  it("ignores titles, lyrics, labels, ids, and locale", () => {
    const decorated = {
      ...PINNED_INPUT,
      title: "Amazing Grace",
      lyrics: "how sweet the sound",
      label: "Verse",
      itemId: "item-a",
      songsetId: "songset-1",
      locale: "zh-Hant",
      from: { ...PINNED_INPUT.from, title: "Amazing Grace", locale: "zh-Hant" },
      to: { ...PINNED_INPUT.to, label: "Chorus" },
    } as unknown as GlueDescriptorInput;
    const built = buildGlueDescriptor(decorated);
    expect(built.canonicalJson).toBe(PINNED_CANONICAL_JSON);
    expect(built.key).toBe(PINNED_KEY);
    expect(Object.keys(built.descriptor)).toHaveLength(8);
    for (const forbidden of ["title", "lyric", "label", "itemId", "songsetId", "locale"]) {
      expect(built.canonicalJson.toLowerCase()).not.toContain(forbidden.toLowerCase());
    }
  });

  it("rejects beats outside the supported set", () => {
    expect(() => buildGlueDescriptor(withInput({ beats: 3 as unknown as 4 }))).toThrow(
      RangeError
    );
  });
});
