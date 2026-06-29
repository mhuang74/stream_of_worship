import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { ProjectionPlayer } from "@/components/play/ProjectionPlayer";

// Mock hooks
vi.mock("@/hooks/useWakeLock", () => ({
  useWakeLock: vi.fn().mockReturnValue({
    isSupported: false,
    isActive: false,
    error: null,
    request: vi.fn(),
    release: vi.fn(),
  }),
}));

const { sendStatusMock, receiverMockImpl } = vi.hoisted(() => ({
  sendStatusMock: vi.fn(),
  receiverMockImpl: vi.fn(),
}));

vi.mock("@/hooks/usePresentation", () => ({
  usePresentationReceiver: receiverMockImpl.mockReturnValue({ sendStatus: sendStatusMock }),
}));

import { usePresentationReceiver } from "@/hooks/usePresentation";

describe("ProjectionPlayer", () => {
  const defaultProps = {
    videoSrc: "https://example.com/video.mp4",
    initialSongTitle: "Amazing Grace",
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();

    // Re-establish the default receiver mock return (clearAllMocks preserves
    // implementations, but individual tests below override via
    // mockImplementation; reset to a sane default for tests that don't).
    vi.mocked(usePresentationReceiver).mockReturnValue({ sendStatus: sendStatusMock });
    // Mock screen.orientation
    Object.defineProperty(window, "screen", {
      value: {
        orientation: {
          lock: vi.fn().mockResolvedValue(undefined),
          unlock: vi.fn(),
        },
      },
      writable: true,
      configurable: true,
    });

    // Mock HTMLMediaElement
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
    Object.defineProperty(window.HTMLMediaElement.prototype, "currentTime", {
      value: 0,
      writable: true,
      configurable: true,
    });
    Object.defineProperty(window.HTMLMediaElement.prototype, "duration", {
      value: 0,
      writable: true,
      configurable: true,
    });

    // Mock navigator.wakeLock
    Object.defineProperty(navigator, "wakeLock", {
      value: undefined,
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  describe("rendering", () => {
    it("renders video element", async () => {
      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      const video = document.querySelector("video");
      expect(video).toBeInTheDocument();
    });

    it("sets video src correctly", async () => {
      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      const video = document.querySelector("video");
      expect(video).toHaveAttribute("src", defaultProps.videoSrc);
    });

    it("video uses object-cover class for landscape", async () => {
      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      const video = document.querySelector("video");
      expect(video).toHaveClass("object-cover");
    });

    it("renders projection player container", async () => {
      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      expect(screen.getByTestId("projection-player")).toBeInTheDocument();
    });

    it("has no visible controls (chrome-free)", async () => {
      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      // No buttons, no nav, no header
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
      expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    });
  });

  describe("song title overlay", () => {
    it("shows initial song title", async () => {
      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      expect(screen.getByTestId("song-title-text")).toHaveTextContent("Amazing Grace");
    });

    it("title overlay is visible initially when title is set", async () => {
      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      const overlay = screen.getByTestId("song-title-overlay");
      expect(overlay).toHaveStyle({ opacity: "0.5" });
    });

    it("title overlay is hidden when no initial title", async () => {
      await act(async () => {
        render(<ProjectionPlayer videoSrc="https://example.com/video.mp4" />);
      });

      const overlay = screen.getByTestId("song-title-overlay");
      expect(overlay).toHaveStyle({ opacity: "0" });
    });

    it("title fades out after 2 seconds", async () => {
      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      const overlay = screen.getByTestId("song-title-overlay");
      expect(overlay).toHaveStyle({ opacity: "0.5" });

      act(() => {
        vi.advanceTimersByTime(2000);
      });

      expect(overlay).toHaveStyle({ opacity: "0" });
    });

    it("title font size is at most 14px", async () => {
      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      const titleText = screen.getByTestId("song-title-text");
      expect(titleText).toHaveStyle({ fontSize: "14px" });
    });
  });

  describe("Presentation API message handling", () => {
    it("registers usePresentationReceiver hook", async () => {
      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      expect(usePresentationReceiver).toHaveBeenCalled();
    });

    it("provides onPlay callback to usePresentationReceiver", async () => {
      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      const callArgs = vi.mocked(usePresentationReceiver).mock.calls[0][0];
      expect(callArgs.onPlay).toBeDefined();
    });

    it("provides onPause callback to usePresentationReceiver", async () => {
      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      const callArgs = vi.mocked(usePresentationReceiver).mock.calls[0][0];
      expect(callArgs.onPause).toBeDefined();
    });

    it("provides onSeek callback to usePresentationReceiver", async () => {
      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      const callArgs = vi.mocked(usePresentationReceiver).mock.calls[0][0];
      expect(callArgs.onSeek).toBeDefined();
    });

    it("provides onVolume callback to usePresentationReceiver", async () => {
      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      const callArgs = vi.mocked(usePresentationReceiver).mock.calls[0][0];
      expect(callArgs.onVolume).toBeDefined();
    });

    it("provides onSongTitle callback to usePresentationReceiver", async () => {
      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      const callArgs = vi.mocked(usePresentationReceiver).mock.calls[0][0];
      expect(callArgs.onSongTitle).toBeDefined();
    });

    it("updates song title and shows overlay when onSongTitle is called", async () => {
      let onSongTitleCallback: ((title: string) => void) | undefined;
      vi.mocked(usePresentationReceiver).mockImplementation((options) => {
        onSongTitleCallback = options.onSongTitle;
        return { sendStatus: sendStatusMock };
      });

      await act(async () => {
        render(<ProjectionPlayer videoSrc="https://example.com/video.mp4" />);
      });

      // Trigger title change via callback
      await act(async () => {
        onSongTitleCallback?.("How Great Thou Art");
      });

      expect(screen.getByTestId("song-title-text")).toHaveTextContent("How Great Thou Art");

      const overlay = screen.getByTestId("song-title-overlay");
      expect(overlay).toHaveStyle({ opacity: "0.5" });
    });

    it("resets fade timer on chapter change (songTitle message)", async () => {
      let onSongTitleCallback: ((title: string) => void) | undefined;
      vi.mocked(usePresentationReceiver).mockImplementation((options) => {
        onSongTitleCallback = options.onSongTitle;
        return { sendStatus: sendStatusMock };
      });

      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      // Advance 1.5s (not fully faded)
      act(() => {
        vi.advanceTimersByTime(1500);
      });

      // New chapter change - should reset timer
      await act(async () => {
        onSongTitleCallback?.("How Great Thou Art");
      });

      const overlay = screen.getByTestId("song-title-overlay");
      expect(overlay).toHaveStyle({ opacity: "0.5" });

      // After another 1.5s (2.5s total from chapter change - not yet faded)
      act(() => {
        vi.advanceTimersByTime(1500);
      });

      // Still not 2s from the last chapter change
      expect(overlay).toHaveStyle({ opacity: "0.5" });

      // After full 2s from chapter change
      act(() => {
        vi.advanceTimersByTime(500);
      });

      expect(overlay).toHaveStyle({ opacity: "0" });
    });

    it("plays video when onPlay is called", async () => {
      let onPlayCallback: (() => void) | undefined;
      vi.mocked(usePresentationReceiver).mockImplementation((options) => {
        onPlayCallback = options.onPlay;
        return { sendStatus: sendStatusMock };
      });

      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      const video = document.querySelector("video") as HTMLVideoElement;

      await act(async () => {
        onPlayCallback?.();
      });

      expect(video.play).toHaveBeenCalled();
    });

    it("pauses video when onPause is called", async () => {
      let onPauseCallback: (() => void) | undefined;
      vi.mocked(usePresentationReceiver).mockImplementation((options) => {
        onPauseCallback = options.onPause;
        return { sendStatus: sendStatusMock };
      });

      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      const video = document.querySelector("video") as HTMLVideoElement;

      act(() => {
        onPauseCallback?.();
      });

      expect(video.pause).toHaveBeenCalled();
    });

    it("seeks video when onSeek is called", async () => {
      let onSeekCallback: ((pos: number) => void) | undefined;
      vi.mocked(usePresentationReceiver).mockImplementation((options) => {
        onSeekCallback = options.onSeek;
        return { sendStatus: sendStatusMock };
      });

      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      const video = document.querySelector("video") as HTMLVideoElement;

      act(() => {
        onSeekCallback?.(45.0);
      });

      expect(video.currentTime).toBe(45.0);
    });

    it("sets volume when onVolume is called", async () => {
      let onVolumeCallback: ((level: number) => void) | undefined;
      vi.mocked(usePresentationReceiver).mockImplementation((options) => {
        onVolumeCallback = options.onVolume;
        return { sendStatus: sendStatusMock };
      });

      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      const video = document.querySelector("video") as HTMLVideoElement;

      act(() => {
        onVolumeCallback?.(0.5);
      });

      expect(video.volume).toBe(0.5);
      expect(video.muted).toBe(false);
    });

    it("mutes video when volume level is 0", async () => {
      let onVolumeCallback: ((level: number) => void) | undefined;
      vi.mocked(usePresentationReceiver).mockImplementation((options) => {
        onVolumeCallback = options.onVolume;
        return { sendStatus: sendStatusMock };
      });

      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      const video = document.querySelector("video") as HTMLVideoElement;

      act(() => {
        onVolumeCallback?.(0);
      });

      expect(video.volume).toBe(0);
      expect(video.muted).toBe(true);
    });

    it("clamps volume level to 0-1 range", async () => {
      let onVolumeCallback: ((level: number) => void) | undefined;
      vi.mocked(usePresentationReceiver).mockImplementation((options) => {
        onVolumeCallback = options.onVolume;
        return { sendStatus: sendStatusMock };
      });

      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      const video = document.querySelector("video") as HTMLVideoElement;

      act(() => {
        onVolumeCallback?.(2.0);
      });

      expect(video.volume).toBe(1);
    });
  });

  describe("sendStatus (receiver → controller)", () => {
    it("sends ready status on loadedmetadata", async () => {
      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      const video = document.querySelector("video") as HTMLVideoElement;

      sendStatusMock.mockClear();
      await act(async () => {
        video.dispatchEvent(new Event("loadedmetadata"));
      });

      expect(sendStatusMock).toHaveBeenCalledWith({ type: "ready" });
    });

    it("sends media status on loadedmetadata", async () => {
      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      const video = document.querySelector("video") as HTMLVideoElement;
      video.currentTime = 25;
      Object.defineProperty(video, "duration", {
        value: 240,
        writable: true,
        configurable: true,
      });
      video.volume = 0.7;
      video.muted = true;

      sendStatusMock.mockClear();
      await act(async () => {
        video.dispatchEvent(new Event("loadedmetadata"));
      });

      expect(sendStatusMock).toHaveBeenCalledWith({
        type: "media",
        currentTime: 25,
        duration: 240,
        playerState: "paused",
        volume: 0.7,
        isMuted: true,
      });
    });

    it("sends ready status on canplay", async () => {
      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      const video = document.querySelector("video") as HTMLVideoElement;

      sendStatusMock.mockClear();
      await act(async () => {
        video.dispatchEvent(new Event("canplay"));
      });

      expect(sendStatusMock).toHaveBeenCalledWith({ type: "ready" });
    });

    it("sends error status when onPlay's video.play() rejects", async () => {
      // Override play to reject for this test only. vi.spyOn is scoped to
      // a single mock so vi.restoreAllMocks in afterEach cleans it up
      // automatically without manual prototype restoration.
      const playSpy = vi
        .spyOn(window.HTMLMediaElement.prototype, "play")
        .mockRejectedValueOnce(new Error("not allowed"));

      let onPlayCallback: (() => void) | undefined;
      vi.mocked(usePresentationReceiver).mockImplementation((options) => {
        onPlayCallback = options.onPlay;
        return { sendStatus: sendStatusMock };
      });

      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      sendStatusMock.mockClear();
      await act(async () => {
        onPlayCallback?.();
      });

      expect(sendStatusMock).toHaveBeenCalledWith({
        type: "error",
        message: "TV projection failed — check connection",
      });

      playSpy.mockRestore();
    });

    it("sends media status on timeupdate, seeked, and volumechange", async () => {
      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      const video = document.querySelector("video") as HTMLVideoElement;
      Object.defineProperty(video, "duration", {
        value: 240,
        writable: true,
        configurable: true,
      });

      sendStatusMock.mockClear();
      video.currentTime = 32;
      await act(async () => {
        video.dispatchEvent(new Event("timeupdate"));
      });
      video.currentTime = 48;
      await act(async () => {
        video.dispatchEvent(new Event("seeked"));
      });
      video.volume = 0.5;
      await act(async () => {
        video.dispatchEvent(new Event("volumechange"));
      });

      expect(sendStatusMock).toHaveBeenCalledWith(
        expect.objectContaining({ type: "media", currentTime: 32 }),
      );
      expect(sendStatusMock).toHaveBeenCalledWith(
        expect.objectContaining({ type: "media", currentTime: 48 }),
      );
      expect(sendStatusMock).toHaveBeenCalledWith(
        expect.objectContaining({ type: "media", volume: 0.5 }),
      );
    });
  });

  describe("orientation lock", () => {
    it("attempts to lock orientation to landscape on mount", async () => {
      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      expect(window.screen.orientation.lock).toHaveBeenCalledWith("landscape");
    });

    it("unlocks orientation on unmount", async () => {
      const { unmount } = render(<ProjectionPlayer {...defaultProps} />);

      await act(async () => {
        unmount();
      });

      expect(window.screen.orientation.unlock).toHaveBeenCalled();
    });

    it("does not throw if orientation lock fails", async () => {
      Object.defineProperty(window, "screen", {
        value: {
          orientation: {
            lock: vi.fn().mockRejectedValue(new Error("Not supported")),
            unlock: vi.fn(),
          },
        },
        writable: true,
        configurable: true,
      });

      await expect(
        act(async () => {
          render(<ProjectionPlayer {...defaultProps} />);
        })
      ).resolves.not.toThrow();
    });

    it("does not throw if orientation API is unavailable", async () => {
      Object.defineProperty(window, "screen", {
        value: { orientation: null },
        writable: true,
        configurable: true,
      });

      await expect(
        act(async () => {
          render(<ProjectionPlayer {...defaultProps} />);
        })
      ).resolves.not.toThrow();
    });
  });

  describe("wake lock", () => {
    it("calls useWakeLock hook", async () => {
      const { useWakeLock } = await import("@/hooks/useWakeLock");

      await act(async () => {
        render(<ProjectionPlayer {...defaultProps} />);
      });

      expect(useWakeLock).toHaveBeenCalled();
    });
  });
});
