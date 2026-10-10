import { render, screen, fireEvent } from "@testing-library/react";
import { Header } from "@/components/layout/Header";
import { LocaleProvider } from "@/contexts/LocaleContext";
import { beforeEach, describe, it, expect, vi } from "vitest";

const mockPathname = vi.hoisted(() => vi.fn(() => "/songsets"));
const mockSession = vi.hoisted(() =>
  vi.fn(() => ({
    user: { id: 1, name: "Michael", email: "m@example.com" },
  }))
);
const mockRefresh = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({
  usePathname: mockPathname,
  useRouter: () => ({ push: vi.fn(), refresh: mockRefresh }),
}));

vi.mock("@/lib/auth-client", () => ({
  useSession: () => ({ data: mockSession(), isPending: false }),
  signOut: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

function renderHeader(initialLocale: "en" | "zh-Hant" = "en") {
  render(
    <LocaleProvider initialLocale={initialLocale}>
      <Header />
    </LocaleProvider>
  );
}

describe("Header", () => {
  beforeEach(() => {
    mockPathname.mockReturnValue("/songsets");
    mockSession.mockReturnValue({
      user: { id: 1, name: "Michael", email: "m@example.com" },
    });
  });

  it("renders the app name", () => {
    renderHeader();
    expect(screen.getByText("Stream of Worship")).toBeInTheDocument();
  });

  it("has a link to the home page", () => {
    renderHeader();
    const homeLink = screen.getByRole("link", { name: /stream of worship/i });
    expect(homeLink).toHaveAttribute("href", "/");
  });

  it("renders a mobile-only Home icon link for signed-in users", () => {
    renderHeader();
    // Two Dashboard links exist: the mobile Home icon and the desktop nav
    // (CSS-hidden in jsdom, which doesn't apply stylesheets).
    const dashboardHomeIcon = screen.getAllByRole("link", { name: "Dashboard" })[0];
    expect(dashboardHomeIcon).toHaveAttribute("href", "/");
    expect(dashboardHomeIcon).toHaveClass("lg:hidden");
  });

  it("does not render the Home icon link when signed out", () => {
    mockSession.mockReturnValue(null);
    renderHeader();
    expect(screen.queryByRole("link", { name: "Dashboard" })).not.toBeInTheDocument();
  });

  it("renders desktop navigation links", () => {
    renderHeader();
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    expect(nav).toHaveClass("hidden", "lg:flex");
    const authedLinks = nav.querySelectorAll("a");
    expect(authedLinks).toHaveLength(5);
    expect(authedLinks[0]).toHaveAttribute("href", "/");
    expect(authedLinks[1]).toHaveAttribute("href", "/listen");
    expect(authedLinks[2]).toHaveAttribute("href", "/favorites");
    expect(authedLinks[3]).toHaveAttribute("href", "/songsets");
    expect(authedLinks[4]).toHaveAttribute("href", "/worship");
    expect(authedLinks[1]).toHaveTextContent("Listen");
    expect(authedLinks[2]).toHaveTextContent("Favorites");
  });

  it("renders Traditional Chinese navigation links in zh-Hant", () => {
    renderHeader("zh-Hant");
    const listenLink = screen.getByRole("link", { name: "收聽" });
    const favoritesLink = screen.getByRole("link", { name: "我的最愛" });
    expect(listenLink).toHaveAttribute("href", "/listen");
    expect(favoritesLink).toHaveAttribute("href", "/favorites");
  });

  it("does not render on projection routes", () => {
    mockPathname.mockReturnValue("/songsets/test/play/projection");

    renderHeader();

    expect(screen.queryByRole("banner")).not.toBeInTheDocument();
  });

  it("renders About link when signed out", () => {
    mockSession.mockReturnValue(null);
    renderHeader();
    expect(screen.getByRole("link", { name: "About" })).toHaveAttribute(
      "href",
      "https://streamofworship.com/about"
    );
  });

  it("renders Traditional Chinese About link in zh-Hant when signed out", () => {
    mockSession.mockReturnValue(null);
    renderHeader("zh-Hant");
    expect(screen.getByRole("link", { name: "關於" })).toHaveAttribute(
      "href",
      "https://streamofworship.com/zh-Hant/about"
    );
  });

  it("renders Sign in link when signed out", () => {
    mockSession.mockReturnValue(null);
    renderHeader();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });

  it("renders the language toggle in the header when signed out", () => {
    mockSession.mockReturnValue(null);
    renderHeader();
    expect(screen.getByRole("button", { name: "English" })).toBeInTheDocument();
  });

  it("renders the Traditional Chinese language toggle in zh-Hant when signed out", () => {
    mockSession.mockReturnValue(null);
    renderHeader("zh-Hant");
    expect(screen.getByRole("button", { name: "繁體中文" })).toBeInTheDocument();
  });

  it("does not render a language toggle in the header when signed in", () => {
    renderHeader();
    expect(screen.queryByRole("button", { name: "English" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "繁體中文" })).not.toBeInTheDocument();
  });
  it("refreshes server content and persists the cookie when the language is switched", () => {
    mockSession.mockReturnValue(null);
    renderHeader();
    mockRefresh.mockClear();
    fireEvent.click(screen.getByRole("button", { name: "繁體中文" }));
    expect(document.cookie).toContain("sow_locale=zh-Hant");
    expect(mockRefresh).toHaveBeenCalled();
  });

});
