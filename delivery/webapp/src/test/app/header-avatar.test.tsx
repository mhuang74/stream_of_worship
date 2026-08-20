import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import { Header } from "@/components/layout/Header";

const mockPathname = vi.hoisted(() => vi.fn(() => "/songsets"));
const mockPush = vi.fn();
const mockRefresh = vi.fn();
const mockSignOut = vi.fn();
const mockSession = vi.hoisted(() => vi.fn());
const mockToast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  usePathname: mockPathname,
  useRouter: () => ({ push: mockPush, refresh: mockRefresh }),
}));

vi.mock("@/lib/auth-client", () => ({
  useSession: () => ({ data: mockSession(), isPending: false }),
  signOut: (...args: unknown[]) => mockSignOut(...args),
}));

vi.mock("sonner", () => ({
  toast: mockToast,
}));

function renderHeader(initialLocale: "en" | "zh-Hant" = "en") {
  render(<Header />, initialLocale);
}

describe("Header avatar dropdown", () => {
  beforeEach(() => {
    mockPathname.mockReturnValue("/songsets");
    mockSession.mockReturnValue({
      user: { id: 1, name: "Michael", email: "m@example.com" },
    });
    mockSignOut.mockResolvedValue(undefined);
    vi.clearAllMocks();
  });

  it("shows the user's initial in the avatar when signed in", () => {
    renderHeader();
    expect(screen.getByText("M")).toBeInTheDocument();
  });

  it("opens dropdown with user name, Settings, and Sign out", async () => {
    renderHeader();
    fireEvent.click(screen.getByRole("button", { name: /M/ }));
    await waitFor(() => {
      expect(screen.getByText("Michael")).toBeInTheDocument();
      expect(screen.getByRole("menuitem", { name: /Settings/ })).toBeInTheDocument();
      expect(screen.getByRole("menuitem", { name: /Sign out/ })).toBeInTheDocument();
    });
  });

  it("navigates to /settings when Settings is clicked", async () => {
    renderHeader();
    fireEvent.click(screen.getByRole("button", { name: /M/ }));
    await waitFor(() => {
      fireEvent.click(screen.getByRole("menuitem", { name: /Settings/ }));
    });
    expect(mockPush).toHaveBeenCalledWith("/settings");
  });

  it("calls signOut() and navigates to /login on sign out", async () => {
    renderHeader();
    fireEvent.click(screen.getByRole("button", { name: /M/ }));
    await waitFor(() => {
      fireEvent.click(screen.getByRole("menuitem", { name: /Sign out/ }));
    });
    await waitFor(() => {
      expect(mockSignOut).toHaveBeenCalled();
    });
    expect(mockToast.success).toHaveBeenCalledWith("Signed out");
    expect(mockPush).toHaveBeenCalledWith("/login");
    expect(mockRefresh).toHaveBeenCalled();
  });

  it("shows Sign in / Create account buttons and landing nav when signed out", () => {
    mockSession.mockReturnValue(null);
    renderHeader();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
    expect(screen.getByRole("link", { name: "Create account" })).toHaveAttribute(
      "href",
      "/register"
    );
    expect(screen.getByRole("link", { name: "Features" })).toHaveAttribute("href", "#features");
    expect(screen.getByRole("link", { name: "How it works" })).toHaveAttribute(
      "href",
      "#how-it-works"
    );
    expect(screen.getByRole("link", { name: "Songs" })).toHaveAttribute("href", "/songsets");
  });
});
