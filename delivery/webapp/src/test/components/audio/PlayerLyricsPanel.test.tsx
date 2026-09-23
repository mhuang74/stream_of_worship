import { describe, it, expect, vi, beforeEach, type Mock } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import { PlayerLyricsPanel } from "@/components/audio/PlayerLyricsPanel";

const mockUseSongLyrics = vi.fn();

vi.mock("@/hooks/useSongLyrics", () => ({
  useSongLyrics: (...args: unknown[]) => mockUseSongLyrics(...args),
  clearLyricsCache: vi.fn(),
}));

const mockUseLyricsFeedback = vi.fn();
vi.mock("@/hooks/useLyricsFeedback", () => ({
  useLyricsFeedback: (...args: unknown[]) => mockUseLyricsFeedback(...args),
}));

const mockUseAudioPlayer = vi.fn();
vi.mock("@/hooks/useAudioPlayer", () => ({
  useAudioPlayer: (...args: unknown[]) => mockUseAudioPlayer(...args),
}));

// Default player state for tests that don't drive playback. Set at module
// scope so it survives `vi.clearAllMocks()` (mockClear keeps implementations);
// cursor tests override per-test via mockPlayerTime.
mockUseAudioPlayer.mockReturnValue({ currentTime: 0, seek: vi.fn() });

function mockFeedbackOk() {
  mockUseLyricsFeedback.mockReturnValue({
    feedback: null,
    loading: false,
    submit: vi.fn().mockResolvedValue(true),
    retract: vi.fn().mockResolvedValue(true),
  });
}

describe("PlayerLyricsPanel", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFeedbackOk();
  });

  it("(a) loading: true → renders spinner + 'Loading lyrics…'", () => {
    mockUseSongLyrics.mockReturnValue({
      lrcContent: null,
      lines: null,
      loading: true,
      error: null,
    });

    render(<PlayerLyricsPanel recordingContentHash="abc123" />);

    expect(screen.getByText(/loading lyrics/i)).toBeInTheDocument();
  });

  it("(b) error → renders 'Lyrics unavailable'", () => {
    mockUseSongLyrics.mockReturnValue({
      lrcContent: null,
      lines: null,
      loading: false,
      error: "Network error",
    });

    render(<PlayerLyricsPanel recordingContentHash="abc123" />);

    expect(screen.getByText(/lyrics unavailable/i)).toBeInTheDocument();
  });

  it("(c) lrcContent with valid LRC (2+ timestamped lines) → renders timestamped lines", () => {
    mockUseSongLyrics.mockReturnValue({
      lrcContent: "[00:12.34]赞美耶和华\n[00:15.00]Second line",
      lines: null,
      loading: false,
      error: null,
    });

    render(<PlayerLyricsPanel recordingContentHash="abc123" />);

    expect(screen.getByText("赞美耶和华")).toBeInTheDocument();
    expect(screen.getByText("Second line")).toBeInTheDocument();
  });

  it("(d) lines non-empty → renders <pre> block with joined lines", () => {
    mockUseSongLyrics.mockReturnValue({
      lrcContent: null,
      lines: ["Line one", "Line two"],
      loading: false,
      error: null,
    });

    render(<PlayerLyricsPanel recordingContentHash="abc123" />);

    const pre = document.querySelector("pre");
    expect(pre).toBeInTheDocument();
    expect(pre?.textContent).toContain("Line one");
    expect(pre?.textContent).toContain("Line two");
  });

  it("(e) lrcContent plain text (fails isValidLRC) → renders <pre> block", () => {
    mockUseSongLyrics.mockReturnValue({
      lrcContent: "Just some plain text lyrics\nwithout timestamps",
      lines: null,
      loading: false,
      error: null,
    });

    render(<PlayerLyricsPanel recordingContentHash="abc123" />);

    const pre = document.querySelector("pre");
    expect(pre).toBeInTheDocument();
    expect(pre?.textContent).toContain("Just some plain text lyrics");
  });

  it("(f) both null → renders 'No lyrics available for this recording.'", () => {
    mockUseSongLyrics.mockReturnValue({
      lrcContent: null,
      lines: null,
      loading: false,
      error: null,
    });

    render(<PlayerLyricsPanel recordingContentHash="abc123" />);

    expect(screen.getByText(/no lyrics available for this recording/i)).toBeInTheDocument();
  });

  it("(g) v6: lrcContent with only 1 timestamped line → renders as <pre> block (not parsed as LRC)", () => {
    mockUseSongLyrics.mockReturnValue({
      lrcContent: "[00:12.34]Only one timestamped line\nThis is plain text",
      lines: null,
      loading: false,
      error: null,
    });

    render(<PlayerLyricsPanel recordingContentHash="abc123" />);

    const pre = document.querySelector("pre");
    expect(pre).toBeInTheDocument();
    expect(pre?.textContent).toContain("Only one timestamped line");
    expect(pre?.textContent).toContain("This is plain text");
  });

  it("(h) zh-Hant: error state renders Traditional Chinese message", () => {
    mockUseSongLyrics.mockReturnValue({
      lrcContent: null,
      lines: null,
      loading: false,
      error: "Network error",
    });

    render(<PlayerLyricsPanel recordingContentHash="abc123" />, "zh-Hant");

    expect(screen.getByText("歌詞載入失敗")).toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------
// Lyrics Feedback footer (issue #194)
// --------------------------------------------------------------------------

describe("PlayerLyricsPanel — Lyrics Feedback footer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFeedbackOk();
  });

  it("loading state: no feedback row (nothing on screen yet)", () => {
    mockUseSongLyrics.mockReturnValue({
      lrcContent: null,
      lines: null,
      loading: true,
      error: null,
    });
    render(<PlayerLyricsPanel recordingContentHash="abc123" />);
    expect(screen.queryByTestId("lyrics-feedback-row")).not.toBeInTheDocument();
  });

  it("error state: no feedback row", () => {
    mockUseSongLyrics.mockReturnValue({
      lrcContent: null,
      lines: null,
      loading: false,
      error: "boom",
    });
    render(<PlayerLyricsPanel recordingContentHash="abc123" />);
    expect(screen.queryByTestId("lyrics-feedback-row")).not.toBeInTheDocument();
  });

  it("synced lyrics: feedback row with happy + sad, no sad chips open", () => {
    mockUseSongLyrics.mockReturnValue({
      lrcContent: "[00:01.00]Hello world\n[00:05.00]Second line",
      lines: null,
      loading: false,
      error: null,
    });
    render(<PlayerLyricsPanel recordingContentHash="abc123" />);
    expect(screen.getByTestId("lyrics-feedback-row")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /serve me well/i })).toBeInTheDocument();
    expect(screen.queryByText(/timing is wrong/i)).not.toBeInTheDocument();
  });

  it("no lyrics: feedback row present, happy hidden", () => {
    mockUseSongLyrics.mockReturnValue({
      lrcContent: null,
      lines: null,
      loading: false,
      error: null,
    });
    render(<PlayerLyricsPanel recordingContentHash="abc123" />);
    expect(screen.getByTestId("lyrics-feedback-row")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /serve me well/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /report a problem/i })).toBeInTheDocument();
  });

  it("unsynced fallback (lines): feedback row shows missing chip, not timing", () => {
    mockUseSongLyrics.mockReturnValue({
      lrcContent: null,
      lines: ["fallback line"],
      loading: false,
      error: null,
    });
    render(<PlayerLyricsPanel recordingContentHash="abc123" />);
    fireEvent.click(screen.getByRole("button", { name: /report a problem/i }));
    expect(screen.getByText(/lyrics missing/i)).toBeInTheDocument();
    expect(screen.queryByText(/timing is wrong/i)).not.toBeInTheDocument();
  });
});

// --------------------------------------------------------------------------
// Playback cursor: highlight, click-to-seek, auto-scroll
// --------------------------------------------------------------------------

describe("PlayerLyricsPanel — playback cursor", () => {

  let scrollSpy: Mock;

  const ROW_HEIGHT = 50;
  // data-lyric-index → row.offsetTop
  let rowTops: Record<string, number>;

  let origOffsetTop: PropertyDescriptor | undefined;
  let origOffsetHeight: PropertyDescriptor | undefined;
  let origClientHeight: PropertyDescriptor | undefined;

  beforeEach(() => {
    vi.clearAllMocks();
    mockFeedbackOk();
    mockUseSongLyrics.mockReturnValue({
      lrcContent: "[00:01.00]Hello world\n[00:05.00]Second line\n[00:09.00]Third line",
      lines: null,
      loading: false,
      error: null,
    });
    // jsdom has no scrollTo; install a recording spy on the container prototype.
    scrollSpy = vi.fn();
    Element.prototype.scrollTo = scrollSpy;

    // jsdom reports 0 for all layout metrics; under the containment check
    // that means "everything visible" → no scroll. Install per-element
    // layout so the page-flip logic can be exercised (same scaffolding as
    // LyricJumpList.test.tsx, keyed by data-lyric-index).
    rowTops = {};
    origOffsetTop = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetTop");
    origOffsetHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetHeight");
    origClientHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "clientHeight");
    Object.defineProperty(HTMLElement.prototype, "offsetTop", {
      configurable: true,
      get(this: HTMLElement) {
        const key = this.getAttribute("data-lyric-index");
        return key ? (rowTops[key] ?? 0) : 0; // container.offsetTop = 0
      },
    });
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
      configurable: true,
      get(this: HTMLElement) {
        return this.hasAttribute("data-lyric-index") ? ROW_HEIGHT : 0;
      },
    });
    Object.defineProperty(HTMLElement.prototype, "clientHeight", {
      configurable: true,
      get(this: HTMLElement) {
        return this.classList.contains("overflow-y-auto") ? 300 : 0;
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

  function mockPlayerTime(currentTime: number) {
    mockUseAudioPlayer.mockReturnValue({ currentTime, seek: vi.fn() });
  }

  it("currentTime 6 → second line highlighted", () => {
    mockPlayerTime(6);
    render(<PlayerLyricsPanel recordingContentHash="abc123" />);

    const row1 = document.querySelector('[data-lyric-index="1"]');
    expect(row1).toHaveStyle({ backgroundColor: "#fef3c7", color: "#92400e" });
    expect(document.querySelector('[data-lyric-index="0"]')).not.toHaveAttribute("data-active");
    expect(document.querySelector('[data-lyric-index="2"]')).not.toHaveAttribute("data-active");
  });

  it("cursor leads: highlights next line 0.3s before its timestamp", () => {
    // Line 2 starts at 5.0s; at 4.8s the 0.3s lead must already select it.
    mockPlayerTime(4.8);
    render(<PlayerLyricsPanel recordingContentHash="abc123" />);

    expect(document.querySelector('[data-lyric-index="1"]')).toHaveAttribute("data-active");
    expect(document.querySelector('[data-lyric-index="0"]')).not.toHaveAttribute("data-active");
  });

  it("currentTime 0 (before first line) → no row highlighted", () => {
    mockPlayerTime(0);
    render(<PlayerLyricsPanel recordingContentHash="abc123" />);

    expect(document.querySelector("[data-active]")).not.toBeInTheDocument();
  });

  it("clicking a lyric row seeks to that line's timestamp", () => {
    mockPlayerTime(6);
    render(<PlayerLyricsPanel recordingContentHash="abc123" />);

    const seekFn = mockUseAudioPlayer.mock.results.at(-1)!.value.seek;
    fireEvent.click(document.querySelector('[data-lyric-index="2"]')!);
    expect(seekFn).toHaveBeenCalledWith(9);
  });

  it("does not scroll when the active row stays inside the visible region", () => {
    mockPlayerTime(6);
    const { rerender } = render(<PlayerLyricsPanel recordingContentHash="abc123" />);
    scrollSpy.mockClear();

    mockPlayerTime(10);
    rerender(<PlayerLyricsPanel recordingContentHash="abc123" />);

    // Row 2 at [150, 200] inside [0, 300] → no scroll.
    expect(scrollSpy).not.toHaveBeenCalled();
  });

  it("page-flips when the active row exits below the visible region", () => {
    rowTops = { "2": 400 };
    mockPlayerTime(6);
    const { rerender } = render(<PlayerLyricsPanel recordingContentHash="abc123" />);
    scrollSpy.mockClear();

    mockPlayerTime(10);
    rerender(<PlayerLyricsPanel recordingContentHash="abc123" />);

    // Row 2 at [400, 450], bottom 450 > 300 → one flip landing it one
    // row-height below the top.
    expect(scrollSpy).toHaveBeenCalledTimes(1);
    expect(scrollSpy).toHaveBeenCalledWith({ top: 400 - ROW_HEIGHT });
  });

  it("scrolls up with the same placement when the cursor exits above, clamped at 0", () => {
    rowTops = { "0": 0, "1": 60 };
    mockPlayerTime(6);
    const { rerender } = render(<PlayerLyricsPanel recordingContentHash="abc123" />);
    // Row 1 at [60, 110] is fully inside [0, 300] → no scroll yet.
    expect(scrollSpy).not.toHaveBeenCalled();

    const scroller = document.querySelector(".overflow-y-auto") as HTMLElement;
    scroller.scrollTop = 500;
    scrollSpy.mockClear();

    mockPlayerTime(1.5);
    rerender(<PlayerLyricsPanel recordingContentHash="abc123" />);

    // Active row 0 (1.5 + 0.3 lead ≥ 1) at rowTop 0 < viewTop 500 →
    // upward flip; 0 − 50 clamps to 0.
    expect(scrollSpy).toHaveBeenCalledWith({ top: 0 });
  });
});
