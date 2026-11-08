import { describe, it, expect, vi, beforeEach } from "vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import { ArrangementEditorSheet } from "@/components/songset/ArrangementEditorSheet";
import type { SongListItem } from "@/components/songset/SongList";

// The preview transport is owned by issue #286's audio slice; the sheet is
// tested against its documented controller contract.
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
    isFallbackMode: false,
  },
}));

vi.mock("@/hooks/useArrangementPreview", () => ({
  useArrangementPreview: (options: typeof preview.options) => {
    preview.options = options;
    return preview.controller;
  },
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
    preview.controller.timelinePositionSeconds = 0;
    preview.controller.totalSeconds = 120;
    preview.controller.isFallbackMode = false;
    preview.controller.play.mockResolvedValue(undefined);
    preview.controller.startAt.mockResolvedValue(undefined);
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

  it("initializes the draft with every source section in source order", () => {
    renderSheet();

    expect(draftSectionIndices()).toEqual(["0", "1", "2"]);
    expect(screen.getByTestId("arrangement-add-0")).toHaveTextContent("Verse 1");
    expect(screen.getByTestId("arrangement-add-1")).toHaveTextContent("Chorus");
    expect(screen.getByTestId("arrangement-add-2")).toHaveTextContent("Verse 2");
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

  it("omits a step, stops the preview, and keeps the palette occurrence available", () => {
    renderSheet();

    const rows = screen.getAllByTestId("arrangement-draft-step");
    fireEvent.click(within(rows[1]).getByRole("button", { name: /remove step/i }));

    expect(draftSectionIndices()).toEqual(["0", "2"]);
    expect(preview.controller.stop).toHaveBeenCalled();
    expect(screen.getByTestId("arrangement-add-1")).not.toBeDisabled();
  });

  it("duplicates a step in place", () => {
    renderSheet();

    const rows = screen.getAllByTestId("arrangement-draft-step");
    fireEvent.click(within(rows[1]).getByRole("button", { name: /duplicate step/i }));

    expect(draftSectionIndices()).toEqual(["0", "1", "1", "2"]);
  });

  it("appends a palette section to the end of the draft", () => {
    renderSheet();

    fireEvent.click(screen.getByTestId("arrangement-add-1"));

    expect(draftSectionIndices()).toEqual(["0", "1", "2", "1"]);
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

    fireEvent.click(screen.getByTestId("arrangement-add-1"));
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
    expect(screen.getByText(/no sections to arrange/i)).toBeInTheDocument();
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
    fireEvent.click(screen.getByTestId("arrangement-add-0"));

    expect(screen.getByTestId("arrangement-too-many-error")).toBeInTheDocument();
    expect(screen.getByTestId("arrangement-save")).toBeDisabled();
    expect(screen.getByTestId("arrangement-preview-play")).toBeDisabled();
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

  it("wires preview transport controls to the hook", () => {
    preview.controller.status = "paused";
    renderSheet();

    fireEvent.click(screen.getByTestId("arrangement-preview-play"));
    expect(preview.controller.play).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId("arrangement-preview-stop"));
    expect(preview.controller.stop).toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("arrangement-start-at-item-1"));
    expect(preview.controller.startAt).toHaveBeenCalledWith("item-1");
  });

  it("pauses instead of playing while the preview is playing", () => {
    preview.controller.status = "playing";
    renderSheet();

    fireEvent.click(screen.getByTestId("arrangement-preview-play"));

    expect(preview.controller.pause).toHaveBeenCalledTimes(1);
    expect(preview.controller.play).not.toHaveBeenCalled();
  });

  it("shows original vs arranged duration and the reset semantics", () => {
    renderSheet();

    expect(screen.getByText("Original")).toBeInTheDocument();
    expect(screen.getByText("Arranged")).toBeInTheDocument();
    expect(
      screen.getByText(/Saving keeps only these sections; Reset restores the full recording/i)
    ).toBeInTheDocument();
  });
});
