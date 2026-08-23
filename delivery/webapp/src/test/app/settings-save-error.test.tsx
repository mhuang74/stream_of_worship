import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import SettingsPage from "@/app/settings/page";

const mockPush = vi.fn();
const mockToast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

vi.mock("sonner", () => ({
  toast: mockToast,
}));

vi.mock("@/lib/auth-client", () => ({
  signOut: () => Promise.resolve(),
  updateUser: vi.fn(() => Promise.resolve({ data: null, error: null })),
  changePassword: vi.fn(() => Promise.resolve({ data: null, error: null })),
  sendVerificationEmail: vi.fn(() => Promise.resolve({ data: null, error: null })),
  useSession: () => ({
    data: { user: { id: "1", name: "Test User", email: "user@example.com" } },
    isPending: false,
  }),
}));

const SETTINGS_PAYLOAD = {
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
};

describe("SettingsPage save errors", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("routes API save failures through settings.failedSave instead of raw error", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({ settings: SETTINGS_PAYLOAD }),
        })
        .mockResolvedValueOnce({
          ok: false,
          json: () => Promise.resolve({ error: "defaultResolution is invalid" }),
        })
    );

    render(<SettingsPage />);
    await waitFor(() => {
      expect(screen.getByRole("switch")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole("switch"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(mockToast.error).toHaveBeenCalledTimes(1);
    });
    expect(mockToast.error).toHaveBeenCalledWith("Failed to save settings");
    expect(mockToast.error).not.toHaveBeenCalledWith(
      expect.stringContaining("defaultResolution is invalid")
    );
  });
});
