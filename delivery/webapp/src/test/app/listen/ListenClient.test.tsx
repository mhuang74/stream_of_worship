import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, fireEvent, waitFor } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import { ListenClient } from "@/app/listen/ListenClient";
import type { SongCardData } from "@/components/songset/SongCard";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/listen",
}));

const mockPlay = vi.fn();
const mockPause = vi.fn();

vi.mock("@/contexts/AudioPlayerContext", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/contexts/AudioPlayerContext")>();
  return {
    ...actual,
    useAudioPlayerContext: () => ({
      play: mockPlay,
      pause: mockPause,
      stop: vi.fn(),
      currentTrack: null,
      state: {
        isPlaying: false,
        currentTime: 0,
        duration: 0,
        volume: 1,
        isMuted: false,
        isLooping: false,
        loopWindowStart: 0,
        loopWindowEnd: 0,
      },
      togglePlay: vi.fn(),
      seek: vi.fn(),
      setVolume: vi.fn(),
      toggleMute: vi.fn(),
      toggleLoop: vi.fn(),
      setLoopWindow: vi.fn(),
      clearLoopWindow: vi.fn(),
      audioRef: { current: null },
    }),
  };
});

vi.mock("@/lib/r2/public-url", () => ({
  getPublicAudioUrl: vi.fn(() => null),
}));

vi.mock("@/hooks/useOfflineRedirect", () => ({
  useOfflineRedirect: () => {},
}));

const mockFetch = vi.fn();
global.fetch = mockFetch;

function makeSong(id: string, title?: string): SongCardData {
  return {
    id,
    title: title ?? `Song ${id}`,
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

describe("ListenClient", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/songs/albums") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ albums: [] }),
        });
      }
      if (url.startsWith("/api/discovery")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ songs: [], hasMore: false }),
        });
      }
      if (url === "/api/favorites") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ songIds: [] }),
        });
      }
      if (url.includes("inMySongsets=1")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              songs: [
                {
                  ...makeSong("ms-1", "Songset Song 1"),
                  memberSongsets: [
                    { id: "ss-1", name: "Sunday Set" },
                    { id: "ss-2", name: "Easter Set" },
                  ],
                },
                {
                  ...makeSong("ms-2", "Songset Song 2"),
                  memberSongsets: [{ id: "ss-1", name: "Sunday Set" }],
                },
              ],
            }),
        });
      }
      if (url.includes("/api/songs")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ songs: [], total: 0 }),
        });
      }
      return Promise.resolve({ ok: false });
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders all three sections with localized headings in English", async () => {
    render(
      <ListenClient favoriteSongIds={[]} />
    );

    expect(screen.getByRole("heading", { name: "Listen" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Search" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Songs in My Songsets" })
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Discovery" })).toBeInTheDocument();
  });

  it("renders localized headings in zh-Hant", async () => {
    render(
      <ListenClient favoriteSongIds={[]} />,
      "zh-Hant"
    );

    expect(screen.getByRole("heading", { name: "收聽" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "搜尋" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "我的敬拜歌單裡的詩歌" })
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "探索" })).toBeInTheDocument();
  });

  it("renders my-songset songs deduplicated with songset chips as links", async () => {
    render(
      <ListenClient favoriteSongIds={[]} />
    );

    await waitFor(() => {
      expect(screen.getByTestId("listen-my-songsets-list")).toBeInTheDocument();
    });
    expect(screen.getByText("Songset Song 1")).toBeInTheDocument();
    expect(screen.getByText("Songset Song 2")).toBeInTheDocument();

    // Chips link to /songsets/{id}.
    const chipsList1 = screen.getByTestId("member-songset-chips-ms-1");
    const chipLinks = chipsList1.querySelectorAll("a");
    expect(chipLinks).toHaveLength(2);
    expect(chipLinks[0]).toHaveAttribute("href", "/songsets/ss-1");
    expect(chipLinks[0]).toHaveTextContent("Sunday Set");
    expect(chipLinks[1]).toHaveAttribute("href", "/songsets/ss-2");
    expect(screen.getByTestId("member-songset-chips-ms-2").querySelectorAll("a")).toHaveLength(1);
  });

  it("shows per-section empty states", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/songs/albums") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ albums: [] }) });
      }
      if (url.startsWith("/api/discovery")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ songs: [], hasMore: false }) });
      }
      if (url === "/api/favorites") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ songIds: [] }) });
      }
      if (url.includes("inMySongsets=1")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ songs: [] }) });
      }
      if (url.includes("/api/songs")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ songs: [], total: 0 }) });
      }
      return Promise.resolve({ ok: false });
    });

    render(
      <ListenClient favoriteSongIds={[]} />
    );

    await waitFor(() => {
      expect(screen.getByText("No songset songs yet")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByText("Nothing new from the community yet")).toBeInTheDocument();
    });
  });

  it("renders Alternate Chinese empty states in zh-Hant", async () => {
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/songs/albums") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ albums: [] }) });
      }
      if (url.startsWith("/api/discovery")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ songs: [], hasMore: false }) });
      }
      if (url === "/api/favorites") {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ songIds: [] }) });
      }
      if (url.includes("inMySongsets=1")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ songs: [] }) });
      }
      if (url.includes("/api/songs")) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ songs: [], total: 0 }) });
      }
      return Promise.resolve({ ok: false });
    });

    render(
      <ListenClient favoriteSongIds={[]} />,
      "zh-Hant"
    );

    await waitFor(() => {
      expect(screen.getByText("還沒有敬拜歌單詩歌")).toBeInTheDocument();
    });
    await waitFor(() => {
      expect(screen.getByText("社群還沒有新的詩歌")).toBeInTheDocument();
    });
  });

  it("renders discovery songs with favorite count badges and load-more when hasMore", async () => {
    const discoverySongs = [
      { ...makeSong("d-1", "Discovery 1"), favoriteCount: 7 },
      { ...makeSong("d-2", "Discovery 2"), favoriteCount: 3 },
    ];
    mockFetch.mockImplementation((url: string) => {
      if (url === "/api/songs/albums") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ albums: [] }),
        });
      }
      if (url.startsWith("/api/discovery")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve(
              url.includes("offset=0")
                ? { songs: discoverySongs, hasMore: true }
                : { songs: [], hasMore: false }
            ),
        });
      }
      if (url === "/api/favorites") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ songIds: [] }),
        });
      }
      if (url.includes("inMySongsets=1")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ songs: [] }),
        });
      }
      if (url.includes("/api/songs")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ songs: [], total: 0 }),
        });
      }
      return Promise.resolve({ ok: false });
    });

    render(
      <ListenClient favoriteSongIds={[]} />
    );

    await waitFor(() => {
      expect(screen.getByText("Discovery 1")).toBeInTheDocument();
    });
    expect(screen.getAllByTestId("favorited-by-badge").length).toBe(2);
    expect(screen.getByTestId("discovery-load-more")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("discovery-load-more"));
    await waitFor(() => {
      expect(
        mockFetch.mock.calls.some(([url]) => String(url).includes("offset=2"))
      ).toBe(true);
    });
  });
  it("tapping a my-songsets card's heart toggles optimistically (completion-gated)", async () => {
    let unfavorited = false;
    mockFetch.mockImplementation((url: string, init?: RequestInit) => {
      if (url === "/api/songs/albums") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ albums: [] }),
        });
      }
      if (url.startsWith("/api/discovery")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ songs: [], hasMore: false }),
        });
      }
      if (url === "/api/favorites") {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ songIds: ["ms-1"] }),
        });
      }
      if (url.startsWith("/api/favorites/")) {
        unfavorited = init?.method === "DELETE";
        return Promise.resolve({ ok: true });
      }
      if (url.includes("inMySongsets=1")) {
        return Promise.resolve({
          ok: true,
          json: () =>
            Promise.resolve({
              songs: [
                {
                  ...makeSong("ms-1", "Songset Song 1"),
                  memberSongsets: [{ id: "ss-1", name: "Sunday Set" }],
                },
              ],
            }),
        });
      }
      if (url.includes("/api/songs")) {
        return Promise.resolve({
          ok: true,
          json: () => Promise.resolve({ songs: [], total: 0 }),
        });
      }
      return Promise.resolve({ ok: false });
    });

    // Favorited songs are completion-gated in (isFavorite bypasses the gate),
    // so the heart is enabled and its toggle is optimistic.
    render(<ListenClient favoriteSongIds={["ms-1"]} />);

    await waitFor(() => {
      expect(screen.getByTestId("listen-my-songsets-list")).toBeInTheDocument();
    });

    const heart = screen.getAllByTestId("favorite-button")[0];
    expect(heart).toHaveAttribute("data-favorite", "true");

    fireEvent.click(heart);
    await waitFor(() => {
      expect(unfavorited).toBe(true);
    });
    // Optimistic: card stays while the heart state flips. Re-query: React
    // re-renders the card, so the original element reference goes stale.
    expect(screen.getByText("Songset Song 1")).toBeInTheDocument();
    expect(screen.getAllByTestId("favorite-button")[0]).toHaveAttribute(
      "data-favorite",
      "false"
    );
  });
});
