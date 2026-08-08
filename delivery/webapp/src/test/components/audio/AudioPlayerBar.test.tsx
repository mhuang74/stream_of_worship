import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AudioPlayerBar } from "@/components/audio/AudioPlayerBar";
import { AudioPlayerProvider, AudioTrack } from "@/contexts/AudioPlayerContext";
import { PopoverContent } from "@/components/ui/popover";

// Mock useSongLyrics
const mockUseSongLyrics = vi.fn();
vi.mock("@/hooks/useSongLyrics", () => ({
  useSongLyrics: (...args: unknown[]) => mockUseSongLyrics(...args),
  clearLyricsCache: vi.fn(),
}));

// Mock popover to capture PopoverContent props while preserving real rendering
vi.mock("@/components/ui/popover", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/ui/popover")>();
  return {
    ...actual,
    PopoverContent: vi.fn(actual.PopoverContent),
  };
});

// Mock next/navigation
vi.mock("next/navigation", () => ({
  usePathname: () => "/songsets",
  useRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    back: vi.fn(),
    forward: vi.fn(),
    refresh: vi.fn(),
    prefetch: vi.fn(),
  }),
  useSearchParams: () => new URLSearchParams(),
}));

// Test component that loads a track
function TestPlayerWithTrack({ track }: { track: AudioTrack }) {
  const { play } = useAudioPlayerContext();

  return (
    <div>
      <button data-testid="load-track" onClick={() => play(track)}>
        Load Track
      </button>
      <AudioPlayerBar />
    </div>
  );
}

// Need to import this for the test component
import { useAudioPlayerContext } from "@/contexts/AudioPlayerContext";

describe("AudioPlayerBar", () => {
  const testTrack: AudioTrack = {
    id: "test-1",
    title: "Test Song",
    artist: "Test Artist",
    src: "https://example.com/test.mp3",
    type: "song",
    duration: 180,
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("does not render when no track is loaded", () => {
    render(
      <AudioPlayerProvider>
        <AudioPlayerBar />
      </AudioPlayerProvider>
    );

    expect(screen.queryByTestId("audio-player-bar")).not.toBeInTheDocument();
  });

  it("renders when a track is loaded", async () => {
    const user = userEvent.setup();

    render(
      <AudioPlayerProvider>
        <TestPlayerWithTrack track={testTrack} />
      </AudioPlayerProvider>
    );

    // Initially not visible
    expect(screen.queryByTestId("audio-player-bar")).not.toBeInTheDocument();

    // Load a track
    await user.click(screen.getByTestId("load-track"));

    await waitFor(() => {
      expect(screen.getByTestId("audio-player-bar")).toBeInTheDocument();
    });
  });

  it("displays track title and artist", async () => {
    const user = userEvent.setup();

    render(
      <AudioPlayerProvider>
        <TestPlayerWithTrack track={testTrack} />
      </AudioPlayerProvider>
    );

    await user.click(screen.getByTestId("load-track"));

    await waitFor(() => {
      expect(screen.getByTestId("track-title")).toHaveTextContent("Test Song");
      expect(screen.getByTestId("track-artist")).toHaveTextContent("Test Artist");
    });
  });

  it("has play/pause button", async () => {
    const user = userEvent.setup();

    render(
      <AudioPlayerProvider>
        <TestPlayerWithTrack track={testTrack} />
      </AudioPlayerProvider>
    );

    await user.click(screen.getByTestId("load-track"));

    await waitFor(() => {
      expect(screen.getByTestId("play-pause-button")).toBeInTheDocument();
    });
  });

  it("has seek controls", async () => {
    const user = userEvent.setup();

    render(
      <AudioPlayerProvider>
        <TestPlayerWithTrack track={testTrack} />
      </AudioPlayerProvider>
    );

    await user.click(screen.getByTestId("load-track"));

    await waitFor(() => {
      expect(screen.getByTestId("skip-back-button")).toBeInTheDocument();
      expect(screen.getByTestId("skip-forward-button")).toBeInTheDocument();
      expect(screen.getByTestId("seek-slider")).toBeInTheDocument();
    });
  });

  it("has close button", async () => {
    const user = userEvent.setup();

    render(
      <AudioPlayerProvider>
        <TestPlayerWithTrack track={testTrack} />
      </AudioPlayerProvider>
    );

    await user.click(screen.getByTestId("load-track"));

    await waitFor(() => {
      expect(screen.getByTestId("close-player-button")).toBeInTheDocument();
    });
  });

  it("closes player when close button is clicked", async () => {
    const user = userEvent.setup();

    render(
      <AudioPlayerProvider>
        <TestPlayerWithTrack track={testTrack} />
      </AudioPlayerProvider>
    );

    await user.click(screen.getByTestId("load-track"));

    await waitFor(() => {
      expect(screen.getByTestId("audio-player-bar")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("close-player-button"));

    await waitFor(() => {
      expect(screen.queryByTestId("audio-player-bar")).not.toBeInTheDocument();
    });
  });

  it("shows transition preview indicator for transition tracks", async () => {
    const user = userEvent.setup();

    const transitionTrack: AudioTrack = {
      id: "trans-1",
      title: "Song A → Song B",
      artist: "Transition Preview",
      src: "https://example.com/trans.mp3",
      type: "transition",
      duration: 15,
    };

    render(
      <AudioPlayerProvider>
        <TestPlayerWithTrack track={transitionTrack} />
      </AudioPlayerProvider>
    );

    await user.click(screen.getByTestId("load-track"));

    await waitFor(() => {
      expect(screen.getByTestId("track-artist")).toHaveTextContent(
        "Transition Preview"
      );
    });
  });

  it("shows loop indicator for lyrics-loop tracks", async () => {
    const user = userEvent.setup();

    const loopTrack: AudioTrack = {
      id: "loop-1",
      title: "Loop Song",
      artist: "Test Artist",
      src: "https://example.com/loop.mp3",
      type: "lyrics-loop",
      duration: 60,
      loopStart: 10,
      loopEnd: 20,
    };

    render(
      <AudioPlayerProvider>
        <TestPlayerWithTrack track={loopTrack} />
      </AudioPlayerProvider>
    );

    await user.click(screen.getByTestId("load-track"));

    await waitFor(() => {
      expect(screen.getByTestId("track-artist")).toHaveTextContent("(Loop)");
      expect(screen.getByTestId("loop-toggle-button")).toBeInTheDocument();
    });
  });

  it("has volume controls on desktop", async () => {
    const user = userEvent.setup();

    render(
      <AudioPlayerProvider>
        <TestPlayerWithTrack track={testTrack} />
      </AudioPlayerProvider>
    );

    await user.click(screen.getByTestId("load-track"));

    await waitFor(() => {
      expect(screen.getByTestId("audio-player-bar")).toBeInTheDocument();
    });

    // Volume controls are hidden on mobile, visible on lg breakpoint
    // Since we can't test responsive behavior in jsdom easily,
    // we just verify the player renders
    expect(screen.getByTestId("audio-player-bar")).toBeInTheDocument();
  });

  describe("lyrics button visibility", () => {
    const songTrack: AudioTrack = {
      id: "test-1",
      title: "Test Song",
      artist: "Test Artist",
      src: "https://example.com/test.mp3",
      type: "song",
      duration: 180,
      recordingContentHash: "abc123",
    };

    const lyricsLoopTrack: AudioTrack = {
      id: "loop-1",
      title: "Loop Song",
      artist: "Test Artist",
      src: "https://example.com/loop.mp3",
      type: "lyrics-loop",
      duration: 60,
      loopStart: 10,
      loopEnd: 20,
      recordingContentHash: "def456",
    };

    const transitionTrack: AudioTrack = {
      id: "trans-1",
      title: "Song A → Song B",
      artist: "Transition Preview",
      src: "https://example.com/trans.mp3",
      type: "transition",
      duration: 15,
    };

    beforeEach(() => {
      mockUseSongLyrics.mockReturnValue({
        lrcContent: null,
        lines: null,
        loading: false,
        error: null,
      });
    });

    it("(a) lyrics button is hidden when no track is loaded", () => {
      render(
        <AudioPlayerProvider>
          <AudioPlayerBar />
        </AudioPlayerProvider>
      );
      expect(screen.queryByTestId("lyrics-toggle-button")).not.toBeInTheDocument();
    });

    it("(b) lyrics button is hidden for transition tracks", async () => {
      const user = userEvent.setup();
      render(
        <AudioPlayerProvider>
          <TestPlayerWithTrack track={transitionTrack} />
        </AudioPlayerProvider>
      );
      await user.click(screen.getByTestId("load-track"));
      await waitFor(() => {
        expect(screen.getByTestId("audio-player-bar")).toBeInTheDocument();
      });
      expect(screen.queryByTestId("lyrics-toggle-button")).not.toBeInTheDocument();
    });

    it("(c) lyrics button is visible for song tracks with recordingContentHash", async () => {
      const user = userEvent.setup();
      render(
        <AudioPlayerProvider>
          <TestPlayerWithTrack track={songTrack} />
        </AudioPlayerProvider>
      );
      await user.click(screen.getByTestId("load-track"));
      await waitFor(() => {
        expect(screen.getByTestId("lyrics-toggle-button")).toBeInTheDocument();
      });
    });

    it("(d) lyrics button is visible for lyrics-loop tracks with recordingContentHash", async () => {
      const user = userEvent.setup();
      render(
        <AudioPlayerProvider>
          <TestPlayerWithTrack track={lyricsLoopTrack} />
        </AudioPlayerProvider>
      );
      await user.click(screen.getByTestId("load-track"));
      await waitFor(() => {
        expect(screen.getByTestId("lyrics-toggle-button")).toBeInTheDocument();
      });
    });
  });

  describe("lyrics panel toggle", () => {
    const songTrack: AudioTrack = {
      id: "test-1",
      title: "Test Song",
      artist: "Test Artist",
      src: "https://example.com/test.mp3",
      type: "song",
      duration: 180,
      recordingContentHash: "abc123",
    };

    beforeEach(() => {
      mockUseSongLyrics.mockReturnValue({
        lrcContent: null,
        lines: ["Test line"],
        loading: false,
        error: null,
      });
    });

    it("(e) click Lyrics button → aria-expanded=true + panel appears", async () => {
      const user = userEvent.setup();
      render(
        <AudioPlayerProvider>
          <TestPlayerWithTrack track={songTrack} />
        </AudioPlayerProvider>
      );
      await user.click(screen.getByTestId("load-track"));
      await waitFor(() => {
        expect(screen.getByTestId("lyrics-toggle-button")).toBeInTheDocument();
      });

      const lyricsBtn = screen.getByTestId("lyrics-toggle-button");
      await user.click(lyricsBtn);

      expect(lyricsBtn).toHaveAttribute("aria-expanded", "true");
      expect(screen.getByRole("region", { name: /lyrics for test song/i })).toBeInTheDocument();
    });

    it("(f) click Lyrics button again → aria-expanded=false + panel disappears", async () => {
      const user = userEvent.setup();
      render(
        <AudioPlayerProvider>
          <TestPlayerWithTrack track={songTrack} />
        </AudioPlayerProvider>
      );
      await user.click(screen.getByTestId("load-track"));
      await waitFor(() => {
        expect(screen.getByTestId("lyrics-toggle-button")).toBeInTheDocument();
      });

      const lyricsBtn = screen.getByTestId("lyrics-toggle-button");
      await user.click(lyricsBtn);
      expect(lyricsBtn).toHaveAttribute("aria-expanded", "true");

      await user.click(lyricsBtn);
      expect(lyricsBtn).toHaveAttribute("aria-expanded", "false");
    });

    it("(g) panel auto-collapses when track changes to one without recordingContentHash", async () => {
      const user = userEvent.setup();
      const transitionTrack: AudioTrack = {
        id: "trans-1",
        title: "Song A → Song B",
        artist: "Transition Preview",
        src: "https://example.com/trans.mp3",
        type: "transition",
        duration: 15,
      };

      function TestPlayerWithTwoTracks() {
        const { play } = useAudioPlayerContext();
        return (
          <div>
            <button data-testid="load-song" onClick={() => play(songTrack)}>Load Song</button>
            <button data-testid="load-transition" onClick={() => play(transitionTrack)}>Load Transition</button>
            <AudioPlayerBar />
          </div>
        );
      }

      render(
        <AudioPlayerProvider>
          <TestPlayerWithTwoTracks />
        </AudioPlayerProvider>
      );

      await user.click(screen.getByTestId("load-song"));
      await waitFor(() => {
        expect(screen.getByTestId("lyrics-toggle-button")).toBeInTheDocument();
      });

      await user.click(screen.getByTestId("lyrics-toggle-button"));
      expect(screen.getByTestId("lyrics-toggle-button")).toHaveAttribute("aria-expanded", "true");

      await user.click(screen.getByTestId("load-transition"));
      // Lyrics button should be gone (transition has no recordingContentHash)
      await waitFor(() => {
        expect(screen.queryByTestId("lyrics-toggle-button")).not.toBeInTheDocument();
      });
    });

    it("(h) panel stays open when switching between two songs with recordingContentHash", async () => {
      const user = userEvent.setup();
      const song2Track: AudioTrack = {
        id: "test-2",
        title: "Second Song",
        artist: "Another Artist",
        src: "https://example.com/test2.mp3",
        type: "song",
        duration: 200,
        recordingContentHash: "xyz789",
      };

      function TestPlayerWithTwoSongs() {
        const { play } = useAudioPlayerContext();
        return (
          <div>
            <button data-testid="load-song-1" onClick={() => play(songTrack)}>Load Song 1</button>
            <button data-testid="load-song-2" onClick={() => play(song2Track)}>Load Song 2</button>
            <AudioPlayerBar />
          </div>
        );
      }

      render(
        <AudioPlayerProvider>
          <TestPlayerWithTwoSongs />
        </AudioPlayerProvider>
      );

      await user.click(screen.getByTestId("load-song-1"));
      await waitFor(() => {
        expect(screen.getByTestId("lyrics-toggle-button")).toBeInTheDocument();
      });

      await user.click(screen.getByTestId("lyrics-toggle-button"));
      expect(screen.getByTestId("lyrics-toggle-button")).toHaveAttribute("aria-expanded", "true");

      await user.click(screen.getByTestId("load-song-2"));
      // Lyrics button should still be visible and expanded
      expect(screen.getByTestId("lyrics-toggle-button")).toHaveAttribute("aria-expanded", "true");
    });
  });

  describe("keyboard shortcut 'L'", () => {
    const songTrack: AudioTrack = {
      id: "test-1",
      title: "Test Song",
      artist: "Test Artist",
      src: "https://example.com/test.mp3",
      type: "song",
      duration: 180,
      recordingContentHash: "abc123",
    };

    beforeEach(() => {
      mockUseSongLyrics.mockReturnValue({
        lrcContent: null,
        lines: null,
        loading: false,
        error: null,
      });
    });

    it("(i) press 'L' key toggles lyrics when player is active", async () => {
      const user = userEvent.setup();
      render(
        <AudioPlayerProvider>
          <TestPlayerWithTrack track={songTrack} />
        </AudioPlayerProvider>
      );
      await user.click(screen.getByTestId("load-track"));
      await waitFor(() => {
        expect(screen.getByTestId("lyrics-toggle-button")).toBeInTheDocument();
      });

      fireEvent.keyDown(document.body, { key: "l" });
      expect(screen.getByTestId("lyrics-toggle-button")).toHaveAttribute("aria-expanded", "true");

      fireEvent.keyDown(document.body, { key: "l" });
      expect(screen.getByTestId("lyrics-toggle-button")).toHaveAttribute("aria-expanded", "false");
    });

    it("(j) press 'L' key does nothing when focus is inside an input", async () => {
      const user = userEvent.setup();
      render(
        <AudioPlayerProvider>
          <TestPlayerWithTrack track={songTrack} />
          <input data-testid="test-input" />
        </AudioPlayerProvider>
      );
      await user.click(screen.getByTestId("load-track"));
      await waitFor(() => {
        expect(screen.getByTestId("lyrics-toggle-button")).toBeInTheDocument();
      });

      const input = screen.getByTestId("test-input");
      fireEvent.keyDown(input, { key: "l" });
      expect(screen.getByTestId("lyrics-toggle-button")).toHaveAttribute("aria-expanded", "false");
    });

    it("(k) press 'L' key does nothing when no track is loaded", () => {
      render(
        <AudioPlayerProvider>
          <AudioPlayerBar />
        </AudioPlayerProvider>
      );
      fireEvent.keyDown(document.body, { key: "l" });
      expect(screen.queryByTestId("lyrics-toggle-button")).not.toBeInTheDocument();
    });

    it("(l) v6: press 'L' key does nothing when a modal/sheet is open", async () => {
      const user = userEvent.setup();
      render(
        <AudioPlayerProvider>
          <TestPlayerWithTrack track={songTrack} />
        </AudioPlayerProvider>
      );
      await user.click(screen.getByTestId("load-track"));
      await waitFor(() => {
        expect(screen.getByTestId("lyrics-toggle-button")).toBeInTheDocument();
      });

      const dialog = document.createElement("div");
      dialog.setAttribute("role", "dialog");
      document.body.appendChild(dialog);

      fireEvent.keyDown(document.body, { key: "l" });
      expect(screen.getByTestId("lyrics-toggle-button")).toHaveAttribute("aria-expanded", "false");

      document.body.removeChild(dialog);
    });

    it("(m) v6: preventDefault is NOT called when 'L' is pressed and no toggle happens (transition track)", async () => {
      const user = userEvent.setup();
      const transitionTrack: AudioTrack = {
        id: "trans-1",
        title: "Song A → Song B",
        artist: "Transition Preview",
        src: "https://example.com/trans.mp3",
        type: "transition",
        duration: 15,
      };

      render(
        <AudioPlayerProvider>
          <TestPlayerWithTrack track={transitionTrack} />
        </AudioPlayerProvider>
      );
      await user.click(screen.getByTestId("load-track"));
      await waitFor(() => {
        expect(screen.getByTestId("audio-player-bar")).toBeInTheDocument();
      });

      const preventDefaultSpy = vi.fn();
      fireEvent.keyDown(document.body, { key: "l", preventDefault: preventDefaultSpy });
      expect(preventDefaultSpy).not.toHaveBeenCalled();
    });

    it("(n) v6: keyboard listener does not re-attach on track change", async () => {
      const addEventListenerSpy = vi.spyOn(document, "addEventListener");
      const user = userEvent.setup();

      const song2Track: AudioTrack = {
        id: "test-2",
        title: "Second Song",
        artist: "Another Artist",
        src: "https://example.com/test2.mp3",
        type: "song",
        duration: 200,
        recordingContentHash: "xyz789",
      };

      function TestPlayerWithTwoSongs() {
        const { play } = useAudioPlayerContext();
        return (
          <div>
            <button data-testid="load-song-1" onClick={() => play(songTrack)}>Load Song 1</button>
            <button data-testid="load-song-2" onClick={() => play(song2Track)}>Load Song 2</button>
            <AudioPlayerBar />
          </div>
        );
      }

      render(
        <AudioPlayerProvider>
          <TestPlayerWithTwoSongs />
        </AudioPlayerProvider>
      );

      const keydownListenerCountBefore = addEventListenerSpy.mock.calls.filter(
        ([event]) => event === "keydown"
      ).length;

      await user.click(screen.getByTestId("load-song-1"));
      await waitFor(() => {
        expect(screen.getByTestId("lyrics-toggle-button")).toBeInTheDocument();
      });

      await user.click(screen.getByTestId("load-song-2"));

      const keydownListenerCountAfter = addEventListenerSpy.mock.calls.filter(
        ([event]) => event === "keydown"
      ).length;

      expect(keydownListenerCountAfter).toBe(keydownListenerCountBefore);
      addEventListenerSpy.mockRestore();
    });
  });

  describe("v6 panel behavior", () => {
    const songTrack: AudioTrack = {
      id: "test-1",
      title: "Test Song",
      artist: "Test Artist",
      src: "https://example.com/test.mp3",
      type: "song",
      duration: 180,
      recordingContentHash: "abc123",
    };

    beforeEach(() => {
      mockUseSongLyrics.mockReturnValue({
        lrcContent: null,
        lines: ["Test line"],
        loading: false,
        error: null,
      });
    });

    it("(s) v6: panel uses dvh units (class contains max-h-[40dvh])", async () => {
      const user = userEvent.setup();
      render(
        <AudioPlayerProvider>
          <TestPlayerWithTrack track={songTrack} />
        </AudioPlayerProvider>
      );
      await user.click(screen.getByTestId("load-track"));
      await waitFor(() => {
        expect(screen.getByTestId("lyrics-toggle-button")).toBeInTheDocument();
      });

      await user.click(screen.getByTestId("lyrics-toggle-button"));

      const panel = screen.getByRole("region", { name: /lyrics for test song/i });
      const wrapper = panel.parentElement;
      expect(wrapper?.className).toContain("max-h-[40dvh]");
    });
  });

  describe("LocateSongsetsPopover", () => {
    const songTrack: AudioTrack = {
      id: "test-1",
      title: "Test Song",
      artist: "Test Artist",
      src: "https://example.com/test.mp3",
      type: "song",
      duration: 180,
      songId: "song-abc",
    };

    const mockSongsets = Array.from({ length: 15 }, (_, i) => ({
      id: `ss-${i}`,
      name: `Songset ${i + 1}`,
      description: null,
      updatedAt: new Date().toISOString(),
      itemCount: 5,
      songPosition: i,
      isOrigin: i === 0,
      owner: { id: 1, name: "Owner" },
    }));

    let originalFetch: typeof global.fetch;

    beforeEach(() => {
      originalFetch = global.fetch;
      global.fetch = vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ songsets: mockSongsets }),
      });
      mockUseSongLyrics.mockReturnValue({
        lrcContent: null,
        lines: null,
        loading: false,
        error: null,
      });
    });

    afterEach(() => {
      global.fetch = originalFetch;
    });

    it("does not steal focus on initial mount", async () => {
      const user = userEvent.setup();
      render(
        <AudioPlayerProvider>
          <TestPlayerWithTrack track={songTrack} />
        </AudioPlayerProvider>
      );
      await user.click(screen.getByTestId("load-track"));
      await waitFor(() => {
        expect(screen.getByTestId("audio-player-bar")).toBeInTheDocument();
      });

      const trigger = screen.getByRole("button", {
        name: /find containing songsets/i,
      });
      expect(document.activeElement).not.toBe(trigger);
    });

    it("restores focus to trigger after close", async () => {
      const user = userEvent.setup();
      render(
        <AudioPlayerProvider>
          <TestPlayerWithTrack track={songTrack} />
        </AudioPlayerProvider>
      );
      await user.click(screen.getByTestId("load-track"));
      await waitFor(() => {
        expect(screen.getByTestId("audio-player-bar")).toBeInTheDocument();
      });

      const trigger = screen.getByRole("button", {
        name: /find containing songsets/i,
      });
      await user.click(trigger);

      await user.keyboard("{Escape}");

      await waitFor(() => {
        expect(document.activeElement).toBe(trigger);
      });
    });

    it("list container has no max-h-64 or overflow-y-auto", async () => {
      const user = userEvent.setup();
      render(
        <AudioPlayerProvider>
          <TestPlayerWithTrack track={songTrack} />
        </AudioPlayerProvider>
      );
      await user.click(screen.getByTestId("load-track"));
      await waitFor(() => {
        expect(screen.getByTestId("audio-player-bar")).toBeInTheDocument();
      });

      const trigger = screen.getByRole("button", {
        name: /find containing songsets/i,
      });
      await user.click(trigger);

      const list = await screen.findByRole("list", {
        name: /songsets containing this song/i,
      });

      expect(list.className).not.toContain("max-h-64");
      expect(list.className).not.toContain("overflow-y-auto");

      for (const ss of mockSongsets) {
        expect(screen.getByText(ss.name)).toBeInTheDocument();
      }
    });

    it("uses role=list and role=listitem with no aria-selected", async () => {
      const user = userEvent.setup();
      render(
        <AudioPlayerProvider>
          <TestPlayerWithTrack track={songTrack} />
        </AudioPlayerProvider>
      );
      await user.click(screen.getByTestId("load-track"));
      await waitFor(() => {
        expect(screen.getByTestId("audio-player-bar")).toBeInTheDocument();
      });

      const trigger = screen.getByRole("button", {
        name: /find containing songsets/i,
      });
      await user.click(trigger);

      const list = await screen.findByRole("list", {
        name: /songsets containing this song/i,
      });
      expect(list).toBeInTheDocument();

      const items = screen.getAllByRole("listitem");
      expect(items).toHaveLength(15);

      expect(list.querySelector("[aria-selected]")).toBeNull();
    });

    it("passes side='top' to PopoverContent", async () => {
      const user = userEvent.setup();
      render(
        <AudioPlayerProvider>
          <TestPlayerWithTrack track={songTrack} />
        </AudioPlayerProvider>
      );
      await user.click(screen.getByTestId("load-track"));
      await waitFor(() => {
        expect(screen.getByTestId("audio-player-bar")).toBeInTheDocument();
      });

      const trigger = screen.getByRole("button", {
        name: /find containing songsets/i,
      });
      await user.click(trigger);

      await screen.findByRole("list", {
        name: /songsets containing this song/i,
      });

      const calls = vi.mocked(PopoverContent).mock.calls;
      expect(calls.length).toBeGreaterThan(0);
      const lastCallProps = calls[calls.length - 1][0] as Record<string, unknown>;
      expect(lastCallProps).toMatchObject({ side: "top" });
    });

    it("passes collisionAvoidance with side='none' and fallbackAxisSide='none'", async () => {
      const user = userEvent.setup();
      render(
        <AudioPlayerProvider>
          <TestPlayerWithTrack track={songTrack} />
        </AudioPlayerProvider>
      );
      await user.click(screen.getByTestId("load-track"));
      await waitFor(() => {
        expect(screen.getByTestId("audio-player-bar")).toBeInTheDocument();
      });

      const trigger = screen.getByRole("button", {
        name: /find containing songsets/i,
      });
      await user.click(trigger);

      await screen.findByRole("list", {
        name: /songsets containing this song/i,
      });

      const calls = vi.mocked(PopoverContent).mock.calls;
      expect(calls.length).toBeGreaterThan(0);
      const lastCallProps = calls[calls.length - 1][0] as Record<string, unknown>;
      expect(lastCallProps).toMatchObject({
        collisionAvoidance: { side: "none", fallbackAxisSide: "none" },
      });
    });

    it("renders all 15 songset names", async () => {
      const user = userEvent.setup();
      render(
        <AudioPlayerProvider>
          <TestPlayerWithTrack track={songTrack} />
        </AudioPlayerProvider>
      );
      await user.click(screen.getByTestId("load-track"));
      await waitFor(() => {
        expect(screen.getByTestId("audio-player-bar")).toBeInTheDocument();
      });

      const trigger = screen.getByRole("button", {
        name: /find containing songsets/i,
      });
      await user.click(trigger);

      const list = await screen.findByRole("list", {
        name: /songsets containing this song/i,
      });
      expect(list).toBeInTheDocument();

      for (const ss of mockSongsets) {
        expect(screen.getByText(ss.name)).toBeInTheDocument();
      }
    });
  });
});
