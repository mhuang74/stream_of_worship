import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { BrowseSheet } from "@/components/songset/BrowseSheet";

// Mock fetch globally
const mockFetch = vi.fn();
global.fetch = mockFetch;

describe("BrowseSheet", () => {
  const mockOnOpenChange = vi.fn();
  const mockOnAddSongs = vi.fn().mockResolvedValue(undefined);

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
          durationSeconds: 180,
          tempoBpm: 120,
          musicalKey: "G",
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
          durationSeconds: 240,
          tempoBpm: 100,
          musicalKey: "A",
        },
      ],
    },
  ];

  const mockAlbums = ["Hymns", "Worship", "Christmas"];

  const defaultProps = {
    isOpen: true,
    onOpenChange: mockOnOpenChange,
    onAddSongs: mockOnAddSongs,
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
    it("calls onAddSongs when add button is clicked", async () => {
      renderSheet();
      
      await waitFor(() => {
        expect(screen.getAllByTestId("add-song-button").length).toBeGreaterThan(0);
      });

      const addButtons = screen.getAllByTestId("add-song-button");
      fireEvent.click(addButtons[0]);

      await waitFor(() => {
        expect(mockOnAddSongs).toHaveBeenCalledWith(["song-1"]);
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
});
