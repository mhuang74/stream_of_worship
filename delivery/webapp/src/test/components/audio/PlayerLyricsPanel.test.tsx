import { describe, it, expect, vi, beforeEach } from "vitest";
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
