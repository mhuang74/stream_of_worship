import { render, screen } from "@testing-library/react";
import { BottomNav } from "@/components/layout/BottomNav";
import { beforeEach, describe, it, expect, vi } from "vitest";

const mockPathname = vi.hoisted(() => vi.fn(() => "/songsets"));

vi.mock("next/navigation", () => ({
  usePathname: mockPathname,
}));

describe("BottomNav", () => {
  beforeEach(() => {
    mockPathname.mockReturnValue("/songsets");
  });

  it("renders navigation links", () => {
    render(<BottomNav />);
    expect(screen.getByRole("link", { name: "Songsets" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Settings" })).toBeInTheDocument();
  });

  it("has correct hrefs", () => {
    render(<BottomNav />);
    expect(screen.getByRole("link", { name: "Songsets" })).toHaveAttribute("href", "/songsets");
    expect(screen.getByRole("link", { name: "Settings" })).toHaveAttribute("href", "/settings");
  });

  it("marks active route", () => {
    render(<BottomNav />);
    const songsetsLink = screen.getByRole("link", { name: "Songsets" });
    expect(songsetsLink).toHaveClass("text-primary");
  });

  it("does not render on projection routes", () => {
    mockPathname.mockReturnValue("/songsets/test/play/projection");

    render(<BottomNav />);

    expect(screen.queryByRole("navigation")).not.toBeInTheDocument();
  });
});
