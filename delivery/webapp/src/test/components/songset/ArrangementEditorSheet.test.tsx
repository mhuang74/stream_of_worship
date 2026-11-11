import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import { ArrangementEditorSheet } from "@/components/songset/ArrangementEditorSheet";
import type { SongListItem } from "@/components/songset/SongList";

// The preview transport is the shared Songset audio engine (#293); the sheet
// is tested against its documented controller contract (a timeline window into
// the whole-Songset transport).
const preview = vi.hoisted(() => ({
  options: null as null | {
    active: boolean;
    items: Array<Record<string, unknown>>;
    onPreviewError: (error: { kind: string; itemId?: string }) => void;
  },
  controller: {
    status: "idle" as "idle" | "loading" | "playing" | "paused" | "error",
    errorMessage: null as string | null,
    timelinePositionSeconds: 0,
    totalSeconds: 120,
    progress: null as null | {
      itemId: string;
      itemIndex: number;
      itemCount: number;
      phase: "fetching" | "decoding" | "ready";
      receivedBytes: number;
      totalBytes: number | null;
    },
    play: vi.fn().mockResolvedValue(undefined),
    pause: vi.fn(),
    stop: vi.fn(),
    seek: vi.fn(),
    startAtSeconds: vi.fn().mockResolvedValue(undefined),
  },
}));

// The sheet pauses the global Songset audition on mount; it does not own that
// transport, so a stub context is enough.
const audition = vi.hoisted(() => ({ pause: vi.fn() }));

vi.mock("@/contexts/SongsetListenContext", () => ({
  useSongsetListen: () => ({ audio: { pause: audition.pause } }),
}));

vi.mock("@/hooks/useSongsetAudio", () => ({
  useSongsetAudio: (options: typeof preview.options) => {
    preview.options = options;
    return preview.controller;
  },
}));

// Real LRC timestamps so per-component filtering and line highlighting can be
// asserted without network access.
const lyrics = vi.hoisted(() => ({
  lrcContent:
    "[00:01.00]Verse one line\n[00:04.00]Verse one second\n[00:11.00]Chorus line\n[00:15.00]Chorus second\n[00:21.00]Verse two line\n",
  loading: false,
}));

vi.mock("@/hooks/useSongLyrics", () => ({
  useSongLyrics: () => ({
    lrcContent: lyrics.lrcContent,
    lines: null,
    loading: lyrics.loading,
    error: null,
  }),
}));

const SECTIONS = [
  { label: "Verse", start: 0, end: 10 },
  { label: "Chorus", start: 10, end: 20 },
  { label: "Verse", start: 20, end: 30 },
];

const BEATS = Array.from({ length: 16 }, (_, index) => index * 2);

const BASE_SONG: NonNullable<SongListItem["song"]> = {
  id: "song-1",
  title: "Amazing Grace",
  composer: "John Newton",
  lyricist: null,
  albumName: "Hymns",
  musicalKey: "G",
};

function makeItem(overrides: Partial<SongListItem> = {}): SongListItem {
  return {
    id: "item-1",
    songId: "song-1",
    position: 0,
    song: { ...BASE_SONG },
    recording: {
      contentHash: "abc123",
      hashPrefix: "ab",
      durationSeconds: 30,
      tempoBpm: 120,
      musicalKey: "G",
      sections: SECTIONS,
      beats: BEATS,
    },
    gapBeats: 2,
    crossfadeEnabled: 0,
    keyShiftSemitones: 0,
    tempoRatio: 1,
    arrangementSteps: null,
    arrangedDurationSeconds: 30,
    arrangementAnalysisAvailable: true,
    ...overrides,
  };
}

function draftSectionIndices(): string[] {
  return screen
    .getAllByTestId("arrangement-draft-step")
    .map((element) => element.getAttribute("data-section-index") ?? "");
}

/** jsdom lays every element out at zero; the strip needs a real width. */
function stubStripWidth(element: HTMLElement, width: number) {
  vi.spyOn(element, "getBoundingClientRect").mockReturnValue({
    x: 0,
    y: 0,
    left: 0,
    top: 0,
    right: width,
    bottom: 48,
    width,
    height: 48,
    toJSON: () => ({}),
  } as DOMRect);
}

describe("ArrangementEditorSheet", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    preview.controller.status = "idle";
    preview.controller.errorMessage = null;
    preview.controller.timelinePositionSeconds = 0;
    preview.controller.totalSeconds = 120;
    preview.controller.progress = null;
    // The transport mock keeps position and status coherent with the sheet's
    // calls, so the boundary effect sees what the real hook would expose.
    preview.controller.play.mockImplementation(async () => {
      preview.controller.status = "playing";
    });
    preview.controller.pause.mockImplementation(() => {
      preview.controller.status = "paused";
    });
    preview.controller.stop.mockImplementation(() => {
      preview.controller.status = "idle";
      preview.controller.timelinePositionSeconds = 0;
    });
    preview.controller.seek.mockImplementation((seconds: number) => {
      preview.controller.timelinePositionSeconds = seconds;
    });
    // Mirrors the hook: seeking + playing lands the transport on the requested
    // timeline position, one second in, so a lyric line is current.
    preview.controller.startAtSeconds.mockImplementation(async (seconds: number) => {
      preview.controller.timelinePositionSeconds = seconds + 1;
      preview.controller.status = "playing";
    });
    lyrics.lrcContent =
      "[00:01.00]Verse one line\n[00:04.00]Verse one second\n[00:11.00]Chorus line\n[00:15.00]Chorus second\n[00:21.00]Verse two line\n";
    lyrics.loading = false;
    preview.options = null;
  });

  function renderSheet(
    overrides: Partial<Parameters<typeof ArrangementEditorSheet>[0]> = {}
  ) {
    const item = overrides.item ?? makeItem();
    const props = {
      isOpen: true,
      onOpenChange: vi.fn(),
      item,
      items: [item],
      onSave: vi.fn().mockResolvedValue(undefined),
      ...overrides,
    };
    return { props, ...render(<ArrangementEditorSheet {...props} />) };
  }

  /** Re-render after the transport mock moved (position/status are external). */
  async function moveTransport(
    view: { props: Parameters<typeof ArrangementEditorSheet>[0]; rerender: (ui: React.ReactElement) => void },
    next: { position?: number; status?: typeof preview.controller.status }
  ) {
    await act(async () => {
      if (next.position !== undefined) {
        preview.controller.timelinePositionSeconds = next.position;
      }
      if (next.status !== undefined) preview.controller.status = next.status;
      view.rerender(<ArrangementEditorSheet {...view.props} />);
    });
  }

  async function openAddDialog() {
    fireEvent.click(screen.getByTestId("arrangement-add-section"));
    return screen.findByTestId("arrangement-add-dialog");
  }

  async function addSectionFromDialog(index: number) {
    await openAddDialog();
    fireEvent.click(screen.getByTestId(`arrangement-add-${index}`));
  }

  it("pauses the global Songset audition when the sheet mounts", () => {
    renderSheet();

    expect(audition.pause).toHaveBeenCalledTimes(1);
  });

  it("initializes the draft with every source section in source order", async () => {
    renderSheet();

    expect(draftSectionIndices()).toEqual(["0", "1", "2"]);
    await openAddDialog();
    expect(screen.getByTestId("arrangement-add-0")).toHaveTextContent("Verse 1");
    expect(screen.getByTestId("arrangement-add-1")).toHaveTextContent("Chorus");
    expect(screen.getByTestId("arrangement-add-2")).toHaveTextContent("Verse 2");
    expect(screen.getByTestId("arrangement-component-count")).toHaveTextContent(
      /3 components/
    );
  });

  it("initializes from a saved arrangement instead of the source sections", () => {
    const saved = {
      version: 1,
      steps: [
        { sectionIndex: 2, label: "Verse", startSeconds: 20, endSeconds: 30 },
        { sectionIndex: 2, label: "Verse", startSeconds: 20, endSeconds: 30 },
        { sectionIndex: 0, label: "Verse", startSeconds: 0, endSeconds: 10 },
      ],
    };
    renderSheet({
      item: makeItem({
        arrangementSteps: JSON.stringify(saved),
        arrangedDurationSeconds: 30,
        arrangementAnalysisAvailable: true,
      }),
    });

    expect(draftSectionIndices()).toEqual(["2", "2", "0"]);
  });

  it("shows each component's label, source timecode, and its own lyric lines", () => {
    renderSheet();

    const rows = screen.getAllByTestId("arrangement-draft-step");
    expect(within(rows[0]).getByText("Verse 1")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Chorus")).toBeInTheDocument();
    expect(within(rows[0]).getByTestId("arrangement-bounds-trigger-0")).toHaveTextContent(
      "0:00.0–0:10.0"
    );

    // Only the lines inside that Step's source range belong to the block.
    expect(within(rows[0]).getByText("Verse one line")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Chorus line")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Verse two line")).toBeInTheDocument();
    expect(within(rows[0]).queryByText("Chorus line")).toBeNull();
    expect(screen.getAllByTestId("arrangement-lyric-line")).toHaveLength(5);
  });

  it("assigns a pickup line just before a boundary to the next component", () => {
    // Forced-alignment onsets can precede the beat-snapped section bound, so a
    // line 0.3 s before a boundary is the next section's pickup, not the tail
    // of the section that is ending.
    lyrics.lrcContent =
      "[00:01.00]Verse one line\n[00:04.00]Verse one second\n[00:09.70]Chorus pickup\n[00:11.00]Chorus line\n";
    renderSheet();

    const rows = screen.getAllByTestId("arrangement-draft-step");
    expect(within(rows[0]).getAllByTestId("arrangement-lyric-line")).toHaveLength(2);
    expect(within(rows[0]).queryByText("Chorus pickup")).toBeNull();
    expect(within(rows[1]).getAllByTestId("arrangement-lyric-line")).toHaveLength(2);
    expect(within(rows[1]).getByText("Chorus pickup")).toBeInTheDocument();
  });

  it("keeps a late line and a dropped-section pickup with their own components", () => {
    // Source section 10–20 s (Chorus) is dropped, so no Step tiles the 10 s or
    // 20 s boundary: the line 0.3 s after the first Step's end still belongs to
    // it, and the line 0.2 s before the second Step's start is its pickup.
    lyrics.lrcContent = "[00:10.30]Late tail\n[00:19.80]Dropped pickup\n";
    const saved = {
      version: 1,
      steps: [
        { sectionIndex: 0, label: "Verse", startSeconds: 0, endSeconds: 10 },
        { sectionIndex: 2, label: "Verse", startSeconds: 20, endSeconds: 30 },
      ],
    };
    renderSheet({
      item: makeItem({
        arrangementSteps: JSON.stringify(saved),
        arrangedDurationSeconds: 20,
        arrangementAnalysisAvailable: true,
      }),
    });

    const rows = screen.getAllByTestId("arrangement-draft-step");
    expect(within(rows[0]).getByText("Late tail")).toBeInTheDocument();
    expect(within(rows[0]).queryByText("Dropped pickup")).toBeNull();
    expect(within(rows[1]).getByText("Dropped pickup")).toBeInTheDocument();
    expect(within(rows[1]).queryByText("Late tail")).toBeNull();
  });

  it("labels a component without lyric lines as instrumental", () => {
    // Lyrics start after the intro, so the Intro Step has no lines of its own.
    lyrics.lrcContent =
      "[00:11.00]Verse line one\n[00:15.00]Verse line two\n[00:21.00]Verse line three\n";
    const item = makeItem({
      recording: {
        contentHash: "abc123",
        hashPrefix: "ab",
        durationSeconds: 30,
        tempoBpm: 120,
        musicalKey: "G",
        sections: [
          { label: "Intro", start: 0, end: 10 },
          { label: "Verse", start: 10, end: 30 },
        ],
        beats: BEATS,
      },
    });
    renderSheet({ item });

    const rows = screen.getAllByTestId("arrangement-draft-step");
    expect(within(rows[0]).getByText(/no lyrics in this section/i)).toBeInTheDocument();
    expect(within(rows[1]).getByText("Verse line one")).toBeInTheDocument();
  });

  it("reviews 4 beats each side of a junction", () => {
    renderSheet();

    const rows = screen.getAllByTestId("arrangement-draft-step");
    fireEvent.click(
      within(rows[0]).getByRole("button", {
        name: /review verse 1 to chorus: 4 beats each side, played twice/i,
      })
    );

    // Tempo 120 → 0.5 s/beat, so the junction at 10 s plays 8..12 s.
    expect(preview.controller.startAtSeconds).toHaveBeenCalledWith(8);
    expect(screen.getByTestId("arrangement-audition-pass")).toHaveTextContent(
      "Pass 1 of 2"
    );
    expect(preview.controller.stop).not.toHaveBeenCalled();

    // The strip draws that window against the 0..30 s arrangement.
    const region = screen.getByTestId("arrangement-transition-region");
    expect(Number.parseFloat(region.style.left)).toBeCloseTo((8 / 30) * 100, 5);
    expect(Number.parseFloat(region.style.width)).toBeCloseTo((4 / 30) * 100, 5);
  });

  it("keeps the final component out of the review", () => {
    renderSheet();

    const rows = screen.getAllByTestId("arrangement-draft-step");
    expect(
      within(rows[2]).getByTestId("arrangement-review-transition-2")
    ).toBeDisabled();
  });

  it("highlights the current lyric line while the review plays", () => {
    renderSheet();

    const rows = screen.getAllByTestId("arrangement-draft-step");
    fireEvent.click(within(rows[0]).getByTestId("arrangement-review-transition-0"));

    // The window opens at 8 s, one tick in is 9 s, so the second Verse line is
    // the current one.
    const activeLine = within(rows[0]).getByText("Verse one second");
    expect(activeLine).toHaveAttribute("data-active", "true");
    expect(screen.getByTestId("arrangement-audition-phase")).toHaveTextContent(
      "Playing: Amazing Grace · Verse 1"
    );
  });

  it("pauses and stops the review transport", () => {
    preview.controller.status = "playing";
    preview.controller.timelinePositionSeconds = 3;
    renderSheet();

    fireEvent.click(screen.getByTestId("arrangement-audition-play"));
    expect(preview.controller.pause).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId("arrangement-audition-stop"));
    expect(preview.controller.stop).toHaveBeenCalled();
  });

  it("loops a transition review twice and stops at the end", async () => {
    const view = renderSheet();
    fireEvent.click(screen.getByTestId("arrangement-review-transition-0"));
    expect(preview.controller.status).toBe("playing");

    // Pass 1 → 2: the window end (8..12) is reached, so the sheet rewinds.
    await moveTransport(view, { position: 11.99 });
    expect(screen.getByTestId("arrangement-audition-pass")).toHaveTextContent(
      "Pass 2 of 2"
    );
    expect(preview.controller.seek).toHaveBeenLastCalledWith(8);

    await moveTransport(view, { position: 11.99 });
    expect(screen.getByTestId("arrangement-audition-pass")).toHaveTextContent(
      "Review complete · 2 passes"
    );
    expect(preview.controller.pause).toHaveBeenCalledTimes(1);

    // A third boundary cannot seek again: the guard already handled pass 2.
    await moveTransport(view, { position: 11.995, status: "playing" });
    expect(preview.controller.seek).toHaveBeenCalledTimes(1);
  });

  it("completes a review whose window ends at the Songset end", async () => {
    // A 2 s final component makes the junction window (8..12) end exactly at
    // the timeline end, where the transport stops itself instead of ticking
    // past the boundary.
    preview.controller.totalSeconds = 12;
    const item = makeItem({
      recording: {
        contentHash: "abc123",
        hashPrefix: "ab",
        durationSeconds: 12,
        tempoBpm: 120,
        musicalKey: "G",
        sections: [
          { label: "Verse", start: 0, end: 10 },
          { label: "Chorus", start: 10, end: 12 },
        ],
        beats: BEATS,
      },
      arrangedDurationSeconds: 12,
    });
    const view = renderSheet({ item, items: [item] });

    fireEvent.click(screen.getByTestId("arrangement-review-transition-0"));
    expect(preview.controller.startAtSeconds).toHaveBeenLastCalledWith(8);

    // Natural end of pass 1: the sheet counts the pass and restarts playback
    // inside the window, because a seek alone would leave it stopped.
    await moveTransport(view, { position: 12, status: "paused" });
    expect(screen.getByTestId("arrangement-audition-pass")).toHaveTextContent(
      "Pass 2 of 2"
    );
    expect(preview.controller.startAtSeconds).toHaveBeenLastCalledWith(8);
    expect(preview.controller.seek).not.toHaveBeenCalled();

    await moveTransport(view, { position: 8, status: "playing" });
    await moveTransport(view, { position: 12, status: "paused" });
    expect(screen.getByTestId("arrangement-audition-pass")).toHaveTextContent(
      "Review complete · 2 passes"
    );
    expect(preview.controller.pause).toHaveBeenCalled();
  });

  it("ignores a stale position at the timeline end when a new window opens", async () => {
    // The arrangement preview stops at the timeline end…
    preview.controller.totalSeconds = 30;
    const view = renderSheet();
    fireEvent.click(screen.getByTestId("arrangement-audition-play"));
    await moveTransport(view, { position: 29.99 });
    await moveTransport(view, { position: 30, status: "paused" });
    expect(preview.controller.startAtSeconds).toHaveBeenCalledTimes(1);

    // …and the junction review's start is still in flight, so that render
    // carries position 30. The hook's own seek is synchronous, so this only
    // pins the invariant; it must never spend a pass.
    preview.controller.startAtSeconds.mockImplementationOnce(() => new Promise(() => {}));
    fireEvent.click(screen.getByTestId("arrangement-review-transition-0"));

    expect(preview.controller.startAtSeconds).toHaveBeenCalledTimes(2);
    expect(preview.controller.startAtSeconds).toHaveBeenLastCalledWith(8);
    expect(screen.getByTestId("arrangement-audition-pass")).toHaveTextContent(
      "Pass 1 of 2"
    );
  });

  it("restarts from the whole arrangement after a completed review", async () => {
    const view = renderSheet();
    fireEvent.click(screen.getByTestId("arrangement-review-transition-0"));
    await moveTransport(view, { position: 11.99 });
    await moveTransport(view, { position: 11.99 });
    expect(screen.getByTestId("arrangement-audition-pass")).toHaveTextContent(
      /review complete/i
    );

    fireEvent.click(screen.getByTestId("arrangement-audition-play"));

    expect(preview.controller.startAtSeconds).toHaveBeenLastCalledWith(0);
    // The rail's button starts the arrangement preview, never a review.
    expect(screen.queryByTestId("arrangement-audition-pass")).toBeNull();
  });

  it("seeks to a component by tapping its band", () => {
    renderSheet();
    const strip = screen.getByTestId("arrangement-audition-timeline");
    stubStripWidth(strip, 200);
    const bands = within(strip).getAllByRole("button");
    expect(bands).toHaveLength(3);

    fireEvent.pointerDown(bands[1], { pointerId: 1, clientX: 150, clientY: 6, button: 0 });
    fireEvent.pointerUp(strip, { pointerId: 1 });

    // Band 1 starts at 10 s; a tap seeks there and starts no review.
    expect(preview.controller.seek).toHaveBeenLastCalledWith(10);
    expect(preview.controller.startAtSeconds).not.toHaveBeenCalled();
  });

  it("scrubs the strip and seeks inside the arrangement window", () => {
    renderSheet();
    const strip = screen.getByTestId("arrangement-audition-timeline");
    stubStripWidth(strip, 200);

    fireEvent.pointerDown(strip, { pointerId: 2, clientX: 20, clientY: 6, button: 0 });
    // Under the 5 px tap tolerance nothing happens yet.
    fireEvent.pointerMove(strip, { pointerId: 2, clientX: 22, clientY: 7 });
    expect(preview.controller.seek).not.toHaveBeenCalled();

    fireEvent.pointerMove(strip, { pointerId: 2, clientX: 120, clientY: 7 });
    // 120 / 200 of the 0..30 s arrangement window.
    expect(preview.controller.seek).toHaveBeenLastCalledWith(18);

    fireEvent.pointerUp(strip, { pointerId: 2 });
  });

  it("nudges the playhead with the keyboard", () => {
    renderSheet();
    const playhead = screen.getByTestId("arrangement-audition-playhead");

    fireEvent.keyDown(playhead, { key: "ArrowRight" });
    expect(preview.controller.seek).toHaveBeenLastCalledWith(1);

    fireEvent.keyDown(playhead, { key: "End" });
    expect(preview.controller.seek).toHaveBeenLastCalledWith(30);

    fireEvent.keyDown(playhead, { key: "Home" });
    expect(preview.controller.seek).toHaveBeenLastCalledWith(0);
  });

  it("shows every component in the arrangement preview", () => {
    renderSheet();
    const strip = screen.getByTestId("arrangement-audition-timeline");

    expect(screen.getByTestId("arrangement-audition-summary")).toHaveTextContent(
      "Whole arrangement · 3 components"
    );
    const bands = within(strip).getAllByRole("button");
    expect(bands.map((band) => band.textContent)).toEqual([
      "Verse 1",
      "Chorus",
      "Verse 2",
    ]);
    // A lone item has no neighbour, so no lead-in or lead-out claims appear.
    expect(screen.queryByTestId("arrangement-next-opening-notice")).toBeNull();

    fireEvent.click(screen.getByTestId("arrangement-audition-play"));
    expect(preview.controller.startAtSeconds).toHaveBeenCalledWith(0);
  });

  it("previews a single component with no junction to review", () => {
    const item = makeItem({
      arrangementSteps: JSON.stringify({
        version: 1,
        steps: [{ sectionIndex: 0, label: "Verse", startSeconds: 0, endSeconds: 10 }],
      }),
      arrangedDurationSeconds: 10,
    });
    renderSheet({ item, items: [item] });

    expect(screen.getByTestId("arrangement-audition-timeline")).toBeInTheDocument();
    expect(screen.getByTestId("arrangement-audition-play")).not.toBeDisabled();
    // One component has no transition of its own.
    expect(screen.getByTestId("arrangement-review-transition-0")).toBeDisabled();

    fireEvent.click(screen.getByTestId("arrangement-audition-play"));
    expect(preview.controller.startAtSeconds).toHaveBeenCalledWith(0);
  });

  it("always leads in from the previous song and previews from there", () => {
    const previous = makeItem({
      id: "item-0",
      position: 0,
      song: { ...BASE_SONG, title: "Prev Song" },
    });
    const item = makeItem({ id: "item-1", position: 1 });
    renderSheet({ item, items: [previous, item] });

    // The previous item ends at 30 s, a 1 s gap follows, so this item starts
    // at 31 s and an 8-beat (4 s) lead-in opens the arrangement at 27 s.
    expect(
      screen.getByText("Prev Song · Full song → Amazing Grace · Verse 1")
    ).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("arrangement-audition-play"));
    expect(preview.controller.startAtSeconds).toHaveBeenCalledWith(27);
  });

  it("always leads out into the next song for 8 beats", () => {
    const item = makeItem({ id: "item-1", position: 0 });
    const next = makeItem({
      id: "item-2",
      position: 1,
      song: { ...BASE_SONG, title: "Next Song" },
    });
    renderSheet({ item, items: [item, next] });

    // The next item starts at 31 s; 8 beats of it is 3 s inside that item's
    // 30 s, so the arrangement window ends at 34 s.
    expect(screen.getByTestId("arrangement-next-opening-notice")).toHaveTextContent(
      "Next: Next Song · Opening — first 3 s"
    );
    expect(screen.getByTestId("arrangement-audition-playhead")).toHaveAttribute(
      "aria-valuemax",
      "34"
    );

    fireEvent.click(screen.getByTestId("arrangement-audition-play"));
    expect(preview.controller.startAtSeconds).toHaveBeenCalledWith(0);
  });

  it("pauses the arrangement preview at its window end", async () => {
    const view = renderSheet();
    fireEvent.click(screen.getByTestId("arrangement-audition-play"));

    await moveTransport(view, { position: 29.99 });
    expect(preview.controller.pause).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("arrangement-audition-pass")).toBeNull();

    // A finished preview starts over rather than resuming at its end.
    fireEvent.click(screen.getByTestId("arrangement-audition-play"));
    expect(preview.controller.startAtSeconds).toHaveBeenCalledTimes(2);
    expect(preview.controller.startAtSeconds).toHaveBeenLastCalledWith(0);
    expect(preview.controller.play).not.toHaveBeenCalled();
  });

  it("starts the arrangement preview instead of resuming a paused review", async () => {
    const view = renderSheet();
    fireEvent.click(screen.getByTestId("arrangement-review-transition-0"));

    await moveTransport(view, { position: 9, status: "paused" });
    fireEvent.click(screen.getByTestId("arrangement-audition-play"));

    expect(preview.controller.startAtSeconds).toHaveBeenLastCalledWith(0);
    expect(screen.queryByTestId("arrangement-audition-pass")).toBeNull();
  });

  it("opens a bounds transaction and auditions inside the candidate boundary", () => {
    renderSheet();
    fireEvent.click(screen.getByTestId("arrangement-bounds-trigger-0"));

    const endInput = screen.getByTestId("arrangement-bounds-input-end-0");
    expect(endInput).toHaveValue("0:10.0");

    fireEvent.click(screen.getByTestId("arrangement-bounds-audition-end-0"));

    // Three seconds inside the candidate end: 10 s - 3 s.
    expect(preview.controller.startAtSeconds).toHaveBeenCalledWith(7);
  });

  it("nudges a bound by 0.1 s and applies it as manual bounds", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    renderSheet({ onSave });
    fireEvent.click(screen.getByTestId("arrangement-bounds-trigger-0"));

    fireEvent.click(screen.getByTestId("arrangement-bounds-end-inc-0"));
    expect(screen.getByTestId("arrangement-bounds-input-end-0")).toHaveValue("0:10.1");
    fireEvent.click(screen.getByTestId("arrangement-bounds-apply-0"));

    expect(screen.getByTestId("arrangement-bounds-edited-0")).toBeInTheDocument();
    expect(screen.getByTestId("arrangement-status")).toHaveTextContent(/unsaved/i);

    fireEvent.click(screen.getByTestId("arrangement-save"));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave).toHaveBeenCalledWith({
      version: 1,
      steps: [
        {
          sectionIndex: 0,
          label: "Verse",
          startSeconds: 0,
          endSeconds: 10.1,
          manualBounds: true,
        },
        { sectionIndex: 1, label: "Chorus", startSeconds: 10, endSeconds: 20 },
        { sectionIndex: 2, label: "Verse", startSeconds: 20, endSeconds: 30 },
      ],
    });
  });

  it("restarts the bounds transaction from the committed Step after Apply", () => {
    renderSheet();
    fireEvent.click(screen.getByTestId("arrangement-bounds-trigger-0"));
    fireEvent.click(screen.getByTestId("arrangement-bounds-end-inc-0"));
    fireEvent.click(screen.getByTestId("arrangement-bounds-apply-0"));

    // Apply rebuilds the transport input from the committed draft, so the new
    // range is what a later audition plays.
    const forwarded = JSON.parse(
      String(preview.options?.items[0]?.arrangementSteps)
    ) as { steps: Array<{ endSeconds: number }> };
    expect(forwarded.steps[0]?.endSeconds).toBeCloseTo(10.1);

    fireEvent.click(screen.getByTestId("arrangement-bounds-trigger-0"));

    // The reopened session reads the committed bound, not the pre-Apply text,
    // and has no local edits of its own.
    expect(screen.getByTestId("arrangement-bounds-input-end-0")).toHaveValue("0:10.1");
    expect(screen.getByTestId("arrangement-bounds-apply-0")).toBeDisabled();

    // Auditioning after Apply starts inside the edited clip (10.1 s - 3 s).
    fireEvent.click(screen.getByTestId("arrangement-bounds-audition-end-0"));
    expect(preview.controller.startAtSeconds).toHaveBeenLastCalledWith(7.1);
  });

  it("discards the bounds candidate on Cancel without touching the draft", () => {
    renderSheet();
    fireEvent.click(screen.getByTestId("arrangement-bounds-trigger-0"));
    expect(screen.getByTestId("arrangement-audition-play")).toBeDisabled();

    fireEvent.change(screen.getByTestId("arrangement-bounds-input-end-0"), {
      target: { value: "0:09.0" },
    });
    fireEvent.click(screen.getByTestId("arrangement-bounds-cancel-0"));

    expect(screen.queryByTestId("arrangement-bounds-edited-0")).toBeNull();
    expect(screen.getByTestId("arrangement-status")).toHaveTextContent(/^Saved$/);
    expect(screen.getByTestId("arrangement-audition-play")).not.toBeDisabled();

    // Reopening starts a fresh session: the abandoned text and its edit flag
    // are gone, so Apply cannot fire without a new edit.
    fireEvent.click(screen.getByTestId("arrangement-bounds-trigger-0"));
    expect(screen.getByTestId("arrangement-bounds-input-end-0")).toHaveValue("0:10.0");
    expect(screen.getByTestId("arrangement-bounds-apply-0")).toBeDisabled();
  });

  it("blocks preview while a bounds candidate is unsavable", () => {
    renderSheet();
    fireEvent.click(screen.getByTestId("arrangement-bounds-trigger-0"));

    fireEvent.change(screen.getByTestId("arrangement-bounds-input-end-0"), {
      target: { value: "9.5" },
    });

    expect(screen.getByTestId("arrangement-bounds-error-0")).toBeInTheDocument();
    expect(screen.getByTestId("arrangement-bounds-apply-0")).toBeDisabled();
    expect(screen.getByTestId("arrangement-audition-play")).toBeDisabled();
    expect(screen.getByTestId("arrangement-review-transition-0")).toBeDisabled();
    // Typing an unsavable value blocks the transport but must not blank the
    // rail: the strip and summary describe the arrangement, not the candidate.
    expect(screen.getByTestId("arrangement-audition-timeline")).toBeInTheDocument();
    expect(screen.getByTestId("arrangement-audition-summary")).toBeInTheDocument();
  });

  it("offers an inferred Intro from the first sung lyric and flags it edited", async () => {
    lyrics.lrcContent =
      "[00:05.00]Intro over\n[00:11.00]Chorus line\n[00:15.00]Chorus second\n";
    const onSave = vi.fn().mockResolvedValue(undefined);
    renderSheet({ onSave });

    await openAddDialog();
    const introRow = screen.getByTestId("arrangement-add-inferred-intro");
    expect(introRow).toHaveTextContent("Intro (inferred from lyrics)");
    expect(introRow).toHaveTextContent("0:00.0–first lyric at 0:05.0");
    fireEvent.click(introRow);

    expect(draftSectionIndices()).toEqual(["0", "1", "2", "inferred-intro"]);
    const rows = screen.getAllByTestId("arrangement-draft-step");
    expect(within(rows[3]).getByText("Intro (inferred)")).toBeInTheDocument();
    expect(within(rows[3]).getByTestId("arrangement-bounds-edited-3")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("arrangement-save"));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    const saved = onSave.mock.calls[0][0] as {
      steps: Array<Record<string, unknown>>;
    };
    expect(saved.steps[3]).toEqual({
      sectionIndex: null,
      label: "intro",
      startSeconds: 0,
      endSeconds: 5,
      manualBounds: true,
    });
  });

  it("removes a step, stops the preview, and keeps the section available in the add dialog", async () => {
    renderSheet();

    const rows = screen.getAllByTestId("arrangement-draft-step");
    fireEvent.click(within(rows[1]).getByRole("button", { name: /remove step/i }));

    expect(draftSectionIndices()).toEqual(["0", "2"]);
    expect(preview.controller.stop).toHaveBeenCalled();
    expect(screen.getByTestId("arrangement-add-section")).not.toBeDisabled();
    await openAddDialog();
    expect(screen.getByTestId("arrangement-add-1")).not.toBeDisabled();
  });

  it("duplicates a step in place", () => {
    renderSheet();

    const rows = screen.getAllByTestId("arrangement-draft-step");
    fireEvent.click(within(rows[1]).getByRole("button", { name: /duplicate step/i }));

    expect(draftSectionIndices()).toEqual(["0", "1", "1", "2"]);
  });

  it("appends a section chosen in the add dialog to the end of the draft", async () => {
    renderSheet();

    await addSectionFromDialog(1);

    expect(draftSectionIndices()).toEqual(["0", "1", "2", "1"]);
    await waitFor(() =>
      expect(screen.queryByTestId("arrangement-add-dialog")).toBeNull()
    );
  });

  it("opens one picker listing every source section with duration and occurrence count", async () => {
    renderSheet();
    await openAddDialog();

    const rows = [0, 1, 2].map((index) =>
      screen.getByTestId(`arrangement-add-${index}`)
    );
    expect(rows[0]).toHaveTextContent("Verse 1");
    expect(rows[1]).toHaveTextContent("Chorus");
    expect(rows[1]).toHaveTextContent("0:10");
    expect(rows[2]).toHaveTextContent("Verse 2");
    // The sheet opens with every section already in the draft, so each
    // occurrence count starts at one.
    expect(rows[1]).toHaveTextContent("in arrangement ×1");

    fireEvent.click(rows[1]);
    await waitFor(() =>
      expect(screen.queryByTestId("arrangement-add-dialog")).toBeNull()
    );

    await openAddDialog();
    expect(screen.getByTestId("arrangement-add-1")).toHaveTextContent(
      "in arrangement ×2"
    );
  });

  it("disables the picker row for a section that snapping skips", async () => {
    const item = makeItem({
      recording: {
        contentHash: "abc123",
        hashPrefix: "ab",
        durationSeconds: 30,
        tempoBpm: 120,
        musicalKey: "G",
        sections: [
          { label: "Verse", start: 0, end: 10 },
          { label: "Tail", start: 25, end: 40 },
        ],
        beats: [0, 10, 20],
      },
      arrangementSteps: null,
    });
    renderSheet({ item, items: [item] });
    await openAddDialog();

    expect(screen.getByTestId("arrangement-add-0")).not.toBeDisabled();
    expect(screen.getByTestId("arrangement-add-1")).toBeDisabled();
  });

  it("undoes the last change and clears the dirty flag back at the start", () => {
    renderSheet();
    const undoButton = screen.getByTestId("arrangement-undo");
    expect(undoButton).toBeDisabled();

    const rows = screen.getAllByTestId("arrangement-draft-step");
    fireEvent.click(within(rows[1]).getByRole("button", { name: /remove step/i }));
    expect(draftSectionIndices()).toEqual(["0", "2"]);
    expect(undoButton).not.toBeDisabled();
    expect(screen.getByTestId("arrangement-status")).toHaveTextContent(/unsaved/i);

    fireEvent.click(undoButton);

    expect(draftSectionIndices()).toEqual(["0", "1", "2"]);
    expect(undoButton).toBeDisabled();
    expect(screen.getByTestId("arrangement-status")).toHaveTextContent(/^Saved$/);
  });

  it("undoes with the keyboard shortcut", async () => {
    renderSheet();

    await addSectionFromDialog(1);
    expect(draftSectionIndices()).toEqual(["0", "1", "2", "1"]);

    fireEvent.keyDown(window, { key: "z", ctrlKey: true });

    expect(draftSectionIndices()).toEqual(["0", "1", "2"]);
  });

  it("forwards sibling analysis so the transport can validate saved siblings", () => {
    const previous = makeItem({ id: "item-0", position: 0 });
    const item = makeItem({ id: "item-1", position: 1 });
    renderSheet({ item, items: [previous, item] });

    // Without the sibling's sections/beats the transport cannot tell a stale
    // saved arrangement from a valid one, so it must forward both.
    expect(preview.options?.items[0]).toMatchObject({
      itemId: "item-0",
      recordingSections: SECTIONS,
      recordingBeats: BEATS,
    });
    expect(preview.options?.items[1]).toMatchObject({
      itemId: "item-1",
      recordingSections: SECTIONS,
      recordingBeats: BEATS,
    });
  });

  it("saves the draft as one canonical arrangement and closes", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onOpenChange = vi.fn();
    renderSheet({ onSave, onOpenChange });

    fireEvent.click(screen.getByTestId("arrangement-save"));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave).toHaveBeenCalledWith({
      version: 1,
      steps: [
        { sectionIndex: 0, label: "Verse", startSeconds: 0, endSeconds: 10 },
        { sectionIndex: 1, label: "Chorus", startSeconds: 10, endSeconds: 20 },
        { sectionIndex: 2, label: "Verse", startSeconds: 20, endSeconds: 30 },
      ],
    });
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it("keeps the sheet open and shows the translated error when saving fails", async () => {
    const onSave = vi
      .fn()
      .mockRejectedValue(
        new Error(
          "Song arrangement is unavailable or stale; repair the Recording analysis or reset the arrangement."
        )
      );
    const onOpenChange = vi.fn();
    renderSheet({ onSave, onOpenChange });

    fireEvent.click(screen.getByTestId("arrangement-save"));

    await waitFor(() =>
      expect(screen.getByTestId("arrangement-save-error")).toHaveTextContent(
        /unavailable or stale/i
      )
    );
    expect(onOpenChange).not.toHaveBeenCalled();
  });

  it("asks before discarding unsaved changes and can keep editing", async () => {
    const onOpenChange = vi.fn();
    renderSheet({ onOpenChange });

    await addSectionFromDialog(1);
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));

    await waitFor(() =>
      expect(screen.getByText("Discard arrangement changes?")).toBeInTheDocument()
    );
    fireEvent.click(screen.getByRole("button", { name: /keep editing/i }));
    expect(onOpenChange).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));
    await waitFor(() =>
      expect(screen.getByText("Discard arrangement changes?")).toBeInTheDocument()
    );
    fireEvent.click(screen.getByRole("button", { name: /^discard$/i }));

    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it("resets to NULL only after the confirmation dialog", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const onOpenChange = vi.fn();
    renderSheet({ onSave, onOpenChange });

    fireEvent.click(screen.getByTestId("arrangement-reset"));
    await waitFor(() =>
      expect(screen.getByText("Reset arrangement?")).toBeInTheDocument()
    );
    expect(onSave).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /^reset arrangement$/i }));

    await waitFor(() => expect(onSave).toHaveBeenCalledWith(null));
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it("disables preview and save and explains when analysis is unavailable", () => {
    const item = makeItem({
      recording: {
        contentHash: "abc123",
        hashPrefix: "ab",
        durationSeconds: 30,
        tempoBpm: 120,
        musicalKey: "G",
        sections: null,
        beats: [],
      },
      arrangementSteps: null,
    });
    renderSheet({ item, items: [item] });

    expect(screen.queryAllByTestId("arrangement-draft-step")).toHaveLength(0);
    expect(screen.getByTestId("arrangement-add-section")).toBeDisabled();
    expect(screen.queryByTestId("arrangement-add-dialog")).toBeNull();
    expect(screen.getByTestId("arrangement-save")).toBeDisabled();
    expect(screen.getByTestId("arrangement-audition-play")).toBeDisabled();
  });

  it("explains when analysis exists but every section collapses during snapping", () => {
    const item = makeItem({
      recording: {
        contentHash: "abc123",
        hashPrefix: "ab",
        durationSeconds: 30,
        tempoBpm: 120,
        musicalKey: "G",
        sections: [{ label: "Intro", start: 0.17, end: 0.3 }],
        beats: [0, 10],
      },
      arrangementSteps: null,
    });
    renderSheet({ item, items: [item] });

    expect(screen.queryAllByTestId("arrangement-draft-step")).toHaveLength(0);
    expect(
      screen.getByText(/analysis is unavailable, so a new arrangement cannot be created/i)
    ).toBeInTheDocument();
    expect(screen.getByTestId("arrangement-save")).toBeDisabled();
    expect(screen.getByTestId("arrangement-audition-play")).toBeDisabled();
  });

  it("keeps a malformed saved arrangement open for explanation and reset", () => {
    const item = makeItem({
      arrangementSteps: "not-json",
      arrangedDurationSeconds: null,
      arrangementAnalysisAvailable: true,
    });
    renderSheet({ item, items: [item] });

    expect(screen.queryAllByTestId("arrangement-draft-step")).toHaveLength(0);
    expect(screen.getByText(/saved arrangement is invalid or stale/i)).toBeInTheDocument();
    expect(screen.getByTestId("arrangement-save")).toBeDisabled();
    expect(screen.getByTestId("arrangement-audition-play")).toBeDisabled();
    expect(screen.getByTestId("arrangement-reset")).not.toBeDisabled();
  });

  it("keeps a saved arrangement editable for reset when analysis is missing", () => {
    const item = makeItem({
      recording: {
        contentHash: "abc123",
        hashPrefix: "ab",
        durationSeconds: 30,
        tempoBpm: 120,
        musicalKey: "G",
        sections: null,
        beats: [],
      },
      arrangementSteps:
        '{"version":1,"steps":[{"sectionIndex":0,"label":"Verse","startSeconds":0,"endSeconds":10}]}',
      arrangedDurationSeconds: 10,
      arrangementAnalysisAvailable: true,
    });
    renderSheet({ item, items: [item] });

    expect(screen.getAllByTestId("arrangement-draft-step")).toHaveLength(1);
    expect(screen.getByText(/unavailable or stale/i)).toBeInTheDocument();
    expect(screen.getByTestId("arrangement-save")).toBeDisabled();
    // Without analysis there is no timeline to draw, so the strip is absent
    // (a single Step still produces a preview when analysis exists).
    expect(screen.queryByTestId("arrangement-audition-timeline")).toBeNull();
    expect(screen.getByTestId("arrangement-audition-play")).toBeDisabled();
    expect(screen.getByTestId("arrangement-reset")).not.toBeDisabled();
  });

  it("blocks preview and save when the draft exceeds 64 steps", () => {
    const manySteps = Array.from({ length: 64 }, () => ({
      sectionIndex: 0,
      label: "Verse",
      startSeconds: 0,
      endSeconds: 10,
    }));
    const item = makeItem({
      arrangementSteps: JSON.stringify({ version: 1, steps: manySteps }),
      arrangedDurationSeconds: 640,
      arrangementAnalysisAvailable: true,
    });
    renderSheet({ item, items: [item] });

    expect(screen.getAllByTestId("arrangement-draft-step")).toHaveLength(64);
    expect(screen.getByTestId("arrangement-add-section")).toBeDisabled();
    fireEvent.click(
      within(screen.getAllByTestId("arrangement-draft-step")[0]).getByRole(
        "button",
        { name: /duplicate step/i }
      )
    );

    expect(screen.getByTestId("arrangement-too-many-error")).toBeInTheDocument();
    expect(screen.getByTestId("arrangement-save")).toBeDisabled();
    expect(screen.getByTestId("arrangement-audition-play")).toBeDisabled();
    // Over-limit is a live editing state: the amber step-count message is the
    // only feedback, never a destructive stale/invalid alert.
    expect(screen.queryByText(/saved arrangement is invalid or stale/i)).toBeNull();
    expect(screen.queryByText(/unavailable or stale/i)).toBeNull();
    expect(screen.queryByTestId("arrangement-save-error")).toBeNull();
  });

  it("clears a surfaced preview error when a review starts", () => {
    renderSheet();

    act(() => {
      preview.options?.onPreviewError({
        kind: "playback-failed",
        itemId: "item-1",
      });
    });
    expect(screen.getByTestId("arrangement-audition-error")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("arrangement-review-transition-0"));

    expect(screen.queryByTestId("arrangement-audition-error")).toBeNull();
    expect(preview.controller.startAtSeconds).toHaveBeenCalledWith(8);
  });

  it("blocks every destructive control until a pending save settles", async () => {
    let resolveSave: (() => void) | null = null;
    const onSave = vi.fn(() => {
      const deferred = Promise.withResolvers<void>();
      resolveSave = deferred.resolve;
      return deferred.promise;
    });
    const onOpenChange = vi.fn();
    renderSheet({ onSave, onOpenChange });

    fireEvent.click(
      within(screen.getAllByTestId("arrangement-draft-step")[1]).getByRole(
        "button",
        { name: /remove step/i }
      )
    );
    const draftBeforeSave = draftSectionIndices();
    fireEvent.click(screen.getByTestId("arrangement-save"));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));

    // Draft mutation, preview, and close requests are all inert mid-save.
    expect(screen.getByTestId("arrangement-audition-play")).toBeDisabled();
    expect(screen.getByTestId("arrangement-undo")).toBeDisabled();
    expect(screen.getByTestId("arrangement-add-section")).toBeDisabled();
    for (const button of screen.getAllByRole("button", { name: /review .* to .*/i })) {
      expect(button).toBeDisabled();
    }
    for (const button of screen.getAllByRole("button", { name: /duplicate/i })) {
      expect(button).toBeDisabled();
    }
    for (const button of screen.getAllByRole("button", { name: /remove/i })) {
      expect(button).toBeDisabled();
    }
    for (const button of screen.getAllByRole("button", { name: /reorder/i })) {
      expect(button).toBeDisabled();
    }
    fireEvent.click(screen.getAllByRole("button", { name: /duplicate/i })[0]);
    fireEvent.click(screen.getAllByRole("button", { name: /remove/i })[0]);
    fireEvent.click(screen.getByTestId("arrangement-review-transition-0"));
    fireEvent.click(screen.getByTestId("arrangement-audition-play"));
    fireEvent.click(screen.getByRole("button", { name: /^cancel$/i }));

    expect(draftSectionIndices()).toEqual(draftBeforeSave);
    expect(preview.controller.startAtSeconds).not.toHaveBeenCalled();
    expect(preview.controller.play).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalled();
    expect(screen.queryByText("Discard arrangement changes?")).toBeNull();

    await act(async () => {
      resolveSave?.();
    });
    // The save itself closes the sheet once it settles.
    await waitFor(() => expect(onOpenChange).toHaveBeenCalledWith(false));
  });

  it("surfaces a no-recording preview rejection with its explanation", () => {
    renderSheet();

    act(() => {
      preview.options?.onPreviewError({ kind: "no-recording", itemId: "item-2" });
    });

    expect(screen.getByTestId("arrangement-audition-error")).toHaveTextContent(
      /no selected recording/i
    );
  });

  it("shows loading progress as a percentage", () => {
    preview.controller.status = "loading";
    preview.controller.progress = {
      itemId: "item-1",
      itemIndex: 0,
      itemCount: 1,
      phase: "fetching",
      receivedBytes: 5,
      totalBytes: 10,
    };
    renderSheet();

    expect(screen.getByTestId("arrangement-audition-play")).toHaveTextContent(
      "Loading preview... 50%"
    );
  });

  it("shows original vs arranged duration and the reset semantics", () => {
    renderSheet();

    expect(screen.getByText(/Arranged 30s/)).toBeInTheDocument();
    expect(screen.getByText(/Original 30s/)).toBeInTheDocument();
    expect(
      screen.getByText(/Saving keeps only these sections; Reset restores the full recording/i)
    ).toBeInTheDocument();
  });
});
