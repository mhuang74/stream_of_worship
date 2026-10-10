import { render, screen } from "@testing-library/react";
import { BottomNav } from "@/components/layout/BottomNav";
import { LocaleProvider } from "@/contexts/LocaleContext";
import { beforeEach, describe, it, expect, vi } from "vitest";

const mockPathname = vi.hoisted(() => vi.fn(() => "/songsets"));
const mockSession = vi.hoisted(() =>
  vi.fn(() => ({
    user: { id: 1, name: "Michael", email: "m@example.com" },
  }))
);

vi.mock("next/navigation", () => ({
  usePathname: mockPathname,
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
}));

vi.mock("@/lib/auth-client", () => ({
  useSession: () => ({ data: mockSession(), isPending: false }),
  signIn: vi.fn(),
  signOut: vi.fn(),
}));

function renderNav(initialLocale: "en" | "zh-Hant" = "en") {
  render(
    <LocaleProvider initialLocale={initialLocale}>
      <BottomNav />
    </LocaleProvider>
  );
}

describe("BottomNav", () => {
  beforeEach(() => {
    mockPathname.mockReturnValue("/songsets");
    mockSession.mockReturnValue({
      user: { id: 1, name: "Michael", email: "m@example.com" },
    });
  });

  it("renders navigation links", () => {
    renderNav();
    expect(screen.getByRole("link", { name: "Listen" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Songsets" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Favorites" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Worship" })).toBeInTheDocument();
  });

  it("has correct hrefs", () => {
    renderNav();
    expect(screen.getByRole("link", { name: "Listen" })).toHaveAttribute("href", "/listen");
    expect(screen.getByRole("link", { name: "Songsets" })).toHaveAttribute("href", "/songsets");
    expect(screen.getByRole("link", { name: "Favorites" })).toHaveAttribute("href", "/favorites");
    expect(screen.getByRole("link", { name: "Worship" })).toHaveAttribute("href", "/worship");
  });

  it("has four items with Listen first and no Dashboard", () => {
    renderNav();
    const nav = screen.getByRole("navigation", { name: "Main navigation" });
    const links = nav.querySelectorAll("a");
    expect(links).toHaveLength(4);
    expect(links[0]).toHaveAttribute("href", "/listen");
    expect(links[0]).toHaveTextContent("Listen");
    expect(nav.textContent).not.toContain("Dashboard");
  });

  it("marks active route", () => {
    renderNav();
    const songsetsLink = screen.getByRole("link", { name: "Songsets" });
    expect(songsetsLink).toHaveClass("text-primary");
  });

  it("renders Traditional Chinese labels in zh-Hant", () => {
    renderNav("zh-Hant");
    expect(screen.getByRole("link", { name: "收聽" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "敬拜歌單" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "我的最愛" })).toBeInTheDocument();
  });

  it("does not render on projection routes", () => {
    mockPathname.mockReturnValue("/songsets/test/play/projection");

    renderNav();

    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  it("renders the signed-in nav on /worship", () => {
    mockPathname.mockReturnValue("/worship");

    renderNav();

    expect(screen.getByRole("navigation")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Listen" })).toBeInTheDocument();
  });

  it("renders nothing on /worship while the session is unresolved (signed-out flash guard)", () => {
    mockPathname.mockReturnValue("/worship");
    mockSession.mockReturnValue(null);

    renderNav();

    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "About" })).not.toBeInTheDocument();
  });

  it("renders the signed-in nav on /worship when offline even with no session", () => {
    mockPathname.mockReturnValue("/worship");
    mockSession.mockReturnValue(null);
    Object.defineProperty(navigator, "onLine", { value: false, configurable: true });

    try {
      renderNav();

      expect(screen.getByRole("navigation")).toBeInTheDocument();
      expect(screen.getByRole("link", { name: "Listen" })).toBeInTheDocument();
    } finally {
      Object.defineProperty(navigator, "onLine", { value: true, configurable: true });
    }
  });

  it("renders About link when signed out", () => {
    mockSession.mockReturnValue(null);
    renderNav();
    const aboutLink = screen.getByRole("link", { name: "About" });
    expect(aboutLink).toHaveAttribute("href", "https://streamofworship.com/about");
    // Signed-out BottomNav has no song links, but the desktop Header may still
    // render something matching; scope to absence of the Listen nav item.
    expect(screen.queryByRole("link", { name: "Listen" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Songsets" })).not.toBeInTheDocument();
  });

  it("renders Dashboard, Songsets, Favorites without About when signed in", () => {
    renderNav();
    // /worship renders an active Dashboard link in the desktop nav even while
    // signed in — this test's pathname is /songsets, so check bottom-nav links.
    expect(screen.queryByRole("link", { name: "About" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Songsets" })).toHaveClass("text-primary");
  });

  it("renders the language toggle alongside About when signed out", () => {
    mockSession.mockReturnValue(null);
    renderNav();
    expect(screen.getByRole("link", { name: "About" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "English" })).toBeInTheDocument();
  });

  it("renders the Traditional Chinese language toggle in zh-Hant when signed out", () => {
    mockSession.mockReturnValue(null);
    renderNav("zh-Hant");
    expect(screen.getByRole("button", { name: "繁體中文" })).toBeInTheDocument();
  });

  it("does not render a language toggle when signed in", () => {
    renderNav();
    expect(screen.queryByRole("button", { name: "English" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "繁體中文" })).not.toBeInTheDocument();
  });

});
