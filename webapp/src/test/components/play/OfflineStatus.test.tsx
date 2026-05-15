import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { OfflineStatus } from "@/components/play/OfflineStatus";

// Mock sonner toast
vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

describe("OfflineStatus", () => {
  const mockProps = {
    songsetId: "test-songset",
    renderJobId: "test-job",
    mp3R2Key: "https://r2.example.com/audio.mp3",
    mp4R2Key: "https://r2.example.com/video.mp4",
    chaptersR2Key: "https://r2.example.com/chapters.json",
  };

  beforeEach(() => {
    vi.clearAllMocks();

    // Mock caches API
    const mockCache = {
      match: vi.fn().mockResolvedValue(null),
      put: vi.fn().mockResolvedValue(undefined),
    };

    Object.defineProperty(global, "caches", {
      value: {
        open: vi.fn().mockResolvedValue(mockCache),
      },
      writable: true,
      configurable: true,
    });

    // Mock fetch
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      clone: vi.fn().mockReturnValue({
        ok: true,
      }),
    });

    // Mock navigator.userAgent
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

    it("renders offline ready badge when cached", async () => {
      const mockCache = {
        match: vi.fn().mockResolvedValue({ ok: true }),
        put: vi.fn().mockResolvedValue(undefined),
      };

      Object.defineProperty(global, "caches", {
        value: {
          open: vi.fn().mockResolvedValue(mockCache),
        },
        writable: true,
        configurable: true,
      });

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
    it("caches artifacts when download button clicked", async () => {
      const { toast } = await import("sonner");

      render(<OfflineStatus {...mockProps} />);

      const downloadButton = await screen.findByRole("button", { name: /download for offline/i });
      fireEvent.click(downloadButton);

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalledWith(mockProps.mp3R2Key);
        expect(global.fetch).toHaveBeenCalledWith(mockProps.mp4R2Key);
        expect(global.fetch).toHaveBeenCalledWith(mockProps.chaptersR2Key);
      });

      await waitFor(() => {
        expect(toast.success).toHaveBeenCalledWith("Downloaded for offline playback");
      });
    });

    it("shows error when caching fails", async () => {
      const { toast } = await import("sonner");

      global.fetch = vi.fn().mockRejectedValue(new Error("Network error"));

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
    it("requests persistent storage on first cache", async () => {
      const mockPersist = vi.fn().mockResolvedValue(true);
      Object.defineProperty(navigator, "storage", {
        value: {
          persist: mockPersist,
        },
        writable: true,
        configurable: true,
      });

      render(<OfflineStatus {...mockProps} />);

      const downloadButton = await screen.findByRole("button", { name: /download for offline/i });
      fireEvent.click(downloadButton);

      await waitFor(() => {
        expect(mockPersist).toHaveBeenCalled();
      });
    });
  });
});
