import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import SettingsPage from "@/app/settings/page";

const mockPush = vi.fn();
const mockRefresh = vi.fn();
const mockSignOut = vi.fn();
const mockToast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush, refresh: mockRefresh }),
}));

vi.mock("sonner", () => ({
  toast: mockToast,
}));

vi.mock("@/lib/auth-client", () => ({
  signOut: (...args: unknown[]) => mockSignOut(...args),
  updateUser: vi.fn(() => Promise.resolve({ data: null, error: null })),
  changePassword: vi.fn(() => Promise.resolve({ data: null, error: null })),
  sendVerificationEmail: vi.fn(() => Promise.resolve({ data: null, error: null })),
  useSession: () => ({
    data: { user: { id: "1", name: "Test User", email: "user@example.com" } },
    isPending: false,
  }),
}));

function mockFetchSettings() {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue({
      ok: true,
      json: () =>
        Promise.resolve({
          settings: {
            offlineAutoCache: true,
            defaultGapBeats: 2.0,
            defaultVideoTemplate: "dark",
            defaultResolution: "720p",
            lyricsLoopWindowSeconds: 3.0,
            defaultFontSizePreset: "M",
            defaultFontFamily: "noto_serif_tc",
            defaultKeyShiftSemitones: 0,
            timingReviewFont: "sans",
            locale: "en",
          },
        }),
    })
  );
}

describe("SettingsPage sign-out", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockSignOut.mockResolvedValue(undefined);
    mockFetchSettings();
  });

  it("renders the Account section with a Sign out button", async () => {
    render(<SettingsPage />);
    await waitFor(() => {
      expect(screen.getByText("Account")).toBeInTheDocument();
    });
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("calls signOut() and navigates to /login on success", async () => {
    render(<SettingsPage />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => {
      expect(mockSignOut).toHaveBeenCalled();
    });
    expect(mockToast.success).toHaveBeenCalledWith("Signed out");
    expect(mockPush).toHaveBeenCalledWith("/login");
    expect(mockRefresh).toHaveBeenCalled();
  });

  it("shows the keyed error toast when sign-out fails", async () => {
    mockSignOut.mockRejectedValue(new Error("network"));
    render(<SettingsPage />);
    await waitFor(() => {
      expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    await waitFor(() => {
      expect(mockToast.error).toHaveBeenCalledWith("Sign out failed");
    });
    expect(mockPush).not.toHaveBeenCalled();
  });
});
