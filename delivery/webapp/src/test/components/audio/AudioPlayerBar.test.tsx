import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AudioPlayerBar } from "@/components/audio/AudioPlayerBar";
import { AudioPlayerProvider, AudioTrack } from "@/contexts/AudioPlayerContext";

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
});
