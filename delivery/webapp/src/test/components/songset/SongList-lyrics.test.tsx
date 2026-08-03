import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SongList, SongListItem } from "@/components/songset/SongList";

/* eslint-disable @typescript-eslint/no-explicit-any */

// Mock dnd-kit
vi.mock("@dnd-kit/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@dnd-kit/core")>();
  return {
    ...actual,
    DndContext: ({ children, onDragStart }: any) => (
      <div data-testid="dnd-context" data-ondragstart={!!onDragStart}>{children}</div>
    ),
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

// Mock useSongLyrics
const mockUseSongLyrics = vi.fn();
vi.mock("@/hooks/useSongLyrics", () => ({
  useSongLyrics: (...args: unknown[]) => mockUseSongLyrics(...args),
  clearLyricsCache: vi.fn(),
}));

// jsdom doesn't implement scrollIntoView
Element.prototype.scrollIntoView = vi.fn();

describe("SongList — lyrics expansion", () => {
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

  beforeEach(() => {
    vi.clearAllMocks();
    mockUseSongLyrics.mockReturnValue({
      lrcContent: null,
      lines: null,
      loading: false,
      error: null,
    });
  });

  const defaultProps = {
    items: mockItems,
    onReorder: vi.fn(),
    onRemove: vi.fn(),
    onEditTransition: vi.fn(),
  };

  const renderList = (props = {}) => {
    return render(<SongList {...defaultProps} {...props} />);
  };

  it("(a) click chevron → lyrics panel appears", () => {
    renderList();

    const chevron = screen.getAllByRole("button", { name: /expand lyrics/i })[0];
    fireEvent.click(chevron);

    expect(screen.getByRole("region")).toBeInTheDocument();
  });

  it("(b) click another chevron → first collapses, second expands (accordion)", () => {
    renderList();

    const chevrons = screen.getAllByRole("button", { name: /expand lyrics/i });

    fireEvent.click(chevrons[0]);
    expect(screen.getAllByRole("region").length).toBe(1);

    fireEvent.click(chevrons[1]);
    expect(screen.getAllByRole("region").length).toBe(1);
  });

  it("(c) verify LRC parsing → timestamped display", async () => {
    mockUseSongLyrics.mockReturnValue({
      lrcContent: "[00:12.34]赞美耶和华\n[00:15.00]Second line",
      lines: null,
      loading: false,
      error: null,
    });

    renderList();

    const chevron = screen.getAllByRole("button", { name: /expand lyrics/i })[0];
    fireEvent.click(chevron);

    await waitFor(() => {
      expect(screen.getByText("赞美耶和华")).toBeInTheDocument();
      expect(screen.getByText("Second line")).toBeInTheDocument();
    });
  });

  it("(e) item.recording === null → distinct 'recording missing' message without hook invocation", () => {
    const itemsWithNullRecording: SongListItem[] = [
      {
        ...mockItems[0],
        recording: null,
      },
    ];

    renderList({ items: itemsWithNullRecording });

    const chevron = screen.getAllByRole("button", { name: /expand lyrics/i })[0];
    fireEvent.click(chevron);

    expect(screen.getByText(/recording missing/i)).toBeInTheDocument();
    // Hook is called with undefined (rules of hooks), but no fetch occurs
    expect(mockUseSongLyrics).toHaveBeenCalledWith(undefined);
  });

  it("shows loading state", () => {
    mockUseSongLyrics.mockReturnValue({
      lrcContent: null,
      lines: null,
      loading: true,
      error: null,
    });

    renderList();

    const chevron = screen.getAllByRole("button", { name: /expand lyrics/i })[0];
    fireEvent.click(chevron);

    expect(screen.getByText(/loading lyrics/i)).toBeInTheDocument();
  });

  it("shows error state", () => {
    mockUseSongLyrics.mockReturnValue({
      lrcContent: null,
      lines: null,
      loading: false,
      error: "Network error",
    });

    renderList();

    const chevron = screen.getAllByRole("button", { name: /expand lyrics/i })[0];
    fireEvent.click(chevron);

    expect(screen.getByText(/lyrics unavailable/i)).toBeInTheDocument();
  });

  it("shows plain text lines from lyricsLines", () => {
    mockUseSongLyrics.mockReturnValue({
      lrcContent: null,
      lines: ["Line one", "Line two"],
      loading: false,
      error: null,
    });

    renderList();

    const chevron = screen.getAllByRole("button", { name: /expand lyrics/i })[0];
    fireEvent.click(chevron);

    const pre = document.querySelector("pre");
    expect(pre).toBeInTheDocument();
    expect(pre?.textContent).toContain("Line one");
    expect(pre?.textContent).toContain("Line two");
  });

  it("shows 'No lyrics available' when both null", () => {
    mockUseSongLyrics.mockReturnValue({
      lrcContent: null,
      lines: null,
      loading: false,
      error: null,
    });

    renderList();

    const chevron = screen.getAllByRole("button", { name: /expand lyrics/i })[0];
    fireEvent.click(chevron);

    expect(screen.getByText(/no lyrics available for this recording/i)).toBeInTheDocument();
  });

  it("chevron has aria-expanded attribute", () => {
    renderList();

    const chevron = screen.getAllByRole("button", { name: /expand lyrics/i })[0];
    expect(chevron).toHaveAttribute("aria-expanded", "false");

    fireEvent.click(chevron);
    expect(chevron).toHaveAttribute("aria-expanded", "true");
  });
});
