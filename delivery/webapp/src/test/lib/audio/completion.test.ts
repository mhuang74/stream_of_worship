import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  getCompletedSongIds,
  isSongCompleted,
  markSongCompleted,
  subscribeCompletion,
  resetCompletionForTests,
} from "@/lib/audio/completion";

describe("completion module (client-side 90% listen gate)", () => {
  beforeEach(() => resetCompletionForTests());
  afterEach(() => resetCompletionForTests());

  it("returns false for a song not yet completed", () => {
    expect(isSongCompleted("song-1")).toBe(false);
  });

  it("marks a song completed and it is read back", () => {
    markSongCompleted("song-1");
    expect(isSongCompleted("song-1")).toBe(true);
    expect(getCompletedSongIds().has("song-1")).toBe(true);
  });

  it("is idempotent — re-marking does not duplicate", () => {
    markSongCompleted("song-1");
    markSongCompleted("song-1");
    expect(getCompletedSongIds().size).toBe(1);
  });

  it("marks songs independently", () => {
    markSongCompleted("a");
    markSongCompleted("b");
    expect(isSongCompleted("a")).toBe(true);
    expect(isSongCompleted("b")).toBe(true);
    expect(isSongCompleted("c")).toBe(false);
  });

  it("notifies subscribers only on the first mark of a song", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeCompletion(listener);

    markSongCompleted("song-1");
    expect(listener).toHaveBeenCalledTimes(1);

    // Already completed — no-op, no notification.
    markSongCompleted("song-1");
    expect(listener).toHaveBeenCalledTimes(1);

    unsubscribe();
    markSongCompleted("song-2");
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("persists across module reloads via localStorage", () => {
    markSongCompleted("song-1");
    // Reading the raw storage key outside the module proves persistence.
    const stored = JSON.parse(window.localStorage.getItem("sow_completed_song_ids") ?? "[]");
    expect(stored).toContain("song-1");
  });
});
