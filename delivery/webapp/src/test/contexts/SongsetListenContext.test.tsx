import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderWithLocale as render } from "@/test/render";
import {
  SongsetListenProvider,
  useSongsetListen,
} from "@/contexts/SongsetListenContext";
import type { UseSongsetAudioResult } from "@/hooks/useSongsetAudio";
import type { ListenSongsetItemPayload } from "@/lib/arrangements-listen";

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
  useAudioPlayerContext: vi.fn(),
}));

vi.mock("@/contexts/AudioPlayerContext", () => ({
  useAudioPlayerContext: mocks.useAudioPlayerContext,
}));

vi.mock("@/hooks/useSongsetAudio", () => ({
  useSongsetAudio: mocks.useSongsetAudio,
}));

vi.mock("@/lib/audio/songset-audio", () => ({
  validateSongsetAudioSources: mocks.validate,
}));

const mockFetch = vi.fn();
global.fetch = mockFetch;

function itemPayload(id: string, title: string): ListenSongsetItemPayload {
  return {
    id,
    recordingHashPrefix: `hash-${id}`,
    arrangementSteps: null,
    gapBeats: null,
    crossfadeEnabled: null,
    crossfadeDurationSeconds: null,
    song: { title },
    recording: {
      contentHash: `content-${id}`,
      tempoBpm: 120,
      durationSeconds: 100,
      sections: null,
      beats: null,
    },
  };
}

function makeAudio(overrides: Partial<UseSongsetAudioResult> = {}): UseSongsetAudioResult {
  return {
    status: "idle",
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

function Probe() {
  const context = useSongsetListen();
  return (
    <div>
      <span data-testid="load-state">{context.loadState}</span>
      <span data-testid="load-error">{context.loadErrorKey ?? ""}</span>
      <span data-testid="item-count">{context.items.length}</span>
      <span data-testid="request">{context.request?.songsetId ?? ""}</span>
      <button
        type="button"
        data-testid="open"
        onClick={() => context.open({ songsetId: "set-1", name: "Sunday Worship" })}
      />
      <button type="button" data-testid="retry-load" onClick={context.retryLoad} />
      <button type="button" data-testid="close" onClick={context.close} />
    </div>
  );
}

function renderProvider(enabled = true) {
  return render(
    <SongsetListenProvider enabled={enabled}>
      <Probe />
    </SongsetListenProvider>,
  );
}

function lastAudioOptions(): { active: boolean; items: { itemId: string }[] } {
  const calls = mocks.useSongsetAudio.mock.calls;
  return calls[calls.length - 1][0];
}

describe("SongsetListenProvider", () => {
  let audio: UseSongsetAudioResult;
  let player: { currentTrack: unknown; stop: () => void };

  beforeEach(() => {
    vi.clearAllMocks();
    mockFetch.mockReset();
    audio = makeAudio();
    mocks.useSongsetAudio.mockImplementation(() => audio);
    player = { currentTrack: null, stop: mocks.playerStop };
    mocks.useAudioPlayerContext.mockImplementation(() => player);
    mocks.play.mockResolvedValue(undefined);
    mocks.retry.mockResolvedValue(undefined);
    mocks.validate.mockReturnValue(null);
    mockPayload({ deletedItemCount: 0, items: [itemPayload("item-1", "Amazing Grace")] });
  });

  it("stops the Song transport, issues exactly one GET, and autoplays a ready payload", async () => {
    renderProvider();

    fireEvent.click(screen.getByTestId("open"));

    await waitFor(() => {
      expect(screen.getByTestId("item-count")).toHaveTextContent("1");
    });

    expect(mocks.playerStop).toHaveBeenCalledTimes(1);
    expect(mockFetch).toHaveBeenCalledTimes(1);
    const [url, init] = mockFetch.mock.calls[0];
    expect(url).toBe("/api/songsets/set-1");
    expect((init as RequestInit | undefined)?.signal).toBeDefined();
    expect(screen.getByTestId("load-state")).toHaveTextContent("ready");
    await waitFor(() => {
      expect(mocks.play).toHaveBeenCalledTimes(1);
    });
    expect(lastAudioOptions().active).toBe(true);
  });

  it.each([401, 404])("reports a missing Songset for HTTP %i and never plays", async (status) => {
    mockPayload({}, status);
    renderProvider();

    fireEvent.click(screen.getByTestId("open"));

    await waitFor(() => {
      expect(screen.getByTestId("load-error")).toHaveTextContent("notFound");
    });
    expect(screen.getByTestId("load-state")).toHaveTextContent("error");
    expect(mocks.play).not.toHaveBeenCalled();
  });

  it("reports a build failure for a server error and never plays", async () => {
    mockPayload({}, 500);
    renderProvider();

    fireEvent.click(screen.getByTestId("open"));

    await waitFor(() => {
      expect(screen.getByTestId("load-error")).toHaveTextContent("build");
    });
    expect(screen.getByTestId("load-state")).toHaveTextContent("error");
    expect(mocks.play).not.toHaveBeenCalled();
  });

  it("does not play a payload with deleted items", async () => {
    mockPayload({ deletedItemCount: 1, items: [itemPayload("item-1", "Amazing Grace")] });
    renderProvider();

    fireEvent.click(screen.getByTestId("open"));

    await waitFor(() => {
      expect(screen.getByTestId("load-state")).toHaveTextContent("ready");
    });
    expect(screen.getByTestId("item-count")).toHaveTextContent("1");
    expect(mocks.play).not.toHaveBeenCalled();
  });

  it.each([
    ["no recording", { kind: "no-recording", itemId: "item-1", message: "no" }],
    ["invalid arrangement", { kind: "invalid-arrangement", itemId: null, message: "bad" }],
  ])("does not play when validation reports %s", async (_label, validation) => {
    mocks.validate.mockReturnValue(validation);
    renderProvider();

    fireEvent.click(screen.getByTestId("open"));

    await waitFor(() => {
      expect(screen.getByTestId("load-state")).toHaveTextContent("ready");
    });
    expect(mocks.play).not.toHaveBeenCalled();
  });

  it("closes the audition when the Song transport starts a track", async () => {
    const view = renderProvider();
    fireEvent.click(screen.getByTestId("open"));
    await waitFor(() => {
      expect(screen.getByTestId("request")).toHaveTextContent("set-1");
    });

    player.currentTrack = { id: "track-1" };
    view.rerender(
      <SongsetListenProvider enabled>
        <Probe />
      </SongsetListenProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("request")).toHaveTextContent("");
    });
    expect(mocks.stop).toHaveBeenCalled();
  });

  it("closes an open audition when the dock is disabled", async () => {
    const view = renderProvider(true);
    fireEvent.click(screen.getByTestId("open"));
    await waitFor(() => {
      expect(screen.getByTestId("request")).toHaveTextContent("set-1");
    });

    view.rerender(
      <SongsetListenProvider enabled={false}>
        <Probe />
      </SongsetListenProvider>,
    );

    await waitFor(() => {
      expect(screen.getByTestId("request")).toHaveTextContent("");
    });
    expect(mocks.stop).toHaveBeenCalled();
  });

  it("retryLoad re-issues the GET and clears the load error", async () => {
    mockPayload({}, 500);
    renderProvider();
    fireEvent.click(screen.getByTestId("open"));
    await waitFor(() => {
      expect(screen.getByTestId("load-error")).toHaveTextContent("build");
    });

    mockPayload({ deletedItemCount: 0, items: [itemPayload("item-1", "Amazing Grace")] });
    fireEvent.click(screen.getByTestId("retry-load"));

    await waitFor(() => {
      expect(screen.getByTestId("load-state")).toHaveTextContent("ready");
    });
    expect(screen.getByTestId("load-error")).toHaveTextContent("");
    expect(mockFetch).toHaveBeenCalledTimes(2);
    await waitFor(() => {
      expect(mocks.play).toHaveBeenCalledTimes(1);
    });
  });
});
