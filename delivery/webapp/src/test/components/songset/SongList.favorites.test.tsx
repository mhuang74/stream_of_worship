import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SongList, SongListItem } from "@/components/songset/SongList";
import {
  markSongCompleted,
  resetCompletionForTests,
} from "@/lib/audio/completion";

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
    CSS: {
      Transform: {
        toString: vi.fn(() => ""),
      },
    },
  };
});

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

vi.mock("@/hooks/useSongLyrics", () => ({
  useSongLyrics: () => ({
    lrcContent: null,
    lines: null,
    loading: false,
    error: null,
  }),
  clearLyricsCache: vi.fn(),
}));

Element.prototype.scrollIntoView = vi.fn();

describe("SongList favorites", () => {
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
    },
  ];

  const defaultProps = {
    items: mockItems,
    onReorder: vi.fn(),
    onRemove: vi.fn(),
    onEditTransition: vi.fn(),
  };

  it("shows a filled heart on a favorited song in readOnly mode", () => {
    resetCompletionForTests();
    render(
      <SongList
        {...defaultProps}
        readOnly
        favoriteIds={new Set(["song-1"])}
      />
    );
    const button = screen.getByTestId("favorite-button");
    expect(button).toHaveAttribute("data-favorite", "true");
    expect(button.querySelector("svg")).toHaveClass("fill-current");
  });

  it("calls onToggleFavorite when the favorite button is clicked", () => {
    resetCompletionForTests();
    markSongCompleted("song-1");
    const onToggleFavorite = vi.fn();
    render(
      <SongList
        {...defaultProps}
        favoriteIds={new Set([])}
        onToggleFavorite={onToggleFavorite}
      />
    );
    const buttons = screen.getAllByTestId("favorite-button");
    const button = buttons.find((b) => !b.hasAttribute("disabled"));
    expect(button).toBeTruthy();
    fireEvent.click(button!);
    expect(onToggleFavorite).toHaveBeenCalledWith("song-1");
  });

  it("renders no favorite button for a non-favorited song in readOnly mode", () => {
    resetCompletionForTests();
    render(
      <SongList
        {...defaultProps}
        readOnly
        favoriteIds={new Set([])}
      />
    );
    expect(screen.queryByTestId("favorite-button")).not.toBeInTheDocument();
  });
});
