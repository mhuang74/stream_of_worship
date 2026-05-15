import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SongList, SongListItem } from "@/components/songset/SongList";

// Mock dnd-kit
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

describe("SongList", () => {
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
        durationSeconds: 240,
        tempoBpm: 100,
        musicalKey: "A",
      },
      gapBeats: 2,
      crossfadeEnabled: 1,
      keyShiftSemitones: 0,
      tempoRatio: 1.0,
      markedLineCount: 3,
    },
  ];

  const defaultProps = {
    items: mockItems,
    onReorder: vi.fn(),
    onRemove: vi.fn(),
    onEditTransition: vi.fn(),
    onSelectSong: vi.fn(),
  };

  const renderList = (props = {}) => {
    return render(<SongList {...defaultProps} {...props} />);
  };

  describe("rendering", () => {
    it("renders all songs in the list", () => {
      renderList();
      expect(screen.getByText("Amazing Grace")).toBeInTheDocument();
      expect(screen.getByText("How Great Thou Art")).toBeInTheDocument();
    });

    it("renders song numbers", () => {
      renderList();
      expect(screen.getByText("1")).toBeInTheDocument();
      expect(screen.getByText("2")).toBeInTheDocument();
    });

    it("renders song metadata (composer, duration, key)", () => {
      renderList();
      expect(screen.getByText(/John Newton/)).toBeInTheDocument();
      expect(screen.getByText(/3:00/)).toBeInTheDocument();
      // Key is shown in the metadata row
      expect(screen.getAllByText(/G/).length).toBeGreaterThan(0);
    });

    it("renders marked lines badge when songs have marked lines", () => {
      renderList();
      expect(screen.getByText(/3 marked/)).toBeInTheDocument();
    });

    it("renders empty state when no items", () => {
      renderList({ items: [] });
      expect(screen.getByText(/No songs in this songset/i)).toBeInTheDocument();
      expect(screen.getByText(/Tap the \+ button to add songs/i)).toBeInTheDocument();
    });
  });

  describe("interactions", () => {
    it("calls onRemove when remove button clicked", async () => {
      const onRemove = vi.fn();
      renderList({ onRemove });

      // Find and click the remove button for the first song
      const removeButtons = screen.getAllByRole("button", { name: /remove/i });
      fireEvent.click(removeButtons[0]);

      await waitFor(() => {
        expect(onRemove).toHaveBeenCalledWith("item-1");
      });
    });

    it("calls onSelectSong when song is clicked", async () => {
      const onSelectSong = vi.fn();
      renderList({ onSelectSong });

      // Click on the first song title
      fireEvent.click(screen.getByText("Amazing Grace"));

      await waitFor(() => {
        expect(onSelectSong).toHaveBeenCalledWith("item-1");
      });
    });

    it("calls onEditTransition when transition button clicked", async () => {
      const onEditTransition = vi.fn();
      renderList({ onEditTransition });

      // Find transition buttons (only for non-first songs)
      const transitionButtons = screen.getAllByRole("button", { name: /gap/i });
      fireEvent.click(transitionButtons[0]);

      await waitFor(() => {
        expect(onEditTransition).toHaveBeenCalledWith("item-2");
      });
    });
  });

  describe("drag and drop", () => {
    it("renders drag handles for each item", () => {
      renderList();
      const dragHandles = screen.getAllByRole("button", { name: /drag to reorder/i });
      expect(dragHandles.length).toBe(2);
    });

    it("does not render drag handles in readOnly mode", () => {
      renderList({ readOnly: true });
      const dragHandles = screen.queryAllByRole("button", { name: /drag to reorder/i });
      expect(dragHandles.length).toBe(0);
    });
  });

  describe("readOnly mode", () => {
    it("does not show remove buttons in readOnly mode", () => {
      renderList({ readOnly: true });
      const removeButtons = screen.queryAllByRole("button", { name: /remove/i });
      expect(removeButtons.length).toBe(0);
    });
  });
});
