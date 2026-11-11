import { describe, it, expect } from "vitest";
import {
  buildSectionHeadings,
  headingForPositions,
  sectionIndexAtPosition,
} from "@/lib/section-labels";
import type { RecordingSection } from "@/lib/arrangements";

const verseChorusChorus: RecordingSection[] = [
  { label: "verse", start: 0, end: 20 },
  { label: "chorus", start: 20, end: 40 },
  { label: "chorus", start: 40, end: 60 },
];

describe("buildSectionHeadings", () => {
  it("numbers a label only when it repeats, in section order", () => {
    expect(buildSectionHeadings(verseChorusChorus).map((h) => h.displayLabel)).toEqual([
      "verse",
      "chorus 1",
      "chorus 2",
    ]);
  });

  it("leaves a unique label unnumbered even beside numbered repeats", () => {
    const sections: RecordingSection[] = [
      { label: "intro", start: 0, end: 10 },
      { label: "chorus", start: 10, end: 20 },
      { label: "chorus", start: 20, end: 30 },
    ];
    expect(buildSectionHeadings(sections).map((h) => h.displayLabel)).toEqual([
      "intro",
      "chorus 1",
      "chorus 2",
    ]);
  });

  it("keeps the section index aligned with the Recording's array", () => {
    expect(buildSectionHeadings(verseChorusChorus).map((h) => h.index)).toEqual([0, 1, 2]);
    expect(buildSectionHeadings(verseChorusChorus)[1]!.section).toEqual({
      label: "chorus",
      start: 20,
      end: 40,
    });
  });

  it("returns nothing without section data", () => {
    expect(buildSectionHeadings(null)).toEqual([]);
    expect(buildSectionHeadings([])).toEqual([]);
  });
});

describe("sectionIndexAtPosition", () => {
  it("matches the half-open section range and nothing in a gap", () => {
    const sections: RecordingSection[] = [
      { label: "verse", start: 0, end: 10 },
      { label: "chorus", start: 20, end: 30 },
    ];
    expect(sectionIndexAtPosition(sections, 0)).toBe(0);
    expect(sectionIndexAtPosition(sections, 9.999)).toBe(0);
    expect(sectionIndexAtPosition(sections, 10)).toBe(-1);
    expect(sectionIndexAtPosition(sections, 25)).toBe(1);
    expect(sectionIndexAtPosition(sections, 30)).toBe(-1);
  });
});

describe("headingForPositions", () => {
  it("emits each heading once, before the run of lines sung in it", () => {
    expect(
      headingForPositions(verseChorusChorus, [1, 5, 21, 25, 41, 45])
    ).toEqual(["verse", null, "chorus 1", null, "chorus 2", null]);
  });

  it("skips a section that has no lines", () => {
    // No position falls in 20–40, so `chorus 1` never appears.
    expect(headingForPositions(verseChorusChorus, [1, 45])).toEqual(["verse", "chorus 2"]);
  });

  it("inherits the previous heading across a position that matches no section", () => {
    const gapped: RecordingSection[] = [
      { label: "verse", start: 0, end: 10 },
      { label: "chorus", start: 20, end: 30 },
      { label: "chorus", start: 40, end: 50 },
    ];
    // 15 s sits in the uncovered 10–20 s span, so `verse` stays in force and
    // no empty heading appears between the two verse lines.
    expect(headingForPositions(gapped, [1, 15, 21, 41])).toEqual([
      "verse",
      null,
      "chorus 1",
      "chorus 2",
    ]);
  });

  it("emits nothing before the first matched section", () => {
    const late: RecordingSection[] = [{ label: "verse", start: 10, end: 20 }];
    expect(headingForPositions(late, [1, 12])).toEqual([null, "verse"]);
  });

  it("renders flat without section data", () => {
    expect(headingForPositions(null, [1, 2, 3])).toEqual([null, null, null]);
    expect(headingForPositions([], [1, 2])).toEqual([null, null]);
  });

  it("numbers repeated labels identically to the Arrange sheet", () => {
    const headings = buildSectionHeadings(verseChorusChorus);
    const labels = headingForPositions(verseChorusChorus, [21, 41]);
    expect(labels).toEqual([headings[1]!.displayLabel, headings[2]!.displayLabel]);
  });
});
