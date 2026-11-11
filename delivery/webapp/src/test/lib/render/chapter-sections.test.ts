import { describe, it, expect } from "vitest";
import { chapterLineHeadings } from "@/lib/render/chapter-sections";
import type { Chapter } from "@/lib/render/chapters";
import type { TimelineItemSource } from "@/lib/arrangements";

// A 30 s Recording, sections verse 0–10, chorus 10–20, chorus 20–30.
const SECTIONS = [
  { label: "verse", start: 0, end: 10 },
  { label: "chorus", start: 10, end: 20 },
  { label: "chorus", start: 20, end: 30 },
];

function item(overrides: Partial<TimelineItemSource> = {}): TimelineItemSource {
  return {
    itemId: "item-1",
    recordingHashPrefix: "hash-1",
    arrangementSteps: null,
    gapBeats: null,
    crossfadeEnabled: null,
    crossfadeDurationSeconds: null,
    tempoBpm: null,
    recordingDurationSeconds: 30,
    recordingSections: SECTIONS,
    recordingBeats: [],
    ...overrides,
  };
}

function chapters(
  lines: { text: string; startSeconds: number }[],
  overrides: Partial<Chapter> = {}
): Chapter[] {
  return [
    {
      position: 1,
      songTitle: "Song",
      startSeconds: 0,
      endSeconds: 30,
      lines,
      ...overrides,
    },
  ];
}

describe("chapterLineHeadings", () => {
  it("heads each run of lines with its Section Label in the shared numbering order", () => {
    const rows = chapterLineHeadings(
      chapters([
        { text: "verse line", startSeconds: 1 },
        { text: "chorus one", startSeconds: 11 },
        { text: "chorus two", startSeconds: 21 },
      ]),
      [item()]
    );

    expect(rows).toEqual([["verse", "chorus 1", "chorus 2"]]);
  });

  it("emits one heading per run, not per line", () => {
    const rows = chapterLineHeadings(
      chapters([
        { text: "a", startSeconds: 1 },
        { text: "b", startSeconds: 3 },
        { text: "c", startSeconds: 12 },
      ]),
      [item()]
    );

    expect(rows).toEqual([["verse", null, "chorus 1"]]);
  });

  it("maps global times back through an arranged cut, not the raw Recording", () => {
    // Arrangement plays only 20–30 s of the Recording, so the chapter is 10 s
    // long and both lines are sung in the second chorus.
    const arranged = item({
      arrangementSteps: JSON.stringify({
        version: 1,
        steps: [{ sectionIndex: 2, label: "chorus", startSeconds: 20, endSeconds: 30 }],
      }),
    });
    const rows = chapterLineHeadings(
      chapters(
        [
          { text: "first", startSeconds: 0 },
          { text: "second", startSeconds: 5 },
        ],
        { endSeconds: 10 }
      ),
      [arranged]
    );

    expect(rows).toEqual([["chorus 2", null]]);
  });

  it("numbers a repeated Step's lines at the repeat's own position", () => {
    const repeated = item({
      arrangementSteps: JSON.stringify({
        version: 1,
        steps: [
          { sectionIndex: 1, label: "chorus", startSeconds: 10, endSeconds: 20 },
          { sectionIndex: 2, label: "chorus", startSeconds: 20, endSeconds: 30 },
        ],
      }),
    });
    const rows = chapterLineHeadings(
      chapters(
        [
          { text: "first chorus", startSeconds: 1 },
          { text: "second chorus", startSeconds: 11 },
        ],
        { endSeconds: 20 }
      ),
      [repeated]
    );

    expect(rows).toEqual([["chorus 1", "chorus 2"]]);
  });

  it("renders flat for a Recording with no section data", () => {
    const rows = chapterLineHeadings(
      chapters([
        { text: "a", startSeconds: 1 },
        { text: "b", startSeconds: 5 },
      ]),
      [item({ recordingSections: null })]
    );

    expect(rows).toEqual([[null, null]]);
  });

  it("renders flat when the chapters and the timeline disagree", () => {
    const twoItems = [
      item({ itemId: "item-1" }),
      item({ itemId: "item-2", recordingHashPrefix: "hash-2" }),
    ];
    const lines = [{ text: "a", startSeconds: 1 }];

    // Chapter start does not match its Item's timeline start.
    expect(
      chapterLineHeadings(chapters(lines, { startSeconds: 2 }), [item()])
    ).toBeNull();

    // Fewer chapters than Items (a dropped Item).
    expect(chapterLineHeadings(chapters(lines), twoItems)).toBeNull();

    // No Items at all.
    expect(chapterLineHeadings(chapters(lines), [])).toBeNull();
  });

  it("renders flat for an unrendered or mismatched Item list", () => {
    const lines = [{ text: "a", startSeconds: 1 }];
    // A structurally invalid arrangement cannot produce a trustworthy timeline.
    expect(
      chapterLineHeadings(chapters(lines), [
        item({ arrangementSteps: JSON.stringify({ version: 1 }) }),
      ])
    ).toBeNull();
  });

  it("renders flat when a line falls outside every arranged clip", () => {
    const arranged = item({
      arrangementSteps: JSON.stringify({
        version: 1,
        steps: [{ sectionIndex: 0, label: "verse", startSeconds: 0, endSeconds: 10 }],
      }),
    });
    // Line 12 s is past the arranged cut (10 s): the manifest and the timeline
    // disagree, so no headings at all rather than a stale label.
    expect(
      chapterLineHeadings(
        chapters(
          [
            { text: "sung in verse", startSeconds: 1 },
            { text: "beyond the cut", startSeconds: 12 },
          ],
          { endSeconds: 15 }
        ),
        [arranged]
      )
    ).toBeNull();
  });

  it("resolves headings per chapter in order", () => {
    const twoItems = [
      item({ itemId: "item-1" }),
      item({
        itemId: "item-2",
        recordingHashPrefix: "hash-2",
        recordingSections: null,
        gapBeats: 0,
      }),
    ];
    const rows = chapterLineHeadings(
      [
        {
          position: 1,
          songTitle: "One",
          startSeconds: 0,
          endSeconds: 30,
          lines: [{ text: "a", startSeconds: 11 }],
        },
        {
          position: 2,
          songTitle: "Two",
          startSeconds: 30,
          endSeconds: 60,
          lines: [{ text: "b", startSeconds: 31 }],
        },
      ],
      twoItems
    );

    expect(rows).toEqual([["chorus 1"], [null]]);
  });
});
