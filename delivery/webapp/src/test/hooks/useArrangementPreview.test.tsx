import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ArrangementTimeline, TimelineItemSource } from "@/lib/arrangements";
import type {
  ArrangementPreviewError,
  ArrangementPreviewSession,
} from "@/lib/audio/arrangement-preview";
import {
  useArrangementPreview,
  type UseArrangementPreviewOptions,
} from "@/hooks/useArrangementPreview";

const mocks = vi.hoisted(() => ({
  playerStop: vi.fn(),
  playerPlay: vi.fn(),
  markSongCompleted: vi.fn(),
  createSession: vi.fn(),
}));

vi.mock("@/contexts/AudioPlayerContext", () => ({
  useAudioPlayerContext: () => ({
    stop: mocks.playerStop,
    play: mocks.playerPlay,
  }),
}));

vi.mock("@/lib/audio/completion", () => ({
  markSongCompleted: mocks.markSongCompleted,
}));

vi.mock("@/lib/audio/arrangement-preview", async () => {
  const actual = await vi.importActual<Record<string, unknown>>(
    "@/lib/audio/arrangement-preview",
  );
  return {
    ...actual,
    createArrangementPreviewSession: mocks.createSession,
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

const ITEMS: TimelineItemSource[] = [item({ itemId: "a" }), item({ itemId: "b" })];

interface FakeSession {
  session: ArrangementPreviewSession;
  setPlaying: (value: boolean) => void;
  setPosition: (value: number) => void;
  setError: (value: ArrangementPreviewError | null) => void;
  setFallback: (value: boolean) => void;
  notify: () => void;
}

/** totalSeconds 200 matches buildArrangementTimeline(ITEMS). */
function makeFakeSession(totalSeconds = 200): FakeSession {
  const listeners = new Set<() => void>();
  const timeline: ArrangementTimeline = { items: [], totalSeconds };
  let playing = false;
  let position = 0;
  let error: ArrangementPreviewError | null = null;
  let fallback = false;
  const session: ArrangementPreviewSession = {
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
    seek: vi.fn((seconds: number) => {
      position = seconds;
    }),
    startAt: vi.fn((seconds: number) => {
      position = seconds;
    }),
    getTimelinePositionSeconds: () => position,
    isPlaying: () => playing,
    isFallbackMode: () => fallback,
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
    setFallback: (value) => {
      fallback = value;
    },
    notify: () => {
      for (const listener of [...listeners]) listener();
    },
  };
}

function renderPreview(
  overrides: Partial<UseArrangementPreviewOptions> = {},
) {
  const onPreviewError = vi.fn();
  const onFallbackNotice = vi.fn();
  const initialProps: UseArrangementPreviewOptions = {
    items: ITEMS,
    active: true,
    onPreviewError,
    onFallbackNotice,
    ...overrides,
  };
  const view = renderHook(
    (props: UseArrangementPreviewOptions) => useArrangementPreview(props),
    { initialProps },
  );
  return { ...view, onPreviewError, onFallbackNotice };
}

async function runPlay(
  result: { current: ReturnType<typeof useArrangementPreview> },
) {
  await act(async () => {
    await result.current.play();
  });
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("useArrangementPreview", () => {
  let fake: FakeSession;

  beforeEach(() => {
    vi.clearAllMocks();
    fake = makeFakeSession();
    mocks.createSession.mockImplementation(() => fake.session);
  });

  it("starts idle with the arranged total and no session", () => {
    const { result } = renderPreview();
    expect(result.current.status).toBe("idle");
    expect(result.current.totalSeconds).toBe(200);
    expect(result.current.timelinePositionSeconds).toBe(0);
    expect(result.current.errorMessage).toBeNull();
    expect(result.current.isFallbackMode).toBe(false);
    expect(mocks.createSession).not.toHaveBeenCalled();
    expect(mocks.playerStop).not.toHaveBeenCalled();
  });

  it("stops the global player, then starts one session on play", async () => {
    const { result } = renderPreview();
    await runPlay(result);
    expect(mocks.playerStop).toHaveBeenCalledTimes(1);
    expect(mocks.createSession).toHaveBeenCalledTimes(1);
    expect(fake.session.play).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe("playing");
    // The global player is stopped BEFORE the preview session starts.
    expect(mocks.playerStop.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.createSession.mock.invocationCallOrder[0],
    );
  });

  it("rejects a missing recording before playback (never original audio)", async () => {
    const brokenItems = [
      item({ itemId: "a" }),
      item({ itemId: "b", recordingHashPrefix: null }),
    ];
    const { result, onPreviewError } = renderPreview({ items: brokenItems });
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

  it("rejects an invalid saved sibling arrangement before playback", async () => {
    const brokenItems = [
      item({ itemId: "a" }),
      item({ itemId: "b", arrangementSteps: "{ not json" }),
    ];
    const { result, onPreviewError } = renderPreview({ items: brokenItems });
    await runPlay(result);
    expect(onPreviewError).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "invalid-arrangement", itemId: "b" }),
    );
    expect(result.current.status).toBe("error");
    expect(mocks.createSession).not.toHaveBeenCalled();
  });

  it("pauses and reports the paused position", async () => {
    const { result } = renderPreview();
    await runPlay(result);
    act(() => {
      result.current.pause();
    });
    expect(fake.session.pause).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe("paused");
    expect(result.current.timelinePositionSeconds).toBe(42);
  });

  it("stops and returns to idle", async () => {
    const { result } = renderPreview();
    await runPlay(result);
    act(() => {
      result.current.stop();
    });
    expect(fake.session.stop).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe("idle");
    expect(result.current.timelinePositionSeconds).toBe(0);
  });

  it("applies a pre-play seek when playback starts", async () => {
    const { result } = renderPreview();
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

  it("startAt seeks to that item's start and plays", async () => {
    const { result } = renderPreview();
    await act(async () => {
      await result.current.startAt("b");
    });
    expect(fake.session.seek).toHaveBeenCalledWith(100);
    expect(fake.session.play).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe("playing");
  });

  it("startAtSeconds auditions from a timeline position and plays", async () => {
    const { result } = renderPreview();
    await act(async () => {
      await result.current.startAtSeconds(140);
    });
    expect(fake.session.seek).toHaveBeenCalledWith(140);
    expect(fake.session.play).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe("playing");
  });

  it("reports an unknown startAt item id", async () => {
    const { result, onPreviewError } = renderPreview();
    await act(async () => {
      await result.current.startAt("missing");
    });
    expect(onPreviewError).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "playback-failed", itemId: "missing" }),
    );
    expect(mocks.createSession).not.toHaveBeenCalled();
  });

  it("tears the session down when the timeline inputs change (draft mutation)", async () => {
    const { result, rerender } = renderPreview();
    await runPlay(result);
    const mutated = [
      ITEMS[0],
      item({ itemId: "b", arrangementSteps: null, recordingHashPrefix: "hash-b2" }),
    ];
    rerender({
      items: mutated,
      active: true,
      onPreviewError: vi.fn(),
      onFallbackNotice: vi.fn(),
    });
    expect(fake.session.stop).toHaveBeenCalled();
    expect(result.current.status).toBe("idle");
    await act(async () => {
      await result.current.play();
    });
    expect(mocks.createSession).toHaveBeenCalledTimes(2);
  });

  it("does not tear down when the items array is new but content is identical", async () => {
    const { result, rerender } = renderPreview();
    await runPlay(result);
    rerender({
      items: [item({ itemId: "a" }), item({ itemId: "b" })],
      active: true,
      onPreviewError: vi.fn(),
      onFallbackNotice: vi.fn(),
    });
    expect(fake.session.stop).not.toHaveBeenCalled();
    expect(result.current.status).toBe("playing");
  });

  it("tears down on active=false and blocks play until reactivated", async () => {
    const { result, rerender } = renderPreview();
    await runPlay(result);
    rerender({
      items: ITEMS,
      active: false,
      onPreviewError: vi.fn(),
      onFallbackNotice: vi.fn(),
    });
    expect(fake.session.stop).toHaveBeenCalled();
    expect(result.current.status).toBe("idle");
    await act(async () => {
      await result.current.play();
    });
    expect(mocks.createSession).toHaveBeenCalledTimes(1);
    rerender({
      items: ITEMS,
      active: true,
      onPreviewError: vi.fn(),
      onFallbackNotice: vi.fn(),
    });
    // Reopening an unchanged sheet starts from a clean idle view rather than
    // the stale "playing" status of the torn-down session.
    expect(result.current.status).toBe("idle");
    expect(result.current.timelinePositionSeconds).toBe(0);
    await act(async () => {
      await result.current.play();
    });
    expect(mocks.createSession).toHaveBeenCalledTimes(2);
  });

  it("contains a rejecting session play as a reported error", async () => {
    const failing = makeFakeSession();
    const failure = new Error("Decoder exploded");
    vi.mocked(failing.session.play).mockRejectedValueOnce(failure);
    mocks.createSession.mockImplementation(() => failing.session);
    const { result, onPreviewError } = renderPreview();

    // A throwing play() must not escape as an unhandled rejection or leave the
    // sheet stuck on "loading".
    await expect(runPlay(result)).resolves.toBeUndefined();

    expect(result.current.status).toBe("error");
    expect(result.current.errorMessage).toBe("Decoder exploded");
    expect(onPreviewError).toHaveBeenCalledTimes(1);
    expect(onPreviewError).toHaveBeenCalledWith({
      kind: "playback-failed",
      itemId: null,
      message: "Decoder exploded",
    });
  });

  it("tears down on unmount", async () => {
    const { result, unmount } = renderPreview();
    await runPlay(result);
    unmount();
    expect(fake.session.stop).toHaveBeenCalled();
  });

  it("reuses the session across pause/resume (no new session)", async () => {
    const { result } = renderPreview();
    await runPlay(result);
    act(() => {
      result.current.pause();
    });
    await runPlay(result);
    expect(mocks.createSession).toHaveBeenCalledTimes(1);
    expect(fake.session.play).toHaveBeenCalledTimes(2);
    expect(mocks.playerStop).toHaveBeenCalledTimes(2);
  });

  it("surfaces a session playback failure once and reports error status", async () => {
    const { result, onPreviewError } = renderPreview();
    await runPlay(result);
    const error: ArrangementPreviewError = {
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
  });

  it("recreates the session after a playback error", async () => {
    const { result } = renderPreview();
    await runPlay(result);
    act(() => {
      fake.setError({
        kind: "playback-failed",
        itemId: "a",
        message: "boom",
      });
      fake.setPlaying(false);
      fake.notify();
    });
    const nextFake = makeFakeSession();
    mocks.createSession.mockImplementation(() => nextFake.session);
    await runPlay(result);
    expect(fake.session.stop).toHaveBeenCalled();
    expect(mocks.createSession).toHaveBeenCalledTimes(2);
    expect(result.current.status).toBe("playing");
    expect(result.current.errorMessage).toBeNull();
  });

  it("forwards the iOS fallback notice once and exposes isFallbackMode", async () => {
    const { result, onFallbackNotice } = renderPreview();
    await runPlay(result);
    act(() => {
      fake.setFallback(true);
      fake.notify();
    });
    expect(onFallbackNotice).toHaveBeenCalledTimes(1);
    expect(result.current.isFallbackMode).toBe(true);
    act(() => {
      fake.notify();
    });
    expect(onFallbackNotice).toHaveBeenCalledTimes(1);
  });

  it("mirrors transport position notifications into hook state", async () => {
    const { result } = renderPreview();
    await runPlay(result);
    act(() => {
      fake.setPosition(7);
      fake.notify();
    });
    expect(result.current.timelinePositionSeconds).toBe(7);
    expect(result.current.status).toBe("playing");
  });

  it("never writes Completion and never plays a global song track", async () => {
    const { result } = renderPreview();
    await runPlay(result);
    act(() => {
      result.current.pause();
    });
    act(() => {
      result.current.seek(10);
    });
    await runPlay(result);
    act(() => {
      result.current.stop();
    });
    expect(mocks.markSongCompleted).not.toHaveBeenCalled();
    expect(mocks.playerPlay).not.toHaveBeenCalled();
  });
});
