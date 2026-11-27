import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";

import { useSongsetAudio } from "@/hooks/useSongsetAudio";
import type { TimelineItemSource } from "@/lib/arrangements";

const mocks = vi.hoisted(() => {
  const prepareAt = vi.fn().mockResolvedValue(undefined);
  const session = {
    prepareAt,
    play: vi.fn().mockResolvedValue(undefined),
    pause: vi.fn(),
    stop: vi.fn(),
    seek: vi.fn(),
    destroy: vi.fn(),
    isPlaying: vi.fn(() => false),
    getTimelinePositionSeconds: vi.fn(() => 0),
    getProgress: vi.fn(() => null),
    getError: vi.fn(() => null),
    onStateChange: vi.fn(() => () => {}),
  };
  return {
    session,
    prepareAt,
    createSongsetAudioSession: vi.fn(() => session),
  };
});

vi.mock("@/lib/audio/songset-audio", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/audio/songset-audio")>();
  return {
    ...actual,
    createSongsetAudioSession: mocks.createSongsetAudioSession,
  };
});

vi.mock("@/contexts/AudioPlayerContext", () => ({
  useAudioPlayerContext: () => ({ stop: vi.fn() }),
}));

const ITEMS: TimelineItemSource[] = [
  {
    itemId: "item-1",
    recordingHashPrefix: "a1b2c3d4e5f6",
    arrangementSteps: null,
    gapBeats: 2,
    crossfadeEnabled: false,
    crossfadeDurationSeconds: null,
    tempoBpm: 120,
    recordingDurationSeconds: 120,
  },
];

describe("useSongsetAudio.prepareAt", () => {
  beforeEach(() => {
    mocks.createSongsetAudioSession.mockClear();
    mocks.prepareAt.mockClear();
    mocks.session.getError.mockReturnValue(null);
  });

  it("builds the session before preparing a cold preview", async () => {
    const { result } = renderHook(() =>
      useSongsetAudio({ items: ITEMS, active: true, onPreviewError: vi.fn() })
    );

    // No `play()` yet: the first transition preview after opening the sheet.
    await act(async () => {
      await result.current.prepareAt(10);
    });

    expect(mocks.createSongsetAudioSession).toHaveBeenCalledTimes(1);
    expect(mocks.prepareAt).toHaveBeenCalledWith(10);

    // Preparing again reuses the session instead of rebuilding it.
    await act(async () => {
      await result.current.prepareAt(20);
    });
    expect(mocks.createSongsetAudioSession).toHaveBeenCalledTimes(1);
    expect(mocks.prepareAt).toHaveBeenLastCalledWith(20);
  });

  it("prepares nothing while inactive", async () => {
    const { result } = renderHook(() =>
      useSongsetAudio({ items: ITEMS, active: false, onPreviewError: vi.fn() })
    );

    await act(async () => {
      await result.current.prepareAt(10);
    });

    expect(mocks.createSongsetAudioSession).not.toHaveBeenCalled();
    expect(mocks.prepareAt).not.toHaveBeenCalled();
  });
});
