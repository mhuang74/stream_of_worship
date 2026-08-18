import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { FavoritesClient } from "@/app/favorites/FavoritesClient";
import type { SongCardData } from "@/components/songset/SongCard";

const mockReplace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: mockReplace }),
}));

vi.mock("sonner", () => ({
  toast: {
    error: vi.fn(),
  },
}));

function makeSongs(count: number): SongCardData[] {
  return Array.from({ length: count }, (_, i) => ({
    id: `song-${i + 1}`,
    title: `Song ${i + 1}`,
    composer: "Composer",
    lyricist: null,
    albumName: null,
    musicalKey: null,
    recordings: [],
  }));
}

describe("FavoritesClient pagination", () => {
  beforeEach(() => {
    mockReplace.mockClear();
    vi.stubGlobal("scrollTo", vi.fn());
  });

  it("renders numbered page buttons for total > pageSize", () => {
    render(
      <FavoritesClient
        initialSongs={makeSongs(20)}
        initialTotal={45}
        currentPage={1}
        pageSize={20}
      />
    );

    const nav = screen.getByRole("navigation", { name: "Favorites pagination" });
    expect(nav).toBeInTheDocument();
    expect(screen.getByTestId("pagination-page-1")).toHaveAttribute(
      "aria-current",
      "page"
    );
    expect(screen.getByTestId("pagination-page-2")).toBeInTheDocument();
    expect(screen.getByTestId("pagination-page-3")).toBeInTheDocument();
  });

  it("fetches the next page and syncs the URL when page 2 is clicked", async () => {
    render(
      <FavoritesClient
        initialSongs={makeSongs(20)}
        initialTotal={45}
        currentPage={1}
        pageSize={20}
      />
    );

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ songs: makeSongs(20), total: 45 }),
    });
    vi.stubGlobal("fetch", fetchMock);

    fireEvent.click(screen.getByTestId("pagination-page-2"));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/songs?limit=20&offset=20&favoritesOnly=1&visibilityStatus=published%2Creview"
      );
    });
    await waitFor(() => {
      expect(mockReplace).toHaveBeenCalledWith("/favorites?page=2");
    });
    vi.unstubAllGlobals();
  });
});
