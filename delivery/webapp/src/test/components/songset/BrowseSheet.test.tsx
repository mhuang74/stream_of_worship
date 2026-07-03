import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { BrowseSheet } from "@/components/songset/BrowseSheet";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const mockPlay = vi.fn();

vi.mock("@/contexts/AudioPlayerContext", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/contexts/AudioPlayerContext")>();
  return {
    ...actual,
    useAudioPlayerContext: () => ({
      play: mockPlay,
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

const mockGetPublicAudioUrl = vi.fn(() => null);

vi.mock("@/lib/r2/public-url", () => ({
  getPublicAudioUrl: (...args: unknown[]) => mockGetPublicAudioUrl(...args),
}));

// Mock fetch globally
const mockFetch = vi.fn();
global.fetch = mockFetch;

describe("BrowseSheet", () => {
  const mockOnOpenChange = vi.fn();
  const mockOnAddSong = vi.fn().mockResolvedValue(undefined);

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
    {
      id: "song-2",
      title: "How Great Thou Art",
      composer: "Stuart Hine",
      lyricist: null,
      albumName: "Hymns",
      musicalKey: "A",
      recordings: [
        {
          contentHash: "def456",
          hashPrefix: "def456",
          durationSeconds: 240,
          tempoBpm: 100,
          musicalKey: "A",
          visibilityStatus: "published",
        },
      ],
    },
  ];

  const mockAlbums = ["Hymns", "Worship", "Christmas"];

  const defaultProps = {
    isOpen: true,
    onOpenChange: mockOnOpenChange,
    onAddSong: mockOnAddSong,
    existingSongIds: [],
  };

  beforeEach(() => {
    vi.clearAllMocks();
    
    // Default mock responses
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/songs/albums") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ albums: mockAlbums }),
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

  const renderSheet = (props = {}) => {
    return render(<BrowseSheet {...defaultProps} {...props} />);
  };

  describe("rendering", () => {
    it("renders sheet when isOpen is true", async () => {
      renderSheet();
      await waitFor(() => {
        expect(screen.getByText("Browse Songs")).toBeInTheDocument();
      });
    });

    it("renders search component", async () => {
      renderSheet();
      await waitFor(() => {
        expect(screen.getByTestId("search-input")).toBeInTheDocument();
      });
    });

    it("renders song cards when songs are loaded", async () => {
      renderSheet();
      await waitFor(() => {
        expect(screen.getByText("Amazing Grace")).toBeInTheDocument();
        expect(screen.getByText("How Great Thou Art")).toBeInTheDocument();
      });
    });

    it("renders album filter", async () => {
      renderSheet();
      await waitFor(() => {
        expect(screen.getByTestId("album-filter")).toBeInTheDocument();
      });
    });

    it("renders done button", async () => {
      renderSheet();
      await waitFor(() => {
        expect(screen.getByRole("button", { name: /done/i })).toBeInTheDocument();
      });
    });
  });

  describe("search functionality", () => {
    it("fetches songs on mount", async () => {
      renderSheet();
      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledWith(expect.stringContaining("/api/songs"));
      });
    });

    it("fetches albums on mount", async () => {
      renderSheet();
      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledWith("/api/songs/albums");
      });
    });

    it("searches with query when user types", async () => {
      renderSheet();
      
      await waitFor(() => {
        expect(screen.getByTestId("search-input")).toBeInTheDocument();
      });

      const input = screen.getByTestId("search-input");
      fireEvent.change(input, { target: { value: "amazing" } });

      // Wait for debounce
      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledWith(
          expect.stringContaining("/api/songs/search")
        );
      });
    });
  });

  describe("add song functionality", () => {
    it("calls onAddSong when add button is clicked", async () => {
      renderSheet();
      
      await waitFor(() => {
        expect(screen.getAllByTestId("add-song-button").length).toBeGreaterThan(0);
      });

      const addButtons = screen.getAllByTestId("add-song-button");
      fireEvent.click(addButtons[0]);

      await waitFor(() => {
        expect(mockOnAddSong).toHaveBeenCalledWith(mockSongs[0]);
      });
    });

    it("disables add button for already added songs", async () => {
      renderSheet({ existingSongIds: ["song-1"] });
      
      await waitFor(() => {
        const addButtons = screen.getAllByTestId("add-song-button");
        expect(addButtons[0]).toBeDisabled();
      });
    });
  });

  describe("error handling", () => {
    it("shows error message when fetch fails", async () => {
      mockFetch.mockImplementation(() => 
        Promise.resolve({
          ok: false,
          status: 500,
          statusText: "Internal Server Error",
        })
      );
      
      renderSheet();
      
      await waitFor(() => {
        expect(screen.getByText(/failed to search songs/i)).toBeInTheDocument();
      }, { timeout: 3000 });
    });

    it("shows retry button when fetch fails", async () => {
      mockFetch.mockImplementation(() => 
        Promise.resolve({
          ok: false,
          status: 500,
          statusText: "Internal Server Error",
        })
      );
      
      renderSheet();
      
      await waitFor(() => {
        expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
      }, { timeout: 3000 });
    });
  });

  describe("empty states", () => {
    it("shows empty state when no songs found", async () => {
      mockFetch.mockImplementation((url: string) => {
        if (url.includes("/api/songs")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ songs: [], total: 0 }),
          });
        }
        return Promise.resolve({ ok: false });
      });

      renderSheet();
      
      await waitFor(() => {
        expect(screen.getByText(/no songs available/i)).toBeInTheDocument();
      });
    });
  });

  describe("sheet closing", () => {
    it("calls onOpenChange when done button is clicked", async () => {
      renderSheet();

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /done/i })).toBeInTheDocument();
      });

      fireEvent.click(screen.getByRole("button", { name: /done/i }));
      expect(mockOnOpenChange).toHaveBeenCalledWith(false);
    });
  });

  describe("describe mode", () => {
    it("renders browse and describe mode tabs", async () => {
      renderSheet();
      await waitFor(() => {
        expect(screen.getByTestId("browse-mode-tab")).toBeInTheDocument();
        expect(screen.getByTestId("describe-mode-tab")).toBeInTheDocument();
      });
    });

    it("starts in browse mode by default", async () => {
      renderSheet();
      await waitFor(() => {
        expect(screen.getByTestId("search-input")).toBeInTheDocument();
        expect(screen.queryByTestId("semantic-search-input")).not.toBeInTheDocument();
      });
    });

    it("switches to describe mode when describe tab is clicked", async () => {
      renderSheet();
      await waitFor(() => {
        expect(screen.getByTestId("describe-mode-tab")).toBeInTheDocument();
      });

      fireEvent.click(screen.getByTestId("describe-mode-tab"));

      await waitFor(() => {
        expect(screen.getByTestId("semantic-search-input")).toBeInTheDocument();
        expect(screen.queryByTestId("search-input")).not.toBeInTheDocument();
      });
    });

    it("switches back to browse mode from describe mode", async () => {
      renderSheet();
      await waitFor(() => {
        expect(screen.getByTestId("describe-mode-tab")).toBeInTheDocument();
      });

      fireEvent.click(screen.getByTestId("describe-mode-tab"));
      await waitFor(() => {
        expect(screen.getByTestId("semantic-search-input")).toBeInTheDocument();
      });

      fireEvent.click(screen.getByTestId("browse-mode-tab"));
      await waitFor(() => {
        expect(screen.getByTestId("search-input")).toBeInTheDocument();
      });
    });
  });

  describe("advanced filters", () => {
    it("renders advanced filters toggle", async () => {
      renderSheet();
      await waitFor(() => {
        expect(screen.getByTestId("advanced-filters-toggle")).toBeInTheDocument();
      });
    });

    it("opens advanced panel when toggle is clicked", async () => {
      renderSheet();
      await waitFor(() => {
        expect(screen.getByTestId("advanced-filters-toggle")).toBeInTheDocument();
      });

      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));

      await waitFor(() => {
        expect(screen.getByTestId("advanced-filters-panel")).toBeInTheDocument();
      });
    });

    it("renders results after applying advanced filters", async () => {
      renderSheet();
      await waitFor(() => {
        expect(screen.getByTestId("advanced-filters-toggle")).toBeInTheDocument();
      });

      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));
      fireEvent.click(screen.getByTestId("key-chip-D"));
      fireEvent.click(screen.getByTestId("advanced-apply-button"));

      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledWith(
          expect.stringContaining("keys=D")
        );
      });
    });

    it("renders results after applying BPM filter", async () => {
      renderSheet();
      await waitFor(() => {
        expect(screen.getByTestId("advanced-filters-toggle")).toBeInTheDocument();
      });

      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));
      fireEvent.click(screen.getByTestId("bpm-chip-slow"));
      fireEvent.click(screen.getByTestId("advanced-apply-button"));

      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledWith(
          expect.stringContaining("bpmRange=slow")
        );
      });
    });

    it("shows filter-specific empty state when no results match filters", async () => {
      mockFetch.mockImplementation((url: string) => {
        if (url === "/api/songs/albums") {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ albums: mockAlbums }),
          });
        }
        if (url.includes("/api/songs")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ songs: [], total: 0 }),
          });
        }
        return Promise.resolve({ ok: false });
      });

      renderSheet();
      await waitFor(() => {
        expect(screen.getByTestId("advanced-filters-toggle")).toBeInTheDocument();
      });

      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));
      fireEvent.click(screen.getByTestId("key-chip-D"));
      fireEvent.click(screen.getByTestId("advanced-apply-button"));

      await waitFor(() => {
        expect(screen.getByText(/no songs match your filters/i)).toBeInTheDocument();
      });
    });
  });

  describe("play functionality", () => {
    it("renders song cards with play buttons", async () => {
      renderSheet();

      await waitFor(() => {
        const playButtons = screen.getAllByTestId("song-play-button");
        expect(playButtons.length).toBeGreaterThan(0);
      });
    });

    it("uses public R2 URL when available", async () => {
      mockGetPublicAudioUrl.mockReturnValue("https://pub-test.r2.dev/abc123/audio.mp3");

      renderSheet();

      await waitFor(() => {
        expect(screen.getAllByTestId("song-play-button").length).toBeGreaterThan(0);
      });

      const playButtons = screen.getAllByTestId("song-play-button");
      fireEvent.click(playButtons[0]);

      await waitFor(() => {
        expect(mockGetPublicAudioUrl).toHaveBeenCalledWith("abc123");
        expect(mockPlay).toHaveBeenCalledWith({
          id: "song-song-1",
          title: "Amazing Grace",
          artist: "John Newton",
          src: "https://pub-test.r2.dev/abc123/audio.mp3",
          type: "song",
          duration: 180,
        });
      });

      expect(mockFetch).not.toHaveBeenCalledWith(
        "/api/signed-url",
        expect.anything()
      );
    });

    it("falls back to /api/signed-url when public URL is not available", async () => {
      mockGetPublicAudioUrl.mockReturnValue(null);
      mockFetch.mockImplementation((url: string) => {
        if (url === "/api/signed-url") {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ url: "https://r2.example.com/audio.mp3" }),
          });
        }
        if (url === "/api/songs/albums") {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ albums: mockAlbums }),
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

      renderSheet();

      await waitFor(() => {
        expect(screen.getAllByTestId("song-play-button").length).toBeGreaterThan(0);
      });

      const playButtons = screen.getAllByTestId("song-play-button");
      fireEvent.click(playButtons[0]);

      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledWith(
          "/api/signed-url",
          expect.objectContaining({
            method: "POST",
            body: JSON.stringify({ hashPrefix: "abc123", fileType: "audio" }),
          })
        );
      });
    });

    it("calls play() with signed URL data when falling back", async () => {
      mockGetPublicAudioUrl.mockReturnValue(null);
      mockFetch.mockImplementation((url: string) => {
        if (url === "/api/signed-url") {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ url: "https://r2.example.com/audio.mp3" }),
          });
        }
        if (url === "/api/songs/albums") {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ albums: mockAlbums }),
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

      renderSheet();

      await waitFor(() => {
        expect(screen.getAllByTestId("song-play-button").length).toBeGreaterThan(0);
      });

      const playButtons = screen.getAllByTestId("song-play-button");
      fireEvent.click(playButtons[0]);

      await waitFor(() => {
        expect(mockPlay).toHaveBeenCalledWith({
          id: "song-song-1",
          title: "Amazing Grace",
          artist: "John Newton",
          src: "https://r2.example.com/audio.mp3",
          type: "song",
          duration: 180,
        });
      });
    });

    it("shows error toast when signed URL fetch fails", async () => {
      mockGetPublicAudioUrl.mockReturnValue(null);
      const { toast } = await import("sonner");

      mockFetch.mockImplementation((url: string) => {
        if (url === "/api/signed-url") {
          return Promise.resolve({ ok: false });
        }
        if (url === "/api/songs/albums") {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ albums: mockAlbums }),
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

      renderSheet();

      await waitFor(() => {
        expect(screen.getAllByTestId("song-play-button").length).toBeGreaterThan(0);
      });

      const playButtons = screen.getAllByTestId("song-play-button");
      fireEvent.click(playButtons[0]);

      await waitFor(() => {
        expect(toast.error).toHaveBeenCalledWith("Failed to load audio preview");
      });
    });

    it("shows error toast when song has no recordings", async () => {
      const { toast } = await import("sonner");

      const songsNoRecordings = [
        {
          id: "song-nr",
          title: "No Recording Song",
          composer: "Test",
          lyricist: null,
          albumName: null,
          musicalKey: null,
          recordings: [],
        },
      ];

      mockFetch.mockImplementation((url: string) => {
        if (url === "/api/songs/albums") {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ albums: mockAlbums }),
          });
        }
        if (url.includes("/api/songs")) {
          return Promise.resolve({
            ok: true,
            json: () => Promise.resolve({ songs: songsNoRecordings, total: 1 }),
          });
        }
        return Promise.resolve({ ok: false });
      });

      renderSheet();

      await waitFor(() => {
        expect(screen.getByText("No Recording Song")).toBeInTheDocument();
      });

      const playButtons = screen.getAllByTestId("song-play-button");
      fireEvent.click(playButtons[0]);

      await waitFor(() => {
        expect(toast.error).toHaveBeenCalledWith("No audio available for this song");
      });
    });
  });
});
