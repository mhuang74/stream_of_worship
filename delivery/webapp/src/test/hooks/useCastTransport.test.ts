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
    requestSession: vi.fn(() => {
      if (opts?.requestSession === "reject") {
        // Cast SDK cancel: error object with `code: "cancel"`. Real SDK errors
        // carry a `code` string ("cancel" | "receiver_unavailable" | "timeout"
        // | "session_request_failed" | …) and an optional `description`.
        return Promise.reject({ code: "cancel", description: "User cancelled" });
      }
      if (opts?.requestSession === "receiver_unavailable") {
        return Promise.reject({
          code: "receiver_unavailable",
          description: "No Cast devices found",
        });
      }
      return Promise.resolve();
    }),
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
    volumeLevel: 1,
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
      AutoJoinPolicy: { TAB_AND_ORIGIN_SCOPED: "tab_and_origin_scoped" },
      StreamType: { BUFFERED: "buffered" },
      media: {
        DEFAULT_MEDIA_RECEIVER_APP_ID: "DEFAULT",
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
    it("falls back to the Default Media Receiver constant when env app id is unset", async () => {
      vi.stubEnv("NEXT_PUBLIC_CAST_RECEIVER_APP_ID", "");
      const mock = setupCastSdkMock();
      const mod = await freshModule();
      const { result } = renderHook(() => mod.useCastTransport({ media: MEDIA }));
      await act(async () => {
        window.__onGCastApiAvailable?.(true);
      });
      await waitFor(() => expect(result.current.isSupported).toBe(true));
      // setOptions called with the Default Media Receiver constant, not empty.
      expect(mock.ctx.setOptions).toHaveBeenCalledTimes(1);
      const opts = mock.ctx.setOptions.mock.calls[0][0];
      expect(opts.receiverApplicationId).toBe("DEFAULT");
      vi.unstubAllEnvs();
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
      // Availability flips to "unavailable" so the diagnostic sheet can surface.
      await waitFor(() => expect(result.current.availability).toBe("unavailable"));
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

    it("re-runs setOptions when the receiver app id changes across mounts (per-app-id singleton)", async () => {
      // First mount with env app id "test-app-id".
      vi.stubEnv("NEXT_PUBLIC_CAST_RECEIVER_APP_ID", "test-app-id");
      const mock = setupCastSdkMock();
      const mod = await freshModule();
      const r1 = renderHook(() => mod.useCastTransport({ media: MEDIA }));
      await act(async () => {
        window.__onGCastApiAvailable?.(true);
      });
      await waitFor(() => expect(r1.result.current.isSupported).toBe(true));
      expect(mock.ctx.setOptions).toHaveBeenCalledTimes(1);
      expect(mock.ctx.setOptions.mock.calls[0][0].receiverApplicationId).toBe("test-app-id");
      r1.unmount();
      // Second mount in the same module session with a different env app id —
      // setOptions must run again rather than silently inheriting the first
      // mount's options (handles per-route dev/staging receiver scenarios).
      vi.stubEnv("NEXT_PUBLIC_CAST_RECEIVER_APP_ID", "staging-app-id");
      const r2 = renderHook(() => mod.useCastTransport({ media: MEDIA }));
      await act(async () => {
        window.__onGCastApiAvailable?.(true);
      });
      await waitFor(() => expect(r2.result.current.isSupported).toBe(true));
      expect(mock.ctx.setOptions).toHaveBeenCalledTimes(2);
      expect(mock.ctx.setOptions.mock.calls[1][0].receiverApplicationId).toBe("staging-app-id");
      r2.unmount();
    });
  });

  describe("SDK load failure → availability", () => {
    it("sets availability='unavailable' when loadCastSdk rejects (CDN block / network blip)", async () => {
      resetWindow();
      // Inject a script tag whose global callback fires (false) — simulates
      // the SDK script failing to initialize (script blocked, network 5xx).
      const mod = await freshModule();
      const { result } = renderHook(() => mod.useCastTransport({ media: MEDIA }));
      await act(async () => {
        window.__onGCastApiAvailable?.(false);
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(result.current.isSupported).toBe(false);
      // SDK failure must surface as "unavailable" (not "unknown") so the
      // diagnostic sheet + Presentation/iPhone fallbacks can render instead of
      // stranding the user with a blank transport surface.
      await waitFor(() => expect(result.current.availability).toBe("unavailable"));
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

    it("requestSession cancel → isConnecting=false, no session leak, no telemetry POST", async () => {
      const { result, ctx } = await mountHook(
        { media: MEDIA },
        { requestSession: "reject" },
      );
      fetchSpy.mockClear();
      await act(async () => {
        await result.current.start();
      });
      expect(result.current.isConnecting).toBe(false);
      expect(result.current.isConnected).toBe(false);
      expect(ctx.endCurrentSession).not.toHaveBeenCalled();
      // User-cancel is a silent reset: no lastError, no onError, no telemetry.
      expect(result.current.lastError).toBeNull();
      expect(
        fetchSpy.mock.calls.some(
          (c) => typeof c[0] === "string" && c[0].endsWith("/api/log-client-error"),
        ),
      ).toBe(false);
    });

    it("requestSession non-cancel error (receiver_unavailable) → surfaces lastError + onError + telemetry POST", async () => {
      const onError = vi.fn();
      const { result, ctx } = await mountHook(
        { media: MEDIA, onError },
        { requestSession: "receiver_unavailable" },
      );
      fetchSpy.mockClear();
      await act(async () => {
        await result.current.start();
      });
      expect(result.current.isConnecting).toBe(false);
      expect(result.current.isConnected).toBe(false);
      // No session was created — endCurrentSession not called.
      expect(ctx.endCurrentSession).not.toHaveBeenCalled();
      // Non-cancel errors must surface to the user, not be swallowed.
      expect(result.current.lastError).toBe("No Cast devices found");
      expect(onError).toHaveBeenCalledWith("No Cast devices found");
      const post = fetchSpy.mock.calls.find(
        (c) => typeof c[0] === "string" && c[0].endsWith("/api/log-client-error"),
      );
      expect(post).toBeTruthy();
      const body = JSON.parse(String(post?.[1]?.body));
      expect(body.kind).toBe("cast_load");
      expect(body.message).toBe("No Cast devices found");
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
    it("update currentTime/playerState/volume/isMuted AND snapshot", async () => {
      const { result, fireEvent, setPlayer } = await mountHook();
      expect(result.current).not.toHaveProperty("lastStatusAtMs");
      vi.setSystemTime(new Date("2026-01-01T00:00:00Z"));
      await act(async () => {
        setPlayer({ currentTime: 42 });
        fireEvent("currentTimeChanged");
      });
      expect(result.current.currentTime).toBe(42);
      await act(async () => {
        setPlayer({ playerState: "playing" });
        fireEvent("playerStateChanged");
      });
      expect(result.current.playerState).toBe("playing");
      await act(async () => {
        setPlayer({ volumeLevel: 0.5 });
        fireEvent("volumeLevelChanged");
      });
      expect(result.current.volume).toBe(0.5);
      await act(async () => {
        setPlayer({ isMuted: true });
        fireEvent("isMutedChanged");
      });
      expect(result.current.isMuted).toBe(true);
    });

    it("normalizes UPPERCASE chrome.cast.media.PlayerState to lowercase", async () => {
      // The real Web Sender RemotePlayer.playerState is
      // chrome.cast.media.PlayerState, whose values are UPPERCASE
      // (PLAYING / PAUSED / BUFFERING / IDLE). The hook must normalize so the
      // controller's lowercase comparisons stay correct in live sessions.
      const { result, fireEvent, setPlayer } = await mountHook();
      await act(async () => {
        setPlayer({ playerState: "PLAYING" });
        fireEvent("playerStateChanged");
      });
      expect(result.current.playerState).toBe("playing");
      await act(async () => {
        setPlayer({ playerState: "BUFFERING" });
        fireEvent("playerStateChanged");
      });
      // Buffering tracking keys off the normalized lowercase value.
      expect(result.current.bufferingSinceMs).not.toBeNull();
      await act(async () => {
        setPlayer({ playerState: "PAUSED" });
        fireEvent("playerStateChanged");
      });
      expect(result.current.playerState).toBe("paused");
      expect(result.current.bufferingSinceMs).toBeNull();
    });

    it("seeds volume from player.volumeLevel on mount (not the undefined player.volume)", async () => {
      // Real Web Sender RemotePlayer exposes receiver volume as `volumeLevel`
      // — `player.volume` is undefined in live sessions. The hook must seed its
      // volume state from `volumeLevel` so the slider reflects the TV volume.
      const { result } = await mountHook(undefined, {
        player: { volumeLevel: 0.25 },
      });
      expect(result.current.volume).toBe(0.25);
    });

    it("reconciles volume from player.volumeLevel on reconnect", async () => {
      const { result, fireEvent, setPlayer } = await mountHook(undefined, {
        player: { volumeLevel: 0.4 },
      });
      // Disconnect first.
      await act(async () => {
        setPlayer({ isConnected: false });
        fireEvent("isConnectedChanged");
      });
      // Receiver volume changes while disconnected — the reconnect listener
      // must seed from `volumeLevel`, not the stale `volume` field.
      await act(async () => {
        setPlayer({ volumeLevel: 0.7, isConnected: true });
        fireEvent("isConnectedChanged");
      });
      expect(result.current.volume).toBe(0.7);
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

    it("re-clamps against duration at fire time (duration reported between call and fire)", async () => {
      // Seek issued while duration is 0 (media not loaded on the receiver).
      // Between the seek call and the 200ms debounce fire, the receiver
      // reports its duration (100). The deferred seek must re-clamp against
      // the now-known duration rather than forwarding the raw out-of-range
      // positionSeconds (e.g. a chapter jump to 3600).
      const { result, fireEvent, setPlayer, controller, player } = await mountHook(undefined, {
        player: { duration: 0 },
      });
      vi.useFakeTimers({ shouldAdvanceTime: false, now: Date.now() });
      await act(async () => {
        result.current.seek(3600);
      });
      // Before the debounce fires, the receiver reports its duration.
      await act(async () => {
        setPlayer({ duration: 100 });
        fireEvent("isMediaLoadedChanged");
      });
      await act(async () => {
        vi.advanceTimersByTime(200);
      });
      expect(controller.seek).toHaveBeenCalledTimes(1);
      // Re-clamped against the duration known at fire time, not the raw 3600.
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
      expect((player as Record<string, unknown>).volumeLevel).toBe(1);
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

    it("treats UPPERCASE 'PLAYING' as playing for extrapolation (casing normalized)", async () => {
      // Real receivers report chrome.cast.media.PlayerState.PLAYING. The hook
      // must normalize the snapshot to lowercase so the
      // `state === "playing"` extrapolation branch runs (vs. falling through
      // to the non-playing "frozen" branch). The lastState hint is also
      // lowercased for consistent controller UI copy.
      const { result, fireEvent, setPlayer } = await mountHook(undefined, {
        player: { duration: 1000 },
      });
      const T0 = new Date("2026-01-01T00:00:00Z").getTime();
      vi.setSystemTime(T0);
      await act(async () => {
        setPlayer({ currentTime: 100, playerState: "PLAYING" });
        fireEvent("currentTimeChanged");
        fireEvent("playerStateChanged");
      });
      vi.setSystemTime(T0 + 10_000);
      await act(async () => {
        setPlayer({ isConnected: false });
        fireEvent("isConnectedChanged");
      });
      const proposal = result.current.resumeProposal!;
      // Extrapolated 10s forward — only happens when the normalized state
      // equals "playing".
      expect(proposal.time).toBe(110);
      expect(proposal.isStale).toBe(false);
      expect(proposal.lastState).toBe("playing");
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

    it("cancels a pending debounced seek on unmount (no late controller.seek)", async () => {
      const { result, controller, unmount } = await mountHook(undefined, {
        player: { duration: 1000 },
      });
      vi.useFakeTimers({ shouldAdvanceTime: false, now: Date.now() });
      await act(async () => {
        result.current.seek(50);
      });
      expect(controller.seek).not.toHaveBeenCalled();
      // Unmount before the debounce fires.
      act(() => {
        unmount();
      });
      await act(async () => {
        vi.advanceTimersByTime(300);
      });
      // The deferred controller.seek() must NOT fire after unmount.
      expect(controller.seek).not.toHaveBeenCalled();
      vi.useRealTimers();
    });
  });

  describe("stop()", () => {
    it("ends the session, clears connection + deviceName, sets disconnectedAt", async () => {
      const { result, ctx } = await mountHook(undefined, { loadMedia: "success" });
      await act(async () => {
        await result.current.start();
      });
      expect(result.current.isConnected).toBe(true);
      await act(async () => {
        result.current.stop();
      });
      expect(ctx.endCurrentSession).toHaveBeenCalledWith(true);
      expect(result.current.isConnected).toBe(false);
      expect(result.current.deviceName).toBe("");
    });

    it("explicit stop does NOT emit a resumeProposal (manual stop ≠ disconnect-resume)", async () => {
      const { result, fireEvent, setPlayer } = await mountHook(undefined, {
        loadMedia: "success",
        player: { duration: 1000, currentTime: 50, playerState: "playing" },
      });
      await act(async () => {
        await result.current.start();
      });
      expect(result.current.isConnected).toBe(true);
      // Seed a status snapshot so the disconnect listener would normally
      // compute an extrapolated proposal.
      await act(async () => {
        setPlayer({ currentTime: 80, playerState: "playing" });
        fireEvent("currentTimeChanged");
        fireEvent("playerStateChanged");
      });
      await act(async () => {
        result.current.stop();
      });
      // The SDK fires IS_CONNECTED_CHANGED→false after endCurrentSession —
      // the listener must skip resumeProposal computation because stop() set
      // userInitiatedStopRef.
      await act(async () => {
        setPlayer({ isConnected: false });
        fireEvent("isConnectedChanged");
      });
      expect(result.current.isConnected).toBe(false);
      expect(result.current.resumeProposal).toBeNull();
    });
  });

  describe("transport error telemetry", () => {
    it("play() throw → lastError set, onError fired, POSTs kind:cast_transport", async () => {
      const onError = vi.fn();
      const { result, controller } = await mountHook({ media: MEDIA, onError });
      // Re-mock controller.play to throw.
      controller.play.mockImplementation(() => {
        throw new Error("receiver play blew up");
      });
      fetchSpy.mockClear();
      await act(async () => {
        result.current.play();
      });
      expect(result.current.lastError).toBe("receiver play blew up");
      expect(onError).toHaveBeenCalledWith("receiver play blew up");
      const post = fetchSpy.mock.calls.find(
        (c) => typeof c[0] === "string" && c[0].endsWith("/api/log-client-error"),
      );
      expect(post).toBeTruthy();
      const body = JSON.parse(String(post?.[1]?.body));
      expect(body.kind).toBe("cast_transport");
      expect(body.meta.transportKind).toBe("cast");
      expect(body.meta.mediaSourceKind).toBe("songset");
      expect(body.meta.castAppIdMode).toBe("set");
      expect(body.meta.platform).toBeDefined();
      // Browser + URL reachability telemetry must be populated from the
      // producer side so the endpoint's redaction + expiry logic is exercised.
      expect(body.meta.browser).toBeDefined();
      // The raw presigned URL must NEVER be transmitted — only the
      // pre-redacted { host, path, expired } summary computed on the client.
      expect(body.meta.urlRedacted).toEqual({
        host: "r2.example.com",
        path: "/renders/job-1/video.mp4",
        expired: false,
      });
      expect(body.meta.url).toBeUndefined();
      expect(JSON.stringify(body)).not.toContain("X-Amz");
    });

    it("reports castAppIdMode='default' when env app id is unset (Default Media Receiver fallback)", async () => {
      vi.stubEnv("NEXT_PUBLIC_CAST_RECEIVER_APP_ID", "");
      const onError = vi.fn();
      const { result, controller } = await mountHook({ media: MEDIA, onError });
      controller.play.mockImplementation(() => {
        throw new Error("boom");
      });
      fetchSpy.mockClear();
      await act(async () => {
        result.current.play();
      });
      const post = fetchSpy.mock.calls.find(
        (c) => typeof c[0] === "string" && c[0].endsWith("/api/log-client-error"),
      );
      expect(post).toBeTruthy();
      const body = JSON.parse(String(post?.[1]?.body));
      // env unset + chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID present in the
      // SDK mock → mode is "default" (not "unset").
      expect(body.meta.castAppIdMode).toBe("default");
    });

    it("derives urlExpired from presigned X-Amz-* params on the videoUrl", async () => {
      const past = "20000101T000000Z";
      const mediaWithExpiredUrl: CastMedia = {
        ...MEDIA,
        videoUrl: `https://r2.example.com/renders/job-1/video.mp4?X-Amz-Date=${past}&X-Amz-Expires=3600`,
      };
      const onError = vi.fn();
      const { result, controller } = await mountHook({ media: mediaWithExpiredUrl, onError });
      controller.play.mockImplementation(() => {
        throw new Error("boom");
      });
      fetchSpy.mockClear();
      await act(async () => {
        result.current.play();
      });
      const post = fetchSpy.mock.calls.find(
        (c) => typeof c[0] === "string" && c[0].endsWith("/api/log-client-error"),
      );
      expect(post).toBeTruthy();
      const body = JSON.parse(String(post?.[1]?.body));
      // Client-side redaction: only host+path+expired, never the raw signed URL.
      expect(body.meta.urlRedacted).toEqual({
        host: "r2.example.com",
        path: "/renders/job-1/video.mp4",
        expired: true,
      });
      expect(body.meta.url).toBeUndefined();
      // Defense-in-depth: the signed query params must not transit the network.
      expect(JSON.stringify(body)).not.toContain("X-Amz-Date");
      expect(body.meta.urlExpired).toBeUndefined();
    });

    it("loadMedia failure → posts kind:cast_load with receiver description", async () => {
      const onError = vi.fn();
      const { result } = await mountHook({ media: MEDIA, onError }, { loadMedia: "error" });
      fetchSpy.mockClear();
      await act(async () => {
        await result.current.start();
      });
      expect(result.current.lastError).toBe("receiver rejected media");
      const post = fetchSpy.mock.calls.find(
        (c) => typeof c[0] === "string" && c[0].endsWith("/api/log-client-error"),
      );
      expect(post).toBeTruthy();
      const body = JSON.parse(String(post?.[1]?.body));
      expect(body.kind).toBe("cast_load");
      expect(body.message).toBe("receiver rejected media");
    });
  });

  describe("setMuted idempotency", () => {
    it("short-circuits when already muted (muteOrUnmute not called)", async () => {
      const { result, controller } = await mountHook(undefined, {
        player: { isMuted: true },
      });
      await act(async () => {
        result.current.setMuted(true);
      });
      expect(controller.muteOrUnmute).not.toHaveBeenCalled();
      // Toggling to false still issues muteOrUnmute exactly once.
      await act(async () => {
        result.current.setMuted(false);
      });
      expect(controller.muteOrUnmute).toHaveBeenCalledTimes(1);
    });
  });

  describe("start() session-null branch", () => {
    it("returns silently when getCurrentSession() is null after requestSession", async () => {
      const { result, ctx, session } = await mountHook();
      ctx.getCurrentSession.mockReturnValue(null);
      await act(async () => {
        await result.current.start();
      });
      expect(result.current.isConnecting).toBe(false);
      expect(result.current.isConnected).toBe(false);
      expect(session.loadMedia).not.toHaveBeenCalled();
    });
  });

  describe("start() retry can succeed after a failure", async () => {
    it("first loadMedia fails → endCurrentSession; retry with success → connected, lastError cleared", async () => {
      const onError = vi.fn();
      const { result, ctx, session } = await mountHook({ media: MEDIA, onError });
      // First loadMedia attempt errors.
      session.loadMedia.mockImplementationOnce(
        (_req: unknown, _ok: () => void, err: (e: { code: string; description?: string }) => void) =>
          err({ code: "LOAD_FAILED", description: "receiver rejected media" }),
      );
      await act(async () => {
        await result.current.start();
      });
      expect(result.current.isConnected).toBe(false);
      expect(ctx.endCurrentSession).toHaveBeenCalledTimes(1);
      // Retry: requestSession resolves to a fresh session that loads successfully.
      await act(async () => {
        await result.current.start();
      });
      expect(result.current.isConnected).toBe(true);
      expect(result.current.lastError).toBeNull();
      // endCurrentSession called exactly once (from the first failure only).
      expect(ctx.endCurrentSession).toHaveBeenCalledTimes(1);
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

    it("clears resumeProposal on reconnect so a follow-up disconnect re-fires the controller effect", async () => {
      const { result, fireEvent, setPlayer } = await mountHook(undefined, {
        player: { duration: 1000, currentTime: 100, playerState: "playing" },
      });
      // Disconnect → proposal populated.
      await act(async () => {
        setPlayer({ isConnected: false });
        fireEvent("isConnectedChanged");
      });
      expect(result.current.resumeProposal).not.toBeNull();
      // Reconnect → proposal cleared so the controller's disconnect→resume
      // effect doesn't re-fire on a subsequent disconnect with the same
      // (stale) object reference, which would prevent React from re-running
      // the effect across reconnects.
      await act(async () => {
        setPlayer({ isConnected: true });
        fireEvent("isConnectedChanged");
      });
      expect(result.current.resumeProposal).toBeNull();
      // A second disconnect must produce a fresh proposal object.
      await act(async () => {
        setPlayer({ currentTime: 150, playerState: "playing" });
        fireEvent("currentTimeChanged");
      });
      await act(async () => {
        setPlayer({ isConnected: false });
        fireEvent("isConnectedChanged");
      });
      expect(result.current.resumeProposal).not.toBeNull();
      expect(result.current.resumeProposal?.time).toBeGreaterThanOrEqual(150);
    });
  });
});
