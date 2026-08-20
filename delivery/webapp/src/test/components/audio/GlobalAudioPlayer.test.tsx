import { describe, it, expect, vi, beforeEach } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GlobalAudioPlayer } from "@/components/audio/GlobalAudioPlayer";
import { useAudioPlayer } from "@/hooks/useAudioPlayer";
import {
  AudioTrack,
  useAudioPlayerContext,
} from "@/contexts/AudioPlayerContext";
import { renderWithLocale as render } from "@/test/render";

const mockPathname = vi.hoisted(() => vi.fn(() => "/"));

vi.mock("next/navigation", () => ({
  usePathname: mockPathname,
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

// Test component that uses the audio player
function TestChildComponent() {
  const { currentTrack, isPlaying } = useAudioPlayer();

  return (
    <div>
      <div data-testid="child-content">Child Component</div>
      <div data-testid="track-status">
        {currentTrack ? currentTrack.title : "No track"}
      </div>
      <div data-testid="play-status">{isPlaying ? "Playing" : "Paused"}</div>
    </div>
  );
}

describe("GlobalAudioPlayer", () => {
  beforeEach(() => {
    mockPathname.mockReturnValue("/");
  });

  it("renders children content", () => {
    render(
      <GlobalAudioPlayer>
        <TestChildComponent />
      </GlobalAudioPlayer>
    );

    expect(screen.getByTestId("child-content")).toHaveTextContent(
      "Child Component"
    );
  });

  it("provides audio player context to children", () => {
    render(
      <GlobalAudioPlayer>
        <TestChildComponent />
      </GlobalAudioPlayer>
    );

    expect(screen.getByTestId("track-status")).toHaveTextContent("No track");
    expect(screen.getByTestId("play-status")).toHaveTextContent("Paused");
  });

  it("does not show player bar initially", () => {
    render(
      <GlobalAudioPlayer>
        <TestChildComponent />
      </GlobalAudioPlayer>
    );

    expect(screen.queryByTestId("audio-player-bar")).not.toBeInTheDocument();
  });

  it("wraps content with AudioPlayerProvider", () => {
    const { container } = render(
      <GlobalAudioPlayer>
        <div data-testid="wrapped-content">Wrapped</div>
      </GlobalAudioPlayer>
    );

    expect(screen.getByTestId("wrapped-content")).toBeInTheDocument();
    expect(container).toBeTruthy();
  });

  it("stops playbar audio when entering a controller page", async () => {
    const user = userEvent.setup();
    const testTrack: AudioTrack = {
      id: "track-1",
      title: "Test Song",
      artist: "Test Artist",
      src: "https://example.com/test.mp3",
      type: "song",
      duration: 180,
    };

    function StartPlaybackChild() {
      const { play } = useAudioPlayerContext();
      return (
        <button data-testid="start-playback" onClick={() => play(testTrack)}>
          Start Playback
        </button>
      );
    }

    function TrackStatusChild() {
      const { currentTrack } = useAudioPlayerContext();
      return (
        <div data-testid="track-status">
          {currentTrack ? currentTrack.title : "No track"}
        </div>
      );
    }

    const { rerender } = render(
      <GlobalAudioPlayer>
        <StartPlaybackChild />
        <TrackStatusChild />
      </GlobalAudioPlayer>
    );

    await user.click(screen.getByTestId("start-playback"));
    await waitFor(() => {
      expect(screen.getByTestId("track-status")).toHaveTextContent("Test Song");
    });

    // Navigate to a controller route; re-render so usePathname() re-evaluates.
    // The playbar track must be stopped on route entry.
    mockPathname.mockReturnValue("/songsets/test/play/controller");
    rerender(
      <GlobalAudioPlayer>
        <StartPlaybackChild />
        <TrackStatusChild />
      </GlobalAudioPlayer>
    );

    await waitFor(() => {
      expect(screen.getByTestId("track-status")).toHaveTextContent("No track");
    });
  });
});
