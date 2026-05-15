import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  AudioPlayerProvider,
  useAudioPlayerContext,
  AudioTrack,
} from "@/contexts/AudioPlayerContext";

// Mock audio element
const mockPlay = vi.fn();

// Test component that uses the context
function TestPlayer() {
  const {
    currentTrack,
    state,
    play,
    pause,
    togglePlay,
    seek,
    setVolume,
    toggleMute,
    toggleLoop,
    setLoopWindow,
    clearLoopWindow,
    stop,
  } = useAudioPlayerContext();

  const testTrack: AudioTrack = {
    id: "test-1",
    title: "Test Song",
    artist: "Test Artist",
    src: "https://example.com/test.mp3",
    type: "song",
    duration: 180,
  };

  return (
    <div>
      <div data-testid="current-track">
        {currentTrack ? currentTrack.title : "No track"}
      </div>
      <div data-testid="is-playing">{state.isPlaying ? "playing" : "paused"}</div>
      <div data-testid="current-time">{state.currentTime}</div>
      <div data-testid="volume">{state.volume}</div>
      <div data-testid="is-muted">{state.isMuted ? "muted" : "unmuted"}</div>
      <div data-testid="is-looping">{state.isLooping ? "looping" : "not-looping"}</div>
      <div data-testid="loop-start">{state.loopWindowStart}</div>
      <div data-testid="loop-end">{state.loopWindowEnd}</div>

      <button data-testid="play-button" onClick={() => play(testTrack)}>
        Play
      </button>
      <button data-testid="pause-button" onClick={pause}>
        Pause
      </button>
      <button data-testid="toggle-button" onClick={togglePlay}>
        Toggle
      </button>
      <button data-testid="seek-button" onClick={() => seek(30)}>
        Seek to 30
      </button>
      <button data-testid="volume-button" onClick={() => setVolume(0.5)}>
        Set Volume 0.5
      </button>
      <button data-testid="mute-button" onClick={toggleMute}>
        Toggle Mute
      </button>
      <button data-testid="loop-button" onClick={toggleLoop}>
        Toggle Loop
      </button>
      <button data-testid="set-loop-button" onClick={() => setLoopWindow(10, 20)}>
        Set Loop 10-20
      </button>
      <button data-testid="clear-loop-button" onClick={clearLoopWindow}>
        Clear Loop
      </button>
      <button data-testid="stop-button" onClick={stop}>
        Stop
      </button>
    </div>
  );
}

describe("AudioPlayerContext", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockPlay.mockResolvedValue(undefined);
  });

  it("provides default state when no track is loaded", () => {
    render(
      <AudioPlayerProvider>
        <TestPlayer />
      </AudioPlayerProvider>
    );

    expect(screen.getByTestId("current-track")).toHaveTextContent("No track");
    expect(screen.getByTestId("is-playing")).toHaveTextContent("paused");
    expect(screen.getByTestId("volume")).toHaveTextContent("1");
    expect(screen.getByTestId("is-muted")).toHaveTextContent("unmuted");
    expect(screen.getByTestId("is-looping")).toHaveTextContent("not-looping");
  });

  it("loads and plays a track", async () => {
    const user = userEvent.setup();
    
    render(
      <AudioPlayerProvider>
        <TestPlayer />
      </AudioPlayerProvider>
    );

    await user.click(screen.getByTestId("play-button"));

    await waitFor(() => {
      expect(screen.getByTestId("current-track")).toHaveTextContent("Test Song");
      expect(screen.getByTestId("is-playing")).toHaveTextContent("playing");
    });
  });

  it("pauses playback", async () => {
    const user = userEvent.setup();
    
    render(
      <AudioPlayerProvider>
        <TestPlayer />
      </AudioPlayerProvider>
    );

    // First play
    await user.click(screen.getByTestId("play-button"));
    await waitFor(() => {
      expect(screen.getByTestId("is-playing")).toHaveTextContent("playing");
    });

    // Then pause
    await user.click(screen.getByTestId("pause-button"));
    expect(screen.getByTestId("is-playing")).toHaveTextContent("paused");
  });

  it("toggles play/pause state", async () => {
    const user = userEvent.setup();
    
    render(
      <AudioPlayerProvider>
        <TestPlayer />
      </AudioPlayerProvider>
    );

    // First load a track by playing
    await user.click(screen.getByTestId("play-button"));
    await waitFor(() => {
      expect(screen.getByTestId("is-playing")).toHaveTextContent("playing");
    });

    // Toggle to pause
    await user.click(screen.getByTestId("toggle-button"));
    expect(screen.getByTestId("is-playing")).toHaveTextContent("paused");

    // Toggle back to play
    await user.click(screen.getByTestId("toggle-button"));
    await waitFor(() => {
      expect(screen.getByTestId("is-playing")).toHaveTextContent("playing");
    });
  });

  it("seeks to specified time", async () => {
    const user = userEvent.setup();
    
    render(
      <AudioPlayerProvider>
        <TestPlayer />
      </AudioPlayerProvider>
    );

    // Load track first
    await user.click(screen.getByTestId("play-button"));
    await waitFor(() => {
      expect(screen.getByTestId("current-track")).toHaveTextContent("Test Song");
    });

    await user.click(screen.getByTestId("seek-button"));
    // Seek updates state, but actual audio element is mocked
    expect(screen.getByTestId("current-time")).toHaveTextContent("30");
  });

  it("sets volume", async () => {
    const user = userEvent.setup();
    
    render(
      <AudioPlayerProvider>
        <TestPlayer />
      </AudioPlayerProvider>
    );

    await user.click(screen.getByTestId("volume-button"));
    expect(screen.getByTestId("volume")).toHaveTextContent("0.5");
  });

  it("toggles mute state", async () => {
    const user = userEvent.setup();
    
    render(
      <AudioPlayerProvider>
        <TestPlayer />
      </AudioPlayerProvider>
    );

    expect(screen.getByTestId("is-muted")).toHaveTextContent("unmuted");
    
    await user.click(screen.getByTestId("mute-button"));
    expect(screen.getByTestId("is-muted")).toHaveTextContent("muted");

    await user.click(screen.getByTestId("mute-button"));
    expect(screen.getByTestId("is-muted")).toHaveTextContent("unmuted");
  });

  it("toggles loop state", async () => {
    const user = userEvent.setup();
    
    render(
      <AudioPlayerProvider>
        <TestPlayer />
      </AudioPlayerProvider>
    );

    expect(screen.getByTestId("is-looping")).toHaveTextContent("not-looping");
    
    await user.click(screen.getByTestId("loop-button"));
    expect(screen.getByTestId("is-looping")).toHaveTextContent("looping");

    await user.click(screen.getByTestId("loop-button"));
    expect(screen.getByTestId("is-looping")).toHaveTextContent("not-looping");
  });

  it("sets loop window", async () => {
    const user = userEvent.setup();
    
    render(
      <AudioPlayerProvider>
        <TestPlayer />
      </AudioPlayerProvider>
    );

    await user.click(screen.getByTestId("set-loop-button"));
    
    expect(screen.getByTestId("is-looping")).toHaveTextContent("looping");
    expect(screen.getByTestId("loop-start")).toHaveTextContent("10");
    expect(screen.getByTestId("loop-end")).toHaveTextContent("20");
  });

  it("clears loop window", async () => {
    const user = userEvent.setup();
    
    render(
      <AudioPlayerProvider>
        <TestPlayer />
      </AudioPlayerProvider>
    );

    // Set loop first
    await user.click(screen.getByTestId("set-loop-button"));
    expect(screen.getByTestId("is-looping")).toHaveTextContent("looping");

    // Clear loop
    await user.click(screen.getByTestId("clear-loop-button"));
    expect(screen.getByTestId("is-looping")).toHaveTextContent("not-looping");
    expect(screen.getByTestId("loop-start")).toHaveTextContent("0");
    expect(screen.getByTestId("loop-end")).toHaveTextContent("0");
  });

  it("stops playback and resets", async () => {
    const user = userEvent.setup();
    
    render(
      <AudioPlayerProvider>
        <TestPlayer />
      </AudioPlayerProvider>
    );

    // Play first
    await user.click(screen.getByTestId("play-button"));
    await waitFor(() => {
      expect(screen.getByTestId("is-playing")).toHaveTextContent("playing");
    });

    // Stop
    await user.click(screen.getByTestId("stop-button"));
    expect(screen.getByTestId("is-playing")).toHaveTextContent("paused");
    expect(screen.getByTestId("current-time")).toHaveTextContent("0");
  });

  it("throws error when useAudioPlayerContext is used outside provider", () => {
    // Suppress console.error for this test
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    
    function ComponentWithoutProvider() {
      useAudioPlayerContext();
      return null;
    }

    expect(() => {
      render(<ComponentWithoutProvider />);
    }).toThrow("useAudioPlayerContext must be used within an AudioPlayerProvider");

    consoleError.mockRestore();
  });
});
