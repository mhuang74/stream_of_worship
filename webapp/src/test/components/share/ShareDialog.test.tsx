import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { ShareDialog } from "@/components/share/ShareDialog";
vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const mockFetch = vi.fn();
global.fetch = mockFetch;

// Mock clipboard
Object.assign(navigator, {
  clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
});

// Mock window.open
const mockWindowOpen = vi.fn();
global.open = mockWindowOpen;

function renderDialog(props?: Partial<React.ComponentProps<typeof ShareDialog>>) {
  const defaultProps = {
    open: true,
    onOpenChange: vi.fn(),
    renderJobId: "job-123",
    songsetName: "Sunday Worship",
    ...props,
  };
  return render(<ShareDialog {...defaultProps} />);
}

function mockEmptyShares() {
  return { ok: true, json: async () => ({ shares: [] }) };
}

function mockSizes(mp3: number | null = null, mp4: number | null = null) {
  return { ok: true, json: async () => ({ mp3SizeBytes: mp3, mp4SizeBytes: mp4 }) };
}

function mockExistingShare(token = "tok-1") {
  return {
    ok: true,
    json: async () => ({
      shares: [{ token, shareUrl: `https://example.com/share/${token}`, renderJobId: "job-123" }],
    }),
  };
}

// --------------------------------------------------------------------------
// Basic rendering
// --------------------------------------------------------------------------

describe("ShareDialog rendering", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders when open", async () => {
    mockFetch
      .mockResolvedValueOnce(mockEmptyShares())
      .mockResolvedValueOnce(mockSizes());

    renderDialog();
    await waitFor(() => {
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
  });

  it("shows share-link tab active by default", async () => {
    mockFetch
      .mockResolvedValueOnce(mockEmptyShares())
      .mockResolvedValueOnce(mockSizes());

    renderDialog();
    await waitFor(() => {
      const tab = screen.getByRole("tab", { name: /share link/i });
      expect(tab).toHaveAttribute("aria-selected", "true");
    });
  });

  it("shows send-file tab button", async () => {
    mockFetch
      .mockResolvedValueOnce(mockEmptyShares())
      .mockResolvedValueOnce(mockSizes());

    renderDialog();
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

  it("shows create share link button when no active share", async () => {
    mockFetch
      .mockResolvedValueOnce(mockEmptyShares())
      .mockResolvedValueOnce(mockSizes());

    renderDialog();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /create share link/i })).toBeInTheDocument();
    });
  });

  it("creates share link on button click", async () => {
    mockFetch
      .mockResolvedValueOnce(mockEmptyShares())
      .mockResolvedValueOnce(mockSizes())
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ token: "new-tok", shareUrl: "https://example.com/share/new-tok" }),
      });

    const { toast } = await import("sonner");
    renderDialog();
    await waitFor(() => screen.getByRole("button", { name: /create share link/i }));

    fireEvent.click(screen.getByRole("button", { name: /create share link/i }));
    await waitFor(() => {
      expect(toast.success).toHaveBeenCalledWith("Share link created");
    });
  });

  it("shows existing share URL with copy and revoke buttons", async () => {
    mockFetch
      .mockResolvedValueOnce(mockExistingShare("tok-1"))
      .mockResolvedValueOnce(mockSizes());

    renderDialog();
    await waitFor(() => {
      expect(screen.getByRole("button", { name: /copy share link/i })).toBeInTheDocument();
      expect(screen.getByRole("button", { name: /revoke share link/i })).toBeInTheDocument();
    });
  });

  it("copies share URL to clipboard", async () => {
    mockFetch
      .mockResolvedValueOnce(mockExistingShare("tok-1"))
      .mockResolvedValueOnce(mockSizes());

    const { toast } = await import("sonner");
    renderDialog();
    await waitFor(() => screen.getByRole("button", { name: /copy share link/i }));

    fireEvent.click(screen.getByRole("button", { name: /copy share link/i }));
    await waitFor(() => {
      expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
        expect.stringContaining("tok-1")
      );
      expect(toast.success).toHaveBeenCalledWith("Link copied to clipboard");
    });
  });

  it("revokes share on revoke button click", async () => {
    mockFetch
      .mockResolvedValueOnce(mockExistingShare("tok-1"))
      .mockResolvedValueOnce(mockSizes())
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
      .mockResolvedValueOnce(mockSizes())
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

  it("shows revocation notice text", async () => {
    mockFetch
      .mockResolvedValueOnce(mockExistingShare("tok-1"))
      .mockResolvedValueOnce(mockSizes());

    renderDialog();
    await waitFor(() => {
      expect(
        screen.getByText(/Revoking stops streams; downloaded files unaffected/i)
      ).toBeInTheDocument();
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
    renderDialog();
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
      .mockResolvedValueOnce(mockSizes(50 * 1024 * 1024, 500 * 1024 * 1024)); // 500MB mp4

    renderDialog();
    await waitFor(() => screen.getByRole("tab", { name: /send file/i }));
    fireEvent.click(screen.getByRole("tab", { name: /send file/i }));

    await waitFor(() => {
      const emailBtn = screen.getByRole("button", { name: /send via email/i });
      expect(emailBtn).toBeDisabled();
    });
  });

  it("keeps WhatsApp and Line enabled for 50MB file", async () => {
    mockFetch
      .mockResolvedValueOnce(mockEmptyShares())
      .mockResolvedValueOnce(mockSizes(50 * 1024 * 1024, null));

    renderDialog();
    await waitFor(() => screen.getByRole("tab", { name: /send file/i }));
    fireEvent.click(screen.getByRole("tab", { name: /send file/i }));

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /send via whatsapp/i })).not.toBeDisabled();
      expect(screen.getByRole("button", { name: /send via line/i })).not.toBeDisabled();
    });
  });

  it("disables Line button when file exceeds 1GB", async () => {
    mockFetch
      .mockResolvedValueOnce(mockEmptyShares())
      .mockResolvedValueOnce(mockSizes(null, 1.5 * 1024 * 1024 * 1024));

    renderDialog();
    await waitFor(() => screen.getByRole("tab", { name: /send file/i }));
    fireEvent.click(screen.getByRole("tab", { name: /send file/i }));

    await waitFor(() => {
      const lineBtn = screen.getByRole("button", { name: /send via line/i });
      expect(lineBtn).toBeDisabled();
    });
  });

  it("shows file size when available", async () => {
    mockFetch
      .mockResolvedValueOnce(mockEmptyShares())
      .mockResolvedValueOnce(mockSizes(null, 500 * 1024 * 1024));

    renderDialog();
    await waitFor(() => screen.getByRole("tab", { name: /send file/i }));
    fireEvent.click(screen.getByRole("tab", { name: /send file/i }));

    await waitFor(() => {
      expect(screen.getByText(/500\.0 MB/)).toBeInTheDocument();
    });
  });

  it("opens email client with share link on email button click", async () => {
    mockFetch
      .mockResolvedValueOnce(mockExistingShare("tok-1"))
      .mockResolvedValueOnce(mockSizes(1 * 1024 * 1024, null)); // 1MB - under email limit

    renderDialog();
    await waitFor(() => screen.getByRole("tab", { name: /send file/i }));
    fireEvent.click(screen.getByRole("tab", { name: /send file/i }));

    await waitFor(() => screen.getByRole("button", { name: /send via email/i }));
    fireEvent.click(screen.getByRole("button", { name: /send via email/i }));

    expect(mockWindowOpen).toHaveBeenCalledWith(
      expect.stringContaining("mailto:")
    );
  });
});
