import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import type { CastMedia } from "@/hooks/useCast";

// Cast SDK mock factory. Sets window.chrome.cast + window.cast.framework with
// captured listener handlers so tests can invoke them and assert on calls.
function setupCastSdkMock(opts?: {
  loadMedia?: "success" | "error";
  requestSession?: "resolve" | "reject";
  player?: Partial<Record<string, unknown>>;
}) {
  const ctx = {
    setOptions: vi.fn(),
    requestSession: vi.fn(() =>
      opts?.requestSession === "reject"
        ? Promise.reject(new Error("cancelled"))
        : Promise.resolve(),
    ),
    endCurrentSession: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    getCurrentSession: vi.fn(),
    getCastState: vi.fn(),
    getSessionState: vi.fn(),
  };

  const player: Record<string, unknown> = {
    currentTime: 0,
    duration: 0,
    volume: 1,
    isMediaLoaded: false,
    isMuted: false,
    playerState: "",
    displayName: "Living Room TV",
    canPause: true,
    canSeek: true,
    isConnected: false,
    isPaused: false,
    title: "",
    displayStatus: "",
    ...opts?.player,
  };

  const handlers = new Map<string, Set<(e: unknown) => void>>();

  const controller = {
    addEventListener: vi.fn((type: string, handler: (e: unknown) => void) => {
      if (!handlers.has(type)) handlers.set(type, new Set());
      handlers.get(type)!.add(handler);
    }),
    removeEventListener: vi.fn((type: string, handler: (e: unknown) => void) => {
      handlers.get(type)?.delete(handler);
    }),
    play: vi.fn(),
    pause: vi.fn(),
    seek: vi.fn(),
    stop: vi.fn(),
    setVolumeLevel: vi.fn(),
    playOrPause: vi.fn(),
    muteOrUnmute: vi.fn(),
    getFormattedTime: vi.fn(),
    getSeekPosition: vi.fn(),
    getSeekTime: vi.fn(),
  };

  const session = {
    sessionId: "sess-1",
    appId: "test-app-id",
    displayName: "Living Room TV",
    statusText: "Casting",
    loadMedia: vi.fn(
      (
        req: unknown,
        onSuccess: () => void,
        onError: (err: { code: string; description?: string }) => void,
      ) => {
        if (opts?.loadMedia === "error") {
          onError({ code: "LOAD_FAILED", description: "receiver rejected media" });
        } else {
          onSuccess();
        }
      },
    ),
    stop: vi.fn((onSuccess: () => void) => onSuccess()),
    setVolume: vi.fn((level: number, onSuccess: () => void) => onSuccess()),
    addUpdateListener: vi.fn(),
  };

  // Cast framework globals.
  const framework = {
    CastContext: { getInstance: vi.fn(() => ctx) },
    RemotePlayer: function RemotePlayer() {
      return player;
    },
    RemotePlayerController: function RemotePlayerController() {
      return controller;
    },
    RemotePlayerEventType: {
      CURRENT_TIME_CHANGED: "currentTimeChanged",
      PLAYER_STATE_CHANGED: "playerStateChanged",
      IS_MEDIA_LOADED_CHANGED: "isMediaLoadedChanged",
      VOLUME_LEVEL_CHANGED: "volumeLevelChanged",
      IS_MUTED_CHANGED: "isMutedChanged",
      IS_CONNECTED_CHANGED: "isConnectedChanged",
    },
    CastState: {
      NO_DEVICES_AVAILABLE: "NO_DEVICES_AVAILABLE",
      NOT_CONNECTED: "NOT_CONNECTED",
      CONNECTING: "CONNECTING",
      CONNECTED: "CONNECTED",
    },
    SessionState: { NO_SESSION: "NO_SESSION", SESSION_STARTED: "SESSION_STARTED" },
    CastContextEventType: {
      CAST_STATE_CHANGED: "caststatechanged",
      SESSION_STATE_CHANGED: "sessionstatechanged",
    },
  };

  (window as unknown as { cast: unknown }).cast = { framework };
  (window as unknown as { chrome: unknown }).chrome = {
    cast: {
      DEFAULT_MEDIA_RECEIVER_APP_ID: "DEFAULT",
      AutoJoinPolicy: { TAB_AND_ORIGIN_SCOPED: "tab_and_origin_scoped" },
      StreamType: { BUFFERED: "buffered" },
      media: {
        MediaInfo: function MediaInfo(this: unknown, contentId: string, contentType: string) {
          // @ts-expect-error test mock constructor
          this.contentId = contentId;
          // @ts-expect-error test mock constructor
          this.contentType = contentType;
        },
        LoadRequest: function LoadRequest(this: unknown, media: unknown) {
          // @ts-expect-error test mock constructor
          this.media = media;
          // @ts-expect-error test mock constructor
          this.currentTime = 0;
        },
        MetadataType: { GENERIC: 0 },
        GenericMediaMetadata: function GenericMediaMetadata() {},
      },
    },
  };

  function fireEvent(type: string, event?: unknown) {
    handlers.get(type)?.forEach((h) => h(event ?? { type }));
  }

  function setPlayer(patch: Record<string, unknown>) {
    Object.assign(player, patch);
  }

  ctx.getCurrentSession.mockReturnValue(session);

  return { ctx, player, controller, session, fireEvent, setPlayer, player2: player };
}

function resetWindow() {
  document.head.innerHTML = "";
  delete (window as unknown as Record<string, unknown>).__onGCastApiAvailable;
  delete (window as unknown as { chrome?: unknown }).chrome;
  delete (window as unknown as { cast?: unknown }).cast;
}

const MEDIA: CastMedia = {
  videoUrl: "https://r2.example.com/renders/job-1/video.mp4",
  title: "Amazing Grace",
  source: { kind: "songset", idOrToken: "set-1" },
  startSeconds: 0,
};

async function freshModule() {
  vi.resetModules();
  return await import("@/hooks/useCast");
}

interface MountOptions {
  media?: CastMedia;
  onError?: (message: string) => void;
}

async function mountHook(opts?: MountOptions, sdkOpts?: Parameters<typeof setupCastSdkMock>[0]) {
  const mock = setupCastSdkMock(sdkOpts);
  const mod = await freshModule();
  const onError = opts?.onError ?? vi.fn();
  const { result, unmount } = renderHook(() =>
    mod.useCastTransport({ media: opts?.media ?? MEDIA, onError }),
  );
  await act(async () => {
    window.__onGCastApiAvailable?.(true);
  });
  try {
    await waitFor(() => expect(result.current.isSupported).toBe(true));
  } catch {
    // isSupported may legitimately stay false in some tests; caller asserts.
  }
  return { result, unmount, mod, onError, ...mock };
}

describe("useCastTransport", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubEnv("NEXT_PUBLIC_CAST_RECEIVER_APP_ID", "test-app-id");
    resetWindow();
    fetchSpy = vi.spyOn(global, "fetch").mockResolvedValue(
      new Response("{}", { status: 202 }),
    );
  });

  afterEach(() => {
    fetchSpy.mockRestore();
    vi.unstubAllEnvs();
    vi.useRealTimers();
    resetWindow();
  });

  describe("support detection", () => {
    it("returns isSupported=false when env app id is missing and start() is a no-op", async () => {
      vi.stubEnv("NEXT_PUBLIC_CAST_RECEIVER_APP_ID", "");
      setupCastSdkMock();
      const mod = await freshModule();
      const { result } = renderHook(() => mod.useCastTransport({ media: MEDIA }));
      expect(result.current.isSupported).toBe(false);
      await act(async () => {
        await result.current.start();
      });
      expect(result.current.isSupported).toBe(false);
    });

    it("returns isSupported=false when SDK is unavailable (globals not present)", async () => {
      // SDK script loaded callback fires with true but globals never set.
      const mod = await freshModule();
      const { result } = renderHook(() => mod.useCastTransport({ media: MEDIA }));
      await act(async () => {
        window.__onGCastApiAvailable?.(true);
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(result.current.isSupported).toBe(false);
    });
  });

  describe("singleton init", () => {
    it("calls setOptions exactly once across two hook instances", async () => {
      const mock = setupCastSdkMock();
      const mod = await freshModule();
      const r1 = renderHook(() => mod.useCastTransport({ media: MEDIA }));
      const r2 = renderHook(() => mod.useCastTransport({ media: MEDIA }));
      await act(async () => {
        window.__onGCastApiAvailable?.(true);
      });
      await waitFor(() => expect(r1.result.current.isSupported).toBe(true));
      await waitFor(() => expect(r2.result.current.isSupported).toBe(true));
      // CastContext singleton — setOptions called exactly once.
      expect(mock.ctx.setOptions).toHaveBeenCalledTimes(1);
      r1.unmount();
      r2.unmount();
    });
  });

  describe("start() session lifecycle", () => {
    it("requestSession success + loadMedia success → isConnected=true, deviceName set", async () => {
      const { result, session, player } = await mountHook(
        { media: MEDIA },
        { loadMedia: "success" },
      );
      await act(async () => {
        await result.current.start();
      });
      expect(result.current.isConnected).toBe(true);
      expect(result.current.deviceName).toBe(player.displayName);
      expect(result.current.isConnecting).toBe(false);
      expect(session.loadMedia).toHaveBeenCalledTimes(1);
    });

    it("requestSession cancel → isConnecting=false, no session leak", async () => {
      const { result, ctx } = await mountHook(
        { media: MEDIA },
        { requestSession: "reject" },
      );
      await act(async () => {
        await result.current.start();
      });
      expect(result.current.isConnecting).toBe(false);
      expect(result.current.isConnected).toBe(false);
      expect(ctx.endCurrentSession).not.toHaveBeenCalled();
    });

    it("loadMedia failure → endCurrentSession called, isConnected=false, lastError set, onError fired; retry emits a fresh requestSession", async () => {
      const onError = vi.fn();
      const { result, ctx } = await mountHook(
        { media: MEDIA, onError },
        { loadMedia: "error" },
      );
      await act(async () => {
        await result.current.start();
      });
      expect(result.current.isConnected).toBe(false);
      expect(result.current.isConnecting).toBe(false);
      expect(ctx.endCurrentSession).toHaveBeenCalledWith(true);
      expect(result.current.lastError).toBeTruthy();
      expect(onError).toHaveBeenCalled();
      const firstCalls = ctx.requestSession.mock.calls.length;
      // Retry: fresh requestSession.
      await act(async () => {
        await result.current.start();
      });
      expect(ctx.requestSession.mock.calls.length).toBeGreaterThan(firstCalls);
    });
  });

  describe("status listeners", () => {
    it("update currentTime/playerState/volume/isMuted AND lastStatusAtMs", async () => {
      const { result, fireEvent, setPlayer } = await mountHook();
      expect(result.current.lastStatusAtMs).toBeNull();
      vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
      await act(async () => {
        setPlayer({ currentTime: 42 });
        fireEvent("currentTimeChanged");
      });
      expect(result.current.currentTime).toBe(42);
      expect(result.current.lastStatusAtMs).toBe(Date.now());
      await act(async () => {
        setPlayer({ playerState: "playing" });
        fireEvent("playerStateChanged");
      });
      expect(result.current.playerState).toBe("playing");
      await act(async () => {
        setPlayer({ volume: 0.5 });
        fireEvent("volumeLevelChanged");
      });
      expect(result.current.volume).toBe(0.5);
      await act(async () => {
        setPlayer({ isMuted: true });
        fireEvent("isMutedChanged");
      });
      expect(result.current.isMuted).toBe(true);
      expect(result.current.lastStatusAtMs).not.toBeNull();
    });
  });

  describe("seek debounce + clamp", () => {
    it("three rapid calls in 200ms → one controller.seek() with the last arg", async () => {
      const { result, controller, player } = await mountHook(undefined, {
        player: { duration: 1000 },
      });
      // Switch to fake timers AFTER mount so mountHook's waitFor could use
      // real timers; the debounce is what we want deterministic control over.
      vi.useFakeTimers({ shouldAdvanceTime: false, now: Date.now() });
      await act(async () => {
        result.current.seek(10);
        result.current.seek(20);
        result.current.seek(30);
      });
      expect(controller.seek).not.toHaveBeenCalled();
      await act(async () => {
        vi.advanceTimersByTime(200);
      });
      expect(controller.seek).toHaveBeenCalledTimes(1);
      // RemotePlayerController.seek() reads RemotePlayer.currentTime which the
      // hook set to the last (clamped) requested position.
      expect((player as Record<string, unknown>).currentTime).toBe(30);
      vi.useRealTimers();
    });

    it("clamp seek to [0, duration]", async () => {
      const { result, controller, player } = await mountHook(undefined, {
        player: { duration: 100 },
      });
      vi.useFakeTimers({ shouldAdvanceTime: false, now: Date.now() });
      await act(async () => {
        result.current.seek(1000);
      });
      await act(async () => {
        vi.advanceTimersByTime(200);
      });
      expect(controller.seek).toHaveBeenCalledTimes(1);
      expect((player as Record<string, unknown>).currentTime).toBe(100);
      vi.useRealTimers();
    });
  });

  describe("setVolume clamp", () => {
    it("clamps out-of-range volume to [0,1]", async () => {
      const { result, controller } = await mountHook();
      await act(async () => {
        result.current.setVolume(1.5);
      });
      expect(controller.setVolumeLevel).toHaveBeenCalledWith(1);
      await act(async () => {
        result.current.setVolume(-0.5);
      });
      expect(controller.setVolumeLevel).toHaveBeenCalledWith(0);
    });
  });

  describe("setMuted", () => {
    it("calls muteOrUnmute() and leaves volume untouched", async () => {
      const { result, controller, player } = await mountHook(undefined, {
        player: { isMuted: false },
      });
      await act(async () => {
        result.current.setMuted(true);
      });
      expect(controller.muteOrUnmute).toHaveBeenCalledTimes(1);
      expect(controller.setVolumeLevel).not.toHaveBeenCalled();
      // volume state unchanged
      expect(result.current.volume).toBe(1);
      expect((player as Record<string, unknown>).volume).toBe(1);
    });
  });

  describe("disconnect → resumeProposal", () => {
    it("IS_CONNECTED_CHANGED→false retains currentTime and populates resumeProposal", async () => {
      const { result, fireEvent, setPlayer } = await mountHook(undefined, {
        player: { duration: 1000 },
      });
      await act(async () => {
        setPlayer({ currentTime: 100 });
        fireEvent("currentTimeChanged");
      });
      expect(result.current.currentTime).toBe(100);
      await act(async () => {
        setPlayer({ isConnected: false });
        fireEvent("isConnectedChanged");
      });
      expect(result.current.isConnected).toBe(false);
      // currentTime retained.
      expect(result.current.currentTime).toBe(100);
      expect(result.current.resumeProposal).not.toBeNull();
    });

    it("extrapolation: last playing, 10s ago → time==currentTime+10, isStale=false", async () => {
      const { result, fireEvent, setPlayer } = await mountHook(undefined, {
        player: { duration: 1000 },
      });
      const T0 = new Date("2026-01-01T00:00:00Z").getTime();
      vi.setSystemTime(T0);
      await act(async () => {
        setPlayer({ currentTime: 100, playerState: "playing" });
        fireEvent("currentTimeChanged");
        fireEvent("playerStateChanged");
      });
      vi.setSystemTime(T0 + 10_000);
      await act(async () => {
        setPlayer({ isConnected: false });
        fireEvent("isConnectedChanged");
      });
      const proposal = result.current.resumeProposal!;
      expect(proposal.time).toBe(110);
      expect(proposal.isStale).toBe(false);
      expect(proposal.lastState).toBe("playing");
    });

    it("extrapolation: last playing, 90s ago → isStale=true, time==currentTime+60 clamped", async () => {
      const { result, fireEvent, setPlayer } = await mountHook(undefined, {
        player: { duration: 1000 },
      });
      const T0 = new Date("2026-01-01T00:00:00Z").getTime();
      vi.setSystemTime(T0);
      await act(async () => {
        setPlayer({ currentTime: 100, playerState: "playing" });
        fireEvent("currentTimeChanged");
        fireEvent("playerStateChanged");
      });
      vi.setSystemTime(T0 + 90_000);
      await act(async () => {
        setPlayer({ isConnected: false });
        fireEvent("isConnectedChanged");
      });
      const proposal = result.current.resumeProposal!;
      expect(proposal.isStale).toBe(true);
      // 100 + 60 = 160, within duration 1000.
      expect(proposal.time).toBe(160);
      expect(proposal.lastState).toBe("playing");
    });

    it("extrapolation: last paused → time==currentTime, isStale=false", async () => {
      const { result, fireEvent, setPlayer } = await mountHook(undefined, {
        player: { duration: 1000 },
      });
      const T0 = new Date("2026-01-01T00:00:00Z").getTime();
      vi.setSystemTime(T0);
      await act(async () => {
        setPlayer({ currentTime: 50, playerState: "paused" });
        fireEvent("currentTimeChanged");
        fireEvent("playerStateChanged");
      });
      await act(async () => {
        setPlayer({ isConnected: false });
        fireEvent("isConnectedChanged");
      });
      const proposal = result.current.resumeProposal!;
      expect(proposal.time).toBe(50);
      expect(proposal.isStale).toBe(false);
      expect(proposal.lastState).toBe("paused");
    });

    it("extrapolation: lastStatusAtMs==null → lastState=unknown", async () => {
      const { result, fireEvent, setPlayer } = await mountHook(undefined, {
        player: { duration: 1000, currentTime: 7 },
      });
      // No status event fired → lastStatusAtMs stays null.
      await act(async () => {
        setPlayer({ isConnected: false });
        fireEvent("isConnectedChanged");
      });
      const proposal = result.current.resumeProposal!;
      expect(proposal.lastState).toBe("unknown");
      expect(proposal.isStale).toBe(false);
      expect(proposal.time).toBe(7);
    });

    it("extrapolation clamps time to duration when currentTime+cap exceeds it", async () => {
      const { result, fireEvent, setPlayer } = await mountHook(undefined, {
        player: { duration: 120, currentTime: 100 },
      });
      const T0 = new Date("2026-01-01T00:00:00Z").getTime();
      vi.setSystemTime(T0);
      await act(async () => {
        setPlayer({ playerState: "playing" });
        fireEvent("currentTimeChanged");
        fireEvent("playerStateChanged");
      });
      vi.setSystemTime(T0 + 90_000);
      await act(async () => {
        setPlayer({ isConnected: false });
        fireEvent("isConnectedChanged");
      });
      const proposal = result.current.resumeProposal!;
      // 100 + 60 = 160 → clamped to duration 120.
      expect(proposal.time).toBe(120);
      expect(proposal.isStale).toBe(true);
    });
  });

  describe("buffering tracking", () => {
    it("sets bufferingSinceMs on PLAYER_STATE_CHANGED→buffering and clears on transition out", async () => {
      const { result, fireEvent, setPlayer } = await mountHook();
      expect(result.current.bufferingSinceMs).toBeNull();
      await act(async () => {
        setPlayer({ playerState: "buffering" });
        fireEvent("playerStateChanged");
      });
      expect(result.current.bufferingSinceMs).not.toBeNull();
      await act(async () => {
        setPlayer({ playerState: "playing" });
        fireEvent("playerStateChanged");
      });
      expect(result.current.bufferingSinceMs).toBeNull();
    });
  });

  describe("cleanup", () => {
    it("removes listeners without throwing on unmount", async () => {
      const { unmount, controller } = await mountHook();
      expect(() => unmount()).not.toThrow();
      expect(controller.removeEventListener).toHaveBeenCalled();
    });
  });

  describe("reconnect", () => {
    it("does NOT issue a seek on reconnect (UI reconciliation only)", async () => {
      const { result, fireEvent, setPlayer, controller } = await mountHook(undefined, {
        player: { duration: 1000 },
      });
      // Disconnect first.
      await act(async () => {
        setPlayer({ isConnected: false });
        fireEvent("isConnectedChanged");
      });
      expect(result.current.isConnected).toBe(false);
      expect(controller.seek).not.toHaveBeenCalled();
      // Reconnect.
      await act(async () => {
        setPlayer({ isConnected: true, displayName: "Living Room TV" });
        fireEvent("isConnectedChanged");
      });
      expect(result.current.isConnected).toBe(true);
      expect(controller.seek).not.toHaveBeenCalled();
    });
  });
});
