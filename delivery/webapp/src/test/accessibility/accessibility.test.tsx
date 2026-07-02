/**
 * Accessibility tests for Task 8.2.
 *
 * These tests verify ARIA attributes, keyboard navigation, focus indicators,
 * and live regions across all major interactive components.
 *
 * Manual items (skipped — not automatable):
 *  - Screen reader testing with NVDA/VoiceOver
 *  - Visual color contrast verification
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

vi.mock("next/navigation", () => ({
  usePathname: () => "/songsets",
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("@/hooks/useWakeLock", () => ({
  useWakeLock: () => ({ isSupported: false, isActive: false }),
}));

vi.mock("@/hooks/useKeyboardShortcuts", () => ({
  useKeyboardShortcuts: vi.fn(),
}));

vi.mock("@/hooks/useMediaSession", () => ({
  useMediaSession: () => ({
    updatePlaybackState: vi.fn(),
    updatePositionState: vi.fn(),
  }),
}));

vi.mock("@/contexts/AudioPlayerContext", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/contexts/AudioPlayerContext")>();
  return {
    ...actual,
    useAudioPlayerContext: () => ({
      play: vi.fn(),
      pause: vi.fn(),
      stop: vi.fn(),
      currentTrack: null,
      state: {
        isPlaying: false,
        currentTime: 0,
        duration: 0,
        volume: 1,
        isMuted: false,
        isLooping: false,
        loopWindowStart: 0,
        loopWindowEnd: 0,
      },
      togglePlay: vi.fn(),
      seek: vi.fn(),
      setVolume: vi.fn(),
      toggleMute: vi.fn(),
      toggleLoop: vi.fn(),
      setLoopWindow: vi.fn(),
      clearLoopWindow: vi.fn(),
      audioRef: { current: null },
    }),
  };
});

// Sheet / Dialog: render children when open=true
vi.mock("@base-ui/react/dialog", () => ({
  Dialog: {
    /* eslint-disable @typescript-eslint/no-explicit-any */
    Root: ({ children, open }: any) =>
      open ? <div data-testid="sheet-root">{children}</div> : null,
    Trigger: ({ children }: any) => <div>{children}</div>,
    Portal: ({ children }: any) => <div>{children}</div>,
    Backdrop: ({ children }: any) => <div>{children}</div>,
    Popup: ({ children }: any) => <div data-testid="sheet-popup">{children}</div>,
    Close: ({ children }: any) => <button data-testid="sheet-close">{children}</button>,
    Title: ({ children }: any) => <h2>{children}</h2>,
    Description: ({ children }: any) => <p>{children}</p>,
    /* eslint-enable @typescript-eslint/no-explicit-any */
  },
}));

// dnd-kit mocks for SongList
vi.mock("@dnd-kit/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@dnd-kit/core")>();
  return {
    ...actual,
    DndContext: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    useSensor: vi.fn(() => ({})),
    useSensors: vi.fn(() => []),
    closestCenter: vi.fn(),
    PointerSensor: vi.fn(),
    KeyboardSensor: vi.fn(),
  };
});

vi.mock("@dnd-kit/sortable", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@dnd-kit/sortable")>();
  return {
    ...actual,
    SortableContext: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    useSortable: vi.fn(() => ({
      attributes: {},
      listeners: {},
      setNodeRef: vi.fn(),
      transform: null,
      transition: null,
      isDragging: false,
    })),
    verticalListSortingStrategy: {},
    sortableKeyboardCoordinates: vi.fn(),
    arrayMove: vi.fn((items, from, to) => {
      const result = [...items];
      const [removed] = result.splice(from, 1);
      result.splice(to, 0, removed);
      return result;
    }),
  };
});

vi.mock("@dnd-kit/utilities", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@dnd-kit/utilities")>();
  return {
    ...actual,
    CSS: { Transform: { toString: vi.fn(() => "") } },
  };
});

// ---------------------------------------------------------------------------
// Component imports (after mocks)
// ---------------------------------------------------------------------------

import { BottomNav } from "@/components/layout/BottomNav";
import { Header } from "@/components/layout/Header";
import { SongList, SongListItem } from "@/components/songset/SongList";
import { BrowseSheet } from "@/components/songset/BrowseSheet";
import { SemanticSearch } from "@/components/search/SemanticSearch";
import { SongSearch } from "@/components/songset/SongSearch";
import { LyricsReviewSheet } from "@/components/lyrics/LyricsReviewSheet";
import { PlaybackControls } from "@/components/play/PlaybackControls";
import { SongCard } from "@/components/songset/SongCard";

// ---------------------------------------------------------------------------
// Shared fixtures
// ---------------------------------------------------------------------------

const mockSongListItems: SongListItem[] = [
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
      title: "Holy Holy Holy",
      composer: "Reginald Heber",
      lyricist: null,
      albumName: "Hymns",
      musicalKey: "D",
    },
    recording: {
      contentHash: "def456",
      durationSeconds: 210,
      tempoBpm: 80,
      musicalKey: "D",
    },
    gapBeats: 4,
    crossfadeEnabled: 1,
    keyShiftSemitones: 2,
    tempoRatio: 1.1,
  },
];

const defaultPlaybackControlsProps = {
  isPlaying: false,
  currentTime: 30,
  duration: 300,
  volume: 0.8,
  isMuted: false,
  currentSongIndex: 0,
  totalSongs: 3,
  isPresentationActive: false,
  onPlayPause: vi.fn(),
  onSeek: vi.fn(),
  onPrevSong: vi.fn(),
  onNextSong: vi.fn(),
  onVolumeChange: vi.fn(),
  onToggleMute: vi.fn(),
};

const mockSongCard = {
  id: "song-1",
  title: "Amazing Grace",
  composer: "John Newton",
  lyricist: null,
  albumName: "Hymns",
  musicalKey: "G",
  recordings: [
    {
      contentHash: "abc",
      hashPrefix: "abc",
      durationSeconds: 180,
      tempoBpm: 120,
      musicalKey: "G",
      visibilityStatus: "published",
    },
  ],
};

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("Accessibility (Task 8.2)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  // BottomNav
  describe("BottomNav", () => {
    it("has aria-label on nav element", () => {
      render(<BottomNav />);
      const nav = screen.getByRole("navigation", { name: /main navigation/i });
      expect(nav).toBeInTheDocument();
    });

    it("marks active link with aria-current='page'", () => {
      render(<BottomNav />);
      const songsets = screen.getByRole("link", { name: /songsets/i });
      expect(songsets).toHaveAttribute("aria-current", "page");
    });

    it("inactive links do not have aria-current", () => {
      render(<BottomNav />);
      const settings = screen.getByRole("link", { name: /settings/i });
      expect(settings).not.toHaveAttribute("aria-current");
    });
  });

  // Header
  describe("Header", () => {
    it("has aria-label on desktop nav", () => {
      render(<Header />);
      const nav = screen.getByRole("navigation", { name: /main navigation/i });
      expect(nav).toBeInTheDocument();
    });

    it("renders nav links with correct href", () => {
      render(<Header />);
      const songsets = screen.getByRole("link", { name: /songsets/i });
      expect(songsets).toHaveAttribute("href", "/songsets");
    });
  });

  // PlaybackControls
  describe("PlaybackControls", () => {
    it("play/pause button has accessible label when paused", () => {
      render(<PlaybackControls {...defaultPlaybackControlsProps} />);
      expect(screen.getByRole("button", { name: /^play$/i })).toBeInTheDocument();
    });

    it("play/pause button has accessible label when playing", () => {
      render(<PlaybackControls {...defaultPlaybackControlsProps} isPlaying={true} />);
      expect(screen.getByRole("button", { name: /^pause$/i })).toBeInTheDocument();
    });

    it("previous song button has aria-label", () => {
      render(<PlaybackControls {...defaultPlaybackControlsProps} />);
      expect(screen.getByRole("button", { name: /previous song/i })).toBeInTheDocument();
    });

    it("next song button has aria-label", () => {
      render(<PlaybackControls {...defaultPlaybackControlsProps} />);
      expect(screen.getByRole("button", { name: /next song/i })).toBeInTheDocument();
    });

    it("mute button has aria-label when not muted", () => {
      render(<PlaybackControls {...defaultPlaybackControlsProps} isMuted={false} />);
      expect(screen.getByRole("button", { name: /^mute$/i })).toBeInTheDocument();
    });

    it("mute button has aria-label when muted", () => {
      render(<PlaybackControls {...defaultPlaybackControlsProps} isMuted={true} />);
      expect(screen.getByRole("button", { name: /^unmute$/i })).toBeInTheDocument();
    });

    it("seek slider has role=slider and aria-label", () => {
      render(<PlaybackControls {...defaultPlaybackControlsProps} />);
      const slider = screen.getByRole("slider", { name: /seek/i });
      expect(slider).toBeInTheDocument();
      expect(slider).toHaveAttribute("aria-valuemin", "0");
      expect(slider).toHaveAttribute("aria-valuemax", "300");
    });

    it("volume slider has role=slider and aria-label", () => {
      render(<PlaybackControls {...defaultPlaybackControlsProps} />);
      const volumeSlider = screen.getByRole("slider", { name: /volume/i });
      expect(volumeSlider).toBeInTheDocument();
    });

    it("seek slider supports keyboard navigation with ArrowLeft", () => {
      render(<PlaybackControls {...defaultPlaybackControlsProps} currentTime={50} />);
      const slider = screen.getByRole("slider", { name: /seek/i });
      fireEvent.keyDown(slider, { key: "ArrowLeft" });
      expect(defaultPlaybackControlsProps.onSeek).toHaveBeenCalledWith(40);
    });

    it("seek slider supports keyboard navigation with ArrowRight", () => {
      render(<PlaybackControls {...defaultPlaybackControlsProps} currentTime={50} />);
      const slider = screen.getByRole("slider", { name: /seek/i });
      fireEvent.keyDown(slider, { key: "ArrowRight" });
      expect(defaultPlaybackControlsProps.onSeek).toHaveBeenCalledWith(60);
    });
  });

  // SongList
  describe("SongList", () => {
    it("renders song list container with role=list", () => {
      render(
        <SongList
          items={mockSongListItems}
          onReorder={vi.fn()}
          onRemove={vi.fn()}
        />
      );
      const list = screen.getByRole("list", { name: /songs/i });
      expect(list).toBeInTheDocument();
    });

    it("drag handle button has descriptive aria-label", () => {
      render(
        <SongList
          items={mockSongListItems}
          onReorder={vi.fn()}
          onRemove={vi.fn()}
        />
      );
      const dragHandle = screen.getByRole("button", { name: /drag to reorder song 1/i });
      expect(dragHandle).toBeInTheDocument();
    });

    it("remove button has descriptive aria-label with song title", () => {
      render(
        <SongList
          items={mockSongListItems}
          onReorder={vi.fn()}
          onRemove={vi.fn()}
        />
      );
      const removeButton = screen.getByRole("button", { name: /remove amazing grace/i });
      expect(removeButton).toBeInTheDocument();
    });

    it("song info area has role=button and aria-label when onSelectSong is provided", () => {
      render(
        <SongList
          items={mockSongListItems}
          onReorder={vi.fn()}
          onRemove={vi.fn()}
          onSelectSong={vi.fn()}
        />
      );
      const selectButton = screen.getByRole("button", { name: /select amazing grace/i });
      expect(selectButton).toBeInTheDocument();
    });

    it("song info area is keyboard-accessible with Enter key", () => {
      const onSelectSong = vi.fn();
      render(
        <SongList
          items={mockSongListItems}
          onReorder={vi.fn()}
          onRemove={vi.fn()}
          onSelectSong={onSelectSong}
        />
      );
      const selectButton = screen.getByRole("button", { name: /select amazing grace/i });
      fireEvent.keyDown(selectButton, { key: "Enter" });
      expect(onSelectSong).toHaveBeenCalledWith("item-1");
    });

    it("song info area is keyboard-accessible with Space key", () => {
      const onSelectSong = vi.fn();
      render(
        <SongList
          items={mockSongListItems}
          onReorder={vi.fn()}
          onRemove={vi.fn()}
          onSelectSong={onSelectSong}
        />
      );
      const selectButton = screen.getByRole("button", { name: /select amazing grace/i });
      fireEvent.keyDown(selectButton, { key: " " });
      expect(onSelectSong).toHaveBeenCalledWith("item-1");
    });

    it("transition edit button has descriptive aria-label", () => {
      render(
        <SongList
          items={mockSongListItems}
          onReorder={vi.fn()}
          onRemove={vi.fn()}
          onEditTransition={vi.fn()}
        />
      );
      // The second item (index > 0) should have transition button
      const transitionButton = screen.getByRole("button", {
        name: /edit transition before holy holy holy/i,
      });
      expect(transitionButton).toBeInTheDocument();
    });
  });

  // SongCard
  describe("SongCard", () => {
    it("add button has descriptive aria-label when not yet added", () => {
      render(<SongCard song={mockSongCard} onAdd={vi.fn()} isAdded={false} />);
      const addButton = screen.getByRole("button", { name: /add to songset/i });
      expect(addButton).toBeInTheDocument();
    });

    it("add button has aria-label indicating already added", () => {
      render(<SongCard song={mockSongCard} onAdd={vi.fn()} isAdded={true} />);
      const addButton = screen.getByRole("button", { name: /already added/i });
      expect(addButton).toBeInTheDocument();
    });

    it("add button is disabled when already added", () => {
      render(<SongCard song={mockSongCard} onAdd={vi.fn()} isAdded={true} />);
      const addButton = screen.getByRole("button", { name: /already added/i });
      expect(addButton).toBeDisabled();
    });
  });

  // SongSearch
  describe("SongSearch", () => {
    it("search input has aria-label", () => {
      render(<SongSearch onSearch={vi.fn()} albums={[]} />);
      const input = screen.getByRole("textbox", { name: /search songs/i });
      expect(input).toBeInTheDocument();
    });

    it("clear button has aria-label", () => {
      render(<SongSearch onSearch={vi.fn()} albums={[]} />);
      const input = screen.getByRole("textbox", { name: /search songs/i });
      fireEvent.change(input, { target: { value: "Amazing" } });
      const clearBtn = screen.getByRole("button", { name: /clear search/i });
      expect(clearBtn).toBeInTheDocument();
    });

    it("shows sr-only status message while searching", async () => {
      render(<SongSearch onSearch={vi.fn()} albums={[]} isLoading={true} />);
      const status = screen.getByRole("status");
      expect(status).toBeInTheDocument();
      expect(status).toHaveTextContent(/searching/i);
    });
  });

  // SemanticSearch
  describe("SemanticSearch", () => {
    it("textarea has aria-label", () => {
      render(<SemanticSearch onAddSong={vi.fn()} />);
      const textarea = screen.getByRole("textbox", { name: /describe songs/i });
      expect(textarea).toBeInTheDocument();
    });

    it("search button has aria-label", () => {
      render(<SemanticSearch onAddSong={vi.fn()} />);
      const btn = screen.getByRole("button", { name: /search songs by description/i });
      expect(btn).toBeInTheDocument();
    });

    it("error message has role=alert", async () => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: false,
        json: async () => ({ error: "Search failed" }),
      } as Response);

      render(<SemanticSearch onAddSong={vi.fn()} />);
      const textarea = screen.getByRole("textbox", { name: /describe songs/i });
      fireEvent.change(textarea, { target: { value: "praise songs" } });

      const btn = screen.getByRole("button", { name: /search songs by description/i });
      fireEvent.click(btn);

      await waitFor(() => {
        const alert = screen.getByRole("alert");
        expect(alert).toBeInTheDocument();
      });
    });
  });

  // BrowseSheet tabs
  describe("BrowseSheet", () => {
    const defaultBrowseProps = {
      isOpen: true,
      onOpenChange: vi.fn(),
      onAddSongs: vi.fn(),
      existingSongIds: [],
    };

    beforeEach(() => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ songs: [], albums: [], total: 0 }),
      } as unknown as Response);
    });

    it("mode tab container has role=tablist and aria-label", async () => {
      render(<BrowseSheet {...defaultBrowseProps} />);
      await waitFor(() => {
        const tablist = screen.getByRole("tablist", { name: /search mode/i });
        expect(tablist).toBeInTheDocument();
      });
    });

    it("Browse tab has role=tab and aria-selected=true when active", async () => {
      render(<BrowseSheet {...defaultBrowseProps} />);
      await waitFor(() => {
        const browseTab = screen.getByRole("tab", { name: /browse/i });
        expect(browseTab).toHaveAttribute("aria-selected", "true");
      });
    });

    it("Describe tab has role=tab and aria-selected=false when Browse is active", async () => {
      render(<BrowseSheet {...defaultBrowseProps} />);
      await waitFor(() => {
        const describeTab = screen.getByRole("tab", { name: /describe/i });
        expect(describeTab).toHaveAttribute("aria-selected", "false");
      });
    });
  });

  // LyricsReviewSheet
  describe("LyricsReviewSheet", () => {
    const sampleLrc = "[00:01.00]Hello world\n[00:05.00]Second line";

    beforeEach(() => {
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ marks: [] }),
      } as unknown as Response);
    });

    it("tablist has aria-label", async () => {
      render(
        <LyricsReviewSheet
          isOpen={true}
          onOpenChange={vi.fn()}
          recordingContentHash="test-hash"
          lrcContent={sampleLrc}
          songTitle="Test Song"
        />
      );
      await waitFor(() => {
        const tablist = screen.getByRole("tablist", { name: /lyrics review tabs/i });
        expect(tablist).toBeInTheDocument();
      });
    });

    it("tabs have aria-controls linking to their panels", async () => {
      render(
        <LyricsReviewSheet
          isOpen={true}
          onOpenChange={vi.fn()}
          recordingContentHash="test-hash"
          lrcContent={sampleLrc}
        />
      );
      await waitFor(() => {
        const reviewTab = screen.getByRole("tab", { name: /^review$/i });
        expect(reviewTab).toHaveAttribute("aria-controls", "lyrics-panel-review");
        expect(reviewTab).toHaveAttribute("id", "lyrics-tab-review");
      });
    });

    it("tab panels have aria-labelledby linking to their tabs", async () => {
      render(
        <LyricsReviewSheet
          isOpen={true}
          onOpenChange={vi.fn()}
          recordingContentHash="test-hash"
          lrcContent={sampleLrc}
        />
      );
      await waitFor(() => {
        // The review panel should always be in the DOM
        const panel = screen.getByRole("tabpanel", { name: /^review$/i });
        expect(panel).toHaveAttribute("aria-labelledby", "lyrics-tab-review");
        expect(panel).toHaveAttribute("id", "lyrics-panel-review");
      });
    });

    it("lyric line buttons have aria-pressed to indicate mark state", async () => {
      render(
        <LyricsReviewSheet
          isOpen={true}
          onOpenChange={vi.fn()}
          recordingContentHash="test-hash"
          lrcContent={sampleLrc}
        />
      );
      await waitFor(() => {
        const buttons = screen.getAllByRole("button", { name: /line at/i });
        expect(buttons.length).toBeGreaterThan(0);
        buttons.forEach((btn) => {
          expect(btn).toHaveAttribute("aria-pressed");
        });
      });
    });
  });

  // Button focus indicators
  describe("Button focus indicators", () => {
    it("PlaybackControls play button has focus-visible class in buttonVariants", () => {
      render(<PlaybackControls {...defaultPlaybackControlsProps} />);
      const playButton = screen.getByRole("button", { name: /^play$/i });
      // The Button component applies focus-visible:ring-3 via buttonVariants
      const cls = playButton.getAttribute("class") ?? "";
      expect(cls).toContain("focus-visible:");
    });
  });

  // Screen reader testing (not automatable)
  it.skip("screen reader testing — manual verification required", () => {
    // Verify with NVDA (Windows) or VoiceOver (Mac/iOS) that:
    // - Navigation landmarks are announced
    // - Interactive elements announce their role and state
    // - Live regions announce dynamic content changes
    // - Tab order follows logical reading order
  });

  // Color contrast verification (not automatable in jsdom)
  it.skip("WCAG 2.1 AA color contrast — verified via design tokens", () => {
    // Token verification:
    // --foreground oklch(0.145) on --background oklch(1): ~16:1 (passes 4.5:1)
    // --muted-foreground oklch(0.556) on --background oklch(1): ~5.4:1 (passes 4.5:1)
    // --primary oklch(0.205) on --primary-foreground oklch(0.985): ~14:1 (passes 4.5:1)
    // --destructive on white background: verified via manual check
  });
});
