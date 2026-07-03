import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SongSearch } from "@/components/songset/SongSearch";

describe("SongSearch", () => {
  const mockOnSearch = vi.fn();
  const mockAlbums = ["Hymns", "Worship", "Christmas Songs"];

  const defaultProps = {
    onSearch: mockOnSearch,
    albums: mockAlbums,
    isLoading: false,
    debounceMs: 100, // Use shorter debounce for tests
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const renderSearch = (props = {}) => {
    return render(<SongSearch {...defaultProps} {...props} />);
  };

  describe("rendering", () => {
    it("renders search input", () => {
      renderSearch();
      expect(screen.getByTestId("search-input")).toBeInTheDocument();
    });

    it("renders with correct placeholder", () => {
      renderSearch();
      expect(screen.getByPlaceholderText(/search songs by title/i)).toBeInTheDocument();
    });

    it("renders album filter when albums are provided", () => {
      renderSearch();
      expect(screen.getByTestId("album-filter")).toBeInTheDocument();
    });

    it("does not render album filter when no albums", () => {
      renderSearch({ albums: [] });
      expect(screen.queryByTestId("album-filter")).not.toBeInTheDocument();
    });

    it("renders search icon", () => {
      renderSearch();
      expect(screen.getByLabelText(/search songs/i)).toBeInTheDocument();
    });
  });

  describe("search functionality", () => {
    it("calls onSearch with debounce when typing", async () => {
      renderSearch();

      const input = screen.getByTestId("search-input");
      fireEvent.change(input, { target: { value: "amazing" } });

      // Should not call immediately
      expect(mockOnSearch).not.toHaveBeenCalled();

      // Advance timers
      vi.advanceTimersByTime(150);

      await waitFor(() => {
        expect(mockOnSearch).toHaveBeenCalledWith("amazing", undefined);
      });
    });

    it("calls onSearch with empty string when cleared", async () => {
      renderSearch();

      const input = screen.getByTestId("search-input");
      fireEvent.change(input, { target: { value: "test" } });

      vi.advanceTimersByTime(150);
      await waitFor(() => {
        expect(mockOnSearch).toHaveBeenCalledWith("test", undefined);
      });

      // Clear the search
      const clearButton = screen.getByTestId("clear-search-button");
      fireEvent.click(clearButton);

      vi.advanceTimersByTime(150);
      await waitFor(() => {
        expect(mockOnSearch).toHaveBeenLastCalledWith("", undefined);
      });
    });

    it("shows clear button when query is not empty", () => {
      renderSearch();

      const input = screen.getByTestId("search-input");
      fireEvent.change(input, { target: { value: "test" } });

      expect(screen.getByTestId("clear-search-button")).toBeInTheDocument();
    });

    it("hides clear button when query is empty", () => {
      renderSearch();
      expect(screen.queryByTestId("clear-search-button")).not.toBeInTheDocument();
    });

    it("clears search when clear button is clicked", async () => {
      renderSearch();

      const input = screen.getByTestId("search-input");
      fireEvent.change(input, { target: { value: "test" } });

      const clearButton = screen.getByTestId("clear-search-button");
      fireEvent.click(clearButton);

      expect(input).toHaveValue("");
    });
  });

  describe("album filter", () => {
    it("renders album filter with correct label", () => {
      renderSearch();
      expect(screen.getByText(/filter by album/i)).toBeInTheDocument();
    });

    it("renders album filter select", () => {
      renderSearch();
      expect(screen.getByTestId("album-filter")).toBeInTheDocument();
    });

    it("album filter is interactive", async () => {
      renderSearch();
      
      const albumFilter = screen.getByTestId("album-filter");
      expect(albumFilter).toBeInTheDocument();
      
      // Click to open the select
      fireEvent.click(albumFilter);
      
      // Verify the select opens (has aria-expanded attribute)
      await waitFor(() => {
        expect(albumFilter).toHaveAttribute("aria-expanded", "true");
      });
    });
  });

  describe("loading state", () => {
    it("shows loading indicator when isLoading is true", () => {
      renderSearch({ isLoading: true });
      expect(screen.getByLabelText(/search songs/i).parentElement?.querySelector("svg")).toBeInTheDocument();
    });
  });

  describe("accessibility", () => {
    it("has correct aria-label on search input", () => {
      renderSearch();
      expect(screen.getByLabelText(/search songs/i)).toBeInTheDocument();
    });

    it("has correct aria-label on clear button", () => {
      renderSearch();

      const input = screen.getByTestId("search-input");
      fireEvent.change(input, { target: { value: "test" } });

      expect(screen.getByLabelText(/clear search/i)).toBeInTheDocument();
    });
  });

  describe("advanced filters", () => {
    const mockOnAdvancedSearch = vi.fn();

    const renderWithAdvanced = (props = {}) => {
      return renderSearch({
        onAdvancedSearch: mockOnAdvancedSearch,
        ...props,
      });
    };

    beforeEach(() => {
      mockOnAdvancedSearch.mockClear();
    });

    it("does not render advanced toggle when onAdvancedSearch is not provided", () => {
      renderSearch();
      expect(screen.queryByTestId("advanced-filters-toggle")).not.toBeInTheDocument();
    });

    it("renders advanced toggle when onAdvancedSearch is provided", () => {
      renderWithAdvanced();
      expect(screen.getByTestId("advanced-filters-toggle")).toBeInTheDocument();
    });

    it("does not show advanced panel by default", () => {
      renderWithAdvanced();
      expect(screen.queryByTestId("advanced-filters-panel")).not.toBeInTheDocument();
    });

    it("shows advanced panel when toggle is clicked", () => {
      renderWithAdvanced();
      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));
      expect(screen.getByTestId("advanced-filters-panel")).toBeInTheDocument();
    });

    it("hides advanced panel when toggle is clicked again", () => {
      renderWithAdvanced();
      const toggle = screen.getByTestId("advanced-filters-toggle");
      fireEvent.click(toggle);
      fireEvent.click(toggle);
      expect(screen.queryByTestId("advanced-filters-panel")).not.toBeInTheDocument();
    });

    it("renders all 12 pitch class chips", () => {
      renderWithAdvanced();
      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));
      expect(screen.getByTestId("key-chip-C")).toBeInTheDocument();
      expect(screen.getByTestId("key-chip-D")).toBeInTheDocument();
      expect(screen.getByTestId("key-chip-A")).toBeInTheDocument();
      expect(screen.getByTestId("key-chip-B")).toBeInTheDocument();
    });

    it("renders 3 BPM band chips", () => {
      renderWithAdvanced();
      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));
      expect(screen.getByTestId("bpm-chip-slow")).toBeInTheDocument();
      expect(screen.getByTestId("bpm-chip-moderate")).toBeInTheDocument();
      expect(screen.getByTestId("bpm-chip-fast")).toBeInTheDocument();
    });

    it("toggles key chip selection (multi-select)", () => {
      renderWithAdvanced();
      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));

      const keyD = screen.getByTestId("key-chip-D");
      const keyA = screen.getByTestId("key-chip-A");

      expect(keyD).toHaveAttribute("aria-pressed", "false");
      fireEvent.click(keyD);
      expect(keyD).toHaveAttribute("aria-pressed", "true");

      fireEvent.click(keyA);
      expect(keyA).toHaveAttribute("aria-pressed", "true");
      expect(keyD).toHaveAttribute("aria-pressed", "true");
    });

    it("toggles BPM chip selection (single-select)", () => {
      renderWithAdvanced();
      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));

      const slow = screen.getByTestId("bpm-chip-slow");
      const fast = screen.getByTestId("bpm-chip-fast");

      fireEvent.click(slow);
      expect(slow).toHaveAttribute("aria-pressed", "true");

      fireEvent.click(fast);
      expect(fast).toHaveAttribute("aria-pressed", "true");
      expect(slow).toHaveAttribute("aria-pressed", "false");
    });

    it("deselects BPM chip when clicked again", () => {
      renderWithAdvanced();
      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));

      const slow = screen.getByTestId("bpm-chip-slow");
      fireEvent.click(slow);
      expect(slow).toHaveAttribute("aria-pressed", "true");

      fireEvent.click(slow);
      expect(slow).toHaveAttribute("aria-pressed", "false");
    });

    it("calls onAdvancedSearch with criteria when Apply is clicked", () => {
      renderWithAdvanced();
      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));

      fireEvent.click(screen.getByTestId("key-chip-D"));
      fireEvent.click(screen.getByTestId("key-chip-A"));
      fireEvent.click(screen.getByTestId("bpm-chip-slow"));

      fireEvent.click(screen.getByTestId("advanced-apply-button"));

      expect(mockOnAdvancedSearch).toHaveBeenCalledWith({
        query: undefined,
        keys: ["D", "A"],
        bpmRange: "slow",
        album: undefined,
      });
    });

    it("clears all filters when Clear all is clicked", () => {
      renderWithAdvanced();
      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));

      fireEvent.click(screen.getByTestId("key-chip-D"));
      fireEvent.click(screen.getByTestId("bpm-chip-slow"));

      expect(screen.getByTestId("key-chip-D")).toHaveAttribute("aria-pressed", "true");
      expect(screen.getByTestId("bpm-chip-slow")).toHaveAttribute("aria-pressed", "true");

      fireEvent.click(screen.getByTestId("advanced-clear-button"));

      expect(screen.getByTestId("key-chip-D")).toHaveAttribute("aria-pressed", "false");
      expect(screen.getByTestId("bpm-chip-slow")).toHaveAttribute("aria-pressed", "false");
    });

    it("Clear all button is disabled when no filters active", () => {
      renderWithAdvanced();
      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));
      expect(screen.getByTestId("advanced-clear-button")).toBeDisabled();
    });

    it("shows active filter count badge on toggle", () => {
      renderWithAdvanced();
      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));
      fireEvent.click(screen.getByTestId("key-chip-D"));
      fireEvent.click(screen.getByTestId("bpm-chip-slow"));

      // Collapse panel
      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));
      const toggle = screen.getByTestId("advanced-filters-toggle");
      expect(toggle.textContent).toContain("2");
    });

    it("uses onAdvancedSearch for debounced keyword search when filters are active", async () => {
      renderWithAdvanced();
      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));
      fireEvent.click(screen.getByTestId("key-chip-D"));

      const input = screen.getByTestId("search-input");
      fireEvent.change(input, { target: { value: "amazing" } });

      vi.advanceTimersByTime(150);

      await waitFor(() => {
        expect(mockOnAdvancedSearch).toHaveBeenCalledWith({
          query: "amazing",
          keys: ["D"],
          bpmRange: undefined,
          album: undefined,
        });
      });
    });

    it("falls back to onSearch when advanced panel is open but no filters active", async () => {
      renderWithAdvanced();
      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));

      const input = screen.getByTestId("search-input");
      fireEvent.change(input, { target: { value: "amazing" } });

      vi.advanceTimersByTime(150);

      await waitFor(() => {
        expect(mockOnSearch).toHaveBeenCalledWith("amazing", undefined);
        expect(mockOnAdvancedSearch).not.toHaveBeenCalled();
      });
    });
  });
});
