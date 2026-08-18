import { screen } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import HomePage from "@/app/page";
import { SongsetsClient } from "@/app/songsets/SongsetsClient";
import SettingsPage from "@/app/settings/page";
import { renderWithLocale as render } from "@/test/render";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useParams: () => ({}),
  usePathname: () => "/",
}));

vi.mock("@/lib/i18n/server", () => ({
  resolveUserLocale: vi.fn().mockResolvedValue("en"),
}));

describe("HomePage", () => {
  it("renders title", async () => {
    render(await HomePage());
    expect(screen.getByRole("heading", { name: /stream of worship/i })).toBeInTheDocument();
  });

  it("has link to songsets", async () => {
    render(await HomePage());
    expect(screen.getByRole("link", { name: /view songsets/i })).toHaveAttribute("href", "/songsets");
  });
});

describe("SongsetsPage", () => {
  it("renders heading", () => {
    render(<SongsetsClient initialData={{ songsets: [], total: 0 }} currentPage={1} pageSize={20} initialSearch="" />);
    expect(screen.getByRole("heading", { name: /songsets/i })).toBeInTheDocument();
  });
});

describe("SettingsPage", () => {
  it("renders heading", () => {
    render(<SettingsPage />);
    expect(screen.getByRole("heading", { name: /settings/i })).toBeInTheDocument();
  });
});
