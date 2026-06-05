import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ShareDialog } from "@/components/share/ShareDialog";
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const mockFetch = vi.fn();
global.fetch = mockFetch;

Object.assign(navigator, {
  clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
});

const mockWindowOpen = vi.fn();
global.open = mockWindowOpen;

function renderDialog(props?: Partial<React.ComponentProps<typeof ShareDialog>>) {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    songsetId: "songset-1",
    songsetName: "Sunday Worship",
    durationSeconds: 1080,
    ...props,
  };
  return render(<ShareDialog {...defaultProps} />);
}

function mockEmptyShares() {
  return { ok: true, json: async () => ({ shares: [] }) };
}

function mockExistingShare(token = "tok-1") {
  return {
    ok: true,
    json: async () => ({
      shares: [{ token, shareUrl: `https://example.com/share/${token}`, songsetId: "songset-1", renderJobId: null }],
    }),
  };
}

function mockSizes(mp3: number | null = null, mp4: number | null = null) {
  return { ok: true, json: async () => ({ mp3SizeBytes: mp3, mp4SizeBytes: mp4 }) };
}

// --------------------------------------------------------------------------
// Basic rendering
// --------------------------------------------------------------------------

describe("ShareDialog rendering", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders when open", async () => {
    mockFetch.mockResolvedValueOnce(mockEmptyShares());

    renderDialog();
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
  });

  it("shows share-link tab active by default", async () => {
    mockFetch.mockResolvedValueOnce(mockEmptyShares());

    renderDialog();
    await waitFor(() => {
      const tab = screen.getByRole("tab", { name: /share link/i });
      expect(tab).toHaveAttribute("aria-selected", "true");
    });
  });

  it("hides send-file tab when no renderJobId prop", async () => {
    mockFetch.mockResolvedValueOnce(mockEmptyShares());

    renderDialog({ renderJobId: undefined });
    await waitFor(() => {
      expect(screen.queryByRole("tab", { name: /send file/i })).not.toBeInTheDocument();
    });
  });

  it("shows send-file tab when renderJobId prop provided", async () => {
    mockFetch
      .mockResolvedValueOnce(mockEmptyShares())
      .mockResolvedValueOnce(mockSizes());

    renderDialog({ renderJobId: "job-123" });
    await waitFor(() => {
      expect(screen.getByRole("tab", { name: /send file/i })).toBeInTheDocument();
    });
  });
});

// --------------------------------------------------------------------------
// Share Link tab
// --------------------------------------------------------------------------

describe("Share Link tab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("fetches shares by songsetId on open", async () => {
    mockFetch.mockResolvedValueOnce(mockEmptyShares());

    renderDialog();
    await waitFor(() => {
      expect(mockFetch).toHaveBeenCalledWith(
        expect.stringContaining("songsetId=songset-1")
      );
    });
  });

  it("shows create share link button when no active share", async () => {
    mockFetch.mockResolvedValueOnce(mockEmptyShares());

    renderDialog();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /create share link/i })).toBeInTheDocument();
    });
  });

  it("creates share link with songsetId on button click", async () => {
    mockFetch
      .mockResolvedValueOnce(mockEmptyShares())
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ token: "new-tok", shareUrl: "https://example.com/share/new-tok", songsetId: "songset-1", renderJobId: null }),
      });

    const { toast } = await import("sonner");
    renderDialog();
    await waitFor(() => screen.getByRole("button", { name: /create share link/i }));

    fireEvent.click(screen.getByRole("button", { name: /create share link/i }));
    await waitFor(() => {
      expect(toast.success).toHaveBeenCalledWith("Share link created");
    });
  });

  it("shows formatted message with name, duration, and URL", async () => {
    mockFetch.mockResolvedValueOnce(mockExistingShare("tok-1"));

    renderDialog();
    await waitFor(() => {
      const textarea = screen.getByRole("textbox", { name: /share message/i });
      expect(textarea.value).toContain("Sunday Worship");
      expect(textarea.value).toContain("18 min");
      expect(textarea.value).toContain("https://example.com/share/tok-1");
      expect(textarea.value).toContain("read-only mode");
    });
  });

  it("copies full formatted message to clipboard", async () => {
    mockFetch.mockResolvedValueOnce(mockExistingShare("tok-1"));

    const { toast } = await import("sonner");
    renderDialog();
    await waitFor(() => screen.getByRole("button", { name: /copy share message/i }));

    fireEvent.click(screen.getByRole("button", { name: /copy share message/i }));
    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        expect.stringContaining("Sunday Worship")
      );
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        expect.stringContaining("tok-1")
      );
      expect(toast.success).toHaveBeenCalledWith("Share message copied to clipboard");
    });
  });

  it("shows live-link warning text", async () => {
    mockFetch.mockResolvedValueOnce(mockExistingShare("tok-1"));

    renderDialog();
    await waitFor(() => {
      expect(
        screen.getByText(/link stays live/i)
      ).toBeInTheDocument();
    });
  });

  it("revokes share on revoke button click", async () => {
    mockFetch
      .mockResolvedValueOnce(mockExistingShare("tok-1"))
      .mockResolvedValueOnce({ ok: true, json: async () => ({ success: true }) });

    const { toast } = await import("sonner");
    renderDialog();
    await waitFor(() => screen.getByRole("button", { name: /revoke share link/i }));

    fireEvent.click(screen.getByRole("button", { name: /revoke share link/i }));
    await waitFor(() => {
      expect(toast.success).toHaveBeenCalledWith("Share link revoked");
      expect(screen.queryByRole("button", { name: /revoke share link/i })).not.toBeInTheDocument();
    });
  });

  it("shows error toast when create fails", async () => {
    mockFetch
      .mockResolvedValueOnce(mockEmptyShares())
      .mockResolvedValueOnce({
        ok: false,
        json: async () => ({ error: "Maximum of 20 active shares reached." }),
      });

    const { toast } = await import("sonner");
    renderDialog();
    await waitFor(() => screen.getByRole("button", { name: /create share link/i }));

    fireEvent.click(screen.getByRole("button", { name: /create share link/i }));
    await waitFor(() => {
      expect(toast.error).toHaveBeenCalledWith(
        expect.stringContaining("Maximum of 20")
      );
    });
  });

  it("formats duration under 60 min correctly", async () => {
    mockFetch.mockResolvedValueOnce(mockExistingShare("tok-1"));

    renderDialog({ durationSeconds: 1080 });
    await waitFor(() => {
      const textarea = screen.getByRole("textbox", { name: /share message/i });
      expect(textarea.value).toContain("18 min");
    });
  });

  it("formats duration 60+ min correctly", async () => {
    mockFetch.mockResolvedValueOnce(mockExistingShare("tok-1"));

    renderDialog({ durationSeconds: 5400 });
    await waitFor(() => {
      const textarea = screen.getByRole("textbox", { name: /share message/i });
      expect(textarea.value).toContain("1h 30m");
    });
  });

  it("formats null duration as Not available", async () => {
    mockFetch.mockResolvedValueOnce(mockExistingShare("tok-1"));

    renderDialog({ durationSeconds: null });
    await waitFor(() => {
      const textarea = screen.getByRole("textbox", { name: /share message/i });
      expect(textarea.value).toContain("Not available");
    });
  });
});

// --------------------------------------------------------------------------
// Send File tab
// --------------------------------------------------------------------------

describe("Send File tab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  async function openSendFileTab() {
    mockFetch
      .mockResolvedValueOnce(mockEmptyShares())
      .mockResolvedValueOnce(mockSizes());
    renderDialog({ renderJobId: "job-123" });
    await waitFor(() => screen.getByRole("tab", { name: /send file/i }));
    fireEvent.click(screen.getByRole("tab", { name: /send file/i }));
    await waitFor(() => {
      const tab = screen.getByRole("tab", { name: /send file/i });
      expect(tab).toHaveAttribute("aria-selected", "true");
    });
  }

  it("shows WhatsApp, Line, Email buttons", async () => {
    await openSendFileTab();
    expect(screen.getByRole("button", { name: /send via whatsapp/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /send via line/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /send via email/i })).toBeInTheDocument();
  });

  it("disables email button when file exceeds 25MB limit", async () => {
    mockFetch
      .mockResolvedValueOnce(mockEmptyShares())
      .mockResolvedValueOnce(mockSizes(50 * 1024 * 1024, 500 * 1024 * 1024));

    renderDialog({ renderJobId: "job-123" });
    await waitFor(() => screen.getByRole("tab", { name: /send file/i }));
    fireEvent.click(screen.getByRole("tab", { name: /send file/i }));

    await waitFor(() => {
      const emailBtn = screen.getByRole("button", { name: /send via email/i });
      expect(emailBtn).toBeDisabled();
    });
  });

  it("opens email client with share link on email button click", async () => {
    mockFetch
      .mockResolvedValueOnce(mockExistingShare("tok-1"))
      .mockResolvedValueOnce(mockSizes(1 * 1024 * 1024, null));

    renderDialog({ renderJobId: "job-123" });
    await waitFor(() => screen.getByRole("tab", { name: /send file/i }));
    fireEvent.click(screen.getByRole("tab", { name: /send file/i }));

    await waitFor(() => screen.getByRole("button", { name: /send via email/i }));
    fireEvent.click(screen.getByRole("button", { name: /send via email/i }));

    expect(mockWindowOpen).toHaveBeenCalledWith(
      expect.stringContaining("mailto:")
    );
  });
});
