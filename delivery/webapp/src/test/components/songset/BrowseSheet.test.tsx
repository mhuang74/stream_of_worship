import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { BrowseSheet } from "@/components/songset/BrowseSheet";
import { albumFilterKey } from "@/lib/search/album-filter";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("@/contexts/AudioPlayerContext", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/contexts/AudioPlayerContext")>();
  return {
    ...actual,
    useAudioPlayerContext: () => ({
      play: vi.fn(),
      pause: vi.fn(),
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

const mockAlbums = [
  { albumName: "Hymns", albumSeries: "Classic", songCount: 12 },
  { albumName: "Worship", albumSeries: null, songCount: 8 },
  { albumName: "Christmas", albumSeries: "Seasonal", songCount: 4 },
];
const hymnsFilter = { albumName: "Hymns", albumSeries: "Classic" };

describe("BrowseSheet", () => {
  const defaultProps = {
    isOpen: true,
    onOpenChange: vi.fn(),
    onAddSong: vi.fn().mockResolvedValue(undefined),
    existingSongIds: [],
  };

  beforeEach(() => {
    vi.clearAllMocks();
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/songs/albums") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ albums: mockAlbums }),
        });
      }
      if (url.includes("/api/songs/search/semantic")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ songs: [], total: 0 }),
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

  const renderSheet = (props = {}) => render(<BrowseSheet {...defaultProps} {...props} />);

  const waitForAlbums = async () => {
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith("/api/songs/albums");
    });
    await waitFor(() => {
      expect(screen.getByTestId("album-filter")).toBeInTheDocument();
    });
  };

  const songFetchCalls = () =>
    mockFetch.mock.calls.filter(
      ([url]) =>
        typeof url === "string" &&
        url.includes("/api/songs") &&
        url !== "/api/songs/albums"
    );

  const selectAlbum = async (album = hymnsFilter) => {
    const testId = `album-option-${encodeURIComponent(albumFilterKey(album))}`;
    fireEvent.click(screen.getByTestId("album-filter"));
    await waitFor(() => {
      expect(screen.getByTestId(testId)).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId(testId));
  };

  const selectKeyAndBpm = async () => {
    fireEvent.click(screen.getByTestId("key-filter"));
    await waitFor(() => {
      expect(screen.getByTestId("key-option-D")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("key-option-D"));

    fireEvent.click(screen.getByTestId("bpm-filter"));
    await waitFor(() => {
      expect(screen.getByTestId("bpm-option-slow")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("bpm-option-slow"));
  };

  const expectBefore = (before: Element, after: Element) => {
    expect(before.compareDocumentPosition(after) & Node.DOCUMENT_POSITION_FOLLOWING).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING
    );
  };

  it("opening the sheet fetches albums only, not songs", async () => {
    renderSheet();
    await waitForAlbums();

    expect(songFetchCalls()).toHaveLength(0);
    expect(screen.queryByText("Amazing Grace")).not.toBeInTheDocument();
  });

  it("keyword input and filter changes do not fetch until Search", async () => {
    renderSheet();
    await waitForAlbums();

    fireEvent.change(screen.getByTestId("search-input"), { target: { value: "grace" } });
    await selectAlbum();
    await selectKeyAndBpm();

    expect(songFetchCalls()).toHaveLength(0);

    fireEvent.click(screen.getByTestId("search-button"));

    await waitFor(() => {
      expect(songFetchCalls()).toHaveLength(1);
    });
  });

  it("blank Keyword Search fetches the default catalog", async () => {
    renderSheet();
    await waitForAlbums();

    fireEvent.click(screen.getByTestId("search-button"));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith("/api/songs?limit=50");
    });
    expect(await screen.findByText("Amazing Grace")).toBeInTheDocument();
  });

  it("Keyword Search with filters sends album, key, and BPM params", async () => {
    renderSheet();
    await waitForAlbums();

    await selectAlbum();
    await selectKeyAndBpm();
    fireEvent.click(screen.getByTestId("search-button"));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringMatching(
          /^\/api\/songs\?.*albumName=Hymns.*keys=D.*bpmRange=slow.*limit=50/
        )
      );
      expect(mockFetch).toHaveBeenCalledWith(expect.stringContaining("albumSeries=Classic"));
    });
  });

  it("Describe input and filter changes do not fetch until Search", async () => {
    renderSheet();
    await waitForAlbums();

    fireEvent.click(screen.getByTestId("describe-mode-tab"));
    fireEvent.change(screen.getByTestId("semantic-search-input"), {
      target: { value: "songs about grace" },
    });
    await selectAlbum();
    await selectKeyAndBpm();

    expect(songFetchCalls()).toHaveLength(0);

    fireEvent.click(screen.getByTestId("semantic-search-button"));

    await waitFor(() => {
      expect(songFetchCalls()).toHaveLength(1);
    });
  });

  it("blank Describe Search fetches the default catalog without similarity badges", async () => {
    renderSheet();
    await waitForAlbums();

    fireEvent.click(screen.getByTestId("describe-mode-tab"));
    fireEvent.click(screen.getByTestId("semantic-search-button"));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith("/api/songs?limit=50");
    });
    expect(await screen.findByText("Amazing Grace")).toBeInTheDocument();
    expect(screen.queryByTestId("similarity-badge")).not.toBeInTheDocument();
  });

  it("Describe Search with text sends semantic POST body with filters", async () => {
    renderSheet();
    await waitForAlbums();

    await selectAlbum();
    await selectKeyAndBpm();
    fireEvent.click(screen.getByTestId("describe-mode-tab"));
    fireEvent.change(screen.getByTestId("semantic-search-input"), {
      target: { value: "songs about grace" },
    });
    fireEvent.click(screen.getByTestId("semantic-search-button"));

    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        "/api/songs/search/semantic",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            query: "songs about grace",
            limit: 20,
            albums: [hymnsFilter],
            keys: ["D"],
            bpmRange: ["slow"],
          }),
        })
      );
    });
  });

  it("renders the active input before the album filter in Keyword mode", async () => {
    renderSheet();
    await waitForAlbums();

    const input = screen.getByTestId("search-input");
    const albumFilter = screen.getByTestId("album-filter");
    const searchButton = screen.getByTestId("search-button");
    const resultsRegion = screen.getByTestId("search-results-region");

    expectBefore(input, albumFilter);
    expectBefore(albumFilter, searchButton);
    expectBefore(searchButton, resultsRegion);
    expectBefore(albumFilter, resultsRegion);
    expect(screen.getByTestId("search-controls-region")).toContainElement(input);
    expect(resultsRegion).not.toContainElement(screen.getByTestId("shared-filters"));
  });

  it("renders the active input before the album filter in Describe mode", async () => {
    renderSheet();
    await waitForAlbums();

    fireEvent.click(screen.getByTestId("describe-mode-tab"));

    const input = screen.getByTestId("semantic-search-input");
    const albumFilter = screen.getByTestId("album-filter");
    const searchButton = screen.getByTestId("semantic-search-button");
    const resultsRegion = screen.getByTestId("search-results-region");

    expectBefore(input, albumFilter);
    expectBefore(albumFilter, searchButton);
    expectBefore(searchButton, resultsRegion);
    expectBefore(albumFilter, resultsRegion);
    expect(screen.getByTestId("search-controls-region")).toContainElement(input);
    expect(resultsRegion).not.toContainElement(screen.getByTestId("shared-filters"));
  });

  it("keeps shared filters in the common sheet layout across modes", async () => {
    renderSheet();
    await waitForAlbums();

    expect(screen.getAllByTestId("shared-filters")).toHaveLength(1);
    expect(screen.queryByTestId("semantic-search")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("describe-mode-tab"));

    expect(screen.getAllByTestId("shared-filters")).toHaveLength(1);
    expect(screen.queryByTestId("semantic-search")).not.toBeInTheDocument();
    expect(screen.getByTestId("search-results-region")).not.toContainElement(
      screen.getByTestId("shared-filters")
    );
  });

  it("uses the same Search button size in both modes", async () => {
    renderSheet();
    await waitForAlbums();

    expect(screen.getByTestId("search-button")).toHaveClass(
      "h-8",
      "w-[92px]",
      "text-sm"
    );

    fireEvent.click(screen.getByTestId("describe-mode-tab"));

    expect(screen.getByTestId("semantic-search-button")).toHaveClass(
      "h-8",
      "w-[92px]",
      "text-sm"
    );
  });

  it("renders results content after shared filters in both modes", async () => {
    renderSheet();
    await waitForAlbums();

    fireEvent.click(screen.getByTestId("search-button"));
    const keywordResult = await screen.findByText("Amazing Grace");
    expectBefore(screen.getByTestId("album-filter"), keywordResult);

    fireEvent.click(screen.getByTestId("describe-mode-tab"));
    fireEvent.click(screen.getByTestId("semantic-search-button"));
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith("/api/songs?limit=50");
    });
    const describeResult = await screen.findByTestId("semantic-search-results");

    expectBefore(screen.getByTestId("album-filter"), describeResult);
  });
});
