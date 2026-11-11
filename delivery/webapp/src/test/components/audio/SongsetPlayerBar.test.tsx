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
    gapBeats: null,
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

  it("seeks to an Item's start from its marker and highlights the current one", () => {
    render(<SongsetPlayerBar />);

    const markers = screen.getAllByTestId("songset-marker");
    expect(markers).toHaveLength(2);
    fireEvent.click(markers[1]);
    expect(mocks.seek).toHaveBeenCalledWith(secondItemStart);

    const afterClick = screen.getAllByTestId("songset-marker");
    expect(afterClick[0].classList.contains("bg-primary")).toBe(true);
    expect(afterClick[1].classList.contains("bg-primary")).toBe(false);
  });

  it("marks the second Item once the position crosses into it", () => {
    setSession({ audio: { timelinePositionSeconds: secondItemStart + 1 } });
    render(<SongsetPlayerBar />);

    const markers = screen.getAllByTestId("songset-marker");
    expect(markers[1].classList.contains("bg-primary")).toBe(true);
    expect(markers[0].classList.contains("bg-primary")).toBe(false);
    expect(screen.getByTestId("songset-subtitle")).toHaveTextContent("Second Song");
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
