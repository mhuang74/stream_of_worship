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
    expect(screen.getByRole("link", { name: "詩歌集" })).toBeInTheDocument();
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
    expect(screen.getByRole("link", { name: "About" })).toHaveAttribute("href", "/about");
  });

  it("does not render About link when signed in", () => {
    renderNav();
    expect(screen.queryByRole("link", { name: "About" })).not.toBeInTheDocument();
  });
});
