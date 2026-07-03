import { describe, expect, it } from "vitest";
import { parseMusicalKey, pitchClass } from "./key";

const cases: Array<[string | null, string, string | null, number | null, string | null, number | null, string]> = [
  ["C#", "ok", "C#", 1, "C#", 1, "major"],
  ["Db", "ok", "Db", 1, "Db", 1, "major"],
  ["F# minor", "ok", "F#", 6, "F#", 6, "minor"],
  ["F#m", "ok", "F#", 6, "F#", 6, "minor"],
  ["E大調", "ok", "E", 4, "E", 4, "major"],
  ["E小調", "ok", "E", 4, "E", 4, "minor"],
  ["Em", "ok", "E", 4, "E", 4, "minor"],
  ["Ｄ-F", "range", "D", 2, "F", 5, "major"],
  ["D-Eb-F", "range", "D", 2, "F", 5, "major"],
  ["Em-G", "range", "E", 4, "G", 7, "minor"],
  ["", "missing", null, null, null, null, "unknown"],
  [null, "missing", null, null, null, null, "unknown"],
  ["unknown", "unparseable", null, null, null, null, "unknown"],
];

describe("parseMusicalKey", () => {
  it("matches parser fixtures", () => {
    for (const [raw, status, startRoot, startPitchClass, endRoot, endPitchClass, mode] of cases) {
      const parsed = parseMusicalKey(raw);
      expect(parsed.status).toBe(status);
      expect(parsed.startRoot).toBe(startRoot);
      expect(parsed.startPitchClass).toBe(startPitchClass);
      expect(parsed.endRoot).toBe(endRoot);
      expect(parsed.endPitchClass).toBe(endPitchClass);
      expect(parsed.mode).toBe(mode);
      expect(parsed.root).toBe(startRoot);
      expect(parsed.pitchClass).toBe(startPitchClass);
    }
  });

  it("normalizes enharmonic pitch classes", () => {
    expect(pitchClass("C#")).toBe(1);
    expect(pitchClass("Db")).toBe(1);
    expect(pitchClass("Bb")).toBe(10);
    expect(pitchClass("A#")).toBe(10);
    expect(pitchClass("F# minor")).toBe(6);
    expect(pitchClass("Gb")).toBe(6);
  });
});

