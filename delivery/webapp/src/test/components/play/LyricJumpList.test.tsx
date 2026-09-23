import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, act, within } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import { LyricJumpList } from "@/components/play/LyricJumpList";

describe("LyricJumpList", () => {
  const mockJumpToLine = vi.fn();
  const mockOnOpenChange = vi.fn();

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
    isOpen: false,
    onOpenChange: mockOnOpenChange,
  };

  beforeEach(() => {
    vi.clearAllMocks();
    // The playback-cursor auto-scroll effect calls container.scrollTo on
    // open/line-change; jsdom has none. Tests that don't assert scrolling
    // get a no-op so the effect can't throw; the cursor describe overrides
    // this with a recording spy.
    Element.prototype.scrollTo = () => {};
  });

  describe("rendering", () => {
    it("renders nothing when closed (peek handle removed)", () => {
      render(<LyricJumpList {...defaultProps} />);

      expect(screen.queryByTestId("lyric-jump-sheet")).not.toBeInTheDocument();
      expect(screen.queryByText(/lyrics/i)).not.toBeInTheDocument();
    });

    it("renders the sheet when open", () => {
      render(<LyricJumpList {...defaultProps} isOpen={true} />);

      expect(screen.getByTestId("lyric-jump-sheet")).toBeInTheDocument();
      expect(screen.getByText("Amazing Grace")).toBeInTheDocument();
      expect(screen.getByText("How Great Thou Art")).toBeInTheDocument();
      expect(screen.getByText("Great Is Thy Faithfulness")).toBeInTheDocument();
    });

    it("renders the in-sheet close chip", () => {
      render(<LyricJumpList {...defaultProps} isOpen={true} />);

      expect(screen.getByTestId("lyric-sheet-close")).toBeInTheDocument();
    });

    it("renders translated close chip in zh-Hant", () => {
      render(<LyricJumpList {...defaultProps} isOpen={true} />, "zh-Hant");

      expect(screen.getByTestId("lyric-sheet-close")).toHaveAttribute(
        "aria-label",
        "關閉歌詞清單"
      );
    });

    it("docks above the control bar via the bar-height variable with a height budget", () => {
      render(<LyricJumpList {...defaultProps} isOpen={true} />);

      const sheet = screen.getByTestId("lyric-jump-sheet");
      expect(sheet.className).toContain("bottom-[var(--sow-controller-bar-height)]");
      expect(sheet.className).toContain(
        "max-h-[calc(100dvh-var(--sow-controller-bar-height))]"
      );
      expect(sheet.className).toContain("rounded-t-2xl");
    });

    it("shows current chapter indicator", () => {
      render(<LyricJumpList {...defaultProps} currentSongIndex={0} isOpen={true} />);

      const chapters = screen.getAllByText(/0:00 - 3:00/);
      expect(chapters.length).toBeGreaterThan(0);
    });
  });

  describe("interactions", () => {
    it("in-sheet close chip calls onOpenChange(false)", async () => {
      render(<LyricJumpList {...defaultProps} isOpen={true} />);

      await act(async () => {
        fireEvent.click(screen.getByTestId("lyric-sheet-close"));
      });

      expect(mockOnOpenChange).toHaveBeenCalledWith(false);
      expect(mockJumpToLine).not.toHaveBeenCalled();
    });

    it("backdrop click closes the sheet and stops propagation (no parent chrome toggle)", async () => {
      const stopSpy = vi.spyOn(Event.prototype, "stopPropagation");
      render(<LyricJumpList {...defaultProps} isOpen={true} />);

      // The backdrop is the role=button whose class carries the dim layer;
      // the in-sheet close chip shares the aria label.
      const backdrop = screen
        .getAllByRole("button", { name: /close lyric jump list/i })
        .find((el) => el.className.includes("bg-black/50"));
      expect(backdrop).toBeDefined();
      await act(async () => {
        fireEvent.click(backdrop!);
      });

      // Backdrop tap must not bubble to the player's root tap-toggle.
      expect(stopSpy).toHaveBeenCalled();
      expect(mockOnOpenChange).toHaveBeenCalledTimes(1);
      expect(mockOnOpenChange).toHaveBeenCalledWith(false);
      stopSpy.mockRestore();
    });

    it("backdrop Escape key closes the sheet", async () => {
      render(<LyricJumpList {...defaultProps} isOpen={true} />);

      const backdrop = screen
        .getAllByRole("button", { name: /close lyric jump list/i })
        .find((el) => el.className.includes("bg-black/50"));
      expect(backdrop).toBeDefined();
      await act(async () => {
        fireEvent.keyDown(backdrop!, { key: "Escape" });
      });

      expect(mockOnOpenChange).toHaveBeenCalledWith(false);
    });

    it("expands a non-current song title when clicked", async () => {
      render(<LyricJumpList {...defaultProps} isOpen={true} />);

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

    it("does not call a seek callback when a song title is clicked", () => {
      render(<LyricJumpList {...defaultProps} isOpen={true} />);

      const chapterButton = screen.getByText("How Great Thou Art");
      fireEvent.click(chapterButton);

      expect(mockJumpToLine).not.toHaveBeenCalled();
    });

    it("keeps current-song visual state tied to currentSongIndex", () => {
      render(<LyricJumpList {...defaultProps} currentSongIndex={0} isOpen={true} />);

      const chapterButton = screen.getByText("How Great Thou Art");
      fireEvent.click(chapterButton);

      const currentSongCard = screen.getByText("Amazing Grace").closest(".rounded-lg");
      const expandedSongCard = screen.getByText("How Great Thou Art").closest(".rounded-lg");

      expect(currentSongCard).toHaveClass("bg-white/10");
      expect(expandedSongCard).toHaveClass("bg-white/5");
      expect(screen.getByText("O Lord my God, when I in awesome wonder")).toBeInTheDocument();
    });

    it("shows lines for current chapter", () => {
      render(<LyricJumpList {...defaultProps} currentSongIndex={0} isOpen={true} />);

      expect(screen.getByText("Amazing grace, how sweet the sound")).toBeInTheDocument();
      expect(screen.getByText("That saved a wretch like me")).toBeInTheDocument();
    });

    it("calls onJumpToLine when line is clicked", async () => {
      render(<LyricJumpList {...defaultProps} currentSongIndex={0} isOpen={true} />);

      const lineButton = screen.getByText("Amazing grace, how sweet the sound").closest("button");
      if (lineButton) {
        await act(async () => {
          fireEvent.click(lineButton);
        });
      }

      expect(mockJumpToLine).toHaveBeenCalledWith(0, 0);
    });

    it("calls onJumpToLine with the expanded chapter and line index", () => {
      render(<LyricJumpList {...defaultProps} currentSongIndex={0} isOpen={true} />);

      const chapterButton = screen.getByText("How Great Thou Art");
      fireEvent.click(chapterButton);

      const lineButton = screen.getByText("Consider all the worlds Thy hands have made");
      fireEvent.click(lineButton);

      expect(mockJumpToLine).toHaveBeenCalledWith(1, 1);
    });
  });

  describe("playback cursor", () => {
    let scrollSpy: ReturnType<typeof vi.fn>;

    const ROW_HEIGHT = 50;
    // data-lyric-row="<chapter>-<line>" → row.offsetTop
    let rowTops: Record<string, number>;
    let viewportHeight: number;

    let origOffsetTop: PropertyDescriptor | undefined;
    let origOffsetHeight: PropertyDescriptor | undefined;
    let origClientHeight: PropertyDescriptor | undefined;

    beforeEach(() => {
      vi.clearAllMocks();
      // jsdom has no scrollTo; install a recording spy on the container
      // prototype (mirrors PlayerLyricsPanel.test.tsx).
      scrollSpy = vi.fn();
      Element.prototype.scrollTo = scrollSpy as unknown as typeof Element.prototype.scrollTo;

      // jsdom reports 0 for all layout metrics; under the containment check
      // that means "everything visible" → no scroll. Install per-element
      // layout so the page-flip logic can be exercised.
      rowTops = {};
      viewportHeight = 300;
      origOffsetTop = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetTop");
      origOffsetHeight = Object.getOwnPropertyDescriptor(
        HTMLElement.prototype,
        "offsetHeight"
      );
      origClientHeight = Object.getOwnPropertyDescriptor(
        HTMLElement.prototype,
        "clientHeight"
      );
      Object.defineProperty(HTMLElement.prototype, "offsetTop", {
        configurable: true,
        get(this: HTMLElement) {
          const key = this.getAttribute("data-lyric-row");
          return key ? (rowTops[key] ?? 0) : 0;
        },
      });
      Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
        configurable: true,
        get(this: HTMLElement) {
          return this.hasAttribute("data-lyric-row") ? ROW_HEIGHT : 0;
        },
      });
      Object.defineProperty(HTMLElement.prototype, "clientHeight", {
        configurable: true,
        get(this: HTMLElement) {
          return this.classList.contains("overflow-y-auto") ? viewportHeight : 0;
        },
      });
    });

    afterEach(() => {
      for (const [name, desc] of [
        ["offsetTop", origOffsetTop],
        ["offsetHeight", origOffsetHeight],
        ["clientHeight", origClientHeight],
      ] as const) {
        if (desc) Object.defineProperty(HTMLElement.prototype, name, desc);
        else delete (HTMLElement.prototype as unknown as Record<string, unknown>)[name];
      }
    });

    it("highlights current line with the blue inline cursor", () => {
      render(
        <LyricJumpList {...defaultProps} currentTime={25} currentSongIndex={0} isOpen={true} />
      );

      const activeRow = document.querySelector('[data-lyric-row="0-1"]');
      expect(activeRow).toHaveAttribute("data-active");
      expect(activeRow).toHaveStyle({ backgroundColor: "#bfdbfe", color: "#1e3a8a" });
      expect(document.querySelector('[data-lyric-row="0-0"]')).not.toHaveAttribute(
        "data-active"
      );
      expect(document.querySelector('[data-lyric-row="0-2"]')).not.toHaveAttribute(
        "data-active"
      );
      // Active row's timestamp inherits the dark cursor color via opacity-80
      // (mirrors PR 200); inactive rows keep the dimmed white timestamp.
      expect(within(activeRow as HTMLElement).getByText("0:20")).toHaveClass("opacity-80");
      expect(within(activeRow as HTMLElement).getByText("0:20")).not.toHaveClass(
        "text-white/40"
      );
      const inactiveRow = document.querySelector('[data-lyric-row="0-0"]') as HTMLElement;
      expect(within(inactiveRow).getByText("0:10")).toHaveClass("text-white/40");
      expect(within(inactiveRow).getByText("0:10")).not.toHaveClass("opacity-80");
    });

    it("cursor leads: highlights next line 0.3s before its timestamp", () => {
      // Line 2 starts at 20s; at 19.8s the 0.3s lead must already select it.
      render(
        <LyricJumpList
          {...defaultProps}
          currentTime={19.8}
          currentSongIndex={0}
          isOpen={true}
        />
      );

      expect(document.querySelector('[data-lyric-row="0-1"]')).toHaveAttribute(
        "data-active"
      );
      expect(document.querySelector('[data-lyric-row="0-0"]')).not.toHaveAttribute(
        "data-active"
      );
    });

    it("before first line: no row highlighted", () => {
      render(
        <LyricJumpList {...defaultProps} currentTime={5} currentSongIndex={0} isOpen={true} />
      );

      expect(document.querySelector("[data-active]")).not.toBeInTheDocument();
    });

    it("past lines are dimmed while active line is not", () => {
      render(
        <LyricJumpList {...defaultProps} currentTime={35} currentSongIndex={0} isOpen={true} />
      );

      // At 35s, lines 0 and 1 are past (dimmed); line 2 (30s) is active.
      expect(document.querySelector('[data-lyric-row="0-0"]')).toHaveClass("text-white/40");
      expect(document.querySelector('[data-lyric-row="0-1"]')).toHaveClass("text-white/40");
      expect(document.querySelector('[data-lyric-row="0-2"]')).not.toHaveClass(
        "text-white/40"
      );
      expect(document.querySelector('[data-lyric-row="0-2"]')).toHaveAttribute(
        "data-active"
      );
    });

    it("does not scroll when the active row stays inside the visible region", () => {
      rowTops = { "0-1": 100, "0-2": 150 };
      const { rerender } = render(
        <LyricJumpList {...defaultProps} currentTime={25} currentSongIndex={0} isOpen={true} />
      );
      scrollSpy.mockClear();

      act(() => {
        rerender(
          <LyricJumpList {...defaultProps} currentTime={35} currentSongIndex={0} isOpen={true} />
        );
      });

      // "0-2" occupies [150, 200] inside [0, 300] → no scroll.
      expect(scrollSpy).not.toHaveBeenCalled();
    });

    it("page-flips when the active row exits below the visible region", () => {
      rowTops = { "0-1": 100, "0-2": 400 };
      const { rerender } = render(
        <LyricJumpList {...defaultProps} currentTime={25} currentSongIndex={0} isOpen={true} />
      );
      scrollSpy.mockClear();

      act(() => {
        rerender(
          <LyricJumpList {...defaultProps} currentTime={35} currentSongIndex={0} isOpen={true} />
        );
      });

      // "0-2" occupies [400, 450], bottom 450 > 300 → one flip landing it one
      // row-height below the top.
      expect(scrollSpy).toHaveBeenCalledTimes(1);
      expect(scrollSpy).toHaveBeenCalledWith({ top: 400 - ROW_HEIGHT });
    });

    it("pages to the active row on open when it is below the fold", () => {
      rowTops = { "0-1": 400 };
      const { rerender } = render(
        <LyricJumpList {...defaultProps} currentTime={25} currentSongIndex={0} isOpen={false} />
      );
      scrollSpy.mockClear();

      act(() => {
        rerender(
          <LyricJumpList {...defaultProps} currentTime={25} currentSongIndex={0} isOpen={true} />
        );
      });

      expect(scrollSpy).toHaveBeenCalledWith({ top: 400 - ROW_HEIGHT });
    });

    it("scrolls up with the same placement when the cursor exits above, clamped at 0", () => {
      rowTops = { "0-0": 0, "0-1": 60 };
      const { rerender } = render(
        <LyricJumpList {...defaultProps} currentTime={25} currentSongIndex={0} isOpen={true} />
      );
      // "0-1" at [60, 110] is fully inside [0, 300] → no scroll yet.
      expect(scrollSpy).not.toHaveBeenCalled();

      const scroller = screen
        .getByTestId("lyric-jump-sheet")
        .querySelector(".overflow-y-auto") as HTMLElement;
      scroller.scrollTop = 500;
      scrollSpy.mockClear();

      act(() => {
        rerender(
          <LyricJumpList {...defaultProps} currentTime={10.5} currentSongIndex={0} isOpen={true} />
        );
      });

      // Active "0-0" (10.5 + 0.3 lead ≥ 10) at rowTop 0 < viewTop 500 →
      // upward flip; 0 − 50 clamps to 0.
      expect(scrollSpy).toHaveBeenCalledWith({ top: 0 });
    });

    it("does not scroll while the sheet is closed", () => {
      render(<LyricJumpList {...defaultProps} currentTime={25} currentSongIndex={0} />);

      expect(scrollSpy).not.toHaveBeenCalled();
    });
  });

  describe("time formatting", () => {
    it("formats chapter times correctly", () => {
      render(<LyricJumpList {...defaultProps} isOpen={true} />);

      expect(screen.getByText(/0:00 - 3:00/)).toBeInTheDocument();
      expect(screen.getByText(/3:00 - 7:00/)).toBeInTheDocument();
    });

    it("formats line times correctly", () => {
      render(<LyricJumpList {...defaultProps} currentSongIndex={0} isOpen={true} />);

      expect(screen.getByText("0:10")).toBeInTheDocument();
      expect(screen.getByText("0:20")).toBeInTheDocument();
    });
  });
});