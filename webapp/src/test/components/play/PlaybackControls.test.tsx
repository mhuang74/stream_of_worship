import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { PlaybackControls } from "@/components/play/PlaybackControls";

describe("PlaybackControls", () => {
  const mockPlayPause = vi.fn();
  const mockSeek = vi.fn();
  const mockPrevSong = vi.fn();
  const mockNextSong = vi.fn();
  const mockVolumeChange = vi.fn();
  const mockToggleMute = vi.fn();

  const defaultProps = {
    isPlaying: false,
    currentTime: 30,
    duration: 300,
    volume: 0.8,
    isMuted: false,
    currentSongIndex: 0,
    totalSongs: 3,
    isPresentationActive: false,
    onPlayPause: mockPlayPause,
    onSeek: mockSeek,
    onPrevSong: mockPrevSong,
    onNextSong: mockNextSong,
    onVolumeChange: mockVolumeChange,
    onToggleMute: mockToggleMute,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("rendering", () => {
    it("renders play button when not playing", () => {
      render(<PlaybackControls {...defaultProps} />);

      const playButton = screen.getByRole("button", { name: /play/i });
      expect(playButton).toBeInTheDocument();
    });

    it("renders pause button when playing", () => {
      render(<PlaybackControls {...defaultProps} isPlaying={true} />);

      const pauseButton = screen.getByRole("button", { name: /pause/i });
      expect(pauseButton).toBeInTheDocument();
    });

    it("renders current time and duration", () => {
      render(<PlaybackControls {...defaultProps} />);

      expect(screen.getByText("0:30")).toBeInTheDocument();
      expect(screen.getByText("5:00")).toBeInTheDocument();
    });

    it("renders song counter", () => {
      render(<PlaybackControls {...defaultProps} />);

      expect(screen.getByText("1/3")).toBeInTheDocument();
    });

    it("renders previous song button", () => {
      render(<PlaybackControls {...defaultProps} />);

      const prevButton = screen.getByRole("button", { name: /previous song/i });
      expect(prevButton).toBeInTheDocument();
    });

    it("renders next song button", () => {
      render(<PlaybackControls {...defaultProps} />);

      const nextButton = screen.getByRole("button", { name: /next song/i });
      expect(nextButton).toBeInTheDocument();
    });

    it("renders volume button", () => {
      render(<PlaybackControls {...defaultProps} />);

      const volumeButton = screen.getByRole("button", { name: /mute/i });
      expect(volumeButton).toBeInTheDocument();
    });

    it("renders volume slider", () => {
      render(<PlaybackControls {...defaultProps} />);

      const volumeSlider = screen.getByRole("slider", { name: /volume/i });
      expect(volumeSlider).toBeInTheDocument();
    });

    it("renders scrub bar on desktop", () => {
      render(<PlaybackControls {...defaultProps} />);

      const scrubBar = screen.getByRole("slider", { name: /seek/i });
      expect(scrubBar).toBeInTheDocument();
    });
  });

  describe("presentation mode", () => {
    it("shows connected indicator when presentation is active", () => {
      render(<PlaybackControls {...defaultProps} isPresentationActive={true} />);

      expect(screen.getByText(/connected/i)).toBeInTheDocument();
    });

    it("does not show connected indicator when presentation is inactive", () => {
      render(<PlaybackControls {...defaultProps} isPresentationActive={false} />);

      expect(screen.queryByText(/connected/i)).not.toBeInTheDocument();
    });
  });

  describe("actions", () => {
    it("calls onPlayPause when play/pause button clicked", () => {
      render(<PlaybackControls {...defaultProps} />);

      const playButton = screen.getByRole("button", { name: /play/i });
      fireEvent.click(playButton);

      expect(mockPlayPause).toHaveBeenCalled();
    });

    it("calls onPrevSong when previous song button clicked", () => {
      render(<PlaybackControls {...defaultProps} currentSongIndex={1} />);

      const prevButton = screen.getByRole("button", { name: /previous song/i });
      fireEvent.click(prevButton);

      expect(mockPrevSong).toHaveBeenCalled();
    });

    it("calls onNextSong when next song button clicked", () => {
      render(<PlaybackControls {...defaultProps} />);

      const nextButton = screen.getByRole("button", { name: /next song/i });
      fireEvent.click(nextButton);

      expect(mockNextSong).toHaveBeenCalled();
    });

    it("calls onToggleMute when volume button clicked", () => {
      render(<PlaybackControls {...defaultProps} />);

      const volumeButton = screen.getByRole("button", { name: /mute/i });
      fireEvent.click(volumeButton);

      expect(mockToggleMute).toHaveBeenCalled();
    });

    it("calls onVolumeChange when volume slider changed", () => {
      render(<PlaybackControls {...defaultProps} />);

      const volumeSlider = screen.getByRole("slider", { name: /volume/i });
      fireEvent.change(volumeSlider, { target: { value: "0.5" } });

      expect(mockVolumeChange).toHaveBeenCalledWith(0.5);
    });

    it("calls onSeek when scrub bar clicked", () => {
      render(<PlaybackControls {...defaultProps} />);

      const scrubBar = screen.getByRole("slider", { name: /seek/i });
      fireEvent.click(scrubBar);

      expect(mockSeek).toHaveBeenCalled();
    });
  });

  describe("disabled states", () => {
    it("disables previous song button on first song", () => {
      render(<PlaybackControls {...defaultProps} currentSongIndex={0} />);

      const prevButton = screen.getByRole("button", { name: /previous song/i });
      expect(prevButton).toBeDisabled();
    });

    it("disables next song button on last song", () => {
      render(
        <PlaybackControls {...defaultProps} currentSongIndex={2} totalSongs={3} />
      );

      const nextButton = screen.getByRole("button", { name: /next song/i });
      expect(nextButton).toBeDisabled();
    });

    it("enables previous song button when not on first song", () => {
      render(<PlaybackControls {...defaultProps} currentSongIndex={1} />);

      const prevButton = screen.getByRole("button", { name: /previous song/i });
      expect(prevButton).not.toBeDisabled();
    });

    it("enables next song button when not on last song", () => {
      render(<PlaybackControls {...defaultProps} currentSongIndex={0} />);

      const nextButton = screen.getByRole("button", { name: /next song/i });
      expect(nextButton).not.toBeDisabled();
    });
  });

  describe("time formatting", () => {
    it("formats time correctly for short durations", () => {
      render(<PlaybackControls {...defaultProps} currentTime={65} />);

      expect(screen.getByText("1:05")).toBeInTheDocument();
    });

    it("formats time correctly for long durations", () => {
      render(<PlaybackControls {...defaultProps} duration={3661} />);

      expect(screen.getByText("61:01")).toBeInTheDocument();
    });

    it("handles zero time", () => {
      render(<PlaybackControls {...defaultProps} currentTime={0} />);

      expect(screen.getByText("0:00")).toBeInTheDocument();
    });
  });

  describe("volume states", () => {
    it("shows muted icon when muted", () => {
      render(<PlaybackControls {...defaultProps} isMuted={true} />);

      const volumeButton = screen.getByRole("button", { name: /unmute/i });
      expect(volumeButton).toBeInTheDocument();
    });

    it("shows volume icon when not muted", () => {
      render(<PlaybackControls {...defaultProps} isMuted={false} />);

      const volumeButton = screen.getByRole("button", { name: /mute/i });
      expect(volumeButton).toBeInTheDocument();
    });
  });
});
