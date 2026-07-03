import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SharedFilters } from "@/components/songset/SharedFilters";
import { albumFilterKey } from "@/lib/search/album-filter";

describe("SharedFilters", () => {
  const mockAlbums = [
    { albumName: "Hymns", albumSeries: "Classic", songCount: 12 },
    { albumName: "Worship", albumSeries: null, songCount: 8 },
    { albumName: "Christmas", albumSeries: "Seasonal", songCount: 4 },
  ];
  const hymns = { albumName: "Hymns", albumSeries: "Classic" };
  const worship = { albumName: "Worship", albumSeries: null };
  const hymnsOptionTestId = `album-option-${encodeURIComponent(albumFilterKey(hymns))}`;

  const defaultProps = {
    albums: mockAlbums,
    selectedAlbums: [],
    onSelectedAlbumsChange: vi.fn(),
    selectedKeys: [],
    onSelectedKeysChange: vi.fn(),
    selectedBpm: undefined,
    onSelectedBpmChange: vi.fn(),
    onClearFilters: vi.fn(),
    isLoading: false,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  const renderFilters = (props = {}) => {
    return render(<SharedFilters {...defaultProps} {...props} />);
  };

  describe("rendering", () => {
    it("renders album multi-select when albums are provided", () => {
      renderFilters();
      expect(screen.getByTestId("album-filter")).toBeInTheDocument();
      expect(screen.getByTestId("album-filter")).toHaveTextContent("All 3 Albums");
    });

    it("does not render album multi-select when albums array is empty", () => {
      renderFilters({ albums: [] });
      expect(screen.queryByTestId("album-filter")).not.toBeInTheDocument();
    });

    it("renders advanced filters toggle", () => {
      renderFilters();
      expect(screen.getByTestId("advanced-filters-toggle")).toBeInTheDocument();
    });

    it("does not show advanced panel by default", () => {
      renderFilters();
      expect(screen.queryByTestId("advanced-filters-panel")).not.toBeInTheDocument();
    });

    it("shows advanced panel when toggle is clicked", () => {
      renderFilters();
      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));
      expect(screen.getByTestId("advanced-filters-panel")).toBeInTheDocument();
    });

    it("hides advanced panel when toggle is clicked again", () => {
      renderFilters();
      const toggle = screen.getByTestId("advanced-filters-toggle");
      fireEvent.click(toggle);
      fireEvent.click(toggle);
      expect(screen.queryByTestId("advanced-filters-panel")).not.toBeInTheDocument();
    });
  });

  describe("album selection", () => {
    it("selecting an album calls onSelectedAlbumsChange", async () => {
      renderFilters();

      fireEvent.click(screen.getByTestId("album-filter"));
      await waitFor(() => {
        expect(screen.getByTestId(hymnsOptionTestId)).toBeInTheDocument();
      });

      expect(screen.getByText("Hymns - Classic [12]")).toBeInTheDocument();
      fireEvent.click(screen.getByTestId(hymnsOptionTestId));
      expect(defaultProps.onSelectedAlbumsChange).toHaveBeenCalledWith([hymns]);
    });

    it("deselecting an album calls onSelectedAlbumsChange", async () => {
      renderFilters({ selectedAlbums: [hymns] });

      fireEvent.click(screen.getByTestId("album-filter"));
      await waitFor(() => {
        expect(screen.getByTestId(hymnsOptionTestId)).toBeInTheDocument();
      });

      fireEvent.click(screen.getByTestId(hymnsOptionTestId));
      expect(defaultProps.onSelectedAlbumsChange).toHaveBeenCalledWith([]);
    });

    it("shows only album names in the selected summary", () => {
      renderFilters({ selectedAlbums: [hymns] });

      const summary = screen.getByTestId("album-selected-summary");
      expect(summary).toHaveTextContent("Hymns");
      expect(summary).not.toHaveTextContent("Classic");
    });
  });

  describe("key chips", () => {
    it("selecting a key calls onSelectedKeysChange", () => {
      renderFilters();
      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));

      fireEvent.click(screen.getByTestId("key-chip-D"));
      expect(defaultProps.onSelectedKeysChange).toHaveBeenCalledWith(["D"]);
    });

    it("deselecting a key calls onSelectedKeysChange", () => {
      renderFilters({ selectedKeys: ["D"] });
      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));

      fireEvent.click(screen.getByTestId("key-chip-D"));
      expect(defaultProps.onSelectedKeysChange).toHaveBeenCalledWith([]);
    });
  });

  describe("bpm chips", () => {
    it("selecting a bpm calls onSelectedBpmChange", () => {
      renderFilters();
      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));

      fireEvent.click(screen.getByTestId("bpm-chip-slow"));
      expect(defaultProps.onSelectedBpmChange).toHaveBeenCalledWith("slow");
    });

    it("deselecting a bpm calls onSelectedBpmChange with undefined", () => {
      renderFilters({ selectedBpm: "slow" });
      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));

      fireEvent.click(screen.getByTestId("bpm-chip-slow"));
      expect(defaultProps.onSelectedBpmChange).toHaveBeenCalledWith(undefined);
    });
  });

  describe("actions", () => {
    it("does not render an Apply filters button", () => {
      renderFilters();
      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));
      expect(screen.queryByRole("button", { name: /apply filters/i })).not.toBeInTheDocument();
    });

    it("Clear all button calls onClearFilters", () => {
      renderFilters({ selectedAlbums: [hymns], selectedKeys: ["D"] });
      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));
      fireEvent.click(screen.getByTestId("advanced-clear-button"));
      expect(defaultProps.onClearFilters).toHaveBeenCalled();
    });

    it("Clear all button is disabled when no filters active", () => {
      renderFilters();
      fireEvent.click(screen.getByTestId("advanced-filters-toggle"));
      expect(screen.getByTestId("advanced-clear-button")).toBeDisabled();
    });
  });

  describe("active filter count badge", () => {
    it("shows correct count badge for advanced filters only", () => {
      renderFilters({
        selectedAlbums: [hymns, worship],
        selectedKeys: ["D"],
        selectedBpm: "slow",
      });
      const toggle = screen.getByTestId("advanced-filters-toggle");
      expect(toggle.textContent).toContain("2");
    });

    it("does not show count badge when no filters active", () => {
      renderFilters();
      const toggle = screen.getByTestId("advanced-filters-toggle");
      expect(toggle.textContent).not.toMatch(/\d/);
    });
  });
});
