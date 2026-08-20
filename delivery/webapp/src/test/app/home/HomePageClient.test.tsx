import { describe, it, expect, beforeEach, vi } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import { HomePageClient } from "@/app/page/HomePageClient";
import type { DashboardSongset } from "@/components/dashboard/DashboardSongsetCard";
import type { SongCardData } from "@/components/songset/SongCard";

const mockPush = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("@/contexts/AudioPlayerContext", () => ({
  useAudioPlayerContext: () => ({
    currentTrack: null,
    state: { isPlaying: false },
    play: vi.fn(),
    pause: vi.fn(),
  }),
}));

vi.mock("@/lib/r2/public-url", () => ({
  getPublicAudioUrl: vi.fn(() => null),
}));

function makeSong(id: string): SongCardData {
  return {
    id,
    title: `Song ${id}`,
    composer: "Composer",
    lyricist: null,
    albumName: null,
    musicalKey: "G",
    recordings: [
      {
        contentHash: `hash-${id}`,
        hashPrefix: `prefix-${id}`,
        durationSeconds: 180,
        tempoBpm: 120,
        musicalKey: "G",
        visibilityStatus: "published",
      },
    ],
  };
}

const makeSongset = (id: string, overrides: Partial<DashboardSongset> = {}): DashboardSongset => ({
  id,
  name: `Songset ${id}`,
  itemCount: 2,
  durationSeconds: 360,
  updatedAt: "2026-08-01T00:00:00.000Z",
  renderState: "unrendered",
  lastCompletedRenderJobId: null,
  ...overrides,
});

const defaultProps = {
  locale: "en" as const,
  userName: "Michael",
  stats: {
    songsetsCreated: 5,
    songsetsRendered: 2,
    songsetsShared: 3,
    favoriteSongs: 4,
    catalogSongs: 321,
  },
  recentSongsets: [
    makeSongset("s1", { renderState: "fresh", lastCompletedRenderJobId: "job-1" }),
    makeSongset("s2"),
    makeSongset("s3"),
  ],
  recentFavoriteSongs: [makeSong("f1"), makeSong("f2")],
  communityFavorites: [
    { ...makeSong("c1"), favoriteCount: 7 },
    { ...makeSong("c2"), favoriteCount: 3 },
  ],
};

describe("HomePageClient", () => {
  beforeEach(() => {
    mockPush.mockClear();
    vi.clearAllMocks();
  });

  it("renders greeting with name interpolation", () => {
    render(<HomePageClient {...defaultProps} />);
    expect(
      screen.getByRole("heading", { name: "Welcome back, Michael" })
    ).toBeInTheDocument();
  });

  it("renders all five stat cards with values", () => {
    render(<HomePageClient {...defaultProps} />);
    expect(screen.getByText("Songsets created")).toBeInTheDocument();
    expect(screen.getByText("Songsets rendered")).toBeInTheDocument();
    expect(screen.getByText("Songsets shared")).toBeInTheDocument();
    // "Favorite songs" appears as both a stat label and the section heading
    expect(screen.getAllByText("Favorite songs").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText("Songs in catalog")).toBeInTheDocument();
    expect(screen.getAllByText("5").length).toBeGreaterThan(0);
    expect(screen.getByText("321")).toBeInTheDocument();
  });

  it("renders recent songsets with play button only when fresh render exists", () => {
    render(<HomePageClient {...defaultProps} />);
    const cards = screen.getAllByTestId("dashboard-songset-card");
    expect(cards).toHaveLength(3);
    const playButtons = screen.getAllByRole("button", { name: "Play" });
    expect(playButtons).toHaveLength(1); // only s1 has a fresh render
    const shareButtons = screen.getAllByRole("button", { name: "Share" });
    expect(shareButtons).toHaveLength(3);
  });

  it("navigates to the play page when play is clicked", () => {
    render(<HomePageClient {...defaultProps} />);
    fireEvent.click(screen.getByRole("button", { name: "Play" }));
    expect(mockPush).toHaveBeenCalledWith("/songsets/s1/play");
  });

  it("renders favorite songs and community favorites with favorited-by badge", () => {
    render(<HomePageClient {...defaultProps} />);
    expect(screen.getByText("Song f1")).toBeInTheDocument();
    expect(screen.getByText("Song c1")).toBeInTheDocument();
    expect(screen.getAllByTestId("favorited-by-badge")).toHaveLength(2);
    expect(screen.getAllByTestId("favorited-by-badge")[0]).toHaveTextContent(
      "Favorited by 7"
    );
  });

  it("shows empty state with create-songset CTA when no songsets", () => {
    render(
      <HomePageClient {...defaultProps} recentSongsets={[]} />
    );
    expect(screen.getByText("No songsets yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Create your first songset" })).toHaveAttribute(
      "href",
      "/songsets"
    );
  });

  it("shows empty state with browse-catalog CTA when no favorites", () => {
    render(
      <HomePageClient {...defaultProps} recentFavoriteSongs={[]} communityFavorites={[]} />
    );
    expect(screen.getByText("No favorites yet")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Browse the catalog" })).toHaveAttribute(
      "href",
      "/songsets"
    );
  });

  it("hides community section when there are no community favorites", () => {
    render(<HomePageClient {...defaultProps} communityFavorites={[]} />);
    expect(screen.queryByText("From the community")).not.toBeInTheDocument();
  });

  it("renders view-all links to /songsets and /favorites", () => {
    render(<HomePageClient {...defaultProps} />);
    const viewAllLinks = screen.getAllByRole("link", { name: "View all" });
    expect(viewAllLinks).toHaveLength(2);
    expect(viewAllLinks[0]).toHaveAttribute("href", "/songsets");
    expect(viewAllLinks[1]).toHaveAttribute("href", "/favorites");
  });

  it("opens ShareDialog when share is clicked", async () => {
    render(<HomePageClient {...defaultProps} />);
    fireEvent.click(screen.getAllByRole("button", { name: "Share" })[0]);
    await waitFor(() => {
      expect(screen.getByText("Songset s1")).toBeInTheDocument();
    });
  });
});
