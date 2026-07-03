import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SharedFilters } from "@/components/songset/SharedFilters";

describe("SharedFilters", () => {
  const mockAlbums = ["Hymns", "Worship", "Christmas"];

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
        expect(screen.getByTestId("album-option-Hymns")).toBeInTheDocument();
      });

      fireEvent.click(screen.getByTestId("album-option-Hymns"));
      expect(defaultProps.onSelectedAlbumsChange).toHaveBeenCalledWith(["Hymns"]);
    });

    it("deselecting an album calls onSelectedAlbumsChange", async () => {
      renderFilters({ selectedAlbums: ["Hymns"] });

      fireEvent.click(screen.getByTestId("album-filter"));
      await waitFor(() => {
        expect(screen.getByTestId("album-option-Hymns")).toBeInTheDocument();
      });

      fireEvent.click(screen.getByTestId("album-option-Hymns"));
      expect(defaultProps.onSelectedAlbumsChange).toHaveBeenCalledWith([]);
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
      renderFilters({ selectedAlbums: ["Hymns"], selectedKeys: ["D"] });
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
    it("shows correct count badge when filters are active", () => {
      renderFilters({
        selectedAlbums: ["Hymns", "Worship"],
        selectedKeys: ["D"],
        selectedBpm: "slow",
      });
      const toggle = screen.getByTestId("advanced-filters-toggle");
      expect(toggle.textContent).toContain("4");
    });

    it("does not show count badge when no filters active", () => {
      renderFilters();
      const toggle = screen.getByTestId("advanced-filters-toggle");
      expect(toggle.textContent).not.toMatch(/\d/);
    });
  });
});
