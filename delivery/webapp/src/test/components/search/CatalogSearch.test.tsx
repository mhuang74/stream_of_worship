import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import { BrowseSheet } from "@/components/songset/BrowseSheet";
import { ListenClient } from "@/app/listen/ListenClient";
import { albumFilterKey } from "@/lib/search/album-filter";
import type { SongCardData } from "@/components/songset/SongCard";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/listen",
  redirect: vi.fn(),
}));

const mockPlay = vi.fn();
const mockPause = vi.fn();

vi.mock("@/contexts/AudioPlayerContext", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/contexts/AudioPlayerContext")>();
  return {
    ...actual,
    useAudioPlayerContext: () => ({
      play: mockPlay,
      pause: mockPause,
      stop: vi.fn(),
      currentTrack: null,
      state: {
        isPlaying: false,
        currentTime: 0,
        duration: 0,
        volume: 1,
        isMuted: false,
        isLooping: false,
        loopWindowStart: 0,
        loopWindowEnd: 0,
      },
      togglePlay: vi.fn(),
      seek: vi.fn(),
      setVolume: vi.fn(),
      toggleMute: vi.fn(),
      toggleLoop: vi.fn(),
      setLoopWindow: vi.fn(),
      clearLoopWindow: vi.fn(),
      audioRef: { current: null },
    }),
  };
});

vi.mock("@/lib/r2/public-url", () => ({
  getPublicAudioUrl: vi.fn(() => null),
}));

// useOfflineRedirect probes connectivity; keep it quiet.
vi.mock("@/hooks/useOfflineRedirect", () => ({
  useOfflineRedirect: () => {},
}));

const mockFetch = vi.fn();
global.fetch = mockFetch;

const mockSongs = [
  {
    id: "song-1",
    title: "Amazing Grace",
    composer: "John Newton",
    lyricist: null,
    albumName: "Hymns",
    musicalKey: "G",
    recordings: [
      {
        contentHash: "abc123",
        hashPrefix: "abc123",
        durationSeconds: 180,
        tempoBpm: 120,
        musicalKey: "G",
        visibilityStatus: "published",
      },
    ],
  },
];

function makeFavoriteSong(id: string): SongCardData {
  return {
    id,
    title: `Favorite ${id}`,
    composer: "Composer",
    lyricist: null,
    albumName: null,
    musicalKey: "G",
    recordings: [
      {
        contentHash: `hash-${id}`,
        hashPrefix: `prefix-${id}`,
        durationSeconds: 180,
        tempoBpm: 120,
        musicalKey: "G",
        visibilityStatus: "published",
      },
    ],
  };
}

const mockAlbums = [
  { albumName: "Hymns", albumSeries: "Classic", songCount: 12 },
  { albumName: "Worship", albumSeries: null, songCount: 8 },
];

/**
 * Contract 1 (issue #253): ONE shared search component renders keyword +
 * describe modes with album/key/BPM/theme filters in both consumption
 * shells — the pull-up BrowseSheet and the /listen page — with identical
 * data-testids.
 */
describe("CatalogSearch (shared search component)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/songs/albums") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ albums: mockAlbums }),
        });
      }
      if (url.startsWith("/api/discovery")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ songs: [], hasMore: false }),
        });
      }
      if (url === "/api/favorites") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ songIds: [] }),
        });
      }
      if (url.includes("/api/songs")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ songs: mockSongs, total: mockSongs.length }),
        });
      }
      return Promise.resolve({ ok: false });
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const selectAlbumFilter = async () => {
    const testId = `album-option-${encodeURIComponent(albumFilterKey({ albumName: "Hymns", albumSeries: "Classic" }))}`;
    fireEvent.click(screen.getByTestId("album-filter"));
    await waitFor(() => {
      expect(screen.getByTestId(testId)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId(testId));
  };

  const expectThreeFilterGroups = () => {
    expect(screen.getByTestId("album-filter")).toBeInTheDocument();
    expect(screen.getByTestId("key-filter")).toBeInTheDocument();
    expect(screen.getByTestId("bpm-filter")).toBeInTheDocument();
    expect(screen.getByTestId("theme-filter")).toBeInTheDocument();
  };

  const assertKeywordAndDescribeModes = async (runSearch: () => Promise<void>) => {
    // Keyword mode
    fireEvent.change(screen.getByTestId("search-input"), { target: { value: "grace" } });
    expectThreeFilterGroups();
    expect(screen.getByTestId("keyword-mode-tab")).toHaveAttribute("aria-selected", "true");
    await runSearch();
    expect(
      mockFetch.mock.calls.some(([url]) => String(url).includes("/api/songs/search?"))
    ).toBe(true);

    // Describe mode: same filters, semantic endpoint
    fireEvent.click(screen.getByTestId("describe-mode-tab"));
    expect(screen.getByTestId("describe-mode-tab")).toHaveAttribute("aria-selected", "true");
    expectThreeFilterGroups();
    fireEvent.change(screen.getByTestId("semantic-search-input"), {
      target: { value: "songs about grace" },
    });
    await selectAlbumFilter();
    const songCountBefore = mockFetch.mock.calls.length;
    fireEvent.click(screen.getByTestId("semantic-search-button"));
    await waitFor(() => {
      expect(mockFetch.mock.calls.length).toBeGreaterThan(songCountBefore);
    });
    const semanticCall = mockFetch.mock.calls.find(
      ([url]) => String(url).includes("/api/songs/search/semantic")
    );
    expect(semanticCall).toBeDefined();
    expect(JSON.parse(semanticCall![1].body as string)).toEqual(
      expect.objectContaining({
        query: "songs about grace",
        albums: [{ albumName: "Hymns", albumSeries: "Classic" }],
      })
    );
  };

  it("BrowseSheet and ListenClient render the same testids and filter groups", async () => {
    // Shell 1: BrowseSheet
    render(
      <BrowseSheet
        isOpen
        onOpenChange={vi.fn()}
        onAddSong={vi.fn().mockResolvedValue(undefined)}
        existingSongIds={[]}
      />
    );
    await waitFor(() => {
      expect(screen.getByTestId("album-filter")).toBeInTheDocument();
    });
    expect(screen.getByTestId("search-controls-region")).toBeInTheDocument();
    expect(screen.getByTestId("filters-region")).toBeInTheDocument();
    expect(screen.getByTestId("search-action-row")).toBeInTheDocument();
    expect(screen.getByTestId("search-results-region")).toBeInTheDocument();

    const browseAlbumSelector = screen.getByTestId("album-filter");
    const browseSearchInput = screen.getByTestId("search-input");

    // Shell 2: ListenClient (fresh mount, unreachable from shell 1's DOM)
    render(
      <ListenClient
        initialFavoriteSongs={[makeFavoriteSong("fav-1")]}
        favoriteSongIds={[]}
      />
    );
    await waitFor(() => {
      expect(screen.getAllByTestId("album-filter").length).toBeGreaterThan(1);
    });

    const browseAlbumByIndex = screen
      .getAllByTestId("album-filter")
      .findIndex((el) => el === browseAlbumSelector);

    const searchInputs = screen.getAllByTestId("search-input");
    expect(searchInputs.length).toBeGreaterThanOrEqual(2);

    // Both shells expose identical structure-specific testids.
    for (const testid of [
      "search-input",
      "album-filter",
      "key-filter",
      "bpm-filter",
      "theme-filter",
      "search-button",
      "keyword-mode-tab",
      "describe-mode-tab",
      "search-controls-region",
      "filters-region",
      "search-action-row",
      "search-results-region",
    ]) {
      const count = screen.getAllByTestId(testid).length;
      expect(count, `${testid} must exist in both shells`).toBe(
        count >= 2 ? count : 2
      );
    }

    // The BrowseSheet's album filter was the first mount.
    expect(browseAlbumByIndex).toBe(0);
    expect(browseSearchInput).toBeInstanceOf(HTMLElement);
  });

  it("exercises keyword + describe modes with filters through the Listen shell", async () => {
    render(
      <ListenClient
        initialFavoriteSongs={[makeFavoriteSong("fav-1")]}
        favoriteSongIds={[]}
      />
    );
    await waitFor(() => {
      expect(screen.getAllByTestId("album-filter").length).toBeGreaterThan(0);
    });

    // The listen shell hosts exactly one CatalogSearch instance.
    const listenInput = screen.getAllByTestId("search-input")[0];
    expect(listenInput).toBeInTheDocument();

    await assertKeywordAndDescribeModes(async () => {
      const before = mockFetch.mock.calls.length;
      fireEvent.click(screen.getAllByTestId("search-button")[0]);
      await waitFor(() => {
        expect(mockFetch.mock.calls.length).toBeGreaterThan(before);
      });
    });
  });

  it("search results in the Listen shell render play+favorite cards without add buttons", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/songs/albums") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ albums: mockAlbums }),
        });
      }
      if (url.startsWith("/api/discovery")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ songs: [], hasMore: false }),
        });
      }
      if (url === "/api/favorites") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ songIds: ["song-1"] }),
        });
      }
      if (url.includes("/api/songs")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ songs: mockSongs, total: mockSongs.length }),
        });
      }
      return Promise.resolve({ ok: false });
    });

    render(
      <ListenClient
        initialFavoriteSongs={[makeFavoriteSong("fav-1")]}
        favoriteSongIds={[]}
      />
    );
    await waitFor(() => {
      expect(screen.getAllByTestId("album-filter").length).toBeGreaterThan(0);
    });

    fireEvent.click(screen.getAllByTestId("search-button")[0]);
    await waitFor(() => {
      expect(screen.getAllByText("Amazing Grace").length).toBeGreaterThan(0);
    });

    // Search region shows the heart (favorite) but no add-to-songset button.
    const favoriteButtons = screen.getAllByTestId("favorite-button");
    expect(favoriteButtons.length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByTestId("add-song-button")).not.toBeInTheDocument();
  });
});
