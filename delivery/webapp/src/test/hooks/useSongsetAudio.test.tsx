import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ArrangementTimeline, TimelineItemSource } from "@/lib/arrangements";
import type {
  SongsetAudioError,
  SongsetAudioProgress,
  SongsetAudioSession,
} from "@/lib/audio/songset-audio";
import {
  useSongsetAudio,
  type UseSongsetAudioOptions,
  type UseSongsetAudioResult,
} from "@/hooks/useSongsetAudio";

const mocks = vi.hoisted(() => ({
  playerStop: vi.fn(),
  createSession: vi.fn(),
}));

vi.mock("@/contexts/AudioPlayerContext", () => ({
  useAudioPlayerContext: () => ({
    stop: mocks.playerStop,
  }),
}));

vi.mock("@/lib/audio/songset-audio", async () => {
  const actual = await vi.importActual<Record<string, unknown>>(
    "@/lib/audio/songset-audio",
  );
  return {
    ...actual,
    createSongsetAudioSession: mocks.createSession,
  };
});

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function item(
  overrides: Partial<TimelineItemSource> & { itemId: string },
): TimelineItemSource {
  return {
    recordingHashPrefix: `hash-${overrides.itemId}`,
    recordingDurationSeconds: 100,
    arrangementSteps: null,
    gapBeats: 0,
    crossfadeEnabled: 0,
    crossfadeDurationSeconds: null,
    tempoBpm: null,
    ...overrides,
  };
}

const ITEMS: TimelineItemSource[] = [
  item({ itemId: "a" }),
  item({ itemId: "b" }),
];

interface FakeSession {
  session: SongsetAudioSession;
  setPlaying: (value: boolean) => void;
  setPosition: (value: number) => void;
  setError: (value: SongsetAudioError | null) => void;
  setProgress: (value: SongsetAudioProgress | null) => void;
  notify: () => void;
}

/** totalSeconds 200 matches buildArrangementTimeline(ITEMS). */
function makeFakeSession(totalSeconds = 200): FakeSession {
  const listeners = new Set<() => void>();
  const timeline: ArrangementTimeline = { items: [], totalSeconds };
  let playing = false;
  let position = 0;
  let error: SongsetAudioError | null = null;
  let progress: SongsetAudioProgress | null = null;
  const session: SongsetAudioSession = {
    timeline,
    totalSeconds,
    play: vi.fn(async () => {
      playing = true;
    }),
    pause: vi.fn(() => {
      playing = false;
      position = 42;
    }),
    stop: vi.fn(() => {
      playing = false;
    }),
    destroy: vi.fn(() => {
      playing = false;
    }),
    seek: vi.fn((seconds: number) => {
      position = seconds;
    }),
    startAtSeconds: vi.fn(async (seconds: number) => {
      position = seconds;
      playing = true;
    }),
    getTimelinePositionSeconds: () => position,
    isPlaying: () => playing,
    getProgress: () => progress,
    getError: () => error,
    onStateChange: (listener: () => void) => {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
  };
  return {
    session,
    setPlaying: (value) => {
      playing = value;
    },
    setPosition: (value) => {
      position = value;
    },
    setError: (value) => {
      error = value;
    },
    setProgress: (value) => {
      progress = value;
    },
    notify: () => {
      for (const listener of [...listeners]) listener();
    },
  };
}

function renderAudio(overrides: Partial<UseSongsetAudioOptions> = {}) {
  const onPreviewError = vi.fn();
  const initialProps: UseSongsetAudioOptions = {
    items: ITEMS,
    active: true,
    onPreviewError,
    ...overrides,
  };
  const view = renderHook(
    (props: UseSongsetAudioOptions) => useSongsetAudio(props),
    { initialProps },
  );
  return { ...view, onPreviewError };
}

async function runPlay(result: { current: UseSongsetAudioResult }) {
  await act(async () => {
    await result.current.play();
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useSongsetAudio", () => {
  let fake: FakeSession;

  beforeEach(() => {
    vi.clearAllMocks();
    fake = makeFakeSession();
    mocks.createSession.mockImplementation(() => fake.session);
  });

  it("starts idle with the arranged total, no progress, and no session", () => {
    const { result } = renderAudio();
    expect(result.current.status).toBe("idle");
    expect(result.current.totalSeconds).toBe(200);
    expect(result.current.timelinePositionSeconds).toBe(0);
    expect(result.current.errorMessage).toBeNull();
    expect(result.current.progress).toBeNull();
    expect(mocks.createSession).not.toHaveBeenCalled();
    expect(mocks.playerStop).not.toHaveBeenCalled();
  });

  it("stops the global player, then creates one session on the first play only", async () => {
    const { result } = renderAudio();
    await runPlay(result);
    expect(mocks.playerStop).toHaveBeenCalledTimes(1);
    expect(mocks.createSession).toHaveBeenCalledTimes(1);
    expect(fake.session.play).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe("playing");
    expect(mocks.playerStop.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.createSession.mock.invocationCallOrder[0],
    );

    await runPlay(result);
    expect(mocks.createSession).toHaveBeenCalledTimes(1);
  });

  it("rejects a missing recording before playback and creates no session", async () => {
    const brokenItems = [
      item({ itemId: "a" }),
      item({ itemId: "b", recordingHashPrefix: null }),
    ];
    const { result, onPreviewError } = renderAudio({ items: brokenItems });
    await runPlay(result);
    expect(onPreviewError).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "no-recording", itemId: "b" }),
    );
    expect(result.current.status).toBe("error");
    expect(result.current.errorMessage).not.toBeNull();
    expect(result.current.totalSeconds).toBe(0);
    expect(mocks.createSession).not.toHaveBeenCalled();
    expect(mocks.playerStop).not.toHaveBeenCalled();
  });

  it("tears the session down when the timeline inputs change", async () => {
    const { result, rerender } = renderAudio();
    await runPlay(result);
    const mutated = [
      ITEMS[0],
      item({
        itemId: "b",
        arrangementSteps: null,
        recordingHashPrefix: "hash-b2",
      }),
    ];
    rerender({
      items: mutated,
      active: true,
      onPreviewError: vi.fn(),
    });
    expect(fake.session.destroy).toHaveBeenCalled();
    expect(result.current.status).toBe("idle");
    await runPlay(result);
    expect(mocks.createSession).toHaveBeenCalledTimes(2);
  });

  it("does not tear down when the items array is new but content is identical", async () => {
    const { result, rerender } = renderAudio();
    await runPlay(result);
    rerender({
      items: [item({ itemId: "a" }), item({ itemId: "b" })],
      active: true,
      onPreviewError: vi.fn(),
    });
    expect(fake.session.destroy).not.toHaveBeenCalled();
    expect(result.current.status).toBe("playing");
  });

  it("tears down on active=false and blocks play until reactivated", async () => {
    const { result, rerender } = renderAudio();
    await runPlay(result);
    rerender({
      items: ITEMS,
      active: false,
      onPreviewError: vi.fn(),
    });
    expect(fake.session.destroy).toHaveBeenCalled();
    expect(result.current.status).toBe("idle");
    expect(result.current.progress).toBeNull();
    await runPlay(result);
    expect(mocks.createSession).toHaveBeenCalledTimes(1);
    rerender({
      items: ITEMS,
      active: true,
      onPreviewError: vi.fn(),
    });
    expect(result.current.status).toBe("idle");
    await runPlay(result);
    expect(mocks.createSession).toHaveBeenCalledTimes(2);
  });

  it("tears down on unmount", async () => {
    const { result, unmount } = renderAudio();
    await runPlay(result);
    unmount();
    expect(fake.session.destroy).toHaveBeenCalled();
  });

  it("forwards a session error once per distinct object", async () => {
    const { result, onPreviewError } = renderAudio();
    await runPlay(result);
    const error: SongsetAudioError = {
      kind: "playback-failed",
      itemId: "a",
      message: "boom",
    };
    act(() => {
      fake.setError(error);
      fake.setPlaying(false);
      fake.notify();
    });
    expect(onPreviewError).toHaveBeenCalledTimes(1);
    expect(onPreviewError).toHaveBeenCalledWith(error);
    expect(result.current.status).toBe("error");
    expect(result.current.errorMessage).toBe("boom");

    act(() => {
      fake.notify();
    });
    expect(onPreviewError).toHaveBeenCalledTimes(1);

    const nextError: SongsetAudioError = {
      kind: "playback-failed",
      itemId: "a",
      message: "boom again",
    };
    act(() => {
      fake.setError(nextError);
      fake.notify();
    });
    expect(onPreviewError).toHaveBeenCalledTimes(2);
    expect(onPreviewError).toHaveBeenLastCalledWith(nextError);
  });

  it("rejects play after recording a session failure", async () => {
    const { result } = renderAudio();
    const failure = new Error("autoplay blocked");
    fake.session.play = vi.fn().mockRejectedValueOnce(failure);

    await act(async () => {
      await expect(result.current.play()).rejects.toThrow("autoplay blocked");
    });
    expect(result.current.status).toBe("error");
    // No engine-recorded error: the hook falls back to the rejection's own
    // message; the dialog localizes the playback-failed kind.
    expect(result.current.errorMessage).toBe("autoplay blocked");
  });

  it("snapshots the session progress on every state change", async () => {
    const { result } = renderAudio();
    await runPlay(result);
    expect(result.current.progress).toBeNull();

    const fetching: SongsetAudioProgress = {
      itemId: "a",
      itemIndex: 0,
      itemCount: 2,
      phase: "fetching",
      receivedBytes: 250,
      totalBytes: 1000,
    };
    act(() => {
      fake.setProgress(fetching);
      fake.notify();
    });
    expect(result.current.progress).toEqual(fetching);
    expect(result.current.timelinePositionSeconds).toBe(0);

    const ready: SongsetAudioProgress = {
      ...fetching,
      phase: "ready",
      receivedBytes: 1000,
    };
    act(() => {
      fake.setProgress(ready);
      fake.notify();
    });
    expect(result.current.progress).toEqual(ready);

    act(() => {
      fake.setProgress(null);
      fake.notify();
    });
    expect(result.current.progress).toBeNull();
  });

  it("pauses, seeks, and reports the paused position", async () => {
    const { result } = renderAudio();
    await runPlay(result);
    act(() => {
      result.current.pause();
    });
    expect(fake.session.pause).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe("paused");
    expect(result.current.timelinePositionSeconds).toBe(42);

    act(() => {
      result.current.seek(120);
    });
    expect(fake.session.seek).toHaveBeenCalledWith(120);
    expect(result.current.timelinePositionSeconds).toBe(120);
  });

  it("applies a pre-play seek when playback starts", async () => {
    const { result } = renderAudio();
    act(() => {
      result.current.seek(50);
    });
    expect(result.current.timelinePositionSeconds).toBe(50);
    expect(mocks.createSession).not.toHaveBeenCalled();
    await runPlay(result);
    expect(fake.session.seek).toHaveBeenCalledWith(50);
    expect(fake.session.seek.mock.invocationCallOrder[0]).toBeLessThan(
      fake.session.play.mock.invocationCallOrder[0],
    );
  });

  it("play at the timeline end seeks to 0 before playing instead of rebuilding an empty graph", async () => {
    const { result } = renderAudio();
    await runPlay(result);

    act(() => {
      fake.setPlaying(false);
      fake.setPosition(200);
      fake.notify();
    });
    expect(result.current.status).toBe("paused");

    await act(async () => {
      await result.current.play();
    });

    expect(fake.session.seek).toHaveBeenCalledWith(0);
    expect(fake.session.seek.mock.invocationCallOrder[0]).toBeLessThan(
      fake.session.play.mock.invocationCallOrder.at(-1)!,
    );
    expect(mocks.createSession).toHaveBeenCalledTimes(1);
    expect(fake.session.play).toHaveBeenCalledTimes(2);
  });

  it("startAtSeconds seeks and plays", async () => {
    const { result } = renderAudio();
    await act(async () => {
      await result.current.startAtSeconds(140);
    });
    expect(fake.session.seek).toHaveBeenCalledWith(140);
    expect(fake.session.play).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe("playing");
  });

  it("retry recreates the session at the current position and replays", async () => {
    const { result } = renderAudio();
    await runPlay(result);
    act(() => {
      fake.setError({
        kind: "playback-failed",
        itemId: "a",
        message: "boom",
      });
      fake.setPlaying(false);
      fake.setPosition(73);
      fake.notify();
    });
    expect(result.current.status).toBe("error");

    const nextFake = makeFakeSession();
    mocks.createSession.mockImplementation(() => nextFake.session);
    await act(async () => {
      await result.current.retry();
    });

    expect(fake.session.destroy).toHaveBeenCalled();
    expect(mocks.createSession).toHaveBeenCalledTimes(2);
    expect(nextFake.session.seek).toHaveBeenCalledWith(73);
    expect(nextFake.session.seek.mock.invocationCallOrder[0]).toBeLessThan(
      nextFake.session.play.mock.invocationCallOrder[0],
    );
    expect(nextFake.session.play).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe("playing");
    expect(result.current.errorMessage).toBeNull();
    expect(result.current.progress).toBeNull();
  });

  it("keeps the session on stop so replay reuses decoded clips", async () => {
    const { result } = renderAudio();
    await runPlay(result);

    act(() => {
      result.current.stop();
    });
    expect(fake.session.stop).toHaveBeenCalledTimes(1);
    expect(fake.session.destroy).not.toHaveBeenCalled();
    expect(result.current.status).toBe("idle");
    expect(result.current.timelinePositionSeconds).toBe(0);

    await runPlay(result);
    expect(mocks.createSession).toHaveBeenCalledTimes(1);
    expect(fake.session.play).toHaveBeenCalledTimes(2);
    expect(result.current.status).toBe("playing");
  });

  it("ignores a play that settles after stop", async () => {
    const { result, onPreviewError } = renderAudio();
    const deferred = Promise.withResolvers<void>();
    fake.session.play = vi.fn(() => deferred.promise);

    let playPromise: Promise<void> = Promise.resolve();
    act(() => {
      playPromise = result.current.play();
    });
    expect(result.current.status).toBe("loading");

    act(() => {
      result.current.stop();
    });
    expect(result.current.status).toBe("idle");

    // The session's play settles after Stop: it must not repaint the state.
    await act(async () => {
      deferred.resolve();
      await playPromise;
    });
    expect(result.current.status).toBe("idle");
    expect(result.current.errorMessage).toBeNull();
    expect(onPreviewError).not.toHaveBeenCalled();
  });
});
