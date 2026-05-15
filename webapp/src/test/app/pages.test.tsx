import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import HomePage from "@/app/page";
import SongsetsPage from "@/app/songsets/page";
import SettingsPage from "@/app/settings/page";

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
    render(<SongsetsPage />);
    expect(screen.getByRole("heading", { name: /songsets/i })).toBeInTheDocument();
  });
});

describe("SettingsPage", () => {
  it("renders heading", () => {
    render(<SettingsPage />);
    expect(screen.getByRole("heading", { name: /settings/i })).toBeInTheDocument();
  });
});
