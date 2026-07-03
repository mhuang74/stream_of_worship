import { describe, expect, it } from "vitest";
import { getEffectiveKey } from "./effective-key";

describe("getEffectiveKey", () => {
  it("prefers parseable catalog keys over audio", () => {
    const result = getEffectiveKey({
      catalogKey: "F-G",
      detectedKey: "C",
      detectedConfidence: 0.9,
      detectedMargin: 0.1,
      detectedWindowAgreement: 0.8,
    });

    expect(result.source).toBe("catalog");
    expect(result.display).toBe("F → G");
    expect(result.startPitchClass).toBe(5);
    expect(result.endPitchClass).toBe(7);
    expect(result.warning).toBe("catalog_audio_disagree");
  });

  it("uses high-confidence new-detector audio when catalog is missing", () => {
    const result = getEffectiveKey({
      detectedKey: "D",
      detectedConfidence: 0.71,
      detectedMargin: 0.05,
      detectedWindowAgreement: 0.55,
    });

    expect(result.source).toBe("audio");
    expect(result.display).toBe("D");
  });

  it("marks new-detector threshold failures unknown", () => {
    const result = getEffectiveKey({
      detectedKey: "D",
      detectedConfidence: 0.9,
      detectedMargin: 0.01,
      detectedWindowAgreement: 0.8,
    });

    expect(result.source).toBe("unknown");
    expect(result.warning).toBe("audio_low_confidence");
  });

  it("accepts legacy audio as fallback without margin/window fields", () => {
    const result = getEffectiveKey({
      detectedKey: "Em",
      detectedConfidence: 0.42,
    });

    expect(result.source).toBe("audio_legacy");
    expect(result.mode).toBe("minor");
  });

  it("reports unparseable catalog key instead of falling through", () => {
    const result = getEffectiveKey({
      catalogKey: "unknown",
      detectedKey: "C",
      detectedConfidence: 0.9,
    });

    expect(result.source).toBe("unknown");
    expect(result.warning).toBe("unparseable_catalog");
  });
});

