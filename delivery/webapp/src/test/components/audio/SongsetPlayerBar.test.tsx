import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithLocale as render } from "@/test/render";
import { SongsetPlayerBar } from "@/components/audio/SongsetPlayerBar";
import type { UseSongsetAudioResult } from "@/hooks/useSongsetAudio";
import type { SongsetAudioError } from "@/lib/audio/songset-audio";
import {
  toListenTimelineSources,
  type ListenSongsetItemPayload,
} from "@/lib/arrangements-listen";
import { buildArrangementTimeline, type ArrangementTimeline } from "@/lib/arrangements";
import type { RecordingSection } from "@/lib/arrangements";
import { t } from "@/lib/i18n/messages";

const mocks = vi.hoisted(() => ({
  context: null as unknown,
  play: vi.fn(),
  pause: vi.fn(),
  stop: vi.fn(),
  seek: vi.fn(),
  startAtSeconds: vi.fn(),
  retry: vi.fn(),
  close: vi.fn(),
  retryLoad: vi.fn(),
  lyrics: { lrcContent: null as string | null, lines: null as string[] | null },
}));

vi.mock("@/contexts/SongsetListenContext", () => ({
  useSongsetListen: () => mocks.context,
}));

vi.mock("@/hooks/useSongLyrics", () => ({
  useSongLyrics: () => ({
    lrcContent: mocks.lyrics.lrcContent,
    lines: mocks.lyrics.lines,
    loading: false,
    error: null,
  }),
}));

vi.mock("next/navigation", () => ({
  usePathname: () => "/songsets",
}));

const en = (key: Parameters<typeof t>[1]) => t("en", key);

function itemPayload(id: string, title: string, contentHash: string): ListenSongsetItemPayload {
  return {
    id,
    recordingHashPrefix: `hash-${id}`,
    arrangementSteps: null,
    gapBeats: 0,
    crossfadeEnabled: null,
    crossfadeDurationSeconds: null,
    song: { title },
    recording: {
      contentHash,
      tempoBpm: 120,
      durationSeconds: 10,
      sections: null,
      beats: null,
    },
  };
}

const items = [
  itemPayload("item-1", "Amazing Grace", "content-1"),
  itemPayload("item-2", "Second Song", "content-2"),
];
const timeline = buildArrangementTimeline(toListenTimelineSources(items));
const secondItemStart = timeline.items[1].startSeconds;

function stubSeekRect(element: HTMLElement, width = 800) {
  Object.defineProperty(element, "getBoundingClientRect", {
    configurable: true,
    value: () =>
      ({
        left: 0,
        top: 0,
        right: width,
        bottom: 24,
        width,
        height: 24,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      }) as DOMRect,
  });
}

function makeAudio(overrides: Partial<UseSongsetAudioResult> = {}): UseSongsetAudioResult {
  return {
    status: "playing",
    errorMessage: null,
    timelinePositionSeconds: 0,
    totalSeconds: timeline.totalSeconds,
    progress: null,
    play: mocks.play,
    pause: mocks.pause,
    stop: mocks.stop,
    seek: mocks.seek,
    startAtSeconds: mocks.startAtSeconds,
    retry: mocks.retry,
    ...overrides,
  };
}

interface SessionOverrides {
  items?: ListenSongsetItemPayload[];
  timeline?: ArrangementTimeline;
  audio?: Partial<UseSongsetAudioResult>;
  loadState?: "loading" | "ready" | "error";
  loadErrorKey?: "notFound" | "build" | null;
  deletedItemCount?: number;
  validation?: SongsetAudioError | null;
  previewErrorKind?: "no-recording" | "invalid-arrangement" | "playback-failed" | null;
}

function setSession(overrides: SessionOverrides = {}) {
  const sessionItems = overrides.items ?? items;
  mocks.context = {
    request: { songsetId: "set-1", name: "Sunday Worship" },
    loadState: overrides.loadState ?? "ready",
    loadErrorKey: overrides.loadErrorKey ?? null,
    items: sessionItems,
    deletedItemCount: overrides.deletedItemCount ?? 0,
    timeline: overrides.timeline ?? timeline,
    validation: overrides.validation ?? null,
    previewErrorKind: overrides.previewErrorKind ?? null,
    audio: makeAudio(overrides.audio),
    open: vi.fn(),
    retryLoad: mocks.retryLoad,
    close: mocks.close,
  };
}

describe("SongsetPlayerBar", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(HTMLElement.prototype, "setPointerCapture", {
      configurable: true,
      value: vi.fn(),
    });
    Object.defineProperty(HTMLElement.prototype, "hasPointerCapture", {
      configurable: true,
      value: vi.fn(() => true),
    });
    Object.defineProperty(HTMLElement.prototype, "releasePointerCapture", {
      configurable: true,
      value: vi.fn(),
    });
    mocks.lyrics = { lrcContent: null, lines: null };
    mocks.play.mockResolvedValue(undefined);
    mocks.retry.mockResolvedValue(undefined);
    setSession();
  });

  it("shows the Songset name with the now-playing Song as the secondary line", () => {
    render(<SongsetPlayerBar />);

    expect(screen.getByTestId("songset-title")).toHaveTextContent("Sunday Worship");
    expect(screen.getByTestId("songset-subtitle")).toHaveTextContent(
      `${en("songsets.listen.currentItem")} · Amazing Grace`,
    );
  });

  it("toggles play/pause and closes the audition", () => {
    render(<SongsetPlayerBar />);

    fireEvent.click(screen.getByTestId("songset-play"));
    expect(mocks.pause).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId("songset-close"));
    expect(mocks.close).toHaveBeenCalledTimes(1);
  });

  it("plays from a paused bar and clamps skip to the timeline bounds", () => {
    setSession({ audio: { status: "paused", timelinePositionSeconds: 2 } });
    render(<SongsetPlayerBar />);

    fireEvent.click(screen.getByTestId("songset-play"));
    expect(mocks.play).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId("songset-skip-back"));
    expect(mocks.seek).toHaveBeenCalledWith(0);

    mocks.seek.mockClear();
    fireEvent.click(screen.getByTestId("songset-skip-forward"));
    expect(mocks.seek).toHaveBeenCalledWith(12);
  });

  it("tiles the timeline into proportional segments and marks the current Item", () => {
    render(<SongsetPlayerBar />);

    expect(timeline.totalSeconds).toBe(20);
    const segments = screen.getAllByTestId("songset-seek-segment");
    expect(segments).toHaveLength(2);

    const widthSum = segments.reduce(
      (sum, segment) => sum + Number.parseFloat(segment.style.width),
      0,
    );
    expect(widthSum).toBeCloseTo(100);

    const last = segments[segments.length - 1]!;
    expect(Number.parseFloat(last.style.left) + Number.parseFloat(last.style.width)).toBeCloseTo(
      100,
    );

    const active = segments.filter((segment) => segment.hasAttribute("data-active"));
    expect(active).toHaveLength(1);
    expect(active[0]).toHaveAttribute("data-item-id", "item-1");
    expect(screen.getByTestId("songset-subtitle")).toHaveTextContent("Amazing Grace");
  });

  it("draws the transition zone once at the incoming Item's start", () => {
    const crossfadeItems = [
      itemPayload("item-1", "Amazing Grace", "content-1"),
      {
        ...itemPayload("item-2", "Second Song", "content-2"),
        crossfadeEnabled: 1,
        crossfadeDurationSeconds: 2,
      },
    ];
    const crossfadeTimeline = buildArrangementTimeline(toListenTimelineSources(crossfadeItems));
    setSession({
      items: crossfadeItems,
      timeline: crossfadeTimeline,
      audio: {
        totalSeconds: crossfadeTimeline.totalSeconds,
        timelinePositionSeconds:
          crossfadeTimeline.items[0]!.startSeconds +
          crossfadeTimeline.items[0]!.durationSeconds +
          1,
      },
    });
    render(<SongsetPlayerBar />);

    const segments = screen.getAllByTestId("songset-seek-segment");
    const transitions = screen.getAllByTestId("songset-seek-transition");
    expect(transitions).toHaveLength(1);
    expect(transitions[0]!.parentElement).toBe(segments[1]);
    expect(transitions[0]!.className).toContain("left-0");
    expect(transitions[0]!.style.width).toBe(
      `${(crossfadeTimeline.items[1]!.crossfadeSeconds / crossfadeTimeline.totalSeconds) * 100}%`,
    );
    expect(segments[0]!.querySelector('[data-testid="songset-seek-transition"]')).toBeNull();
    expect(segments[1]).toHaveAttribute("data-active");
    expect(segments[0]).not.toHaveAttribute("data-active");
  });

  it("seeks proportionally from a pointer press", () => {
    render(<SongsetPlayerBar />);
    const slider = screen.getByTestId("songset-seek");
    stubSeekRect(slider);

    fireEvent.pointerDown(slider, { clientX: 400, pointerId: 1 });

    expect(mocks.seek).toHaveBeenCalledWith(10);
  });

  it("snaps a pointer press near an Item boundary to that boundary", () => {
    render(<SongsetPlayerBar />);
    const slider = screen.getByTestId("songset-seek");
    stubSeekRect(slider);

    fireEvent.pointerDown(slider, { clientX: 404, pointerId: 1 });

    expect(mocks.seek).toHaveBeenCalledWith(secondItemStart);
  });

  it("seeks with keyboard steps and timeline bounds", () => {
    setSession({ audio: { timelinePositionSeconds: 2 } });
    render(<SongsetPlayerBar />);
    const slider = screen.getByTestId("songset-seek");

    fireEvent.keyDown(slider, { key: "ArrowRight" });
    expect(mocks.seek).toHaveBeenCalledWith(7);

    mocks.seek.mockClear();
    fireEvent.keyDown(slider, { key: "End" });
    expect(mocks.seek).toHaveBeenCalledWith(timeline.totalSeconds);
  });

  it("is inert when the timeline has no duration", () => {
    setSession({ audio: { totalSeconds: 0, timelinePositionSeconds: 0 } });
    render(<SongsetPlayerBar />);
    const slider = screen.getByTestId("songset-seek");
    expect(slider).toHaveAttribute("aria-disabled", "true");

    stubSeekRect(slider);
    fireEvent.pointerDown(slider, { clientX: 400, pointerId: 1 });
    fireEvent.click(slider);

    expect(mocks.seek).not.toHaveBeenCalled();
  });

  it("expands the lyric pullup, highlights the sung line, and swaps line sets across Items", () => {
    mocks.lyrics = {
      lrcContent: "[00:01.00]First song line\n[00:02.00]First song second\n",
      lines: null,
    };
    setSession({ audio: { timelinePositionSeconds: 1.5 } });
    const view = render(<SongsetPlayerBar />);

    expect(screen.queryByRole("region", { name: /lyrics/i })).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("songset-lyrics-toggle"));

    const rows = (): Element[] => Array.from(document.querySelectorAll("[data-lyric-index]"));
    expect(rows()).toHaveLength(2);
    expect(rows()[0]).toHaveAttribute("data-active", "true");

    // Crossing into the second Item remounts the panel with its own LRC.
    mocks.lyrics = {
      lrcContent: "[00:01.00]Second song line\n[00:02.00]Second song second\n",
      lines: null,
    };
    setSession({ audio: { timelinePositionSeconds: secondItemStart + 1.5 } });
    view.rerender(<SongsetPlayerBar />);

    expect(rows()).toHaveLength(2);
    expect(rows()[0]).toHaveTextContent("Second song line");
    expect(rows()[0]).toHaveAttribute("data-active", "true");
  });

  it("seeks to the tapped line's timeline position", () => {
    mocks.lyrics = {
      lrcContent: "[00:01.00]First song line\n[00:02.00]First song second\n",
      lines: null,
    };
    render(<SongsetPlayerBar />);

    fireEvent.click(screen.getByTestId("songset-lyrics-toggle"));
    const rows = Array.from(document.querySelectorAll("[data-lyric-index]"));
    fireEvent.click(rows[1]);

    expect(mocks.seek).toHaveBeenCalledWith(2);
  });

  // Section Label headings in the audition pullup (issue #304).
  describe("section headings", () => {
    const headingTexts = () =>
      Array.from(document.querySelectorAll("[data-lyric-heading]")).map((n) => n.textContent);

    function withSections(sections: RecordingSection[] | null) {
      const first = itemPayload("item-1", "Amazing Grace", "content-1");
      const second = itemPayload("item-2", "Second Song", "content-2");
      if (!first.recording || !second.recording) throw new Error("fixture needs recordings");
      setSession({
        items: [
          { ...first, recording: { ...first.recording, sections } },
          second,
        ],
      });
    }

    it("heads each run of lines under its Section Label in the shared numbering order", () => {
      mocks.lyrics = {
        lrcContent: "[00:01.00]verse line\n[00:03.00]still verse\n[00:06.00]chorus line\n",
        lines: null,
      };
      withSections([
        { label: "verse", start: 0, end: 5 },
        { label: "chorus", start: 5, end: 10 },
      ]);
      render(<SongsetPlayerBar />);

      fireEvent.click(screen.getByTestId("songset-lyrics-toggle"));

      expect(headingTexts()).toEqual(["verse", "chorus"]);
      // Headings are not rows: line indices and click-to-seek see lines only.
      expect(Array.from(document.querySelectorAll("[data-lyric-index]"))).toHaveLength(3);
      expect(document.querySelectorAll("[data-lyric-heading]")).toHaveLength(2);
    });

    it("numbers a repeated label by occurrence, like the Arrange sheet", () => {
      mocks.lyrics = {
        lrcContent: "[00:01.00]first chorus\n[00:06.00]second chorus\n",
        lines: null,
      };
      withSections([
        { label: "chorus", start: 0, end: 5 },
        { label: "chorus", start: 5, end: 10 },
      ]);
      render(<SongsetPlayerBar />);

      fireEvent.click(screen.getByTestId("songset-lyrics-toggle"));

      expect(headingTexts()).toEqual(["chorus 1", "chorus 2"]);
    });

    it("inherits the previous heading where no section has lines, rather than dropping it", () => {
      mocks.lyrics = {
        lrcContent: "[00:01.00]verse line\n[00:02.00]uncovered line\n",
        lines: null,
      };
      // Gap between sections: the 2 s line matches nothing.
      withSections([
        { label: "verse", start: 0, end: 1.5 },
        { label: "chorus", start: 5, end: 10 },
      ]);
      render(<SongsetPlayerBar />);

      fireEvent.click(screen.getByTestId("songset-lyrics-toggle"));

      expect(headingTexts()).toEqual(["verse"]);
      expect(document.querySelectorAll("[data-lyric-index]")).toHaveLength(2);
    });

    it("renders the flat list when the Recording has no section data", () => {
      mocks.lyrics = {
        lrcContent: "[00:01.00]a\n[00:02.00]b\n",
        lines: null,
      };
      withSections(null);
      render(<SongsetPlayerBar />);

      fireEvent.click(screen.getByTestId("songset-lyrics-toggle"));

      expect(headingTexts()).toEqual([]);
      expect(document.querySelectorAll("[data-lyric-index]")).toHaveLength(2);
    });
  });

  it("surfaces a playback error with a retry that recreates the session", () => {
    setSession({
      audio: { status: "error", errorMessage: "boom" },
      previewErrorKind: "playback-failed",
    });
    render(<SongsetPlayerBar />);

    const subtitle = screen.getByTestId("songset-subtitle");
    expect(subtitle).toHaveAttribute("role", "alert");
    expect(subtitle).toHaveTextContent(en("songsets.listen.error.playbackFailed"));

    fireEvent.click(screen.getByTestId("songset-retry"));
    expect(mocks.retry).toHaveBeenCalledTimes(1);
    expect(mocks.retryLoad).not.toHaveBeenCalled();
  });

  it("surfaces a load error with a retry that re-fetches the Songset", () => {
    setSession({ loadState: "error", loadErrorKey: "build" });
    render(<SongsetPlayerBar />);

    expect(screen.getByTestId("songset-subtitle")).toHaveTextContent(
      en("songsets.listen.error.build"),
    );

    fireEvent.click(screen.getByTestId("songset-retry"));
    expect(mocks.retryLoad).toHaveBeenCalledTimes(1);
    expect(mocks.retry).not.toHaveBeenCalled();
  });

  it("reports an empty Songset and disables playback", () => {
    const emptyTimeline = buildArrangementTimeline([]);
    setSession({ items: [], timeline: emptyTimeline });
    render(<SongsetPlayerBar />);

    expect(screen.getByTestId("songset-subtitle")).toHaveTextContent(
      en("songsets.listen.error.empty"),
    );
    expect(screen.getByTestId("songset-play")).toBeDisabled();
    expect(screen.queryByTestId("songset-lyrics-toggle")).not.toBeInTheDocument();
  });

  it("reports the in-flight Item while the engine prepares it", () => {
    setSession({
      audio: {
        timelinePositionSeconds: secondItemStart + 1,
        progress: {
          itemId: "item-2",
          itemIndex: 1,
          itemCount: 2,
          phase: "fetching",
          receivedBytes: 250,
          totalBytes: 1000,
        },
      },
    });
    render(<SongsetPlayerBar />);

    expect(screen.getByTestId("songset-subtitle")).toHaveTextContent(
      en("songsets.listen.loading").replace("{title}", "Second Song").replace("{percent}", "25"),
    );
  });

  it("reads 'Loading Songset…' before the payload arrives", async () => {
    setSession({ loadState: "loading", items: [] });
    render(<SongsetPlayerBar />);

    await waitFor(() => {
      expect(screen.getByTestId("songset-subtitle")).toHaveTextContent(
        en("songsets.listen.loadingSongset"),
      );
    });
  });
});
