import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SemanticSearch } from "@/components/search/SemanticSearch";

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
    similarity: 0.92,
    recordings: [
      {
        contentHash: "abc123",
        durationSeconds: 180,
        tempoBpm: 72,
        musicalKey: "G",
      },
    ],
  },
  {
    id: "song-2",
    title: "Great Is Thy Faithfulness",
    composer: null,
    lyricist: null,
    albumName: "Hymns",
    musicalKey: "D",
    similarity: 0.78,
    recordings: [
      {
        contentHash: "def456",
        durationSeconds: 210,
        tempoBpm: 80,
        musicalKey: "D",
      },
    ],
  },
];

const defaultProps = {
  onAddSong: vi.fn().mockResolvedValue(undefined),
  existingSongIds: [],
};

describe("SemanticSearch", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const renderComponent = (props = {}) =>
    render(<SemanticSearch {...defaultProps} {...props} />);

  describe("rendering", () => {
    it("renders the textarea", () => {
      renderComponent();
      expect(screen.getByTestId("semantic-search-input")).toBeInTheDocument();
    });

    it("renders the search button", () => {
      renderComponent();
      expect(screen.getByTestId("semantic-search-button")).toBeInTheDocument();
    });

    it("search button is disabled when query is empty", () => {
      renderComponent();
      expect(screen.getByTestId("semantic-search-button")).toBeDisabled();
    });

    it("search button is enabled when query has text", () => {
      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "songs about grace" } });
      expect(screen.getByTestId("semantic-search-button")).not.toBeDisabled();
    });

    it("does not show results before search", () => {
      renderComponent();
      expect(screen.queryByTestId("semantic-search-results")).not.toBeInTheDocument();
    });
  });

  describe("search functionality", () => {
    it("calls the semantic search API on button click", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: mockSongs, query: "grace", total: 2 }),
      });

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "songs about grace" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledWith(
          "/api/songs/search/semantic",
          expect.objectContaining({
            method: "POST",
            body: JSON.stringify({ query: "songs about grace", limit: 20 }),
          })
        );
      });
    });

    it("displays results after successful search", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: mockSongs, query: "grace", total: 2 }),
      });

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "grace" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(screen.getByText("Amazing Grace")).toBeInTheDocument();
        expect(screen.getByText("Great Is Thy Faithfulness")).toBeInTheDocument();
      });
    });

    it("displays similarity badges on results", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: mockSongs, query: "grace", total: 2 }),
      });

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "grace" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        const badges = screen.getAllByTestId("similarity-badge");
        expect(badges.length).toBeGreaterThan(0);
        expect(badges[0].textContent).toContain("92% match");
      });
    });

    it("triggers search on Ctrl+Enter", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: [], query: "test", total: 0 }),
      });

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "worship songs" } });
      fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });

      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalled();
      });
    });

    it("does not trigger search on Enter without Ctrl", async () => {
      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "worship songs" } });
      fireEvent.keyDown(input, { key: "Enter" });

      expect(mockFetch).not.toHaveBeenCalled();
    });
  });

  describe("empty and loading states", () => {
    it("shows loading state while searching", async () => {
      let resolveSearch: ((v: unknown) => void) | null = null;
      mockFetch.mockReturnValue(
        new Promise((resolve) => {
          resolveSearch = resolve;
        })
      );

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "grace" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(screen.getByText("Searching by meaning...")).toBeInTheDocument();
      });

      resolveSearch!({
        ok: true,
        json: () => Promise.resolve({ songs: [], query: "grace", total: 0 }),
      });
    });

    it("shows empty state when no results found", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: [], query: "obscure", total: 0 }),
      });

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "obscure query" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(screen.getByText("No matching songs found")).toBeInTheDocument();
      });
    });
  });

  describe("error handling", () => {
    it("shows error when API returns error status", async () => {
      mockFetch.mockResolvedValue({
        ok: false,
        json: () => Promise.resolve({ error: "Semantic search failed" }),
      });

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "songs" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(screen.getByTestId("semantic-search-error")).toBeInTheDocument();
        expect(screen.getByText("Semantic search failed")).toBeInTheDocument();
      });
    });

    it("shows error when fetch throws", async () => {
      mockFetch.mockRejectedValue(new Error("Network error"));

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "songs" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(screen.getByTestId("semantic-search-error")).toBeInTheDocument();
      });
    });
  });

  describe("add song functionality", () => {
    it("calls onAddSong when add button is clicked", async () => {
      const onAddSong = vi.fn().mockResolvedValue(undefined);
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: mockSongs, query: "grace", total: 2 }),
      });

      renderComponent({ onAddSong });

      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "grace" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(screen.getAllByTestId("add-song-button").length).toBeGreaterThan(0);
      });

      const addButtons = screen.getAllByTestId("add-song-button");
      fireEvent.click(addButtons[0]);

      await waitFor(() => {
        expect(onAddSong).toHaveBeenCalledWith("song-1");
      });
    });

    it("marks already-added songs as added", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: mockSongs, query: "grace", total: 2 }),
      });

      renderComponent({ existingSongIds: ["song-1"] });

      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "grace" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        const addButtons = screen.getAllByTestId("add-song-button");
        expect(addButtons[0]).toBeDisabled();
      });
    });
  });
});
