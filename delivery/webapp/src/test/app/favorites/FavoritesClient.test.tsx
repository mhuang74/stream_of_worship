import { describe, it, expect, beforeEach, vi } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import { FavoritesClient } from "@/app/favorites/FavoritesClient";
import type { SongCardData } from "@/components/songset/SongCard";

const mockReplace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace }),
}));

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
  },
}));

const mockPlay = vi.fn();
const mockPause = vi.fn();

vi.mock("@/contexts/AudioPlayerContext", () => ({
  useAudioPlayerContext: () => ({
    currentTrack: null,
    state: { isPlaying: false },
    play: mockPlay,
    pause: mockPause,
  }),
}));

vi.mock("@/lib/r2/public-url", () => ({
  getPublicAudioUrl: vi.fn(() => null),
}));

function makeSongs(count: number): SongCardData[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `song-${i + 1}`,
    title: `Song ${i + 1}`,
    composer: "Composer",
    lyricist: null,
    albumName: null,
    musicalKey: null,
    recordings: [
      {
        contentHash: `hash-${i + 1}`,
        hashPrefix: `prefix-${i + 1}`,
        durationSeconds: 180,
        tempoBpm: 120,
        musicalKey: "G",
        visibilityStatus: "published",
      },
    ],
  }));
}

describe("FavoritesClient pagination", () => {
  beforeEach(() => {
    mockReplace.mockClear();
    mockPlay.mockClear();
    mockPause.mockClear();
    vi.stubGlobal("scrollTo", vi.fn());
  });

  it("renders numbered page buttons for total > pageSize", () => {
    render(
      <FavoritesClient
        initialSongs={makeSongs(20)}
        initialTotal={45}
        currentPage={1}
        pageSize={20}
      />
    );

    const nav = screen.getByRole("navigation", { name: "Favorites pagination" });
    expect(nav).toBeInTheDocument();
    expect(screen.getByTestId("pagination-page-1")).toHaveAttribute(
      "aria-current",
      "page"
    );
    expect(screen.getByTestId("pagination-page-2")).toBeInTheDocument();
    expect(screen.getByTestId("pagination-page-3")).toBeInTheDocument();
  });

  it("renders Traditional Chinese UI chrome in zh-Hant", () => {
    render(
      <FavoritesClient
        initialSongs={makeSongs(20)}
        initialTotal={45}
        currentPage={1}
        pageSize={20}
      />,
      "zh-Hant"
    );

    expect(
      screen.getByRole("heading", { name: "我的最愛" })
    ).toBeInTheDocument();
    expect(screen.getByText(/45 首最愛詩歌/)).toBeInTheDocument();
    expect(
      screen.getByRole("navigation", { name: "我的最愛分頁" })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "下一頁" })
    ).toHaveAttribute("data-testid", "pagination-next");
  });

  it("fetches the next page and syncs the URL when page 2 is clicked", async () => {
    render(
      <FavoritesClient
        initialSongs={makeSongs(20)}
        initialTotal={45}
        currentPage={1}
        pageSize={20}
      />
    );

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ songs: makeSongs(20), total: 45 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    fireEvent.click(screen.getByTestId("pagination-page-2"));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/songs?limit=20&offset=20&favoritesOnly=1&visibilityStatus=published%2Creview"
      );
    });
    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/favorites?page=2");
    });
    vi.unstubAllGlobals();
  });

  it("renders empty state when initialSongs is empty", () => {
    render(
      <FavoritesClient
        initialSongs={[]}
        initialTotal={0}
        currentPage={1}
        pageSize={20}
      />
    );
    expect(screen.getByText("No favorites yet")).toBeInTheDocument();
  });

  it("renders fetched songs after clicking page 2 and clears isLoading", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ songs: makeSongs(20), total: 45 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <FavoritesClient
        initialSongs={makeSongs(20)}
        initialTotal={45}
        currentPage={1}
        pageSize={20}
      />
    );

    fireEvent.click(screen.getByTestId("pagination-page-2"));

    await waitFor(() => {
      expect(screen.getByTestId("favorites-list")).toBeInTheDocument();
    });
    // Spinner should be gone
    expect(screen.queryByRole("status")).not.toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it("does not leave the loader stuck after rapid page changes", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ songs: makeSongs(20), total: 45 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <FavoritesClient
        initialSongs={makeSongs(20)}
        initialTotal={45}
        currentPage={1}
        pageSize={20}
      />
    );

    fireEvent.click(screen.getByTestId("pagination-page-2"));
    fireEvent.click(screen.getByTestId("pagination-page-3"));

    await waitFor(() => {
      expect(screen.getByTestId("favorites-list")).toBeInTheDocument();
    });

    vi.unstubAllGlobals();
  });

  it("falls back to initialSongs when client fetch fails", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new Error("Network error"));
    vi.stubGlobal("fetch", fetchMock);

    render(
      <FavoritesClient
        initialSongs={makeSongs(20)}
        initialTotal={45}
        currentPage={1}
        pageSize={20}
      />
    );

    fireEvent.click(screen.getByTestId("pagination-page-2"));

    await waitFor(() => {
      expect(screen.getByText("Song 1")).toBeInTheDocument();
    });

    vi.unstubAllGlobals();
  });
});

describe("FavoritesClient playback", () => {
  beforeEach(() => {
    mockReplace.mockClear();
    mockPlay.mockClear();
    mockPause.mockClear();
    vi.stubGlobal("scrollTo", vi.fn());
  });

  it("renders a play button on each song card", () => {
    render(
      <FavoritesClient
        initialSongs={makeSongs(2)}
        initialTotal={2}
        currentPage={1}
        pageSize={20}
      />
    );

    const playButtons = screen.getAllByTestId("song-play-button");
    expect(playButtons).toHaveLength(2);
  });

  it("plays a song via the signed-url fallback when no public URL is available", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ url: "https://signed.example/audio.mp3" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <FavoritesClient
        initialSongs={makeSongs(1)}
        initialTotal={1}
        currentPage={1}
        pageSize={20}
      />
    );

    fireEvent.click(screen.getByTestId("song-play-button"));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith("/api/signed-url", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          hashPrefix: "prefix-1",
          fileType: "audio",
        }),
      });
    });

    await waitFor(() => {
      expect(mockPlay).toHaveBeenCalledWith({
        id: "song-song-1",
        title: "Song 1",
        artist: "Composer",
        src: "https://signed.example/audio.mp3",
        type: "song",
        duration: 180,
        recordingContentHash: "hash-1",
        songId: "song-1",
        originSongsetId: undefined,
      });
    });
    vi.unstubAllGlobals();
  });

  it("shows a loading spinner while resolving the signed URL", async () => {
    let resolveFetch: (value: unknown) => void;
    const fetchMock = vi.fn().mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveFetch = resolve;
        })
    );
    vi.stubGlobal("fetch", fetchMock);

    render(
      <FavoritesClient
        initialSongs={makeSongs(1)}
        initialTotal={1}
        currentPage={1}
        pageSize={20}
      />
    );

    fireEvent.click(screen.getByTestId("song-play-button"));

    await waitFor(() => {
      expect(screen.getByTestId("song-play-button").querySelector(".animate-spin")).toBeInTheDocument();
    });

    resolveFetch!({
      ok: true,
      json: async () => ({ url: "https://signed.example/audio.mp3" }),
    });

    await waitFor(() => {
      expect(mockPlay).toHaveBeenCalled();
    });
    vi.unstubAllGlobals();
  });

  it("shows an error toast when the song has no recording", async () => {
    const songs = makeSongs(1);
    songs[0].recordings = [];

    render(
      <FavoritesClient
        initialSongs={songs}
        initialTotal={1}
        currentPage={1}
        pageSize={20}
      />
    );

    fireEvent.click(screen.getByTestId("song-play-button"));

    const { toast } = await import("sonner");
    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("No audio available for this song");
    });
  });
});
