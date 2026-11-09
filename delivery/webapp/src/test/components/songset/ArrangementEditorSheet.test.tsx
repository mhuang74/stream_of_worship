import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import { ArrangementEditorSheet } from "@/components/songset/ArrangementEditorSheet";
import type { SongListItem } from "@/components/songset/SongList";

// The preview transport is owned by issue #286's audio slice; the sheet is
// tested against its documented controller contract (a timeline window into
// the whole-Songset transport).
const preview = vi.hoisted(() => ({
  options: null as null | {
    active: boolean;
    items: Array<Record<string, unknown>>;
    onPreviewError: (error: { kind: string; itemId?: string }) => void;
    onFallbackNotice: () => void;
  },
  controller: {
    status: "idle" as "idle" | "loading" | "playing" | "paused" | "error",
    errorMessage: null as string | null,
    timelinePositionSeconds: 0,
    totalSeconds: 120,
    play: vi.fn().mockResolvedValue(undefined),
    pause: vi.fn(),
    stop: vi.fn(),
    seek: vi.fn(),
    startAt: vi.fn().mockResolvedValue(undefined),
    startAtSeconds: vi.fn().mockResolvedValue(undefined),
    isFallbackMode: false,
  },
}));

vi.mock("@/hooks/useArrangementPreview", () => ({
  useArrangementPreview: (options: typeof preview.options) => {
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

function makeItem(overrides: Partial<SongListItem> = {}): SongListItem {
  return {
    id: "item-1",
    songId: "song-1",
    position: 0,
    song: {
      id: "song-1",
      title: "Amazing Grace",
      composer: "John Newton",
      lyricist: null,
      albumName: "Hymns",
      musicalKey: "G",
    },
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

describe("ArrangementEditorSheet", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    preview.controller.status = "idle";
    preview.controller.errorMessage = null;
    preview.controller.timelinePositionSeconds = 0;
    preview.controller.totalSeconds = 120;
    preview.controller.isFallbackMode = false;
    preview.controller.play.mockResolvedValue(undefined);
    preview.controller.startAt.mockResolvedValue(undefined);
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

  async function openAddDialog() {
    fireEvent.click(screen.getByTestId("arrangement-add-section"));
    return screen.findByTestId("arrangement-add-dialog");
  }

  async function addSectionFromDialog(index: number) {
    await openAddDialog();
    fireEvent.click(screen.getByTestId(`arrangement-add-${index}`));
  }

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
    expect(within(rows[0]).getByText("0:00–0:10")).toBeInTheDocument();

    // Only the lines inside that Step's source range belong to the block.
    expect(within(rows[0]).getByText("Verse one line")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Chorus line")).toBeInTheDocument();
    expect(within(rows[2]).getByText("Verse two line")).toBeInTheDocument();
    expect(within(rows[0]).queryByText("Chorus line")).toBeNull();
    expect(screen.getAllByTestId("arrangement-lyric-line")).toHaveLength(5);
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

  it("plays a single component from its own timeline position", () => {
    renderSheet();

    const rows = screen.getAllByTestId("arrangement-draft-step");
    fireEvent.click(within(rows[1]).getByRole("button", { name: /play section 2/i }));

    expect(preview.controller.startAtSeconds).toHaveBeenCalledWith(10);
    expect(preview.controller.stop).not.toHaveBeenCalled();
  });

  it("highlights the current lyric line while a component plays", () => {
    renderSheet();

    const rows = screen.getAllByTestId("arrangement-draft-step");
    fireEvent.click(within(rows[1]).getByRole("button", { name: /play section 2/i }));

    const activeLine = within(rows[1]).getByText("Chorus line");
    expect(activeLine).toHaveAttribute("data-active", "true");
    expect(screen.getByTestId("arrangement-preview-phase")).toHaveTextContent(
      "Playing Chorus"
    );
  });

  it("pauses the component that is already playing", () => {
    preview.controller.status = "playing";
    preview.controller.timelinePositionSeconds = 11;
    renderSheet();

    const rows = screen.getAllByTestId("arrangement-draft-step");
    fireEvent.click(within(rows[1]).getByRole("button", { name: /pause section 2/i }));

    expect(preview.controller.pause).toHaveBeenCalledTimes(1);
    expect(preview.controller.startAtSeconds).not.toHaveBeenCalled();
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

  it("starts the song preview at the lead-in window by default", () => {
    const previous = makeItem({ id: "item-0", position: 0 });
    const item = makeItem({ id: "item-1", position: 1 });
    renderSheet({ item, items: [previous, item] });

    // items[0] ends at 30s, then a 1s gap (2 beats at 120 BPM) → 31s start.
    expect(screen.getByTestId("arrangement-lead-in")).toHaveTextContent("10s");
    expect(
      screen.getByText(/hands over into your first component/i)
    ).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("arrangement-preview-play"));

    expect(preview.controller.startAtSeconds).toHaveBeenCalledWith(21);
  });

  it("explains that the first song has no lead-in", () => {
    renderSheet();

    expect(screen.getByTestId("arrangement-lead-in")).toHaveTextContent("0s");
    expect(
      screen.getByText(/first song in this set/i)
    ).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("arrangement-preview-play"));

    expect(preview.controller.startAtSeconds).toHaveBeenCalledWith(0);
  });

  it("pauses and stops the song preview", () => {
    preview.controller.status = "playing";
    renderSheet();

    fireEvent.click(screen.getByTestId("arrangement-preview-play"));
    expect(preview.controller.pause).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId("arrangement-preview-stop"));
    expect(preview.controller.stop).toHaveBeenCalled();
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
    expect(screen.getByTestId("arrangement-preview-play")).toBeDisabled();
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
    expect(screen.getByTestId("arrangement-preview-play")).toBeDisabled();
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
    expect(screen.getByTestId("arrangement-preview-play")).toBeDisabled();
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
    expect(screen.getByTestId("arrangement-preview-play")).toBeDisabled();
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
    expect(screen.getByTestId("arrangement-preview-play")).toBeDisabled();
    // Over-limit is a live editing state: the amber step-count message is the
    // only feedback, never a destructive stale/invalid alert.
    expect(screen.queryByText(/saved arrangement is invalid or stale/i)).toBeNull();
    expect(screen.queryByText(/unavailable or stale/i)).toBeNull();
    expect(screen.queryByTestId("arrangement-save-error")).toBeNull();
  });

  it("clears a surfaced preview error when a component starts playing", () => {
    renderSheet();

    act(() => {
      preview.options?.onPreviewError({
        kind: "playback-failed",
        itemId: "item-1",
      });
    });
    expect(screen.getByTestId("arrangement-preview-error")).toBeInTheDocument();

    const rows = screen.getAllByTestId("arrangement-draft-step");
    fireEvent.click(within(rows[0]).getByRole("button", { name: /play section 1/i }));

    expect(screen.queryByTestId("arrangement-preview-error")).toBeNull();
    expect(preview.controller.startAtSeconds).toHaveBeenCalledWith(0);
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
    expect(screen.getByTestId("arrangement-preview-play")).toBeDisabled();
    expect(screen.getByTestId("arrangement-undo")).toBeDisabled();
    expect(screen.getByTestId("arrangement-add-section")).toBeDisabled();
    for (const button of screen.getAllByRole("button", { name: /play section/i })) {
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
    fireEvent.click(screen.getAllByRole("button", { name: /play section/i })[0]);
    fireEvent.click(screen.getByTestId("arrangement-preview-play"));
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

    expect(screen.getByTestId("arrangement-preview-error")).toHaveTextContent(
      /no selected recording/i
    );
  });

  it("surfaces the iOS fallback notice", () => {
    renderSheet();

    act(() => {
      preview.options?.onFallbackNotice();
    });

    expect(screen.getByTestId("arrangement-fallback-notice")).toHaveTextContent(
      /hard boundaries/i
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
