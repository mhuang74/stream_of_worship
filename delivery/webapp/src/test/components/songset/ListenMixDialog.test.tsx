import { useState } from "react";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithLocale as render } from "@/test/render";
import { ListenMixDialog } from "@/components/songset/ListenMixDialog";
import type { UseSongsetAudioResult } from "@/hooks/useSongsetAudio";
import {
  toListenTimelineSources,
  type ListenSongsetItemPayload,
} from "@/lib/arrangements-listen";
import { buildArrangementTimeline } from "@/lib/arrangements";
import { t } from "@/lib/i18n/messages";

const mocks = vi.hoisted(() => ({
  playerStop: vi.fn(),
  useSongsetAudio: vi.fn(),
  play: vi.fn(),
  pause: vi.fn(),
  stop: vi.fn(),
  seek: vi.fn(),
  startAtSeconds: vi.fn(),
  retry: vi.fn(),
  validate: vi.fn(),
}));

vi.mock("@/contexts/AudioPlayerContext", () => ({
  useAudioPlayerContext: () => ({ stop: mocks.playerStop }),
}));

vi.mock("@/hooks/useSongsetAudio", () => ({
  useSongsetAudio: mocks.useSongsetAudio,
}));

vi.mock("@/lib/audio/songset-audio", () => ({
  validateSongsetAudioSources: mocks.validate,
}));

const mockFetch = vi.fn();
global.fetch = mockFetch;

const en = (key: Parameters<typeof t>[1]) => t("en", key);

function itemPayload(
  id: string,
  title: string | null,
): ListenSongsetItemPayload {
  return {
    id,
    recordingHashPrefix: `hash-${id}`,
    arrangementSteps: null,
    gapBeats: null,
    crossfadeEnabled: null,
    crossfadeDurationSeconds: null,
    song: { title },
    recording: {
      tempoBpm: 120,
      durationSeconds: 100,
      sections: null,
      beats: null,
    },
  };
}

const payloadOne = {
  deletedItemCount: 0,
  items: [itemPayload("item-1", "Amazing Grace")],
};

const payloadTwo = {
  deletedItemCount: 0,
  items: [itemPayload("item-2", "Second Song")],
};

const payloadMix = {
  deletedItemCount: 0,
  items: [
    itemPayload("item-1", "Amazing Grace"),
    itemPayload("item-2", "Second Song"),
  ],
};

const mixTimeline = buildArrangementTimeline(
  toListenTimelineSources(payloadMix.items),
);

function makeAudio(
  overrides: Partial<UseSongsetAudioResult> = {},
): UseSongsetAudioResult {
  return {
    status: "loading",
    errorMessage: null,
    timelinePositionSeconds: 0,
    totalSeconds: 100,
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

function mockPayload(payload: unknown, status = 200) {
  mockFetch.mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  });
}

const DEFAULT_PROPS = {
  songsetId: "set-1",
  songsetName: "Sunday Worship",
  isOpen: true,
  onOpenChange: vi.fn(),
};

function renderDialog(props: Partial<typeof DEFAULT_PROPS> = {}) {
  return render(<ListenMixDialog {...DEFAULT_PROPS} {...props} />);
}

function seekRange(): HTMLInputElement {
  const range = screen
    .getByTestId("listen-seek")
    .querySelector<HTMLInputElement>('input[type="range"]');
  if (!range) throw new Error("seek range input not found");
  return range;
}

function lastAudioOptions(): {
  active: boolean;
  items: { itemId: string }[];
} {
  const calls = mocks.useSongsetAudio.mock.calls;
  return calls[calls.length - 1][0];
}

describe("ListenMixDialog", () => {
  let audio: UseSongsetAudioResult;

  beforeEach(() => {
    vi.clearAllMocks();
    audio = makeAudio();
    mocks.useSongsetAudio.mockImplementation(() => audio);
    mocks.play.mockResolvedValue(undefined);
    mocks.retry.mockResolvedValue(undefined);
    mocks.validate.mockReturnValue(null);
    mockPayload(payloadOne);
  });

  it("stops the global player and issues exactly one GET (no POST)", async () => {
    renderDialog();

    await waitFor(() => {
      expect(lastAudioOptions().items).toHaveLength(1);
    });

    expect(mocks.playerStop).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/songsets/set-1");
    expect((init as RequestInit | undefined)?.method ?? "GET").toBe("GET");
    await waitFor(() => {
      expect(mocks.play).toHaveBeenCalledTimes(1);
    });
  });

  it("renders the loading item title and a determinate percentage", async () => {
    audio = makeAudio({
      progress: {
        itemId: "item-1",
        itemIndex: 0,
        itemCount: 1,
        phase: "fetching",
        receivedBytes: 250,
        totalBytes: 1000,
      },
    });

    renderDialog();

    const label = en("songsets.listen.loading")
      .replace("{title}", "Amazing Grace")
      .replace("{percent}", "25");
    expect(await screen.findByText(label)).toBeInTheDocument();
    const progress = screen.getByRole("progressbar");
    expect(progress).toHaveAttribute("value", "25");
    expect(progress).toHaveAttribute("max", "100");
  });

  it("renders the indeterminate label when the byte total is unknown", async () => {
    audio = makeAudio({
      progress: {
        itemId: "item-1",
        itemIndex: 0,
        itemCount: 1,
        phase: "decoding",
        receivedBytes: 0,
        totalBytes: null,
      },
    });

    renderDialog();

    const label = en("songsets.listen.loadingIndeterminate").replace(
      "{title}",
      "Amazing Grace",
    );
    expect(await screen.findByText(label)).toBeInTheDocument();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("shows Preparing the next song only while a prepare is in flight", async () => {
    // Playing item 0 while item 1 is prepared mid-set: that is
    // "Preparing the next song", not a loading state.
    audio = makeAudio({
      status: "playing",
      totalSeconds: mixTimeline.totalSeconds,
      progress: {
        itemId: "item-2",
        itemIndex: 1,
        itemCount: 2,
        phase: "fetching",
        receivedBytes: 10,
        totalBytes: 100,
      },
    });

    const { rerender } = renderDialog();

    expect(await screen.findByTestId("listen-status")).toHaveTextContent(
      en("songsets.listen.preparingNext"),
    );

    audio = makeAudio({ status: "playing", progress: null });
    rerender(<ListenMixDialog {...DEFAULT_PROPS} />);

    expect(screen.queryByTestId("listen-status")).toBeNull();
  });

  it("shows Play when autoplay is rejected", async () => {
    mocks.play.mockRejectedValueOnce(new Error("autoplay blocked"));

    renderDialog();

    expect(await screen.findByTestId("listen-play")).toHaveTextContent(
      en("songsets.listen.action.play"),
    );
  });

  it("shows the error, Retry, and Render hint; Retry replays", async () => {
    audio = makeAudio({ status: "error", errorMessage: "boom" });

    renderDialog();

    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
    expect(
      screen.getByText(en("songsets.listen.error.renderHint")),
    ).toBeInTheDocument();

    const retry = screen.getByTestId("listen-retry");
    await waitFor(() => expect(mocks.play).toHaveBeenCalled());
    fireEvent.click(retry);
    expect(mocks.retry).toHaveBeenCalledTimes(1);
  });

  it("maps a playback failure to its localized message", async () => {
    audio = makeAudio({ status: "error", errorMessage: "raw engine text" });

    const { rerender } = renderDialog();

    const options = lastAudioOptions() as unknown as {
      onPreviewError: (error: {
        kind: string;
        itemId: string | null;
        message: string;
      }) => void;
    };
    act(() => {
      options.onPreviewError({
        kind: "playback-failed",
        itemId: "item-1",
        message: "raw engine text",
      });
    });
    rerender(<ListenMixDialog {...DEFAULT_PROPS} />);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      en("songsets.listen.error.playbackFailed"),
    );
    expect(
      screen.getByTestId("listen-retry"),
    ).toBeInTheDocument();
  });

  it("refuses a Songset with a deleted Recording and creates no session", async () => {
    mockPayload({ ...payloadOne, deletedItemCount: 2 });

    renderDialog();

    expect(
      await screen.findByText(en("songsets.listen.error.deletedRecording")),
    ).toBeInTheDocument();
    expect(mocks.play).not.toHaveBeenCalled();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("maps validation failures to their strings", async () => {
    mocks.validate.mockReturnValue({
      kind: "no-recording",
      itemId: "item-1",
      message: "missing",
    });

    renderDialog();

    expect(
      await screen.findByText(en("songsets.listen.error.noRecording")),
    ).toBeInTheDocument();
    expect(mocks.play).not.toHaveBeenCalled();
  });

  it("maps load failures to not-found and build strings", async () => {
    mockPayload({}, 404);
    const { unmount } = renderDialog();
    expect(
      await screen.findByText(en("songsets.listen.error.notFound")),
    ).toBeInTheDocument();
    unmount();

    mockPayload({}, 500);
    renderDialog();
    expect(
      await screen.findByText(en("songsets.listen.error.build")),
    ).toBeInTheDocument();
  });

  it("closes via listen-close and sets active=false", async () => {
    function Harness() {
      const [open, setOpen] = useState(true);
      return (
        <ListenMixDialog
          songsetId="set-1"
          songsetName="Sunday Worship"
          isOpen={open}
          onOpenChange={setOpen}
        />
      );
    }

    render(<Harness />);
    await waitFor(() => {
      expect(lastAudioOptions().active).toBe(true);
    });

    fireEvent.click(screen.getByTestId("listen-close"));

    await waitFor(() => {
      expect(lastAudioOptions().active).toBe(false);
    });
  });

  it("remounts on a key change and fetches the new Songset", async () => {
    mockFetch.mockImplementation((url: string) =>
      Promise.resolve({
        ok: true,
        status: 200,
        json: async () =>
          url.includes("set-2") ? payloadTwo : payloadOne,
      }),
    );

    const { rerender } = render(
      <ListenMixDialog key="set-1" {...DEFAULT_PROPS} />,
    );

    await waitFor(() => {
      expect(lastAudioOptions().items[0]?.itemId).toBe("item-1");
    });

    rerender(
      <ListenMixDialog
        key="set-2"
        {...DEFAULT_PROPS}
        songsetId="set-2"
        songsetName="Evening Worship"
      />,
    );

    await waitFor(() => {
      expect(lastAudioOptions().items[0]?.itemId).toBe("item-2");
    });
    expect(mocks.playerStop).toHaveBeenCalledTimes(2);
    expect(mockFetch).toHaveBeenCalledTimes(2);
    expect(mockFetch.mock.calls[1][0]).toBe("/api/songsets/set-2");
    await waitFor(() => {
      expect(mocks.play).toHaveBeenCalledTimes(2);
    });
  });

  it("shows the seek control, m:ss readout, current title, and accessible labels", async () => {
    mockPayload(payloadMix);
    audio = makeAudio({
      status: "playing",
      timelinePositionSeconds: 150,
      totalSeconds: mixTimeline.totalSeconds,
    });

    renderDialog();

    expect(await screen.findByTestId("listen-seek")).toBeInTheDocument();
    expect(screen.getByTestId("listen-time")).toHaveTextContent(
      "2:30 / 3:21",
    );
    expect(screen.getByTestId("listen-current-item")).toHaveTextContent(
      `${en("songsets.listen.currentItem")}: Second Song`,
    );
    const seek = screen.getByTestId("listen-seek");
    const seekLabel = screen.getByText(en("songsets.listen.seekAria"));
    expect(seek).toHaveAttribute("aria-labelledby", seekLabel.id);
    expect(seekRange()).toHaveAttribute("aria-labelledby", seekLabel.id);
    expect(
      screen.getByRole("group", { name: en("songsets.listen.markersAria") }),
    ).toBeInTheDocument();
  });

  it("seeks immediately with the requested seconds when the range changes", async () => {
    audio = makeAudio({
      status: "playing",
      timelinePositionSeconds: 0,
      totalSeconds: 100,
    });

    renderDialog();

    await screen.findByTestId("listen-seek");
    fireEvent.change(seekRange(), { target: { value: "42" } });

    expect(mocks.seek).toHaveBeenCalledWith(42);
  });

  it("renders one marker per song and seeks to the clicked song's start", async () => {
    mockPayload(payloadMix);
    audio = makeAudio({
      status: "paused",
      totalSeconds: mixTimeline.totalSeconds,
    });

    renderDialog();

    await screen.findByTestId("listen-markers");
    const markers = screen.getAllByTestId("listen-marker");
    expect(markers).toHaveLength(2);
    expect(markers[0]).toHaveAccessibleName("Amazing Grace");
    expect(markers[1]).toHaveAccessibleName("Second Song");

    fireEvent.click(markers[1]);
    expect(mocks.seek).toHaveBeenLastCalledWith(
      mixTimeline.items[1].startSeconds,
    );
    fireEvent.click(markers[0]);
    expect(mocks.seek).toHaveBeenLastCalledWith(0);
  });

  it("moves the readout as the timeline position advances", async () => {
    audio = makeAudio({
      status: "playing",
      timelinePositionSeconds: 5,
      totalSeconds: 100,
    });
    const { rerender } = renderDialog();

    expect(await screen.findByTestId("listen-time")).toHaveTextContent(
      "0:05 / 1:40",
    );

    audio = makeAudio({
      status: "playing",
      timelinePositionSeconds: 65,
      totalSeconds: 100,
    });
    rerender(<ListenMixDialog {...DEFAULT_PROPS} />);

    expect(screen.getByTestId("listen-time")).toHaveTextContent(
      "1:05 / 1:40",
    );
  });

  it("keeps the requested position and shows progress when seeking into an unprepared song", async () => {
    mockPayload(payloadMix);
    const targetPosition = mixTimeline.items[1].startSeconds + 5;
    audio = makeAudio({
      status: "playing",
      totalSeconds: mixTimeline.totalSeconds,
      progress: null,
    });
    const { rerender } = renderDialog();

    await screen.findByTestId("listen-seek");
    fireEvent.change(seekRange(), {
      target: { value: String(targetPosition) },
    });
    expect(mocks.seek).toHaveBeenCalledWith(targetPosition);

    audio = makeAudio({
      status: "playing",
      timelinePositionSeconds: targetPosition,
      totalSeconds: mixTimeline.totalSeconds,
      progress: {
        itemId: "item-2",
        itemIndex: 1,
        itemCount: 2,
        phase: "fetching",
        receivedBytes: 25,
        totalBytes: 100,
      },
    });
    rerender(<ListenMixDialog {...DEFAULT_PROPS} />);

    expect(screen.getByTestId("listen-progress")).toHaveTextContent(
      en("songsets.listen.loading")
        .replace("{title}", "Second Song")
        .replace("{percent}", "25"),
    );
    expect(screen.queryByTestId("listen-status")).toBeNull();
    expect(screen.queryByTestId("listen-seek")).toBeNull();
  });
});
