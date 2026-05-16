import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { LyricsEditor } from "@/components/lyrics/LyricsEditor";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

const sampleLrc = "[00:01.00]Hello world\n[00:05.00]Second line";

function renderEditor(props?: Partial<React.ComponentProps<typeof LyricsEditor>>) {
  const defaultProps = {
    recordingContentHash: "test-hash",
    lrcContent: sampleLrc,
    onSave: vi.fn(),
  };
  return render(<LyricsEditor {...defaultProps} {...props} />);
}

describe("LyricsEditor", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.fetch = vi.fn();
  });

  it("renders a textarea with the LRC content", () => {
    renderEditor();
    const textarea = screen.getByRole("textbox", { name: /lrc content editor/i });
    expect(textarea).toBeInTheDocument();
    expect(textarea).toHaveValue(sampleLrc);
  });

  it("shows Save button disabled when content is unchanged", () => {
    renderEditor();
    const saveBtn = screen.getByRole("button", { name: /save lyrics/i });
    expect(saveBtn).toBeDisabled();
  });

  it("enables Save button after editing", () => {
    renderEditor();
    const textarea = screen.getByRole("textbox", { name: /lrc content editor/i });
    fireEvent.change(textarea, { target: { value: "[00:01.00]Modified" } });
    const saveBtn = screen.getByRole("button", { name: /save lyrics/i });
    expect(saveBtn).not.toBeDisabled();
  });

  it("shows Reset button after editing", () => {
    renderEditor();
    const textarea = screen.getByRole("textbox", { name: /lrc content editor/i });
    fireEvent.change(textarea, { target: { value: "[00:01.00]Modified" } });
    expect(screen.getByRole("button", { name: /reset to original/i })).toBeInTheDocument();
  });

  it("resets to original when Reset is clicked", () => {
    renderEditor();
    const textarea = screen.getByRole("textbox", { name: /lrc content editor/i });
    fireEvent.change(textarea, { target: { value: "[00:01.00]Modified" } });
    const resetBtn = screen.getByRole("button", { name: /reset to original/i });
    fireEvent.click(resetBtn);
    expect(textarea).toHaveValue(sampleLrc);
  });

  it("calls fetch and onSave when Save is clicked successfully", async () => {
    const onSave = vi.fn();
    vi.mocked(global.fetch).mockResolvedValue({
      ok: true,
      json: async () => ({ success: true }),
    } as any);

    renderEditor({ onSave });
    const textarea = screen.getByRole("textbox", { name: /lrc content editor/i });
    fireEvent.change(textarea, { target: { value: "[00:01.00]Modified" } });

    const saveBtn = screen.getByRole("button", { name: /save lyrics/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/lyrics/overrides",
        expect.objectContaining({ method: "POST" })
      );
      expect(onSave).toHaveBeenCalledWith("[00:01.00]Modified");
    });
  });

  it("shows error toast on save failure", async () => {
    const { toast } = await import("sonner");
    vi.mocked(global.fetch).mockResolvedValue({ ok: false } as any);

    renderEditor();
    const textarea = screen.getByRole("textbox", { name: /lrc content editor/i });
    fireEvent.change(textarea, { target: { value: "[00:01.00]Modified" } });

    const saveBtn = screen.getByRole("button", { name: /save lyrics/i });
    fireEvent.click(saveBtn);

    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith("Failed to save lyrics");
    });
  });
});
