import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SongsetEditor } from "@/components/songset/SongsetEditor";
import { RenderState } from "@/components/songset/RenderStatusBadge";
import { SongListItem } from "@/components/songset/SongList";

// Mock next/navigation
vi.mock("next/navigation", () => ({
  useRouter: () => ({
    push: vi.fn(),
  }),
  useParams: () => ({ id: "test-songset" }),
}));

vi.mock("@/contexts/AudioPlayerContext", () => ({
  useAudioPlayerContext: () => ({
    currentTrack: null,
    state: { isPlaying: false },
    play: vi.fn(),
  }),
}));

vi.mock("@/lib/r2/public-url", () => ({
  getPublicAudioUrl: vi.fn(() => null),
}));

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
  },
}));

describe("SongsetEditor", () => {
  const mockSongset = {
    id: "songset-1",
    name: "Sunday Worship",
    description: "Easter service songs",
    renderState: "fresh" as RenderState,
    isArtifactsStale: false,
    latestRenderJobId: "job-1",
    lastFailedRenderJobId: null,
    updatedAt: "2024-01-15T10:30:00Z",
  };

  const mockItems: SongListItem[] = [
    {
      id: "item-1",
      songId: "song-1",
      position: 0,
      song: {
        id: "song-1",
        title: "Amazing Grace",
        composer: "John Newton",
        lyricist: null,
        albumName: "Hymns",
        musicalKey: "G",
      },
      recording: {
        contentHash: "abc123",
        hashPrefix: "ab",
        durationSeconds: 180,
        tempoBpm: 120,
        musicalKey: "G",
      },
      gapBeats: 2,
      crossfadeEnabled: 0,
      keyShiftSemitones: 0,
      tempoRatio: 1.0,
    },
    {
      id: "item-2",
      songId: "song-2",
      position: 1,
      song: {
        id: "song-2",
        title: "How Great Thou Art",
        composer: "Stuart Hine",
        lyricist: null,
        albumName: "Hymns",
        musicalKey: "A",
      },
      recording: {
        contentHash: "def456",
        hashPrefix: "de",
        durationSeconds: 240,
        tempoBpm: 100,
        musicalKey: "A",
      },
      gapBeats: 2,
      crossfadeEnabled: 1,
      keyShiftSemitones: 0,
      tempoRatio: 1.0,
      markedLineCount: 2,
    },
  ];

  const defaultProps = {
    songset: mockSongset,
    items: mockItems,
    onUpdateItems: vi.fn().mockResolvedValue(undefined),
    onRemoveItem: vi.fn().mockResolvedValue(undefined),
    onUpdateTransition: vi.fn().mockResolvedValue(undefined),
    onRender: vi.fn(),
    onPlay: vi.fn(),
    onUpdateDescription: vi.fn().mockResolvedValue(undefined),
    onDuplicate: vi.fn().mockResolvedValue(undefined),
    onDelete: vi.fn().mockResolvedValue(undefined),
    onShare: vi.fn(),
    onAddSongs: vi.fn(),
  };

  const renderEditor = (props = {}) => {
    return render(<SongsetEditor {...defaultProps} {...props} />);
  };

  describe("app bar", () => {
    it("renders songset name", () => {
      renderEditor();
      expect(screen.getByText("Sunday Worship")).toBeInTheDocument();
    });

    it("renders song count", () => {
      renderEditor();
      expect(screen.getByText(/2 songs/)).toBeInTheDocument();
    });

    it("has back button", () => {
      renderEditor();
      expect(screen.getByRole("button", { name: /go back/i })).toBeInTheDocument();
    });

    it("has render status badge", () => {
      renderEditor();
      expect(screen.getByText("Rendered")).toBeInTheDocument();
    });

    it("has overflow menu", () => {
      renderEditor();
      expect(screen.getByRole("button", { name: /more options/i })).toBeInTheDocument();
    });
  });

  describe("overflow menu", () => {
    it("opens menu when clicked", async () => {
      renderEditor();
      fireEvent.click(screen.getByRole("button", { name: /more options/i }));

      await waitFor(() => {
        expect(screen.getByRole("menuitem", { name: /render/i })).toBeInTheDocument();
        expect(screen.getByRole("menuitem", { name: /play/i })).toBeInTheDocument();
        expect(screen.getByRole("menuitem", { name: /edit description/i })).toBeInTheDocument();
        expect(screen.getByRole("menuitem", { name: /duplicate/i })).toBeInTheDocument();
        expect(screen.getByRole("menuitem", { name: /share/i })).toBeInTheDocument();
        expect(screen.getByRole("menuitem", { name: /delete/i })).toBeInTheDocument();
      });
    });

    it("calls onRender when render menu item clicked", async () => {
      const onRender = vi.fn();
      renderEditor({ onRender });

      fireEvent.click(screen.getByRole("button", { name: /more options/i }));
      await waitFor(() => {
        fireEvent.click(screen.getByRole("menuitem", { name: /^render$/i }));
      });

      expect(onRender).toHaveBeenCalled();
    });

    it("calls onPlay when play menu item clicked", async () => {
      const onPlay = vi.fn();
      renderEditor({ onPlay });

      fireEvent.click(screen.getByRole("button", { name: /more options/i }));
      await waitFor(() => {
        fireEvent.click(screen.getByRole("menuitem", { name: /^play$/i }));
      });

      expect(onPlay).toHaveBeenCalled();
    });

    it("opens edit description dialog when clicked", async () => {
      renderEditor();

      fireEvent.click(screen.getByRole("button", { name: /more options/i }));
      await waitFor(() => {
        fireEvent.click(screen.getByRole("menuitem", { name: /edit description/i }));
      });

      await waitFor(() => {
        expect(screen.getByRole("dialog")).toBeInTheDocument();
        expect(screen.getByText(/Edit Description/i)).toBeInTheDocument();
      });
    });

    it("calls onDuplicate when duplicate menu item clicked", async () => {
      const onDuplicate = vi.fn().mockResolvedValue(undefined);
      renderEditor({ onDuplicate });

      fireEvent.click(screen.getByRole("button", { name: /more options/i }));
      await waitFor(() => {
        fireEvent.click(screen.getByRole("menuitem", { name: /duplicate/i }));
      });

      await waitFor(() => {
        expect(onDuplicate).toHaveBeenCalled();
      });
    });

    it("calls onShare when share menu item clicked", async () => {
      const onShare = vi.fn();
      renderEditor({ onShare });

      fireEvent.click(screen.getByRole("button", { name: /more options/i }));
      await waitFor(() => {
        fireEvent.click(screen.getByRole("menuitem", { name: /share/i }));
      });

      expect(onShare).toHaveBeenCalled();
    });

    it("opens delete confirmation dialog when delete clicked", async () => {
      renderEditor();

      fireEvent.click(screen.getByRole("button", { name: /more options/i }));
      await waitFor(() => {
        fireEvent.click(screen.getByRole("menuitem", { name: /delete/i }));
      });

      await waitFor(() => {
        expect(screen.getByRole("dialog")).toBeInTheDocument();
        expect(screen.getByText(/Delete Songset/i)).toBeInTheDocument();
      });
    });
  });

  describe("stale banner", () => {
    it("renders stale banner when isArtifactsStale is true", () => {
      renderEditor({
        songset: { ...mockSongset, isArtifactsStale: true },
      });
      expect(screen.getByText(/Artifacts out of date/i)).toBeInTheDocument();
    });

    it("does not render stale banner when isArtifactsStale is false", () => {
      renderEditor();
      expect(screen.queryByText(/Artifacts out of date/i)).not.toBeInTheDocument();
    });

    it("has re-render button in stale banner", () => {
      const onRender = vi.fn();
      renderEditor({
        songset: { ...mockSongset, isArtifactsStale: true },
        onRender,
      });
      expect(screen.getByRole("button", { name: /re-render/i })).toBeInTheDocument();
    });

    it("has play anyway button in stale banner", () => {
      const onPlay = vi.fn();
      renderEditor({
        songset: { ...mockSongset, isArtifactsStale: true },
        onPlay,
      });
      expect(screen.getByRole("button", { name: /play anyway/i })).toBeInTheDocument();
    });

    it("has dismiss button in stale banner", () => {
      renderEditor({
        songset: { ...mockSongset, isArtifactsStale: true },
      });
      expect(screen.getByRole("button", { name: /dismiss/i })).toBeInTheDocument();
    });

    it("dismisses banner when dismiss button clicked", async () => {
      renderEditor({
        songset: { ...mockSongset, isArtifactsStale: true },
      });

      fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));

      await waitFor(() => {
        expect(screen.queryByText(/Artifacts out of date/i)).not.toBeInTheDocument();
      });
    });
  });

  describe("marked lines badge", () => {
    it("renders marked lines badge when there are marked lines", () => {
      renderEditor();
      expect(screen.getByText(/2 marked lines/i)).toBeInTheDocument();
    });

    it("shows desktop nudge text", () => {
      renderEditor();
      expect(screen.getByText(/Open on desktop for text edit/i)).toBeInTheDocument();
    });

    it("does not render marked lines badge when no marked lines", () => {
      renderEditor({
        items: mockItems.map((item) => ({ ...item, markedLineCount: 0 })),
      });
      expect(screen.queryByText(/marked lines/i)).not.toBeInTheDocument();
    });
  });

  describe("song list", () => {
    it("renders all songs", () => {
      renderEditor();
      expect(screen.getByText("Amazing Grace")).toBeInTheDocument();
      expect(screen.getByText("How Great Thou Art")).toBeInTheDocument();
    });

    it("renders description when present", () => {
      renderEditor();
      expect(screen.getByText("Easter service songs")).toBeInTheDocument();
    });
  });

  describe("FAB", () => {
    it("renders add songs FAB", () => {
      renderEditor();
      expect(screen.getByRole("button", { name: /add songs/i })).toBeInTheDocument();
    });

    it("calls onAddSongs when FAB clicked", async () => {
      const onAddSongs = vi.fn();
      renderEditor({ onAddSongs });

      fireEvent.click(screen.getByRole("button", { name: /add songs/i }));

      expect(onAddSongs).toHaveBeenCalled();
    });
  });

  describe("callbacks", () => {
    it("calls onRender when render menu item clicked (unrendered)", async () => {
      const onRender = vi.fn();
      renderEditor({
        songset: { ...mockSongset, renderState: "unrendered" as RenderState },
        onRender,
      });

      fireEvent.click(screen.getByRole("button", { name: /more options/i }));
      await waitFor(() => {
        fireEvent.click(screen.getByRole("menuitem", { name: /^render$/i }));
      });
      expect(onRender).toHaveBeenCalled();
    });

    it("calls onPlay when play menu item clicked (fresh)", async () => {
      const onPlay = vi.fn();
      renderEditor({ onPlay });

      fireEvent.click(screen.getByRole("button", { name: /more options/i }));
      await waitFor(() => {
        fireEvent.click(screen.getByRole("menuitem", { name: /^play$/i }));
      });
      expect(onPlay).toHaveBeenCalled();
    });

    it("calls onRender when render menu item clicked (failed)", async () => {
      const onRender = vi.fn();
      renderEditor({
        songset: { ...mockSongset, renderState: "failed" as RenderState },
        onRender,
      });

      fireEvent.click(screen.getByRole("button", { name: /more options/i }));
      await waitFor(() => {
        fireEvent.click(screen.getByRole("menuitem", { name: /^render$/i }));
      });
      expect(onRender).toHaveBeenCalled();
    });
  });
});
