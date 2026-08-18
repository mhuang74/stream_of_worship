import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  AudioPlayerProvider,
  useAudioPlayerContext,
  AudioTrack,
} from "@/contexts/AudioPlayerContext";
import {
  isSongCompleted,
  resetCompletionForTests,
} from "@/lib/audio/completion";

function TriggerPlay({ track }: { track: AudioTrack }) {
  const { play } = useAudioPlayerContext();
  return <button onClick={() => play(track)}>play</button>;
}

function renderPlayer(track: AudioTrack) {
  return render(
    <AudioPlayerProvider>
      <TriggerPlay track={track} />
    </AudioPlayerProvider>
  );
}

function getAudioElement(): HTMLAudioElement {
  const audio = document.querySelector("audio");
  if (!audio) throw new Error("no <audio> element rendered");
  return audio as HTMLAudioElement;
}

/** Drives the native element to `progress` seconds and fires a timeupdate. */
function advance(audio: HTMLAudioElement, duration: number, currentTime: number) {
  Object.defineProperty(audio, "duration", { configurable: true, value: duration });
  audio.currentTime = currentTime;
  audio.dispatchEvent(new Event("timeupdate"));
}

describe("AudioPlayer completion gate (90% of a full song)", () => {
  beforeEach(() => resetCompletionForTests());

  it("marks a song completed when a full-length play crosses 90%", async () => {
    const user = userEvent.setup();
    renderPlayer({
      id: "song-1",
      title: "Song",
      artist: "Artist",
      src: "https://example.com/a.mp3",
      type: "song",
      duration: 100,
      songId: "song-1",
    });

    await user.click(screen.getByRole("button", { name: "play" }));
    await waitFor(() => expect(getAudioElement()).toBeTruthy());
    // Ensure the player has applied the track (currentTrackRef updated).
    await waitFor(() => expect(isSongCompleted("song-1")).toBe(false));

    advance(getAudioElement(), 100, 90);
    expect(isSongCompleted("song-1")).toBe(true);
  });

  it("does not complete a song below 90%", async () => {
    const user = userEvent.setup();
    renderPlayer({
      id: "song-2",
      title: "Song",
      artist: "Artist",
      src: "https://example.com/b.mp3",
      type: "song",
      duration: 100,
      songId: "song-2",
    });

    await user.click(screen.getByRole("button", { name: "play" }));
    await waitFor(() => expect(getAudioElement()).toBeTruthy());

    advance(getAudioElement(), 100, 50);
    expect(isSongCompleted("song-2")).toBe(false);
  });

  it("does not count playback of a transition or lyric loop toward completion", async () => {
    const user = userEvent.setup();
    renderPlayer({
      id: "song-3",
      title: "Song",
      artist: "Artist",
      src: "https://example.com/c.mp3",
      type: "transition",
      duration: 100,
      songId: "song-3",
    });

    await user.click(screen.getByRole("button", { name: "play" }));
    await waitFor(() => expect(getAudioElement()).toBeTruthy());

    advance(getAudioElement(), 100, 95);
    expect(isSongCompleted("song-3")).toBe(false);
  });
});
