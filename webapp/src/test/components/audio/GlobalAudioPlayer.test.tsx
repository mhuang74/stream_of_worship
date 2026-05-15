import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { GlobalAudioPlayer } from "@/components/audio/GlobalAudioPlayer";
import { useAudioPlayer } from "@/hooks/useAudioPlayer";

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

    // Should have access to audio player state
    expect(screen.getByTestId("track-status")).toHaveTextContent("No track");
    expect(screen.getByTestId("play-status")).toHaveTextContent("Paused");
  });

  it("does not show player bar initially", () => {
    render(
      <GlobalAudioPlayer>
        <TestChildComponent />
      </GlobalAudioPlayer>
    );

    // Player bar should not be visible when no track is loaded
    expect(screen.queryByTestId("audio-player-bar")).not.toBeInTheDocument();
  });

  it("wraps content with AudioPlayerProvider", () => {
    const { container } = render(
      <GlobalAudioPlayer>
        <div data-testid="wrapped-content">Wrapped</div>
      </GlobalAudioPlayer>
    );

    expect(screen.getByTestId("wrapped-content")).toBeInTheDocument();
    // The provider should render without errors
    expect(container).toBeTruthy();
  });
});
