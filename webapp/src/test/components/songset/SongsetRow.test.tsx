import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SongsetRow } from "@/components/songset/SongsetRow";
import { RenderState } from "@/components/songset/RenderStateButton";

describe("SongsetRow", () => {
  const defaultProps = {
    id: "songset-1",
    name: "Test Songset",
    description: "Test description",
    itemCount: 3,
    durationSeconds: 180,
    updatedAt: new Date("2024-01-15T10:30:00Z"),
    renderState: "fresh" as RenderState,
    onRender: vi.fn(),
    onPlay: vi.fn(),
    onRetry: vi.fn(),
    onRename: vi.fn(),
    onDuplicate: vi.fn(),
    onShare: vi.fn(),
    onDelete: vi.fn(),
  };

  const renderRow = (props = {}) => {
    return render(<SongsetRow {...defaultProps} {...props} />);
  };

  describe("metadata display", () => {
    it("renders songset name", () => {
      renderRow();
      expect(screen.getByText("Test Songset")).toBeInTheDocument();
    });

    it("renders songset description", () => {
      renderRow();
      expect(screen.getByText("Test description")).toBeInTheDocument();
    });

    it("renders song count", () => {
      renderRow();
      expect(screen.getByText(/3 songs/i)).toBeInTheDocument();
    });

    it("renders singular 'song' when count is 1", () => {
      renderRow({ itemCount: 1 });
      expect(screen.getByText(/1 song(?!s)/i)).toBeInTheDocument();
    });

    it("renders duration in MM:SS format", () => {
      renderRow();
      expect(screen.getByText(/3:00/i)).toBeInTheDocument();
    });

    it("renders updated date", () => {
      renderRow();
      expect(screen.getByText(/updated/i)).toBeInTheDocument();
    });

    it("handles missing duration gracefully", () => {
      renderRow({ durationSeconds: undefined });
      expect(screen.getByText(/--:--/i)).toBeInTheDocument();
    });
  });

  describe("render state button", () => {
    it("renders render state button", () => {
      renderRow();
      expect(screen.getByRole("button", { name: /play/i })).toBeInTheDocument();
    });

    it("calls onPlay when play button clicked", () => {
      renderRow();
      fireEvent.click(screen.getByRole("button", { name: /play/i }));
      expect(defaultProps.onPlay).toHaveBeenCalled();
    });
  });

  describe("stale state with 'Play anyway'", () => {
    it("renders 'Play anyway' button when stale", () => {
      renderRow({ renderState: "stale" as RenderState });
      expect(screen.getByRole("button", { name: /play anyway/i })).toBeInTheDocument();
    });

    it("calls onPlay when 'Play anyway' clicked", () => {
      renderRow({ renderState: "stale" as RenderState });
      fireEvent.click(screen.getByRole("button", { name: /play anyway/i }));
      expect(defaultProps.onPlay).toHaveBeenCalled();
    });

    it("does not render 'Play anyway' when not stale", () => {
      renderRow({ renderState: "fresh" as RenderState });
      expect(screen.queryByRole("button", { name: /play anyway/i })).not.toBeInTheDocument();
    });
  });

  describe("offline badge", () => {
    it("renders offline badge when offline available", () => {
      renderRow({ isOfflineAvailable: true });
      expect(screen.getByText(/offline/i)).toBeInTheDocument();
    });

    it("does not render offline badge when not available", () => {
      renderRow({ isOfflineAvailable: false });
      expect(screen.queryByText(/offline/i)).not.toBeInTheDocument();
    });
  });

  describe("stale artifacts indicator", () => {
    it("renders stale indicator when artifacts are stale", () => {
      renderRow({ isArtifactsStale: true });
      expect(screen.getByText(/artifacts out of date/i)).toBeInTheDocument();
    });

    it("does not render stale indicator when artifacts are fresh", () => {
      renderRow({ isArtifactsStale: false });
      expect(screen.queryByText(/artifacts out of date/i)).not.toBeInTheDocument();
    });
  });

  describe("context menu", () => {
    it("opens context menu when menu button clicked", async () => {
      renderRow();
      const menuButton = screen.getByRole("button", { name: /open menu/i });
      fireEvent.click(menuButton);
      
      await waitFor(() => {
        expect(screen.getByRole("menuitem", { name: /rename/i })).toBeInTheDocument();
      });
    });

    it("has all menu items", async () => {
      renderRow();
      const menuButton = screen.getByRole("button", { name: /open menu/i });
      fireEvent.click(menuButton);
      
      await waitFor(() => {
        expect(screen.getByRole("menuitem", { name: /rename/i })).toBeInTheDocument();
        expect(screen.getByRole("menuitem", { name: /duplicate/i })).toBeInTheDocument();
        expect(screen.getByRole("menuitem", { name: /render/i })).toBeInTheDocument();
        expect(screen.getByRole("menuitem", { name: /play/i })).toBeInTheDocument();
        expect(screen.getByRole("menuitem", { name: /share/i })).toBeInTheDocument();
        expect(screen.getByRole("menuitem", { name: /delete/i })).toBeInTheDocument();
      });
    });

    it("calls onRename when rename menu item clicked", async () => {
      renderRow();
      const menuButton = screen.getByRole("button", { name: /open menu/i });
      fireEvent.click(menuButton);
      
      await waitFor(() => {
        const renameItem = screen.getByRole("menuitem", { name: /rename/i });
        fireEvent.click(renameItem);
      });
      
      expect(defaultProps.onRename).toHaveBeenCalled();
    });

    it("calls onDuplicate when duplicate menu item clicked", async () => {
      renderRow();
      const menuButton = screen.getByRole("button", { name: /open menu/i });
      fireEvent.click(menuButton);
      
      await waitFor(() => {
        const duplicateItem = screen.getByRole("menuitem", { name: /duplicate/i });
        fireEvent.click(duplicateItem);
      });
      
      expect(defaultProps.onDuplicate).toHaveBeenCalled();
    });

    it("calls onDelete when delete menu item clicked", async () => {
      renderRow();
      const menuButton = screen.getByRole("button", { name: /open menu/i });
      fireEvent.click(menuButton);
      
      await waitFor(() => {
        const deleteItem = screen.getByRole("menuitem", { name: /delete/i });
        fireEvent.click(deleteItem);
      });
      
      expect(defaultProps.onDelete).toHaveBeenCalled();
    });
  });

  describe("data attributes", () => {
    it("has data-songset-id attribute", () => {
      const { container } = renderRow();
      expect(container.querySelector('[data-songset-id="songset-1"]')).toBeInTheDocument();
    });
  });
});
