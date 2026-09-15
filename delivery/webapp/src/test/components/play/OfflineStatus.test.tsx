import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import { OfflineStatus } from "@/components/play/OfflineStatus";

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

vi.mock("@/lib/offline/artifact-cache", () => ({
  ARTIFACT_CACHE_NAME: "sow-artifacts",
  cacheArtifacts: vi.fn().mockResolvedValue(undefined),
  getArtifactCacheStatus: vi.fn().mockResolvedValue({ isCached: false, renderJobId: "test-job" }),
  isOfflineSupportedOnCurrentDevice: vi.fn().mockReturnValue(true),
  requestPersistentStorage: vi.fn().mockResolvedValue(true),
}));

import { NoArtifactsError, downloadOfflineArtifacts } from "@/lib/offline/download-offline";

vi.mock("@/lib/offline/download-offline", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/offline/download-offline")>();
  return {
    ...actual,
    downloadOfflineArtifacts: vi.fn().mockResolvedValue(undefined),
  };
});

describe("OfflineStatus", () => {
  const mockProps = {
    songsetId: "test-songset",
    songsetName: "Test Songset",
    renderJobId: "test-job",
    mp3R2Key: "renders/test-job/output.mp3",
    mp4R2Key: "renders/test-job/output.mp4",
    chaptersR2Key: "renders/test-job/chapters.json",
  };

  beforeEach(() => {
    vi.clearAllMocks();

    const mockCache = {
      match: vi.fn().mockResolvedValue(null),
      put: vi.fn().mockResolvedValue(undefined),
      keys: vi.fn().mockResolvedValue([]),
      delete: vi.fn().mockResolvedValue(true),
    };

    Object.defineProperty(global, "caches", {
      value: {
        open: vi.fn().mockResolvedValue(mockCache),
      },
      writable: true,
      configurable: true,
    });

    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: vi.fn().mockResolvedValue({
        renderJobId: "test-job",
        mp3Url: "/api/r2/artifact/test-job/output.mp3",
        mp4Url: "/api/r2/artifact/test-job/output.mp4",
        chaptersUrl: "/api/r2/artifact/test-job/chapters.json",
      }),
    });

    Object.defineProperty(navigator, "userAgent", {
      value: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("rendering", () => {
    it("renders download button when not cached", async () => {
      render(<OfflineStatus {...mockProps} />);

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /download for offline/i })).toBeInTheDocument();
      });
    });

    it("renders download button in Traditional Chinese in zh-Hant", async () => {
      render(<OfflineStatus {...mockProps} />, "zh-Hant");

      await waitFor(() => {
        expect(screen.getByRole("button", { name: /離線下載/i })).toBeInTheDocument();
      });
    });

    it("renders offline ready badge when cached", async () => {
      const { getArtifactCacheStatus } = await import("@/lib/offline/artifact-cache");
      vi.mocked(getArtifactCacheStatus).mockResolvedValueOnce({ isCached: true, renderJobId: "test-job" });

      render(<OfflineStatus {...mockProps} />);

      await waitFor(() => {
        expect(screen.getByText(/offline ready/i)).toBeInTheDocument();
      });
    });

    it("does not render when no artifacts available", () => {
      render(<OfflineStatus {...mockProps} mp3R2Key={null} mp4R2Key={null} />);

      expect(screen.queryByRole("button")).not.toBeInTheDocument();
      expect(screen.queryByText(/offline/i)).not.toBeInTheDocument();
    });
  });

  describe("caching", () => {
    it("delegates download to the shared helper with songset context", async () => {
      const { toast } = await import("sonner");
      const { downloadOfflineArtifacts } = await import("@/lib/offline/download-offline");

      render(<OfflineStatus {...mockProps} />);

      const downloadButton = await screen.findByRole("button", { name: /download for offline/i });
      fireEvent.click(downloadButton);

      await waitFor(() => {
        expect(downloadOfflineArtifacts).toHaveBeenCalledWith(
          { songsetId: "test-songset", songsetName: "Test Songset", renderJobId: "test-job" },
          expect.any(Function)
        );
      });

      await waitFor(() => {
        expect(toast.success).toHaveBeenCalledWith("Downloaded for offline playback");
      });
    });

    it("shows noArtifacts toast when the helper throws NoArtifactsError", async () => {
      const { toast } = await import("sonner");
      const { NoArtifactsError } = await import("@/lib/offline/download-offline");
      const { downloadOfflineArtifacts } = await import("@/lib/offline/download-offline");
      vi.mocked(downloadOfflineArtifacts).mockRejectedValueOnce(new NoArtifactsError());

      render(<OfflineStatus {...mockProps} />);

      const downloadButton = await screen.findByRole("button", { name: /download for offline/i });
      fireEvent.click(downloadButton);

      await waitFor(() => {
        expect(toast.error).toHaveBeenCalledWith("No artifacts available to cache");
      });
    });

    it("shows downloadFailed when the helper fails for another reason", async () => {
      const { toast } = await import("sonner");
      const { downloadOfflineArtifacts } = await import("@/lib/offline/download-offline");
      vi.mocked(downloadOfflineArtifacts).mockRejectedValueOnce(new Error("Network error"));

      render(<OfflineStatus {...mockProps} />);

      const downloadButton = await screen.findByRole("button", { name: /download for offline/i });
      fireEvent.click(downloadButton);

      await waitFor(() => {
        expect(toast.error).toHaveBeenCalledWith("Failed to download for offline");
      });
    });
  });

  describe("iOS version check", () => {
    it("shows iOS warning for iOS < 17.4", async () => {
      const { isOfflineSupportedOnCurrentDevice } = await import("@/lib/offline/artifact-cache");
      vi.mocked(isOfflineSupportedOnCurrentDevice).mockReturnValueOnce(false);

      Object.defineProperty(navigator, "userAgent", {
        value: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_3_1 like Mac OS X)",
        writable: true,
        configurable: true,
      });

      render(<OfflineStatus {...mockProps} />);

      await waitFor(() => {
        expect(screen.getByText(/update ios for offline/i)).toBeInTheDocument();
      });
    });

    it("does not show iOS warning for iOS 17.4+", async () => {
      Object.defineProperty(navigator, "userAgent", {
        value: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X)",
        writable: true,
        configurable: true,
      });

      render(<OfflineStatus {...mockProps} />);

      await waitFor(() => {
        expect(screen.queryByText(/update ios for offline/i)).not.toBeInTheDocument();
        expect(screen.getByRole("button", { name: /download for offline/i })).toBeInTheDocument();
      });
    });
  });

  describe("storage persistence", () => {
    it("download click delegates to helper without a component-level persist call", async () => {
      const { requestPersistentStorage } = await import("@/lib/offline/artifact-cache");
      const { downloadOfflineArtifacts } = await import("@/lib/offline/download-offline");

      render(<OfflineStatus {...mockProps} />);

      const downloadButton = await screen.findByRole("button", { name: /download for offline/i });
      fireEvent.click(downloadButton);

      await waitFor(() => {
        expect(downloadOfflineArtifacts).toHaveBeenCalled();
      });
      expect(requestPersistentStorage).not.toHaveBeenCalled();
    });
  });

});
