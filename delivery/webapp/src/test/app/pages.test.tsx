import { screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import HomePage from "@/app/page";
import { SongsetsClient } from "@/app/songsets/SongsetsClient";
import SettingsPage from "@/app/settings/page";
import { renderWithLocale as render } from "@/test/render";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), refresh: vi.fn() }),
  useParams: () => ({}),
  usePathname: () => "/",
}));

vi.mock("@/lib/i18n/server", () => ({
  resolveUserLocale: vi.fn().mockResolvedValue("en"),
}));

vi.mock("next/headers", () => ({
  headers: vi.fn().mockResolvedValue(new Headers()),
}));

vi.mock("@/lib/auth", () => ({
  auth: { api: { getSession: vi.fn().mockResolvedValue(null) } },
}));

// Signed-out tests never reach the dashboard queries; mock the module so the
// page's server-only import of @/db (which requires SOW_DATABASE_URL) never
// loads in the jsdom test environment.
vi.mock("@/lib/db/dashboard", () => ({
  getDashboardStats: vi.fn(),
  getRecentSongsets: vi.fn(),
  getRecentFavoriteSongs: vi.fn(),
  getCommunityFavoriteSample: vi.fn(),
}));

vi.mock("@/lib/auth-client", () => ({
  useSession: () => ({ data: null, isPending: false }),
  signOut: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

describe("HomePage", () => {
  it("renders the signed-out landing hero title", async () => {
    render(await HomePage());
    expect(
      screen.getByRole("heading", { name: /lead worship with no awkward interruptions/i })
    ).toBeInTheDocument();
  });

  it("has a get-started link to /register", async () => {
    render(await HomePage());
    expect(screen.getByRole("link", { name: /get started free/i })).toHaveAttribute(
      "href",
      "/register"
    );
  });
});

describe("SongsetsPage", () => {
  it("renders heading", () => {
    render(
      <SongsetsClient
        initialData={{ songsets: [], total: 0 }}
        currentPage={1}
        pageSize={20}
        initialSearch=""
      />
    );
    expect(screen.getByRole("heading", { name: /songsets/i })).toBeInTheDocument();
  });
});

describe("SettingsPage", () => {
  it("renders heading", () => {
    render(<SettingsPage />);
    expect(screen.getByRole("heading", { name: /settings/i })).toBeInTheDocument();
  });
});
