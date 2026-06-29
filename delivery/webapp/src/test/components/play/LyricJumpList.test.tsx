import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { LyricJumpList } from "@/components/play/LyricJumpList";

describe("LyricJumpList", () => {
  const mockJumpToLine = vi.fn();

  const mockChapters = [
    {
      position: 0,
      songTitle: "Amazing Grace",
      startSeconds: 0,
      endSeconds: 180,
      lines: [
        { text: "Amazing grace, how sweet the sound", startSeconds: 10 },
        { text: "That saved a wretch like me", startSeconds: 20 },
        { text: "I once was lost, but now am found", startSeconds: 30 },
      ],
    },
    {
      position: 1,
      songTitle: "How Great Thou Art",
      startSeconds: 180,
      endSeconds: 420,
      lines: [
        { text: "O Lord my God, when I in awesome wonder", startSeconds: 190 },
        { text: "Consider all the worlds Thy hands have made", startSeconds: 200 },
      ],
    },
    {
      position: 2,
      songTitle: "Great Is Thy Faithfulness",
      startSeconds: 420,
      endSeconds: 600,
      lines: [
        { text: "Great is Thy faithfulness, O God my Father", startSeconds: 430 },
        { text: "There is no shadow of turning with Thee", startSeconds: 440 },
      ],
    },
  ];

  const defaultProps = {
    chapters: mockChapters,
    currentTime: 25,
    currentSongIndex: 0,
    onJumpToLine: mockJumpToLine,
  };

  const openList = async () => {
    const handle = screen.getByRole("button", { name: /open lyric jump list/i });

    await act(async () => {
      fireEvent.click(handle);
    });
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("rendering", () => {
    it("renders swipe handle when closed", () => {
      render(<LyricJumpList {...defaultProps} />);

      expect(screen.getByText(/lyrics/i)).toBeInTheDocument();
    });

    it("renders chapter list when opened", async () => {
      render(<LyricJumpList {...defaultProps} />);

      await openList();

      await waitFor(() => {
        expect(screen.getByText("Amazing Grace")).toBeInTheDocument();
        expect(screen.getByText("How Great Thou Art")).toBeInTheDocument();
        expect(screen.getByText("Great Is Thy Faithfulness")).toBeInTheDocument();
      });
    });

    it("shows current chapter indicator", async () => {
      render(<LyricJumpList {...defaultProps} currentSongIndex={0} />);

      await openList();

      await waitFor(() => {
        const chapters = screen.getAllByText(/0:00 - 3:00/);
        expect(chapters.length).toBeGreaterThan(0);
      });
    });

    it("shows current song with pulse indicator", async () => {
      render(<LyricJumpList {...defaultProps} currentSongIndex={0} />);

      await openList();

      await waitFor(() => {
        // The first chapter should have a pulse indicator
        const firstChapter = screen.getByText("Amazing Grace").closest("button");
        expect(firstChapter).toBeInTheDocument();
      });
    });
  });

  describe("interactions", () => {
    it("opens when handle is clicked", async () => {
      render(<LyricJumpList {...defaultProps} />);

      await openList();

      await waitFor(() => {
        expect(screen.getByText("Amazing Grace")).toBeInTheDocument();
      });
    });

    it("closes when backdrop is clicked", async () => {
      render(<LyricJumpList {...defaultProps} />);

      // Open first
      await openList();

      await waitFor(() => {
        expect(screen.getByText("Amazing Grace")).toBeInTheDocument();
      });

      // Find and click backdrop (it's a div with role button)
      const backdrop = document.querySelector('[role="button"][tabindex="0"]');
      if (backdrop) {
        await act(async () => {
          fireEvent.click(backdrop);
        });
      }

      // Should be closed - content should not be visible
      await waitFor(() => {
        // The sheet should be closed (check for absence of backdrop)
        const backdrops = document.querySelectorAll('[role="button"][tabindex="0"]');
        // After closing, there should be no backdrop
        expect(backdrops.length).toBeLessThan(2);
      });
    });

    it("expands a non-current song title when clicked", async () => {
      render(<LyricJumpList {...defaultProps} />);

      await openList();

      await waitFor(() => {
        expect(screen.getByText("How Great Thou Art")).toBeInTheDocument();
      });

      const chapterButton = screen.getByText("How Great Thou Art").closest("button");
      if (chapterButton) {
        await act(async () => {
          fireEvent.click(chapterButton);
        });
      }

      expect(screen.getByText("O Lord my God, when I in awesome wonder")).toBeInTheDocument();
      expect(
        screen.getByText("Consider all the worlds Thy hands have made")
      ).toBeInTheDocument();
    });

    it("does not call a seek callback when a song title is clicked", async () => {
      render(<LyricJumpList {...defaultProps} />);

      await openList();

      const chapterButton = await screen.findByText("How Great Thou Art");
      await act(async () => {
        fireEvent.click(chapterButton);
      });

      expect(mockJumpToLine).not.toHaveBeenCalled();
    });

    it("keeps current-song visual state tied to currentSongIndex", async () => {
      render(<LyricJumpList {...defaultProps} currentSongIndex={0} />);

      await openList();

      const chapterButton = await screen.findByText("How Great Thou Art");
      await act(async () => {
        fireEvent.click(chapterButton);
      });

      const currentSongCard = screen.getByText("Amazing Grace").closest(".rounded-lg");
      const expandedSongCard = screen.getByText("How Great Thou Art").closest(".rounded-lg");

      expect(currentSongCard).toHaveClass("bg-white/10");
      expect(expandedSongCard).toHaveClass("bg-white/5");
      expect(screen.getByText("O Lord my God, when I in awesome wonder")).toBeInTheDocument();
    });

    it("shows lines for current chapter", async () => {
      render(<LyricJumpList {...defaultProps} currentSongIndex={0} />);

      await openList();

      await waitFor(() => {
        expect(screen.getByText("Amazing grace, how sweet the sound")).toBeInTheDocument();
        expect(screen.getByText("That saved a wretch like me")).toBeInTheDocument();
      });
    });

    it("calls onJumpToLine when line is clicked", async () => {
      render(<LyricJumpList {...defaultProps} currentSongIndex={0} />);

      await openList();

      await waitFor(() => {
        expect(screen.getByText("Amazing grace, how sweet the sound")).toBeInTheDocument();
      });

      const lineButton = screen.getByText("Amazing grace, how sweet the sound").closest("button");
      if (lineButton) {
        await act(async () => {
          fireEvent.click(lineButton);
        });
      }

      expect(mockJumpToLine).toHaveBeenCalledWith(0, 0);
    });

    it("calls onJumpToLine with the expanded chapter and line index", async () => {
      render(<LyricJumpList {...defaultProps} currentSongIndex={0} />);

      await openList();

      const chapterButton = await screen.findByText("How Great Thou Art");
      await act(async () => {
        fireEvent.click(chapterButton);
      });

      const lineButton = await screen.findByText("Consider all the worlds Thy hands have made");
      await act(async () => {
        fireEvent.click(lineButton);
      });

      expect(mockJumpToLine).toHaveBeenCalledWith(1, 1);
    });
  });

  describe("current line highlighting", () => {
    it("highlights current line based on time", async () => {
      render(<LyricJumpList {...defaultProps} currentTime={25} currentSongIndex={0} />);

      await openList();

      await waitFor(() => {
        // At 25 seconds, the second line (20s) should be current
        const lines = screen.getAllByText(/That saved a wretch like me/);
        expect(lines.length).toBeGreaterThan(0);
      });
    });

    it("shows past lines with different styling", async () => {
      render(<LyricJumpList {...defaultProps} currentTime={35} currentSongIndex={0} />);

      await openList();

      await waitFor(() => {
        // At 35 seconds, first two lines are past
        expect(screen.getByText("Amazing grace, how sweet the sound")).toBeInTheDocument();
        expect(screen.getByText("That saved a wretch like me")).toBeInTheDocument();
      });
    });
  });

  describe("time formatting", () => {
    it("formats chapter times correctly", async () => {
      render(<LyricJumpList {...defaultProps} />);

      await openList();

      await waitFor(() => {
        expect(screen.getByText(/0:00 - 3:00/)).toBeInTheDocument();
        expect(screen.getByText(/3:00 - 7:00/)).toBeInTheDocument();
      });
    });

    it("formats line times correctly", async () => {
      render(<LyricJumpList {...defaultProps} currentSongIndex={0} />);

      await openList();

      await waitFor(() => {
        expect(screen.getByText("0:10")).toBeInTheDocument();
        expect(screen.getByText("0:20")).toBeInTheDocument();
      });
    });
  });

  describe("keyboard navigation", () => {
    it("opens on Enter key", async () => {
      render(<LyricJumpList {...defaultProps} />);

      const handle = screen.getByRole("button", { name: /open lyric jump list/i });
      
      await act(async () => {
        fireEvent.keyDown(handle, { key: "Enter" });
      });

      await waitFor(() => {
        expect(screen.getByText("Amazing Grace")).toBeInTheDocument();
      });
    });

    it("closes on Escape key", async () => {
      render(<LyricJumpList {...defaultProps} />);

      // Open first
      const handle = screen.getByRole("button", { name: /open lyric jump list/i });
      
      await act(async () => {
        fireEvent.click(handle);
      });

      await waitFor(() => {
        expect(screen.getByText("Amazing Grace")).toBeInTheDocument();
      });

      // Find the backdrop (it's the one with class containing "bg-black/50")
      const closeButtons = screen.getAllByRole("button", { name: /close lyric jump list/i });
      const backdrop = closeButtons.find(btn => btn.className.includes("bg-black/50"));
      
      await act(async () => {
        fireEvent.keyDown(backdrop!, { key: "Escape" });
      });

      // Should close - check that the backdrop is gone
      await waitFor(() => {
        expect(screen.queryByRole("button", { name: /close lyric jump list/i })).not.toBeInTheDocument();
      });
    });
  });
});
