import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, waitFor, act } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import { LyricJumpList } from "@/components/play/LyricJumpList";

const mockUseLyricsFeedback = vi.fn();
vi.mock("@/hooks/useLyricsFeedback", () => ({
  useLyricsFeedback: (...args: unknown[]) => mockUseLyricsFeedback(...args),
}));

describe("LyricJumpList — Lyrics Feedback footer (issue #194)", () => {
  const mockJumpToLine = vi.fn();
  const mockSubmit = vi.fn().mockResolvedValue(true);
  const mockRetract = vi.fn().mockResolvedValue(true);

  const mockChapters = [
    {
      position: 1,
      songTitle: "Amazing Grace",
      startSeconds: 0,
      endSeconds: 180,
      lines: [
        { text: "Amazing grace, how sweet the sound", startSeconds: 10 },
        { text: "That saved a wretch like me", startSeconds: 20 },
      ],
    },
    {
      position: 2,
      songTitle: "How Great Thou Art",
      startSeconds: 180,
      endSeconds: 420,
      lines: [{ text: "O Lord my God, when I in awesome wonder", startSeconds: 190 }],
    },
  ];

  const baseProps = {
    chapters: mockChapters,
    currentTime: 25,
    currentSongIndex: 0,
    onJumpToLine: mockJumpToLine,
    onOpenChange: vi.fn(),
  };

  const renderOpen = (extra: Record<string, unknown> = {}) => {
    render(<LyricJumpList {...baseProps} isOpen={true} {...extra} />);
  };

  beforeEach(() => {
    vi.clearAllMocks();
    // The playback-cursor auto-scroll effect calls container.scrollTo when
    // the sheet opens; jsdom has none. These tests assert the feedback
    // footer, not scrolling.
    Element.prototype.scrollTo = () => {};
    mockSubmit.mockClear();
    mockRetract.mockClear();
    mockUseLyricsFeedback.mockReturnValue({
      feedback: null,
      loading: false,
      submit: mockSubmit,
      retract: mockRetract,
    });
  });

  it("(a) current chapter + hash present: feedback row renders inside the open sheet", async () => {
    renderOpen({ currentRecordingContentHash: "rec-hash-1" });

    expect(screen.getByTestId("lyrics-feedback-row")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /serve me well/i })).toBeInTheDocument();
    expect(mockUseLyricsFeedback).toHaveBeenCalledWith("rec-hash-1");
  });

  it("(b) no content hash (not current): no feedback row even when sheet open", async () => {
    renderOpen();

    expect(screen.queryByTestId("lyrics-feedback-row")).not.toBeInTheDocument();
    expect(mockUseLyricsFeedback).not.toHaveBeenCalled();
  });

  it("(c) targets the current chapter's recording hash when the song changes", async () => {
    const { rerender } = render(
      <LyricJumpList {...baseProps} isOpen={true} currentRecordingContentHash="rec-hash-1" />
    );

    rerender(
      <LyricJumpList
        {...baseProps}
        isOpen={true}
        currentSongIndex={1}
        currentTime={300}
        currentRecordingContentHash="rec-hash-2"
      />
    );

    await waitFor(() => expect(mockUseLyricsFeedback).toHaveBeenCalledWith("rec-hash-2"));
  });

  it("(d) chapter with no timestamped lines: chips offer missing, not timing", async () => {
    renderOpen({
      currentSongIndex: 0,
      chapters: [{ ...mockChapters[1], lines: [] }],
      currentRecordingContentHash: "rec-hash-2",
    });

    fireEvent.click(screen.getByRole("button", { name: /report a problem/i }));
    expect(screen.getByText(/lyrics missing/i)).toBeInTheDocument();
    expect(screen.queryByText(/timing is wrong/i)).not.toBeInTheDocument();
  });

  it("(e) chapter with timestamped lines: chips offer timing, not missing", async () => {
    renderOpen({ currentRecordingContentHash: "rec-hash-1" });

    fireEvent.click(screen.getByRole("button", { name: /report a problem/i }));
    expect(screen.getByText(/timing is wrong/i)).toBeInTheDocument();
    expect(screen.queryByText(/lyrics missing/i)).not.toBeInTheDocument();
  });

  it("(f) chip tap submits sad with that reason against the current recording", async () => {
    renderOpen({ currentRecordingContentHash: "rec-hash-1" });

    fireEvent.click(screen.getByRole("button", { name: /report a problem/i }));
    fireEvent.click(screen.getByText(/timing is wrong/i));

    await waitFor(() => expect(mockSubmit).toHaveBeenCalledWith("sad", "timing"));
  });

  it("(g) zh-Hant: feedback affordances render in Traditional Chinese", async () => {
    render(<LyricJumpList {...baseProps} isOpen={true} currentRecordingContentHash="rec-hash-1" />, "zh-Hant");

    expect(screen.getByRole("button", { name: /這份歌詞很好用/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /回報歌詞問題/ })).toBeInTheDocument();
  });
});