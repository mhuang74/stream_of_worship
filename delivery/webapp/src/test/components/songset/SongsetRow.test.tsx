import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { renderWithLocale, renderWithLocale as render } from "@/test/render";
import { SongsetRow } from "@/components/songset/SongsetRow";
import { RenderState } from "@/components/songset/RenderStatusBadge";
import {
  probeConnectivity,
  setConnectivityProbe,
} from "@/hooks/useConnectivity";

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
    latestRenderJobId: "render-job-1",
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

  const openMenu = async () => {
    fireEvent.click(screen.getByRole("button", { name: /open menu/i }));
    await waitFor(() => {
      expect(screen.getByRole("menuitem", { name: /rename/i })).toBeInTheDocument();
    });
  };

  // Download for Offline gates on Connectivity; the probe seam settles it
  // Online so the enabled-state assertions are deterministic.
  beforeEach(async () => {
    const probe = vi.fn<() => Promise<boolean>>();
    probe.mockResolvedValue(true);
    setConnectivityProbe(probe);
    await probeConnectivity();
  });

  afterEach(() => {
    setConnectivityProbe(null);
  });

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

  describe("remove from offline menu item", () => {
    it("renders Remove from offline menu item when offline available", async () => {
      renderRow({ isOfflineAvailable: true, onRemoveOffline: vi.fn() });
      const menuButton = screen.getByRole("button", { name: /open menu/i });
      fireEvent.click(menuButton);
      await waitFor(() => {
        expect(
          screen.getByRole("menuitem", { name: /remove from offline/i })
        ).toBeInTheDocument();
      });
    });

    it("does not render Remove from offline when offline unavailable", async () => {
      renderRow({ isOfflineAvailable: false, onRemoveOffline: vi.fn() });
      const menuButton = screen.getByRole("button", { name: /open menu/i });
      fireEvent.click(menuButton);
      await waitFor(() => {
        expect(screen.getByRole("menuitem", { name: /rename/i })).toBeInTheDocument();
      });
      expect(
        screen.queryByRole("menuitem", { name: /remove from offline/i })
      ).not.toBeInTheDocument();
    });

    it("does not render Remove from offline when handler missing", async () => {
      renderRow({ isOfflineAvailable: true });
      const menuButton = screen.getByRole("button", { name: /open menu/i });
      fireEvent.click(menuButton);
      await waitFor(() => {
        expect(screen.getByRole("menuitem", { name: /rename/i })).toBeInTheDocument();
      });
      expect(
        screen.queryByRole("menuitem", { name: /remove from offline/i })
      ).not.toBeInTheDocument();
    });

    it("calls onRemoveOffline when clicked", async () => {
      const onRemoveOffline = vi.fn();
      renderRow({ isOfflineAvailable: true, onRemoveOffline });
      const menuButton = screen.getByRole("button", { name: /open menu/i });
      fireEvent.click(menuButton);
      await waitFor(() => {
        const item = screen.getByRole("menuitem", { name: /remove from offline/i });
        fireEvent.click(item);
      });
      expect(onRemoveOffline).toHaveBeenCalled();
    });

    it("renders Traditional Chinese label in zh-Hant locale", () => {
      renderWithLocale(
        <SongsetRow {...defaultProps} isOfflineAvailable onRemoveOffline={() => {}} />,
        "zh-Hant"
      );
      fireEvent.click(screen.getByRole("button", { name: /開啟選單/i }));
      // item content is checked after opening
      return waitFor(() => {
        expect(screen.getByRole("menuitem", { name: /離線/ })).toBeInTheDocument();
      });
    });
  });

  describe("download for offline menu item", () => {
    async function openMenuAndSettle() {
      fireEvent.click(screen.getByRole("button", { name: /open menu/i }));
      await waitFor(() => {
        expect(screen.getByRole("menuitem", { name: /rename/i })).toBeInTheDocument();
      });
      // The connectivity probe must settle before enabled-state assertions;
      // while Unknown, the item is still disabled (fail toward offline).
      await waitFor(() => {
        expect(
          screen.getByRole("menuitem", { name: /download for offline/i })
        ).not.toHaveAttribute("data-disabled");
      });
    }

    it("shows Download for Offline when not downloaded", async () => {
      renderRow({ onDownloadOffline: vi.fn() });
      await openMenuAndSettle();
      expect(
        screen.getByRole("menuitem", { name: /download for offline/i })
      ).toBeInTheDocument();
    });

    it("hides the item when downloaded and fresh", async () => {
      renderRow({
        isOfflineAvailable: true,
        onDownloadOffline: vi.fn(),
        onRemoveOffline: vi.fn(),
      });
      await openMenu();
      expect(
        screen.queryByRole("menuitem", { name: /download for offline/i })
      ).not.toBeInTheDocument();
    });

    it("shows Re-download for Offline when artifacts are stale", async () => {
      renderRow({
        isOfflineAvailable: true,
        isArtifactsStale: true,
        onDownloadOffline: vi.fn(),
        onRemoveOffline: vi.fn(),
      });
      await openMenuAndSettle();
      expect(
        screen.getByRole("menuitem", { name: /re-download for offline/i })
      ).toBeInTheDocument();
    });

    it("disables the item without any render job", async () => {
      renderRow({
        latestRenderJobId: null,
        lastCompletedRenderJobId: null,
        onDownloadOffline: vi.fn(),
      });
      fireEvent.click(screen.getByRole("button", { name: /open menu/i }));
      await waitFor(() => {
        expect(screen.getByRole("menuitem", { name: /download for offline/i })).toBeInTheDocument();
      });
      // Radix marks disabled menu items with data-disabled, not the DOM
      // disabled attribute.
      expect(
        screen.getByRole("menuitem", { name: /download for offline/i })
      ).toHaveAttribute("data-disabled");
    });

    it("disables the item while a download is in progress", async () => {
      renderRow({ onDownloadOffline: vi.fn(), isOfflineDownloadInProgress: true });
      fireEvent.click(screen.getByRole("button", { name: /open menu/i }));
      await waitFor(() => {
        expect(
          screen.getByRole("menuitem", { name: /downloading for offline/i })
        ).toBeInTheDocument();
      });
      expect(
        screen.getByRole("menuitem", { name: /downloading for offline/i })
      ).toHaveAttribute("data-disabled");
    });

    it("calls onDownloadOffline when clicked", async () => {
      const onDownloadOffline = vi.fn();
      renderRow({ onDownloadOffline });
      await openMenuAndSettle();
      fireEvent.click(
        screen.getByRole("menuitem", { name: /download for offline/i })
      );
      expect(onDownloadOffline).toHaveBeenCalled();
    });

    it("renders the Traditional Chinese label in zh-Hant", async () => {
      renderWithLocale(
        <SongsetRow {...defaultProps} onDownloadOffline={() => {}} />,
        "zh-Hant"
      );
      fireEvent.click(screen.getByRole("button", { name: /開啟選單/i }));
      await waitFor(() => {
        expect(
          screen.getByRole("menuitem", { name: /下載離線副本/ })
        ).toBeInTheDocument();
      });
    });
  });

  describe("offline badge stale tint", () => {
    it("tints the offline badge amber when artifacts are stale", () => {
      const { container } = renderRow({
        isOfflineAvailable: true,
        isArtifactsStale: true,
      });
      const badge = container.querySelector(
        '[data-songset-id] span[data-slot="badge"][data-variant="secondary"]'
      );
      expect(badge).not.toBeNull();
      expect(badge!.className).toContain("text-amber-600");
      expect(badge!.className).toContain("border-amber-500/50");
    });

    it("keeps the neutral offline badge when artifacts are fresh", () => {
      const { container } = renderRow({
        isOfflineAvailable: true,
        isArtifactsStale: false,
      });
      const badge = container.querySelector(
        '[data-songset-id] span[data-slot="badge"][data-variant="secondary"]'
      );
      expect(badge).not.toBeNull();
      expect(badge!.className).not.toContain("text-amber-600");
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

  describe("localization", () => {
    it("renders Traditional Chinese labels in zh-Hant locale", () => {
      renderWithLocale(<SongsetRow {...defaultProps} />, "zh-Hant");
      // Song count label and offline badge are localized
      expect(screen.getByText(/首詩歌/)).toBeInTheDocument();
      // Kebab aria-label is localized
      expect(screen.getByRole("button", { name: /開啟選單/i })).toBeInTheDocument();
    });
  });
});
