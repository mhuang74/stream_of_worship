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
    expect(screen.getByRole("link", { name: "Songsets" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Favorites" })).toBeInTheDocument();
  });

  it("has correct hrefs", () => {
    renderNav();
    expect(screen.getByRole("link", { name: "Songsets" })).toHaveAttribute("href", "/songsets");
    expect(screen.getByRole("link", { name: "Favorites" })).toHaveAttribute("href", "/favorites");
  });

  it("marks active route", () => {
    renderNav();
    const songsetsLink = screen.getByRole("link", { name: "Songsets" });
    expect(songsetsLink).toHaveClass("text-primary");
  });

  it("renders Traditional Chinese labels in zh-Hant", () => {
    renderNav("zh-Hant");
    expect(screen.getByRole("link", { name: "敬拜歌單" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "我的最愛" })).toBeInTheDocument();
  });

  it("does not render on projection routes", () => {
    mockPathname.mockReturnValue("/songsets/test/play/projection");

    renderNav();

    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });

  it("renders About link when signed out", () => {
    mockSession.mockReturnValue(null);
    renderNav();
    const aboutLink = screen.getByRole("link", { name: "About" });
    expect(aboutLink).toHaveAttribute("href", "/about");
    expect(screen.queryByRole("link", { name: "Dashboard" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Songsets" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Favorites" })).not.toBeInTheDocument();
  });

  it("renders Dashboard, Songsets, Favorites without About when signed in", () => {
    renderNav();
    expect(screen.getByRole("link", { name: "Dashboard" })).toHaveAttribute("href", "/");
    expect(screen.getByRole("link", { name: "Songsets" })).toHaveAttribute("href", "/songsets");
    expect(screen.getByRole("link", { name: "Favorites" })).toHaveAttribute("href", "/favorites");
    expect(screen.queryByRole("link", { name: "About" })).not.toBeInTheDocument();
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
