import { describe, it, expect } from "vitest";

import {
  buildGlueExcerptKey,
  type GlueExcerptKeyInput,
} from "@/lib/glue/excerpt-key";
import fixtureJson from "../../fixtures/glue-canonical.cross_language.json";

// Cross-language fixture: delivery/render-worker/tests/test_glue_excerpts.py
// (and test_glue_descriptor.py) load the very same file and assert the same
// canonical string and key for every excerpt case.
interface CrossLanguageFixture {
  descriptors: unknown[];
  excerpts: Array<{
    name: string;
    input: GlueExcerptKeyInput;
    canonicalJson: string;
    key: string;
  }>;
}

const fixture = fixtureJson as unknown as CrossLanguageFixture;

describe("buildGlueExcerptKey", () => {
  it.each(fixture.excerpts)(
    "matches the shared fixture byte-for-byte: $name",
    ({ input, canonicalJson, key }) => {
      const built = buildGlueExcerptKey(input);
      expect(built.canonicalJson).toBe(canonicalJson);
      expect(built.key).toBe(key);
    }
  );

  it("has at least one fixture case (fails loudly if the fixture is unreachable)", () => {
    expect(fixture.excerpts.length).toBeGreaterThan(0);
  });

  it("changes the key when the side changes", () => {
    const base: GlueExcerptKeyInput = {
      side: "A",
      seconds: 12,
      contentHash: "a".repeat(64),
      steps: null,
    };
    expect(buildGlueExcerptKey({ ...base, side: "B" }).key).not.toBe(
      buildGlueExcerptKey(base).key
    );
  });

  it("distinguishes null steps from a full-range step", () => {
    const base: GlueExcerptKeyInput = {
      side: "A",
      seconds: 12,
      contentHash: "a".repeat(64),
      steps: null,
    };
    expect(buildGlueExcerptKey({ ...base, steps: [[0, 12]] }).key).not.toBe(
      buildGlueExcerptKey(base).key
    );
  });

  it("rejects unknown sides and empty hashes", () => {
    expect(() =>
      buildGlueExcerptKey({
        side: "C" as unknown as "A",
        seconds: 12,
        contentHash: "a".repeat(64),
        steps: null,
      })
    ).toThrow(RangeError);
    expect(() =>
      buildGlueExcerptKey({ side: "A", seconds: 12, contentHash: "", steps: null })
    ).toThrow(TypeError);
  });
});
