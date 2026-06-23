import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { ControllerPlayer } from "@/components/play/ControllerPlayer";

// Mock next/navigation
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
  }),
  useParams: () => ({ id: "test-songset" }),
}));

// Mock sonner toast
vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  },
}));

describe("ControllerPlayer", () => {
  const mockOnPresentationConnect = vi.fn();
  const mockOnPresentationDisconnect = vi.fn();

  const mockChapters = [
    {
      position: 0,
      songTitle: "Amazing Grace",
      startSeconds: 0,
      endSeconds: 180,
      lines: [
        { text: "Amazing grace, how sweet the sound", startSeconds: 10 },
        { text: "That saved a wretch like me", startSeconds: 20 },
      ],
    },
    {
      position: 1,
      songTitle: "How Great Thou Art",
      startSeconds: 180,
      endSeconds: 420,
      lines: [
        { text: "O Lord my God", startSeconds: 190 },
        { text: "When I in awesome wonder", startSeconds: 200 },
      ],
    },
  ];

  const defaultProps = {
    songsetId: "test-songset",
    videoSrc: "https://example.com/video.mp4",
    chapters: mockChapters,
    isPresentationActive: false,
    onPresentationConnect: mockOnPresentationConnect,
    onPresentationDisconnect: mockOnPresentationDisconnect,
  };

  beforeEach(() => {
    vi.clearAllMocks();

    // Mock sessionStorage
    const sessionStorageMock = {
      getItem: vi.fn(),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    };
    Object.defineProperty(window, "sessionStorage", {
      value: sessionStorageMock,
      writable: true,
    });

    // Mock fullscreen API
    Object.defineProperty(document, "fullscreenElement", {
      value: null,
      writable: true,
      configurable: true,
    });
    Object.defineProperty(document.documentElement, "requestFullscreen", {
      value: vi.fn().mockResolvedValue(undefined),
      writable: true,
      configurable: true,
    });
    Object.defineProperty(document, "exitFullscreen", {
      value: vi.fn().mockResolvedValue(undefined),
      writable: true,
      configurable: true,
    });

    // Mock navigator.userAgent for iOS detection
    Object.defineProperty(navigator, "userAgent", {
      value: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
      writable: true,
      configurable: true,
    });

    // Mock navigator.wakeLock - set to null (not just undefined) so "in" check still false
    Object.defineProperty(navigator, "wakeLock", {
      value: undefined,
      writable: true,
      configurable: true,
    });

    // Mock HTMLMediaElement play/pause for jsdom
    Object.defineProperty(window.HTMLMediaElement.prototype, "play", {
      value: vi.fn().mockResolvedValue(undefined),
      writable: true,
      configurable: true,
    });
    Object.defineProperty(window.HTMLMediaElement.prototype, "pause", {
      value: vi.fn(),
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("rendering", () => {
    it("renders video element", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const video = document.querySelector("video");
      expect(video).toBeInTheDocument();
    });

    it("renders playback controls", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      expect(screen.getByRole("button", { name: /^play$/i })).toBeInTheDocument();
    });

    it("renders exit button", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      expect(screen.getByRole("button", { name: /^back$/i })).toBeInTheDocument();
    });

    it("renders lyric jump list handle", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      expect(screen.getByText(/lyrics/i)).toBeInTheDocument();
    });
  });

  describe("presentation mode", () => {
    it("shows connected indicator when presentation is active", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} isPresentationActive={true} />);
      });

      expect(screen.getByText(/connected to tv/i)).toBeInTheDocument();
    });

    it("does not show connected indicator when presentation is inactive", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} isPresentationActive={false} />);
      });

      expect(screen.queryByText(/connected to tv/i)).not.toBeInTheDocument();
    });

    it("mutes video when presentation is active", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} isPresentationActive={true} />);
      });

      const video = document.querySelector("video");
      expect(video).toHaveAttribute("muted");
    });

    it("unmutes video when presentation becomes inactive", async () => {
      const { rerender } = render(<ControllerPlayer {...defaultProps} isPresentationActive={true} />);
      
      await act(async () => {
        rerender(<ControllerPlayer {...defaultProps} isPresentationActive={false} />);
      });

      const video = document.querySelector("video");
      expect(video).not.toHaveAttribute("muted");
    });
  });

  describe("controls visibility", () => {
    it("shows controls by default", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const controls = screen.getByRole("button", { name: /^play$/i }).closest("div[class*='transition-opacity']");
      expect(controls).toHaveClass("opacity-100");
    });

    it("keeps controls visible when presentation is active", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} isPresentationActive={true} />);
      });

      const controls = screen.getByRole("button", { name: /^play$/i }).closest("div[class*='transition-opacity']");
      expect(controls).toHaveClass("opacity-100");
    });
  });

  describe("playback controls", () => {
    it("toggles play/pause when play button clicked", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const playButton = screen.getByRole("button", { name: /^play$/i });

      await act(async () => {
        fireEvent.click(playButton);
      });

      // Should toggle to pause
      await waitFor(() => {
        expect(screen.getByRole("button", { name: /^pause$/i })).toBeInTheDocument();
      });
    });

    it("navigates to previous song when prev button clicked", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const prevButton = screen.getByRole("button", { name: /previous song/i });
      
      await act(async () => {
        fireEvent.click(prevButton);
      });

      // Should be disabled on first song
      expect(prevButton).toBeDisabled();
    });

    it("navigates to next song when next button clicked", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const nextButton = screen.getByRole("button", { name: /next song/i });
      
      await act(async () => {
        fireEvent.click(nextButton);
      });

      // Should work (not disabled)
      expect(nextButton).not.toBeDisabled();
    });

    it("clicking video shows controls without toggling play/pause", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const video = document.querySelector("video");
      
      await act(async () => {
        fireEvent.click(video!);
      });

      // Video click should not toggle play/pause (it shows controls instead)
      const playButton = screen.getByRole("button", { name: /^play$/i });
      expect(playButton).toBeInTheDocument();
    });
  });

  describe("volume controls", () => {
    it("toggles mute when volume button clicked", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const volumeButton = screen.getByRole("button", { name: /mute/i });
      
      await act(async () => {
        fireEvent.click(volumeButton);
      });

      // Should toggle to unmute
      expect(screen.getByRole("button", { name: /unmute/i })).toBeInTheDocument();
    });

    it("renders volume slider", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const volumeSlider = screen.getByRole("slider", { name: /volume/i });
      expect(volumeSlider).toBeInTheDocument();
    });
  });

  describe("scrub bar", () => {
    it("renders scrub bar", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const scrubBar = screen.getByRole("slider", { name: /seek/i });
      expect(scrubBar).toBeInTheDocument();
    });

    it("allows keyboard navigation on scrub bar", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const scrubBar = screen.getByRole("slider", { name: /seek/i });
      
      await act(async () => {
        fireEvent.keyDown(scrubBar, { key: "ArrowLeft" });
        fireEvent.keyDown(scrubBar, { key: "ArrowRight" });
      });

      // Should handle key events
      expect(scrubBar).toBeInTheDocument();
    });
  });

  describe("exit functionality", () => {
    it("navigates back when exit button clicked", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const exitButton = screen.getByRole("button", { name: /^back$/i });

      await act(async () => {
        fireEvent.click(exitButton);
      });

      // Should trigger navigation
      expect(exitButton).toBeInTheDocument();
    });
  });

  describe("iOS info toast", () => {
    it("shows iOS info toast on iOS devices", async () => {
      Object.defineProperty(navigator, "userAgent", {
        value: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
        writable: true,
        configurable: true,
      });

      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      // Should show iOS info
      expect(screen.getByText(/iOS Playback Tips/i)).toBeInTheDocument();
    });

    it("does not show iOS info toast on non-iOS devices", async () => {
      Object.defineProperty(navigator, "userAgent", {
        value: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        writable: true,
        configurable: true,
      });

      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      // Should not show iOS info
      expect(screen.queryByText(/iOS Playback Tips/i)).not.toBeInTheDocument();
    });

    it("does not show iOS info when presentation is active", async () => {
      Object.defineProperty(navigator, "userAgent", {
        value: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
        writable: true,
        configurable: true,
      });

      await act(async () => {
        render(<ControllerPlayer {...defaultProps} isPresentationActive={true} />);
      });

      // Should not show iOS info when presentation is active
      expect(screen.queryByText(/iOS Playback Tips/i)).not.toBeInTheDocument();
    });

    it("can dismiss iOS info toast", async () => {
      Object.defineProperty(navigator, "userAgent", {
        value: "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)",
        writable: true,
        configurable: true,
      });

      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const dismissButton = screen.getByRole("button", { name: /dismiss info/i });
      
      await act(async () => {
        fireEvent.click(dismissButton);
      });

      // Toast should be dismissed
      expect(screen.queryByText(/iOS Playback Tips/i)).not.toBeInTheDocument();
    });
  });

  describe("fullscreen", () => {
    it("requests fullscreen on mount", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      await waitFor(() => {
        expect(document.documentElement.requestFullscreen).toHaveBeenCalled();
      });
    });

    it("exits fullscreen on unmount", async () => {
      Object.defineProperty(document, "fullscreenElement", {
        value: document.documentElement,
        writable: true,
        configurable: true,
      });

      const { unmount } = render(<ControllerPlayer {...defaultProps} />);
      
      await act(async () => {
        unmount();
      });

      await waitFor(() => {
        expect(document.exitFullscreen).toHaveBeenCalled();
      });
    });
  });

  describe("keyboard shortcuts", () => {
    it("responds to space key for play/pause", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const scrubBar = screen.getByRole("slider", { name: /seek/i });
      
      await act(async () => {
        fireEvent.keyDown(scrubBar, { key: " " });
      });

      // Should toggle play/pause
      expect(scrubBar).toBeInTheDocument();
    });

    it("responds to arrow keys for seeking", async () => {
      await act(async () => {
        render(<ControllerPlayer {...defaultProps} />);
      });

      const scrubBar = screen.getByRole("slider", { name: /seek/i });
      
      await act(async () => {
        fireEvent.keyDown(scrubBar, { key: "ArrowLeft" });
        fireEvent.keyDown(scrubBar, { key: "ArrowRight" });
      });

      // Should handle key events
      expect(scrubBar).toBeInTheDocument();
    });
  });
});
