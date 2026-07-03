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
  const hymnsOptionTestId = `album-option-${encodeURIComponent(albumFilterKey(hymns))}`;

  const defaultProps = {
    albums: mockAlbums,
    selectedAlbums: [],
    onSelectedAlbumsChange: vi.fn(),
    selectedKeys: [],
    onSelectedKeysChange: vi.fn(),
    selectedBpm: [],
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

    it("does not render advanced filters toggle or panel", () => {
      renderFilters();
      expect(screen.queryByTestId("advanced-filters-toggle")).not.toBeInTheDocument();
      expect(screen.queryByTestId("advanced-filters-panel")).not.toBeInTheDocument();
    });

    it("renders key and bpm filters at top level", () => {
      renderFilters();
      expect(screen.getByTestId("key-filter")).toBeInTheDocument();
      expect(screen.getByTestId("bpm-filter")).toBeInTheDocument();
    });

    it("shows All Musical Keys and All BPM Ranges when empty", () => {
      renderFilters();
      expect(screen.getByTestId("key-filter")).toHaveTextContent("All Musical Keys");
      expect(screen.getByTestId("bpm-filter")).toHaveTextContent("All BPM Ranges");
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

  describe("musical key multi-select", () => {
    it("selecting a key calls onSelectedKeysChange", async () => {
      renderFilters();

      fireEvent.click(screen.getByTestId("key-filter"));
      await waitFor(() => {
        expect(screen.getByTestId("key-option-D")).toBeInTheDocument();
      });

      fireEvent.click(screen.getByTestId("key-option-D"));
      expect(defaultProps.onSelectedKeysChange).toHaveBeenCalledWith(["D"]);
    });

    it("deselecting a key calls onSelectedKeysChange", async () => {
      renderFilters({ selectedKeys: ["D"] });

      fireEvent.click(screen.getByTestId("key-filter"));
      await waitFor(() => {
        expect(screen.getByTestId("key-option-D")).toBeInTheDocument();
      });

      fireEvent.click(screen.getByTestId("key-option-D"));
      expect(defaultProps.onSelectedKeysChange).toHaveBeenCalledWith([]);
    });

    it("shows key name when one selected", () => {
      renderFilters({ selectedKeys: ["C"] });
      expect(screen.getByTestId("key-filter")).toHaveTextContent("C");
    });

    it("shows two key names when two selected", () => {
      renderFilters({ selectedKeys: ["C", "D"] });
      expect(screen.getByTestId("key-filter")).toHaveTextContent("C, D");
    });

    it("shows first two keys plus overflow when 3+ selected", () => {
      renderFilters({ selectedKeys: ["C", "D", "E", "F"] });
      expect(screen.getByTestId("key-filter")).toHaveTextContent("C, D, +2");
    });

    it("shows Clear all item only when keys are selected", async () => {
      renderFilters();
      fireEvent.click(screen.getByTestId("key-filter"));
      await waitFor(() => {
        expect(screen.queryByTestId("key-clear-all")).not.toBeInTheDocument();
      });
    });

    it("Clear all item appears when keys are selected", async () => {
      renderFilters({ selectedKeys: ["C"] });
      fireEvent.click(screen.getByTestId("key-filter"));
      await waitFor(() => {
        expect(screen.getByTestId("key-clear-all")).toBeInTheDocument();
      });
    });
  });

  describe("bpm range multi-select", () => {
    it("selecting a bpm calls onSelectedBpmChange", async () => {
      renderFilters();

      fireEvent.click(screen.getByTestId("bpm-filter"));
      await waitFor(() => {
        expect(screen.getByTestId("bpm-option-slow")).toBeInTheDocument();
      });

      fireEvent.click(screen.getByTestId("bpm-option-slow"));
      expect(defaultProps.onSelectedBpmChange).toHaveBeenCalledWith(["slow"]);
    });

    it("deselecting a bpm calls onSelectedBpmChange removing the band", async () => {
      renderFilters({ selectedBpm: ["slow"] });

      fireEvent.click(screen.getByTestId("bpm-filter"));
      await waitFor(() => {
        expect(screen.getByTestId("bpm-option-slow")).toBeInTheDocument();
      });

      fireEvent.click(screen.getByTestId("bpm-option-slow"));
      expect(defaultProps.onSelectedBpmChange).toHaveBeenCalledWith([]);
    });

    it("shows band label only (no range text) when one selected", () => {
      renderFilters({ selectedBpm: ["slow"] });
      const trigger = screen.getByTestId("bpm-filter");
      expect(trigger).toHaveTextContent("Slow");
      expect(trigger).not.toHaveTextContent("< 90");
    });

    it("shows comma-joined labels when multiple selected", () => {
      renderFilters({ selectedBpm: ["slow", "fast"] });
      expect(screen.getByTestId("bpm-filter")).toHaveTextContent("Slow, Fast");
    });

    it("selecting all three shows all three labels", () => {
      renderFilters({ selectedBpm: ["slow", "moderate", "fast"] });
      expect(screen.getByTestId("bpm-filter")).toHaveTextContent("Slow, Moderate, Fast");
    });

    it("shows Clear all item when bpm bands are selected", async () => {
      renderFilters({ selectedBpm: ["slow"] });
      fireEvent.click(screen.getByTestId("bpm-filter"));
      await waitFor(() => {
        expect(screen.getByTestId("bpm-clear-all")).toBeInTheDocument();
      });
    });
  });

  describe("page-level clear all", () => {
    it("does not render Clear all button when no filters active", () => {
      renderFilters();
      expect(screen.queryByTestId("clear-all-filters")).not.toBeInTheDocument();
    });

    it("renders Clear all button when filters are active", () => {
      renderFilters({ selectedKeys: ["D"] });
      expect(screen.getByTestId("clear-all-filters")).toBeInTheDocument();
    });

    it("Clear all button calls onClearFilters", () => {
      renderFilters({ selectedAlbums: [hymns], selectedKeys: ["D"] });
      fireEvent.click(screen.getByTestId("clear-all-filters"));
      expect(defaultProps.onClearFilters).toHaveBeenCalled();
    });
  });
});
