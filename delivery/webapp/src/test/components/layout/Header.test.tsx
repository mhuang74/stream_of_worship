import { render, screen } from "@testing-library/react";
import { Header } from "@/components/layout/Header";
import { LocaleProvider } from "@/contexts/LocaleContext";
import { beforeEach, describe, it, expect, vi } from "vitest";

const mockPathname = vi.hoisted(() => vi.fn(() => "/songsets"));

vi.mock("next/navigation", () => ({
  usePathname: mockPathname,
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

  it("renders desktop navigation links", () => {
    renderHeader();
    const songsetsLink = screen.getByRole("link", { name: "Songsets" });
    const settingsLink = screen.getByRole("link", { name: "Settings" });
    expect(songsetsLink).toHaveAttribute("href", "/songsets");
    expect(settingsLink).toHaveAttribute("href", "/settings");
  });

  it("renders Traditional Chinese navigation links in zh-Hant", () => {
    renderHeader("zh-Hant");
    const songsetsLink = screen.getByRole("link", { name: "詩歌集" });
    const favoritesLink = screen.getByRole("link", { name: "我的最愛" });
    const settingsLink = screen.getByRole("link", { name: "設定" });
    expect(songsetsLink).toHaveAttribute("href", "/songsets");
    expect(favoritesLink).toHaveAttribute("href", "/favorites");
    expect(settingsLink).toHaveAttribute("href", "/settings");
  });

  it("does not render on projection routes", () => {
    mockPathname.mockReturnValue("/songsets/test/play/projection");

    renderHeader();

    expect(screen.queryByRole("banner")).not.toBeInTheDocument();
  });
});
