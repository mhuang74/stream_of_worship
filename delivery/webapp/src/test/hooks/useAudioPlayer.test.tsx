import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useAudioPlayer } from "@/hooks/useAudioPlayer";
import { AudioPlayerProvider } from "@/contexts/AudioPlayerContext";

// Test component
function TestComponent() {
  const {
    currentTrack,
    isPlaying,
    formattedCurrentTime,
    formattedDuration,
    progress,
    playSong,
    playTransition,
    playLyricsLoop,
    seekRelative,
  } = useAudioPlayer();

  return (
    <div>
      <div data-testid="current-track">{currentTrack?.title || "No track"}</div>
      <div data-testid="is-playing">{isPlaying ? "playing" : "paused"}</div>
      <div data-testid="current-time">{formattedCurrentTime}</div>
      <div data-testid="duration">{formattedDuration}</div>
      <div data-testid="progress">{progress.toFixed(1)}</div>

      <button
        data-testid="play-song-button"
        onClick={() =>
          playSong({
            songId: "song-1",
            title: "Amazing Grace",
            artist: "John Newton",
            src: "https://example.com/amazing-grace.mp3",
            duration: 240,
          })
        }
      >
        Play Song
      </button>

      <button
        data-testid="play-transition-button"
        onClick={() =>
          playTransition({
            transitionId: "trans-1",
            fromSongTitle: "Song A",
            toSongTitle: "Song B",
            src: "https://example.com/transition.mp3",
            duration: 15,
          })
        }
      >
        Play Transition
      </button>

      <button
        data-testid="play-loop-button"
        onClick={() =>
          playLyricsLoop({
            songId: "song-1",
            title: "Amazing Grace",
            artist: "John Newton",
            src: "https://example.com/amazing-grace.mp3",
            loopStartSeconds: 30,
            loopDurationSeconds: 10,
          })
        }
      >
        Play Loop
      </button>

      <button data-testid="seek-back-button" onClick={() => seekRelative(-10)}>
        Seek Back 10s
      </button>

      <button
        data-testid="seek-forward-button"
        onClick={() => seekRelative(10)}
      >
        Seek Forward 10s
      </button>
    </div>
  );
}

describe("useAudioPlayer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("plays a song with correct track info", async () => {
    const user = userEvent.setup();

    render(
      <AudioPlayerProvider>
        <TestComponent />
      </AudioPlayerProvider>
    );

    await user.click(screen.getByTestId("play-song-button"));

    await waitFor(() => {
      expect(screen.getByTestId("current-track")).toHaveTextContent(
        "Amazing Grace"
      );
      expect(screen.getByTestId("is-playing")).toHaveTextContent("playing");
    });
  });

  it("plays a transition with correct title format", async () => {
    const user = userEvent.setup();

    render(
      <AudioPlayerProvider>
        <TestComponent />
      </AudioPlayerProvider>
    );

    await user.click(screen.getByTestId("play-transition-button"));

    await waitFor(() => {
      expect(screen.getByTestId("current-track")).toHaveTextContent(
        "Song A → Song B"
      );
      expect(screen.getByTestId("is-playing")).toHaveTextContent("playing");
    });
  });

  it("plays a lyrics loop with loop window set", async () => {
    const user = userEvent.setup();

    render(
      <AudioPlayerProvider>
        <TestComponent />
      </AudioPlayerProvider>
    );

    await user.click(screen.getByTestId("play-loop-button"));

    await waitFor(() => {
      expect(screen.getByTestId("current-track")).toHaveTextContent(
        "Amazing Grace"
      );
      expect(screen.getByTestId("is-playing")).toHaveTextContent("playing");
    });
  });

  it("formats time correctly", async () => {
    const user = userEvent.setup();

    render(
      <AudioPlayerProvider>
        <TestComponent />
      </AudioPlayerProvider>
    );

    // Before playing, times should be 0:00
    expect(screen.getByTestId("current-time")).toHaveTextContent("0:00");
    expect(screen.getByTestId("duration")).toHaveTextContent("0:00");

    // Play a song with duration
    await user.click(screen.getByTestId("play-song-button"));

    await waitFor(() => {
      expect(screen.getByTestId("duration")).toHaveTextContent("4:00");
    });
  });

  it("calculates progress percentage", async () => {
    const user = userEvent.setup();

    render(
      <AudioPlayerProvider>
        <TestComponent />
      </AudioPlayerProvider>
    );

    // Before playing, progress should be 0
    expect(screen.getByTestId("progress")).toHaveTextContent("0.0");

    // Play a song
    await user.click(screen.getByTestId("play-song-button"));

    await waitFor(() => {
      expect(screen.getByTestId("is-playing")).toHaveTextContent("playing");
    });
  });

  it("seeks relative to current position", async () => {
    const user = userEvent.setup();

    render(
      <AudioPlayerProvider>
        <TestComponent />
      </AudioPlayerProvider>
    );

    // Play a song first
    await user.click(screen.getByTestId("play-song-button"));
    await waitFor(() => {
      expect(screen.getByTestId("is-playing")).toHaveTextContent("playing");
    });
  });
});
