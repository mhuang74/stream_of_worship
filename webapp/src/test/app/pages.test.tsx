import { render, screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import HomePage from "@/app/page";
import { SongsetsClient } from "@/app/songsets/SongsetsClient";
import SettingsPage from "@/app/settings/page";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn() }),
  useParams: () => ({}),
  usePathname: () => "/",
}));

describe("HomePage", () => {
  it("renders title", () => {
    render(<HomePage />);
    expect(screen.getByRole("heading", { name: /stream of worship/i })).toBeInTheDocument();
  });

  it("has link to songsets", () => {
    render(<HomePage />);
    expect(screen.getByRole("link", { name: /view songsets/i })).toHaveAttribute("href", "/songsets");
  });
});

describe("SongsetsPage", () => {
  it("renders heading", () => {
    render(<SongsetsClient initialData={{ songsets: [], total: 0 }} />);
    expect(screen.getByRole("heading", { name: /songsets/i })).toBeInTheDocument();
  });
});

describe("SettingsPage", () => {
  it("renders heading", () => {
    render(<SettingsPage />);
    expect(screen.getByRole("heading", { name: /settings/i })).toBeInTheDocument();
  });
});
