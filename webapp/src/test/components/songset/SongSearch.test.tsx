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
});
