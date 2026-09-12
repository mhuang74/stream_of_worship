import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import { LyricsFeedbackRow } from "@/components/audio/LyricsFeedbackRow";
import { useLyricsFeedback } from "@/hooks/useLyricsFeedback";

const mockUseLyricsFeedback = vi.fn();

vi.mock("@/hooks/useLyricsFeedback", () => ({
  useLyricsFeedback: (...args: unknown[]) => mockUseLyricsFeedback(...args),
}));

function mockHook(overrides: Partial<Record<string, unknown>> = {}) {
  mockUseLyricsFeedback.mockReturnValue({
    feedback: null,
    loading: false,
    submit: vi.fn().mockResolvedValue(true),
    retract: vi.fn().mockResolvedValue(true),
    ...overrides,
  });
}

// situation kinds: synced / unsynced / none
describe("LyricsFeedbackRow", () => {
  const recordingHash = "abc123";

  beforeEach(() => {
    vi.clearAllMocks();
  });

  // -- icon presence per content state --------------------------------------

  it("(a) synced state: happy and sad icons visible, neither filled", () => {
    mockUseLyricsFeedback.mockReturnValue({
      feedback: null,
      loading: false,
      submit: vi.fn(),
      retract: vi.fn(),
    });

    render(<LyricsFeedbackRow recordingContentHash={recordingHash} situation="synced" />);

    expect(screen.getByRole("button", { name: /serve me well/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /report a problem/i })).toBeInTheDocument();
  });

  it("(b) unsynced state: happy and sad icons visible", () => {
    mockUseLyricsFeedback.mockReturnValue({
      feedback: null,
      loading: false,
      submit: vi.fn(),
      retract: vi.fn(),
    });

    render(<LyricsFeedbackRow recordingContentHash={recordingHash} situation="unsynced" />);

    expect(screen.getByRole("button", { name: /serve me well/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /report a problem/i })).toBeInTheDocument();
  });

  it("(c) no lyrics: happy hidden, sad visible", () => {
    mockUseLyricsFeedback.mockReturnValue({
      feedback: null,
      loading: false,
      submit: vi.fn(),
      retract: vi.fn(),
    });

    render(<LyricsFeedbackRow recordingContentHash={recordingHash} situation="none" />);

    expect(screen.queryByRole("button", { name: /serve me well/i })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /report a problem/i })).toBeInTheDocument();
  });

  // -- active icon states ---------------------------------------------------

  it("(d) happy feedback active: happy icon filled (aria-pressed)", () => {
    mockUseLyricsFeedback.mockReturnValue({
      feedback: { rating: "happy", reason: null },
      loading: false,
      submit: vi.fn(),
      retract: vi.fn(),
    });

    render(<LyricsFeedbackRow recordingContentHash={recordingHash} situation="synced" />);

    expect(
      screen.getByRole("button", { name: /serve me well/i })
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("(e) sad feedback active: sad icon filled (aria-pressed)", () => {
    mockUseLyricsFeedback.mockReturnValue({
      feedback: { rating: "sad", reason: "timing" },
      loading: false,
      submit: vi.fn(),
      retract: vi.fn(),
    });

    render(<LyricsFeedbackRow recordingContentHash={recordingHash} situation="synced" />);

    expect(
      screen.getByRole("button", { name: /report a problem/i })
    ).toHaveAttribute("aria-pressed", "true");
  });

  // -- chip filtering per state ---------------------------------------------

  it("(f) sad chips for synced state: timing offered, missing not offered", () => {
    mockUseLyricsFeedback.mockReturnValue({
      feedback: null,
      loading: false,
      submit: vi.fn(),
      retract: vi.fn(),
    });

    render(<LyricsFeedbackRow recordingContentHash={recordingHash} situation="synced" />);
    fireEvent.click(screen.getByRole("button", { name: /report a problem/i }));

    expect(screen.getByText(/timing is wrong/i)).toBeInTheDocument();
    expect(screen.queryByText(/lyrics missing/i)).not.toBeInTheDocument();
    expect(screen.getByText(/wrong text/i)).toBeInTheDocument();
    expect(screen.getByText(/^other$/i)).toBeInTheDocument();
  });

  it("(g) sad chips for unsynced state: missing offered, timing not offered", () => {
    mockUseLyricsFeedback.mockReturnValue({
      feedback: null,
      loading: false,
      submit: vi.fn(),
      retract: vi.fn(),
    });

    render(<LyricsFeedbackRow recordingContentHash={recordingHash} situation="unsynced" />);
    fireEvent.click(screen.getByRole("button", { name: /report a problem/i }));

    expect(screen.getByText(/lyrics missing/i)).toBeInTheDocument();
    expect(screen.queryByText(/timing is wrong/i)).not.toBeInTheDocument();
  });

  it("(g2) sad chips for no-lyrics state: missing offered, timing not offered", () => {
    mockUseLyricsFeedback.mockReturnValue({
      feedback: null,
      loading: false,
      submit: vi.fn(),
      retract: vi.fn(),
    });

    render(<LyricsFeedbackRow recordingContentHash={recordingHash} situation="none" />);
    fireEvent.click(screen.getByRole("button", { name: /report a problem/i }));

    expect(screen.getByText(/lyrics missing/i)).toBeInTheDocument();
    expect(screen.queryByText(/timing is wrong/i)).not.toBeInTheDocument();
  });

  // -- flows -----------------------------------------------------------------

  it("(h) tap happy submits happy", async () => {
    const submit = vi.fn().mockResolvedValue(true);
    mockUseLyricsFeedback.mockReturnValue({
      feedback: null,
      loading: false,
      submit,
      retract: vi.fn(),
    });

    render(<LyricsFeedbackRow recordingContentHash={recordingHash} situation="synced" />);
    fireEvent.click(screen.getByRole("button", { name: /serve me well/i }));

    await waitFor(() => expect(submit).toHaveBeenCalledWith("happy"));
  });

  it("(i) tap active happy again retracts", async () => {
    const retract = vi.fn().mockResolvedValue(true);
    mockUseLyricsFeedback.mockReturnValue({
      feedback: { rating: "happy", reason: null },
      loading: false,
      submit: vi.fn(),
      retract,
    });

    render(<LyricsFeedbackRow recordingContentHash={recordingHash} situation="synced" />);
    fireEvent.click(screen.getByRole("button", { name: /serve me well/i }));

    await waitFor(() => expect(retract).toHaveBeenCalled());
  });

  it("(j) sad → chip tap submits sad with that reason", async () => {
    const submit = vi.fn().mockResolvedValue(true);
    mockUseLyricsFeedback.mockReturnValue({
      feedback: null,
      loading: false,
      submit,
      retract: vi.fn(),
    });

    render(<LyricsFeedbackRow recordingContentHash={recordingHash} situation="synced" />);
    fireEvent.click(screen.getByRole("button", { name: /report a problem/i }));
    fireEvent.click(screen.getByText(/timing is wrong/i));

    await waitFor(() => expect(submit).toHaveBeenCalledWith("sad", "timing"));
  });

  it("(k) tap active sad again retracts (chips not shown first)", async () => {
    const retract = vi.fn().mockResolvedValue(true);
    mockUseLyricsFeedback.mockReturnValue({
      feedback: { rating: "sad", reason: "missing" },
      loading: false,
      submit: vi.fn(),
      retract,
    });

    render(<LyricsFeedbackRow recordingContentHash={recordingHash} situation="unsynced" />);
    fireEvent.click(screen.getByRole("button", { name: /report a problem/i }));

    await waitFor(() => expect(retract).toHaveBeenCalled());
  });

  it("(l) switching from happy to sad opens the chips instead of retracting", () => {
    const retract = vi.fn().mockResolvedValue(true);
    mockUseLyricsFeedback.mockReturnValue({
      feedback: { rating: "happy", reason: null },
      loading: false,
      submit: vi.fn(),
      retract,
    });

    render(<LyricsFeedbackRow recordingContentHash={recordingHash} situation="synced" />);
    fireEvent.click(screen.getByRole("button", { name: /report a problem/i }));

    expect(retract).not.toHaveBeenCalled();
    expect(screen.getByText(/timing is wrong/i)).toBeInTheDocument();
  });

  // -- i18n ------------------------------------------------------------------

  it("(m) zh-Hant: icons and chips render Traditional Chinese", () => {
    mockUseLyricsFeedback.mockReturnValue({
      feedback: null,
      loading: false,
      submit: vi.fn(),
      retract: vi.fn(),
    });

    render(<LyricsFeedbackRow recordingContentHash={recordingHash} situation="synced" />, "zh-Hant");

    expect(screen.getByRole("button", { name: /這份歌詞很好用/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /回報歌詞問題/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /回報歌詞問題/ }));
    expect(screen.getByText(/時間軸不對/)).toBeInTheDocument();
    expect(screen.getByText(/文字有誤/)).toBeInTheDocument();
    expect(screen.getByText(/其他/)).toBeInTheDocument();
    expect(screen.queryByText(/歌詞缺失/)).not.toBeInTheDocument();
  });

  it("(m2) zh-Hant: no-lyrics state hides happy, shows missing chip in Chinese", () => {
    mockUseLyricsFeedback.mockReturnValue({
      feedback: null,
      loading: false,
      submit: vi.fn(),
      retract: vi.fn(),
    });

    render(<LyricsFeedbackRow recordingContentHash={recordingHash} situation="none" />, "zh-Hant");

    expect(screen.queryByRole("button", { name: /這份歌詞很好用/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /回報歌詞問題/ }));
    expect(screen.getByText(/歌詞缺失/)).toBeInTheDocument();
    expect(screen.queryByText(/時間軸不對/)).not.toBeInTheDocument();
  });
});