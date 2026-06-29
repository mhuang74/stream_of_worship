import { render, screen } from "@testing-library/react";
import { Header } from "@/components/layout/Header";
import { beforeEach, describe, it, expect, vi } from "vitest";

const mockPathname = vi.hoisted(() => vi.fn(() => "/songsets"));

vi.mock("next/navigation", () => ({
  usePathname: mockPathname,
}));

describe("Header", () => {
  beforeEach(() => {
    mockPathname.mockReturnValue("/songsets");
  });

  it("renders the app name", () => {
    render(<Header />);
    expect(screen.getByText("Stream of Worship")).toBeInTheDocument();
  });

  it("has a link to the home page", () => {
    render(<Header />);
    const homeLink = screen.getByRole("link", { name: /stream of worship/i });
    expect(homeLink).toHaveAttribute("href", "/");
  });

  it("renders desktop navigation links", () => {
    render(<Header />);
    const songsetsLink = screen.getByRole("link", { name: "Songsets" });
    const settingsLink = screen.getByRole("link", { name: "Settings" });
    expect(songsetsLink).toHaveAttribute("href", "/songsets");
    expect(settingsLink).toHaveAttribute("href", "/settings");
  });

  it("does not render on projection routes", () => {
    mockPathname.mockReturnValue("/songsets/test/play/projection");

    render(<Header />);

    expect(screen.queryByRole("banner")).not.toBeInTheDocument();
  });
});
