import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  AudioPlayerProvider,
  useAudioPlayerContext,
} from "@/contexts/AudioPlayerContext";
import {
  useSongPlayback,
  type PlayableSong,
} from "@/hooks/useSongPlayback";
import {
  clearSongAudioSrc,
  sharedAudioBytesCache,
} from "@/lib/audio/audio-bytes-cache";

const { toastError, getPublicAudioUrl } = vi.hoisted(() => ({
  toastError: vi.fn(),
  getPublicAudioUrl: vi.fn<(hashPrefix: string) => string | null>(() => null),
}));

vi.mock("sonner", () => ({
  toast: { error: toastError },
}));

vi.mock("@/lib/r2/public-url", () => ({ getPublicAudioUrl }));

const SONGS: Record<string, PlayableSong> = {
  "song-1": {
    id: "song-1",
    title: "Amazing Grace",
    artist: "John Newton",
    recording: {
      hashPrefix: "hash-a",
      contentHash: "content-a",
      durationSeconds: 240,
    },
  },
};

/** Minimal stand-in for `/api/recordings/<hash>/audio`. */
function audioResponse(): Response {
  return {
    ok: true,
    status: 200,
    headers: { get: (name: string) => (name === "content-length" ? "8" : null) },
    body: null,
    arrayBuffer: async () => new ArrayBuffer(8),
  } as unknown as Response;
}

function Harness() {
  const { currentTrack, stop } = useAudioPlayerContext();
  const { handlePlay, playingSongId, previewLoadingSongId } = useSongPlayback({
    resolveSong: (songId) => SONGS[songId] ?? null,
    noAudioMessage: "This song has no audio available.",
    failedToLoadMessage: "Failed to load audio.",
  });

  return (
    <div>
      <div data-testid="src">{currentTrack?.src ?? ""}</div>
      <div data-testid="playing">{playingSongId ?? "-"}</div>
      <div data-testid="loading">{previewLoadingSongId ?? "-"}</div>
      <button
        type="button"
        data-testid="play"
        onClick={() => void handlePlay("song-1")}
      >
        play
      </button>
      <button type="button" data-testid="stop" onClick={stop}>
        stop
      </button>
    </div>
  );
}

function renderHarness() {
  return render(
    <AudioPlayerProvider>
      <Harness />
    </AudioPlayerProvider>,
  );
}

beforeEach(async () => {
  vi.clearAllMocks();
  getPublicAudioUrl.mockReturnValue(null);
  await sharedAudioBytesCache.clear();
  clearSongAudioSrc();
  Object.defineProperty(URL, "createObjectURL", {
    value: vi.fn(() => "blob:test-audio"),
    configurable: true,
    writable: true,
  });
  Object.defineProperty(URL, "revokeObjectURL", {
    value: vi.fn(),
    configurable: true,
    writable: true,
  });
});

afterEach(() => {
  Reflect.deleteProperty(URL, "createObjectURL");
  Reflect.deleteProperty(URL, "revokeObjectURL");
  vi.unstubAllGlobals();
});

describe("useSongPlayback", () => {
  it("plays an uncached song from the same-origin proxy as a blob URL", async () => {
    const fetchMock = vi.fn(async () => audioResponse());
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderHarness();

    await user.click(screen.getByTestId("play"));

    await waitFor(() =>
      expect(screen.getByTestId("src")).toHaveTextContent("blob:test-audio"),
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith("/api/recordings/hash-a/audio");
    expect(screen.getByTestId("playing")).toHaveTextContent("song-1");
    await waitFor(() =>
      expect(screen.getByTestId("loading")).toHaveTextContent("-"),
    );
  });

  it("replays the same cached blob URL without a second fetch", async () => {
    const fetchMock = vi.fn(async () => audioResponse());
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderHarness();

    await user.click(screen.getByTestId("play"));
    await waitFor(() =>
      expect(screen.getByTestId("src")).toHaveTextContent("blob:test-audio"),
    );

    await user.click(screen.getByTestId("stop"));
    expect(screen.getByTestId("src")).toHaveTextContent("");

    await user.click(screen.getByTestId("play"));
    await waitFor(() =>
      expect(screen.getByTestId("src")).toHaveTextContent("blob:test-audio"),
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("falls back to the public bucket URL when the cache path fails", async () => {
    const fetchMock = vi.fn(
      async () => ({ ok: false, status: 404 }) as unknown as Response,
    );
    vi.stubGlobal("fetch", fetchMock);
    getPublicAudioUrl.mockReturnValue("https://cdn.example/hash-a/audio.mp3");
    const user = userEvent.setup();
    renderHarness();

    await user.click(screen.getByTestId("play"));

    await waitFor(() =>
      expect(screen.getByTestId("src")).toHaveTextContent(
        "https://cdn.example/hash-a/audio.mp3",
      ),
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith("/api/recordings/hash-a/audio");
  });

  it("falls back to a signed URL when no public bucket URL exists", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/api/signed-url") {
        return {
          ok: true,
          status: 200,
          json: async () => ({ url: "https://signed.example/hash-a.mp3" }),
        } as unknown as Response;
      }
      return { ok: false, status: 404 } as unknown as Response;
    });
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderHarness();

    await user.click(screen.getByTestId("play"));

    await waitFor(() =>
      expect(screen.getByTestId("src")).toHaveTextContent(
        "https://signed.example/hash-a.mp3",
      ),
    );
    expect(fetchMock).toHaveBeenCalledWith("/api/signed-url", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hashPrefix: "hash-a", fileType: "audio" }),
    });
    expect(toastError).not.toHaveBeenCalled();
  });

  it("toggles to pause on a second click while the song plays", async () => {
    const fetchMock = vi.fn(async () => audioResponse());
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderHarness();

    await user.click(screen.getByTestId("play"));
    await waitFor(() =>
      expect(screen.getByTestId("playing")).toHaveTextContent("song-1"),
    );

    await user.click(screen.getByTestId("play"));

    await waitFor(() =>
      expect(screen.getByTestId("playing")).toHaveTextContent("-"),
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("toasts when neither the cache nor the streaming fallbacks produce a URL", async () => {
    const fetchMock = vi.fn(
      async () => ({ ok: false, status: 404 }) as unknown as Response,
    );
    vi.stubGlobal("fetch", fetchMock);
    const user = userEvent.setup();
    renderHarness();

    await user.click(screen.getByTestId("play"));

    await waitFor(() =>
      expect(toastError).toHaveBeenCalledWith("Failed to load audio."),
    );
    expect(screen.getByTestId("src")).toHaveTextContent("");
  });
});
