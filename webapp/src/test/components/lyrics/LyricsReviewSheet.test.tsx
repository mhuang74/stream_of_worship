import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { LyricsReviewSheet } from "@/components/lyrics/LyricsReviewSheet";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// @base-ui/react dialog mock - simulate open dialog rendering children
vi.mock("@base-ui/react/dialog", () => ({
  Dialog: {
    Root: ({ children, open }: any) => (open ? <div data-testid="sheet-root">{children}</div> : null),
    Trigger: ({ children }: any) => <div>{children}</div>,
    Portal: ({ children }: any) => <div>{children}</div>,
    Backdrop: ({ children }: any) => <div>{children}</div>,
    Popup: ({ children }: any) => <div data-testid="sheet-popup">{children}</div>,
    Close: ({ children }: any) => <button data-testid="sheet-close">{children}</button>,
    Title: ({ children }: any) => <h2>{children}</h2>,
    Description: ({ children }: any) => <p>{children}</p>,
  },
}));

const sampleLrc = "[00:01.00]Hello world\n[00:05.00]Second line";

function renderSheet(props?: Partial<React.ComponentProps<typeof LyricsReviewSheet>>) {
  const defaultProps = {
    isOpen: true,
    onOpenChange: vi.fn(),
    recordingContentHash: "test-hash",
    lrcContent: sampleLrc,
    songTitle: "Test Song",
  };
  return render(<LyricsReviewSheet {...defaultProps} {...props} />);
}

describe("LyricsReviewSheet", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ marks: [] }),
    } as any);
  });

  it("renders when open", async () => {
    renderSheet();
    await waitFor(() => {
      expect(screen.getByText("Lyrics Review")).toBeInTheDocument();
    });
  });

  it("does not render when closed", () => {
    renderSheet({ isOpen: false });
    expect(screen.queryByText("Lyrics Review")).not.toBeInTheDocument();
  });

  it("shows song title in description", async () => {
    renderSheet();
    await waitFor(() => {
      expect(screen.getByText("Test Song")).toBeInTheDocument();
    });
  });

  it("renders lyric lines from LRC content", async () => {
    renderSheet();
    await waitFor(() => {
      expect(screen.getByText("Hello world")).toBeInTheDocument();
      expect(screen.getByText("Second line")).toBeInTheDocument();
    });
  });

  it("fetches marks on open", async () => {
    renderSheet();
    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("/api/lyrics/marks?recordingContentHash=test-hash")
      );
    });
  });

  it("shows timestamps for each line", async () => {
    renderSheet();
    await waitFor(() => {
      expect(screen.getByText("00:01.00")).toBeInTheDocument();
      expect(screen.getByText("00:05.00")).toBeInTheDocument();
    });
  });

  it("shows desktop tabs", async () => {
    renderSheet();
    await waitFor(() => {
      expect(screen.getByRole("tab", { name: "Review" })).toBeInTheDocument();
      expect(screen.getByRole("tab", { name: "Edit Text" })).toBeInTheDocument();
      expect(screen.getByRole("tab", { name: "Edit Timing" })).toBeInTheDocument();
    });
  });

  it("marks a line when tapped and calls API", async () => {
    renderSheet();
    await waitFor(() => {
      expect(screen.getByText("Hello world")).toBeInTheDocument();
    });

    const lineButton = screen.getAllByRole("button").find((b) =>
      b.getAttribute("aria-label")?.includes("Hello world")
    );
    expect(lineButton).toBeDefined();
    fireEvent.click(lineButton!);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        "/api/lyrics/marks",
        expect.objectContaining({ method: "POST" })
      );
    });
  });

  it("unmarks a line when tapped again", async () => {
    // Load with one existing mark
    global.fetch = vi.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ marks: [1.0] }),
      } as any)
      .mockResolvedValue({
        ok: true,
        json: async () => ({}),
      } as any);

    renderSheet();
    await waitFor(() => {
      expect(screen.getByText("Hello world")).toBeInTheDocument();
    });

    const lineButton = screen.getAllByRole("button").find((b) =>
      b.getAttribute("aria-label")?.includes("Hello world")
    );
    fireEvent.click(lineButton!);

    await waitFor(() => {
      expect(global.fetch).toHaveBeenCalledWith(
        expect.stringContaining("timestampSeconds=1"),
        expect.objectContaining({ method: "DELETE" })
      );
    });
  });

  it("does not show mobile footer when no marks", async () => {
    renderSheet();
    await waitFor(() => {
      expect(screen.getByText("Hello world")).toBeInTheDocument();
    });
    expect(screen.queryByText(/open on desktop to fix/i)).not.toBeInTheDocument();
  });

  it("shows mobile footer when marks exist", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ marks: [1.0] }),
    } as any);

    renderSheet();
    await waitFor(() => {
      expect(screen.getByText(/open on desktop to fix/i)).toBeInTheDocument();
    });
  });

  it("shows plural 'lines' when multiple marks exist", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ marks: [1.0, 5.0] }),
    } as any);

    renderSheet();
    await waitFor(() => {
      expect(screen.getByText(/2 marked lines/i)).toBeInTheDocument();
    });
  });

  it("shows singular 'line' when one mark exists", async () => {
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ marks: [1.0] }),
    } as any);

    renderSheet();
    await waitFor(() => {
      expect(screen.getByText(/1 marked line$/i)).toBeInTheDocument();
    });
  });

  it("shows loading state while fetching marks", () => {
    // Keep fetch pending
    global.fetch = vi.fn().mockImplementation(() => new Promise(() => {}));
    renderSheet();
    expect(screen.getByText("Loading marks...")).toBeInTheDocument();
  });
});
