import { describe, it, expect, beforeEach, afterEach, vi, type Mock } from "vitest";
import { screen, fireEvent, waitFor, act } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import { HomePageClient } from "@/app/page/HomePageClient";
import { probeConnectivity, setConnectivityProbe } from "@/hooks/useConnectivity";
import { getOfflineRecord } from "@/lib/offline/offline-index";
import type { OfflineSongsetRecord } from "@/lib/offline/offline-index";
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

vi.mock("@/lib/offline/offline-index", () => ({
  getOfflineRecord: vi.fn(async () => null),
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
  themes: [],
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
  const probe = vi.fn<() => Promise<boolean>>();
  let locationAssignMock: Mock;
  let locationReplaceMock: Mock;
  let onLineDescriptor: PropertyDescriptor | undefined;

  function stubOnline(online: boolean): void {
    Object.defineProperty(navigator, "onLine", {
      value: online,
      configurable: true,
    });
  }

  // Fail toward offline: Connectivity stays Unknown until the probe
  // positively confirms Online — settle one successful probe first.
  async function confirmOnline(): Promise<void> {
    await act(async () => {
      await probeConnectivity();
    });
  }

  beforeEach(async () => {
    mockPush.mockClear();
    vi.clearAllMocks();
    probe.mockReset();
    probe.mockResolvedValue(true);
    setConnectivityProbe(probe);
    onLineDescriptor = Object.getOwnPropertyDescriptor(navigator, "onLine");
    stubOnline(true);
    await confirmOnline();
  });

  afterEach(() => {
    setConnectivityProbe(null);
    if (onLineDescriptor) {
      Object.defineProperty(navigator, "onLine", onLineDescriptor);
    }
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

  it("navigates to the play controller when play is clicked", async () => {
    render(<HomePageClient {...defaultProps} />);
    // The handler awaits the offline-index read before navigating.
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Play" }));
    });
    expect(mockPush).toHaveBeenCalledWith("/songsets/s1/play/controller");
  });

  // Fail toward offline (issue #211): navigator.onLine false → definitive
  // Offline → the deterministic full-document path, never SPA navigation.
  it("offline play is a full document navigation", async () => {
    stubOnline(false);
    locationAssignMock = vi.fn();
    locationReplaceMock = vi.fn();
    const locationDescriptor = Object.getOwnPropertyDescriptor(window, "location");
    Object.defineProperty(window, "location", {
      value: { assign: locationAssignMock, replace: locationReplaceMock },
      configurable: true,
    });
    try {
      render(<HomePageClient {...defaultProps} />);
      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Play" }));
      });

      expect(locationAssignMock).toHaveBeenCalledWith("/songsets/s1/play/controller");
      expect(mockPush).not.toHaveBeenCalledWith("/songsets/s1/play/controller");
    } finally {
      if (locationDescriptor) {
        Object.defineProperty(window, "location", locationDescriptor);
      }
    }
  });

  // Cache-first entry (issue #211 follow-up): a songset with an offline
  // record takes the full-document path even when positively online.
  it("cached songset play is a full document navigation even when online", async () => {
    vi.mocked(getOfflineRecord).mockResolvedValue({
      songsetId: "s1",
      renderJobId: "job-1",
      songsetName: "Set 1",
      cachedMp3: true,
      cachedMp4: true,
      cachedChapters: true,
      cachedAt: "2026-09-15T00:00:00.000Z",
      chapterContentHashes: [],
    } satisfies OfflineSongsetRecord);
    locationAssignMock = vi.fn();
    locationReplaceMock = vi.fn();
    const locationDescriptor = Object.getOwnPropertyDescriptor(window, "location");
    Object.defineProperty(window, "location", {
      value: { assign: locationAssignMock, replace: locationReplaceMock },
      configurable: true,
    });
    try {
      render(<HomePageClient {...defaultProps} />);
      fireEvent.click(screen.getByRole("button", { name: "Play" }));
      await waitFor(() => {
        expect(locationAssignMock).toHaveBeenCalledWith(
          "/songsets/s1/play/controller"
        );
      });
      expect(mockPush).not.toHaveBeenCalledWith("/songsets/s1/play/controller");
    } finally {
      if (locationDescriptor) {
        Object.defineProperty(window, "location", locationDescriptor);
      }
    }
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
  it("renders favorite and community song grids in a responsive two-column layout", () => {
    render(<HomePageClient {...defaultProps} />);
    const favoritesGrid = screen
      .getByText("Song f1")
      .closest("div[class*='grid grid-cols-1 md:grid-cols-2 gap-2']");
    const communityGrid = screen
      .getByText("Song c1")
      .closest("div[class*='grid grid-cols-1 md:grid-cols-2 gap-2']");
    expect(favoritesGrid).not.toBeNull();
    expect(communityGrid).not.toBeNull();
    expect(favoritesGrid).toContainElement(screen.getByText("Song f2"));
    expect(communityGrid).toContainElement(screen.getByText("Song c2"));
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
