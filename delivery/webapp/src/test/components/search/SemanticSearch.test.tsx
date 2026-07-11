import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SemanticSearch } from "@/components/search/SemanticSearch";

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
}));

const mockPlay = vi.fn();

vi.mock("@/contexts/AudioPlayerContext", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/contexts/AudioPlayerContext")>();
  return {
    ...actual,
    useAudioPlayerContext: () => ({
      play: mockPlay,
      pause: vi.fn(),
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

const mockGetPublicAudioUrl = vi.fn(() => null);

vi.mock("@/lib/r2/public-url", () => ({
  getPublicAudioUrl: (...args: unknown[]) => mockGetPublicAudioUrl(...args),
}));

const mockFetch = vi.fn();
global.fetch = mockFetch;

const mockSongs = [
  {
    id: "song-1",
    title: "Amazing Grace",
    composer: "John Newton",
    lyricist: null,
    albumName: "Hymns",
    musicalKey: "G",
    similarity: 0.92,
    matchingSnippet: "Amazing grace how sweet the sound",
    whyThisMatch: ["Amazing grace how sweet the sound", "That saved a wretch like me"],
    recordings: [
      {
        contentHash: "abc123",
        hashPrefix: "abc",
        durationSeconds: 180,
        tempoBpm: 72,
        musicalKey: "G",
        visibilityStatus: "published",
      },
    ],
  },
  {
    id: "song-2",
    title: "Great Is Thy Faithfulness",
    composer: null,
    lyricist: null,
    albumName: "Hymns",
    musicalKey: "D",
    similarity: 0.78,
    matchingSnippet: null,
    whyThisMatch: [],
    recordings: [
      {
        contentHash: "def456",
        hashPrefix: "def",
        durationSeconds: 210,
        tempoBpm: 80,
        musicalKey: "D",
        visibilityStatus: "published",
      },
    ],
  },
];

const defaultProps = {
  onAddSong: vi.fn().mockResolvedValue(undefined),
  existingSongIds: [],
};
const hymnsFilter = { albumName: "Hymns", albumSeries: "Classic" };
const worshipFilter = { albumName: "Worship", albumSeries: null };

describe("SemanticSearch", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const renderComponent = (props = {}) =>
    render(<SemanticSearch {...defaultProps} {...props} />);

  describe("rendering", () => {
    it("renders the textarea", () => {
      renderComponent();
      expect(screen.getByTestId("semantic-search-input")).toBeInTheDocument();
    });

    it("renders the search button", () => {
      renderComponent();
      expect(screen.getByTestId("semantic-search-button")).toBeInTheDocument();
    });

    it("search button is enabled when query is empty", () => {
      renderComponent();
      expect(screen.getByTestId("semantic-search-button")).not.toBeDisabled();
    });

    it("search button is enabled when query has text", () => {
      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "songs about grace" } });
      expect(screen.getByTestId("semantic-search-button")).not.toBeDisabled();
    });

    it("does not show results before search", () => {
      renderComponent();
      expect(screen.queryByTestId("semantic-search-results")).not.toBeInTheDocument();
    });

    it("renders describe help text", () => {
      renderComponent();
      expect(screen.getByTestId("describe-help-text")).toBeInTheDocument();
      expect(screen.getByTestId("describe-help-text").textContent).toContain("在神寶座前");
      expect(screen.getByTestId("describe-help-text").textContent).toContain("Enter");
    });
  });

  describe("search functionality", () => {
    it("calls the semantic search API on button click", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: mockSongs, query: "grace", total: 2 }),
      });

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "songs about grace" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledWith(
          "/api/songs/search/semantic",
          expect.objectContaining({
            method: "POST",
            body: JSON.stringify({ query: "songs about grace", limit: 20 }),
          })
        );
      });
    });

    it("browses songs when Search is pressed with a blank description", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: mockSongs, total: 2 }),
      });

      renderComponent();
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledWith("/api/songs?limit=50");
      });
      expect(screen.queryByTestId("similarity-badge")).not.toBeInTheDocument();
    });

    it("sends shared filters in the semantic search body", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: [], query: "grace", total: 0 }),
      });

      renderComponent({ albums: [hymnsFilter, worshipFilter], keys: ["D"], bpmRange: "slow" });
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "songs about grace" } });

      expect(mockFetch).not.toHaveBeenCalled();

      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalledWith(
          "/api/songs/search/semantic",
          expect.objectContaining({
            method: "POST",
            body: JSON.stringify({
              query: "songs about grace",
              limit: 20,
              albums: [hymnsFilter, worshipFilter],
              keys: ["D"],
              bpmRange: "slow",
            }),
          })
        );
      });
    });

    it("displays results after successful search", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: mockSongs, query: "grace", total: 2 }),
      });

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "grace" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(screen.getByText("Amazing Grace")).toBeInTheDocument();
        expect(screen.getByText("Great Is Thy Faithfulness")).toBeInTheDocument();
      });
    });

    it("displays matching snippet for songs with snippets", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: mockSongs, query: "grace", total: 2 }),
      });

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "grace" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(screen.getByTestId("matching-snippet")).toBeInTheDocument();
        expect(screen.getByText(/Amazing grace how sweet the sound/)).toBeInTheDocument();
      });
    });

    it("displays similarity badges on results", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: mockSongs, query: "grace", total: 2 }),
      });

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "grace" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        const badges = screen.getAllByTestId("similarity-badge");
        expect(badges.length).toBeGreaterThan(0);
        expect(badges[0].textContent).toContain("92% match");
      });
    });

    it("shows Why this match? toggle for songs with whyThisMatch", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: mockSongs, query: "grace", total: 2 }),
      });

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "grace" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(screen.getByTestId("why-this-match-toggle")).toBeInTheDocument();
      });
    });

    it("expands Why this match? content on click", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: mockSongs, query: "grace", total: 2 }),
      });

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "grace" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(screen.getByTestId("why-this-match-toggle")).toBeInTheDocument();
      });

      fireEvent.click(screen.getByTestId("why-this-match-toggle"));

      await waitFor(() => {
        expect(screen.getByTestId("why-this-match-content")).toBeInTheDocument();
      });
    });

    it("triggers search on Enter", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: [], query: "test", total: 0 }),
      });

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "worship songs" } });
      fireEvent.keyDown(input, { key: "Enter" });

      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalled();
      });
    });

    it("fires search on plain Enter and calls preventDefault", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: [], query: "test", total: 0 }),
      });
      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "worship songs" } });

      const event = new KeyboardEvent("keydown", { key: "Enter", bubbles: true });
      const spy = vi.spyOn(event, "preventDefault");
      fireEvent(input, event);

      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalled();
      });
      expect(spy).toHaveBeenCalled();
    });
  });

  describe("empty and loading states", () => {
    it("shows loading state while searching", async () => {
      let resolveSearch: ((v: unknown) => void) | null = null;
      mockFetch.mockReturnValue(
        new Promise((resolve) => {
          resolveSearch = resolve;
        })
      );

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "grace" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(screen.getByText("Searching by meaning...")).toBeInTheDocument();
      });

      resolveSearch!({
        ok: true,
        json: () => Promise.resolve({ songs: [], query: "grace", total: 0 }),
      });
    });

    it("shows empty state when no results found", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: [], query: "obscure", total: 0 }),
      });

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "obscure query" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(screen.getByText("No matching songs found")).toBeInTheDocument();
      });
    });
  });

  describe("error handling", () => {
    it("shows error when API returns error status", async () => {
      mockFetch.mockResolvedValue({
        ok: false,
        status: 500,
        json: () => Promise.resolve({ error: "Semantic search failed" }),
      });

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "songs" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(screen.getByTestId("semantic-search-error")).toBeInTheDocument();
        expect(screen.getByText("Semantic search failed")).toBeInTheDocument();
      });
    });

    it("shows error when fetch throws", async () => {
      mockFetch.mockRejectedValue(new Error("Network error"));

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "songs" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(screen.getByTestId("semantic-search-error")).toBeInTheDocument();
      });
    });
  });

  describe("503 auto-fallback", () => {
    it("calls onSwitchToSearchTab on 503 response", async () => {
      const onSwitchToSearchTab = vi.fn();
      mockFetch.mockResolvedValue({
        ok: false,
        status: 503,
        json: () => Promise.resolve({ error: "Semantic search unavailable" }),
      });

      renderComponent({ onSwitchToSearchTab });
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "grace" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(onSwitchToSearchTab).toHaveBeenCalledWith("grace");
      });
    });

    it("shows error when onSwitchToSearchTab is not provided and 503", async () => {
      mockFetch.mockResolvedValue({
        ok: false,
        status: 503,
        json: () => Promise.resolve({ error: "Semantic search unavailable" }),
      });

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "grace" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(screen.getByTestId("semantic-search-error")).toBeInTheDocument();
      });
    });
  });

  describe("add song functionality", () => {
    it("calls onAddSong when add button is clicked", async () => {
      const onAddSong = vi.fn().mockResolvedValue(undefined);
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: mockSongs, query: "grace", total: 2 }),
      });

      renderComponent({ onAddSong });

      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "grace" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(screen.getAllByTestId("add-song-button").length).toBeGreaterThan(0);
      });

      const addButtons = screen.getAllByTestId("add-song-button");
      fireEvent.click(addButtons[0]);

      await waitFor(() => {
        expect(onAddSong).toHaveBeenCalledWith(mockSongs[0]);
      });
    });

    it("marks already-added songs as added", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: mockSongs, query: "grace", total: 2 }),
      });

      renderComponent({ existingSongIds: ["song-1"] });

      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "grace" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        const addButtons = screen.getAllByTestId("add-song-button");
        expect(addButtons[0]).toBeDisabled();
      });
    });
  });

  describe("play functionality", () => {
    it("renders song cards with play buttons", async () => {
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: mockSongs, query: "grace", total: 2 }),
      });

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "grace" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        const playButtons = screen.getAllByTestId("song-play-button");
        expect(playButtons.length).toBeGreaterThan(0);
      });
    });

    it("uses public R2 URL when available", async () => {
      mockGetPublicAudioUrl.mockReturnValue("https://pub-test.r2.dev/abc/audio.mp3");
      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: mockSongs, query: "grace", total: 2 }),
      });

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "grace" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(screen.getAllByTestId("song-play-button").length).toBeGreaterThan(0);
      });

      const playButtons = screen.getAllByTestId("song-play-button");
      fireEvent.click(playButtons[0]);

      await waitFor(() => {
        expect(mockGetPublicAudioUrl).toHaveBeenCalledWith("abc");
        expect(mockPlay).toHaveBeenCalledWith({
          id: "song-song-1",
          title: "Amazing Grace",
          artist: "John Newton",
          src: "https://pub-test.r2.dev/abc/audio.mp3",
          type: "song",
          duration: 180,
        });
      });
    });

    it("shows error toast when song has no recordings", async () => {
      const { toast } = await import("sonner");

      const songsNoRecordings = [
        {
          id: "song-nr",
          title: "No Recording Song",
          composer: "Test",
          lyricist: null,
          albumName: null,
          musicalKey: null,
          similarity: 0.5,
          matchingSnippet: null,
          whyThisMatch: [],
          recordings: [],
        },
      ];

      mockFetch.mockResolvedValue({
        ok: true,
        json: () => Promise.resolve({ songs: songsNoRecordings, query: "test", total: 1 }),
      });

      renderComponent();
      const input = screen.getByTestId("semantic-search-input");
      fireEvent.change(input, { target: { value: "test" } });
      fireEvent.click(screen.getByTestId("semantic-search-button"));

      await waitFor(() => {
        expect(screen.getByText("No Recording Song")).toBeInTheDocument();
      });

      const playButtons = screen.getAllByTestId("song-play-button");
      fireEvent.click(playButtons[0]);

      await waitFor(() => {
        expect(toast.error).toHaveBeenCalledWith("No audio available for this song");
      });
    });
  });
});
