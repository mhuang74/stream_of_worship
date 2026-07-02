import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SongCard, SongCardData } from "@/components/songset/SongCard";

describe("SongCard", () => {
  const mockSong: SongCardData = {
    id: "song-1",
    title: "Amazing Grace",
    composer: "John Newton",
    lyricist: null,
    albumName: "Hymns Collection",
    musicalKey: "G",
    recordings: [
      {
        contentHash: "abc123",
        hashPrefix: "abc",
        durationSeconds: 180,
        tempoBpm: 120,
        musicalKey: "G",
        visibilityStatus: "published",
      },
    ],
  };

  const mockSongNoRecording: SongCardData = {
    id: "song-2",
    title: "How Great Thou Art",
    composer: null,
    lyricist: "Stuart Hine",
    albumName: null,
    musicalKey: null,
    recordings: [],
  };

  const defaultProps = {
    song: mockSong,
    onAdd: vi.fn(),
    isAdded: false,
    isAdding: false,
  };

  const renderCard = (props = {}) => {
    return render(<SongCard {...defaultProps} {...props} />);
  };

  describe("rendering", () => {
    it("renders song title", () => {
      renderCard();
      expect(screen.getByTestId("song-title")).toHaveTextContent("Amazing Grace");
    });

    it("renders composer as artist", () => {
      renderCard();
      expect(screen.getByTestId("song-artist")).toHaveTextContent("John Newton");
    });

    it("renders lyricist when composer is null", () => {
      renderCard({ song: mockSongNoRecording });
      expect(screen.getByTestId("song-artist")).toHaveTextContent("Stuart Hine");
    });

    it("renders 'Unknown Artist' when both composer and lyricist are null", () => {
      renderCard({
        song: { ...mockSongNoRecording, lyricist: null },
      });
      expect(screen.getByTestId("song-artist")).toHaveTextContent("Unknown Artist");
    });

    it("renders duration in MM:SS format", () => {
      renderCard();
      expect(screen.getByTestId("song-duration")).toHaveTextContent("3:00");
    });

    it("renders musical key badge", () => {
      renderCard();
      expect(screen.getByTestId("song-key")).toHaveTextContent("G");
    });

    it("renders tempo BPM", () => {
      renderCard();
      expect(screen.getByTestId("song-tempo")).toHaveTextContent("120 BPM");
    });

    it("renders album name on larger screens", () => {
      renderCard();
      expect(screen.getByTestId("song-album")).toHaveTextContent("Hymns Collection");
    });

    it("renders without recording data gracefully", () => {
      renderCard({ song: mockSongNoRecording });
      expect(screen.getByTestId("song-title")).toHaveTextContent("How Great Thou Art");
      expect(screen.queryByTestId("song-duration")).not.toBeInTheDocument();
      expect(screen.queryByTestId("song-key")).not.toBeInTheDocument();
      expect(screen.queryByTestId("song-tempo")).not.toBeInTheDocument();
    });

    it("uses recording key over song key when available", () => {
      const songWithDifferentKeys = {
        ...mockSong,
        musicalKey: "C",
        recordings: [
          { ...mockSong.recordings[0], musicalKey: "F" },
        ],
      };
      renderCard({ song: songWithDifferentKeys });
      expect(screen.getByTestId("song-key")).toHaveTextContent("F");
    });
  });

  describe("verified badge", () => {
    it("shows verified badge when recording is published", () => {
      renderCard();
      expect(screen.getByTestId("verified-badge")).toBeInTheDocument();
    });

    it("does not show verified badge when recording is review", () => {
      renderCard({
        song: {
          ...mockSong,
          recordings: [
            { ...mockSong.recordings[0], visibilityStatus: "review" },
          ],
        },
      });
      expect(screen.queryByTestId("verified-badge")).not.toBeInTheDocument();
    });

    it("does not show verified badge when recordings is empty", () => {
      renderCard({ song: mockSongNoRecording });
      expect(screen.queryByTestId("verified-badge")).not.toBeInTheDocument();
    });

    it("shows verified badge when any recording is published (mixed statuses)", () => {
      renderCard({
        song: {
          ...mockSong,
          recordings: [
            { ...mockSong.recordings[0], visibilityStatus: "review" },
            { ...mockSong.recordings[0], visibilityStatus: "published" },
          ],
        },
      });
      expect(screen.getByTestId("verified-badge")).toBeInTheDocument();
    });
  });

  describe("BPM rounding", () => {
    it("rounds fractional BPM to nearest integer", () => {
      renderCard({
        song: {
          ...mockSong,
          recordings: [
            { ...mockSong.recordings[0], tempoBpm: 127.97 },
          ],
        },
      });
      expect(screen.getByTestId("song-tempo")).toHaveTextContent("128 BPM");
    });

    it("rounds down fractional BPM", () => {
      renderCard({
        song: {
          ...mockSong,
          recordings: [
            { ...mockSong.recordings[0], tempoBpm: 120.3 },
          ],
        },
      });
      expect(screen.getByTestId("song-tempo")).toHaveTextContent("120 BPM");
    });
  });

  describe("add button", () => {
    it("renders add button when onAdd is provided", () => {
      renderCard();
      expect(screen.getByTestId("add-song-button")).toBeInTheDocument();
    });

    it("does not render add button when onAdd is not provided", () => {
      renderCard({ onAdd: undefined });
      expect(screen.queryByTestId("add-song-button")).not.toBeInTheDocument();
    });

    it("calls onAdd when add button is clicked", async () => {
      const onAdd = vi.fn().mockResolvedValue(undefined);
      renderCard({ onAdd });

      fireEvent.click(screen.getByTestId("add-song-button"));

      await waitFor(() => {
        expect(onAdd).toHaveBeenCalledWith("song-1");
      });
    });

    it("shows checkmark when song is already added", () => {
      renderCard({ isAdded: true });
      const button = screen.getByTestId("add-song-button");
      expect(button).toBeDisabled();
      expect(button.querySelector("svg")).toHaveClass("text-green-500");
    });

    it("shows loading spinner when isAdding is true", () => {
      renderCard({ isAdding: true });
      const button = screen.getByTestId("add-song-button");
      expect(button).toBeDisabled();
      expect(button.querySelector("span")).toHaveClass("animate-spin");
    });

    it("disables button when song is added", () => {
      renderCard({ isAdded: true });
      expect(screen.getByTestId("add-song-button")).toBeDisabled();
    });

    it("has correct aria-label for add button", () => {
      renderCard();
      expect(screen.getByTestId("add-song-button")).toHaveAttribute(
        "aria-label",
        "Add to songset"
      );
    });

    it("has correct aria-label when already added", () => {
      renderCard({ isAdded: true });
      expect(screen.getByTestId("add-song-button")).toHaveAttribute(
        "aria-label",
        "Already added"
      );
    });
  });

  describe("accessibility", () => {
    it("has data-testid for song card", () => {
      renderCard();
      expect(screen.getByTestId("song-card")).toBeInTheDocument();
    });
  });

  describe("play button", () => {
    it("renders song-play-button when onPlay is provided", () => {
      renderCard({ onPlay: vi.fn() });
      expect(screen.getByTestId("song-play-button")).toBeInTheDocument();
    });

    it("renders song-art-placeholder when onPlay is not provided", () => {
      renderCard({ onAdd: undefined });
      expect(screen.getByTestId("song-art-placeholder")).toBeInTheDocument();
    });

    it("shows Disc icon when not hovered and not playing", () => {
      renderCard({ onPlay: vi.fn() });
      const playButton = screen.getByTestId("song-play-button");
      expect(playButton.querySelector("svg")).toHaveClass("lucide", "lucide-disc");
    });

    it("shows Play icon on hover when onPlay is provided", () => {
      renderCard({ onPlay: vi.fn() });
      fireEvent.mouseEnter(screen.getByTestId("song-card"));
      const playButton = screen.getByTestId("song-play-button");
      expect(playButton.querySelector("svg")).toHaveClass("lucide", "lucide-play");
    });

    it("shows Pause icon when isPlaying is true", () => {
      renderCard({ onPlay: vi.fn(), isPlaying: true });
      const playButton = screen.getByTestId("song-play-button");
      expect(playButton.querySelector("svg")).toHaveClass("lucide", "lucide-pause");
    });

    it("shows Loader2 spinner when isPreviewLoading is true", () => {
      renderCard({ onPlay: vi.fn(), isPreviewLoading: true });
      const playButton = screen.getByTestId("song-play-button");
      expect(playButton.querySelector("svg")).toHaveClass("lucide", "lucide-loader-circle");
    });

    it("calls onPlay with song ID when play area is clicked", () => {
      const onPlay = vi.fn();
      renderCard({ onPlay });
      fireEvent.click(screen.getByTestId("song-play-button"));
      expect(onPlay).toHaveBeenCalledWith("song-1");
    });

    it("has role=button when onPlay is provided", () => {
      renderCard({ onPlay: vi.fn() });
      expect(screen.getByTestId("song-play-button")).toHaveAttribute("role", "button");
    });

    it("does not have role=button when onPlay is not provided", () => {
      renderCard({ onAdd: undefined });
      expect(screen.getByTestId("song-art-placeholder")).not.toHaveAttribute("role");
    });

    it("has aria-label 'Play preview' when not playing", () => {
      renderCard({ onPlay: vi.fn() });
      expect(screen.getByTestId("song-play-button")).toHaveAttribute("aria-label", "Play preview");
    });

    it("has aria-label 'Pause preview' when playing", () => {
      renderCard({ onPlay: vi.fn(), isPlaying: true });
      expect(screen.getByTestId("song-play-button")).toHaveAttribute("aria-label", "Pause preview");
    });

    it("has bg-primary/10 class when playing", () => {
      renderCard({ onPlay: vi.fn(), isPlaying: true });
      expect(screen.getByTestId("song-play-button")).toHaveClass("bg-primary/10");
    });

    it("has cursor-pointer class when onPlay is provided", () => {
      renderCard({ onPlay: vi.fn() });
      expect(screen.getByTestId("song-play-button")).toHaveClass("cursor-pointer");
    });
  });
});
