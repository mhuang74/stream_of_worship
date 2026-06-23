import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SongsetRow } from "@/components/songset/SongsetRow";
import { RenderState } from "@/components/songset/RenderStatusBadge";

describe("SongsetRow", () => {
  const defaultProps = {
    id: "songset-1",
    name: "Test Songset",
    description: "Test description",
    itemCount: 3,
    durationSeconds: 180,
    updatedAt: new Date("2024-01-15T10:30:00Z"),
    renderState: "fresh" as RenderState,
    lastCompletedRenderJobId: "render-job-1",
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

  describe("render status badge", () => {
    it("renders render status badge", () => {
      renderRow();
      expect(screen.getByText("Rendered")).toBeInTheDocument();
    });

    it("passes failure fields to the badge", () => {
      renderRow({
        renderState: "failed" as RenderState,
        renderErrorMessage: "FFmpeg crashed",
        failedAt: new Date("2024-06-15T10:30:00Z"),
      });
      expect(screen.getByText("Render failed")).toBeInTheDocument();
      const trigger = screen.getByText("Render failed").closest("button")!;
      fireEvent.focus(trigger);
      expect(screen.getByText("FFmpeg crashed")).toBeInTheDocument();
    });

    it("tooltip interaction does not trigger row navigation", () => {
      const onPlay = vi.fn();
      renderRow({
        renderState: "failed" as RenderState,
        renderErrorMessage: "FFmpeg crashed",
        failedAt: new Date("2024-06-15T10:30:00Z"),
        onPlay,
      });
      const trigger = screen.getByText("Render failed").closest("button")!;
      fireEvent.focus(trigger);
      fireEvent.click(trigger);
      expect(onPlay).not.toHaveBeenCalled();
    });
  });

  describe("stale state badge", () => {
    it("renders 'Needs re-render' badge when stale", () => {
      renderRow({ renderState: "stale" as RenderState });
      expect(screen.getByText("Needs re-render")).toBeInTheDocument();
    });

    it("renders 'Rendered' badge when fresh", () => {
      renderRow({ renderState: "fresh" as RenderState });
      expect(screen.getByText("Rendered")).toBeInTheDocument();
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

  describe("prominent Play button", () => {
    it("shows prominent Play button when renderState is fresh and lastCompletedRenderJobId exists", () => {
      renderRow({ renderState: "fresh" as RenderState, lastCompletedRenderJobId: "render-job-1" });
      const playButtons = screen.getAllByRole("button", { name: /^play$/i });
      expect(playButtons.length).toBe(1);
    });

    it("does not show prominent Play button when lastCompletedRenderJobId is null", () => {
      renderRow({ renderState: "fresh" as RenderState, lastCompletedRenderJobId: null });
      const playButtons = screen.queryAllByRole("button", { name: /^play$/i });
      expect(playButtons.length).toBe(0);
    });

    it("does not show prominent Play button when renderState is stale", () => {
      renderRow({ renderState: "stale" as RenderState, lastCompletedRenderJobId: "render-job-1" });
      const playButtons = screen.queryAllByRole("button", { name: /^play$/i });
      expect(playButtons.length).toBe(0);
    });

    it("does not show prominent Play button when renderState is unrendered", () => {
      renderRow({ renderState: "unrendered" as RenderState, lastCompletedRenderJobId: null });
      const playButtons = screen.queryAllByRole("button", { name: /^play$/i });
      expect(playButtons.length).toBe(0);
    });

    it("does not show prominent Play button when renderState is rendering", () => {
      renderRow({ renderState: "rendering" as RenderState, lastCompletedRenderJobId: null });
      const playButtons = screen.queryAllByRole("button", { name: /^play$/i });
      expect(playButtons.length).toBe(0);
    });

    it("does not show prominent Play button when renderState is failed", () => {
      renderRow({ renderState: "failed" as RenderState, lastCompletedRenderJobId: null });
      const playButtons = screen.queryAllByRole("button", { name: /^play$/i });
      expect(playButtons.length).toBe(0);
    });

    it("does not show prominent Play button when onPlay is undefined", () => {
      renderRow({ renderState: "fresh" as RenderState, lastCompletedRenderJobId: "render-job-1", onPlay: undefined });
      const playButtons = screen.queryAllByRole("button", { name: /^play$/i });
      expect(playButtons.length).toBe(0);
    });

    it("calls onPlay when prominent Play button is clicked", () => {
      const onPlay = vi.fn();
      renderRow({ renderState: "fresh" as RenderState, lastCompletedRenderJobId: "render-job-1", onPlay });
      const playButtons = screen.getAllByRole("button", { name: /^play$/i });
      fireEvent.click(playButtons[0]);
      expect(onPlay).toHaveBeenCalled();
    });

    it("kebab dropdown still contains Play menu item when prominent Play button is shown", async () => {
      renderRow({ renderState: "fresh" as RenderState, lastCompletedRenderJobId: "render-job-1" });
      const menuButton = screen.getByRole("button", { name: /open menu/i });
      fireEvent.click(menuButton);

      await waitFor(() => {
        expect(screen.getByRole("menuitem", { name: /play/i })).toBeInTheDocument();
      });
    });
  });

  describe("kebab trigger visibility", () => {
    it("has touch-visible and hover-capability-aware classes", () => {
      renderRow();
      const menuButton = screen.getByRole("button", { name: /open menu/i });
      expect(menuButton.className).toContain("opacity-100");
      expect(menuButton.className).toContain("[@media(hover:hover)]:opacity-0");
      expect(menuButton.className).toContain("[@media(hover:hover)]:group-hover:opacity-100");
      expect(menuButton.className).toContain("data-[state=open]:opacity-100");
    });
  });
});
