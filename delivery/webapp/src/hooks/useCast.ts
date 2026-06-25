"use client";

// Cast transport hook: the sender-side bridge between the worship controller
// UI and a Google TV / Chromecast receiver via the Google Cast Web Sender SDK.
//
// The receiver's media status is the source of truth for the phone UI: time,
// playing state, volume, and mute all reflect `RemotePlayer` fields mutated by
// the SDK. The phone forwards intent (play / pause / seek / volume / mute) and
// reconciles local UI from Cast status while connected.
//
// Hardening from the v2 review that lives in this module:
//   - Singleton `CastContext.setOptions` (module-level `castContextInitDone`).
//   - One `RemotePlayer` + one `RemotePlayerController` per hook instance;
//     listeners attached once.
//   - `lastStatusAtMs` refreshed on every status event to anchor disconnect
//     extrapolation.
//   - Disconnect → `resumeProposal` (extrapolated TV position + stale flag).
//   - 200ms trailing-debounce latest-wins `seek()`.
//   - `setMuted` routes through `controller.muteOrUnmute()` — never volume.
//   - Buffering tracking (`bufferingSinceMs`).
//   - Unmount-safe: aborts the loader, removes all listeners, never throws.
//   - Error paths POST structured telemetry to `/api/log-client-error`
//     (best-effort; the endpoint is added in Task 8).
//   - Reconnect (status event after disconnect) reconciles UI only — never
//     issues a seek to the receiver.

import { useCallback, useEffect, useRef, useState } from "react";
import { isCastSdkSupported, loadCastSdk } from "@/lib/cast/loader";

/** Media payload the phone hands to the receiver at `start()` time. */
export interface CastMedia {
  videoUrl: string;
  title: string;
  source: { kind: "songset" | "share"; idOrToken: string };
  startSeconds: number;
}

/** Disconnect-resume hint handed to the controller UI for local resume. */
export interface ResumeProposal {
  /** Extrapolated position (seconds) to resume local playback from. */
  time: number;
  /** True when the last receiver status is older than the staleness window. */
  isStale: boolean;
  /** Last observed receiver player state ("playing" | "<actual>" | "unknown"). */
  lastState: string;
}

export interface CastTransportResult {
  isSupported: boolean;
  isAvailable: boolean;
  isConnecting: boolean;
  isConnected: boolean;
  deviceName: string;
  playerState: string;
  currentTime: number;
  lastStatusAtMs: number | null;
  duration: number;
  volume: number;
  isMuted: boolean;
  bufferingSinceMs: number | null;
  lastError: string | null;
  resumeProposal: ResumeProposal | null;
  start: () => Promise<void>;
  stop: () => void;
  play: () => void;
  pause: () => void;
  seek: (seconds: number) => void;
  setVolume: (level: number) => void;
  setMuted: (muted: boolean) => void;
  onError: (message: string) => void;
}

interface UseCastTransportOptions {
  media: CastMedia;
  onError?: (message: string) => void;
}

/**
 * Telemetry payload shape posted to `/api/log-client-error`. Mirrors the zod
 * schema introduced in Task 8 so the structured fields are available even
 * before that endpoint exists (the POST is best-effort and never blocks the UI).
 */
interface ClientErrorPayload {
  message: string;
  kind: "cast_load" | "cast_transport" | "presentation" | "other";
  meta?: {
    browser?: string;
    platform?: string;
    castAppIdMode?: "set" | "default" | "unset";
    transportKind?: "cast" | "presentation" | "none";
    mediaSourceKind?: "songset" | "share";
    urlExpired?: boolean;
    url?: string;
  };
}

/**
 * Best-effort structured telemetry POST. Never throws; failures (including the
 * endpoint not existing yet — added in Task 8) are swallowed so a telemetry
 * hiccup can never break the transport lifecycle.
 */
function postClientError(payload: ClientErrorPayload): void {
  if (typeof window === "undefined") return;
  try {
    void fetch("/api/log-client-error", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      keepalive: true,
    }).catch(() => {
      /* best-effort; endpoint may not exist yet (Task 8) */
    });
  } catch {
    /* never throw from a telemetry path */
  }
}

/**
 * Module-level singleton guard so `CastContext.setOptions()` runs at most once
 * per page load even if multiple `useCastTransport` instances mount (e.g. the
 * hook is used in two places, or React strict-mode double-invokes effects).
 */
let castContextInitDone = false;

/** Reset the singleton guard. Test-only — exported for vi.resetModules paths. */
export function __resetCastContextInitForTests(): void {
  castContextInitDone = false;
}

const SEEK_DEBOUNCE_MS = 200;
const STALE_THRESHOLD_SECONDS = 60;
const STALE_EXTRAPOLATION_CAP_SECONDS = 60;

function detectBrowser(): string {
  if (typeof navigator === "undefined") return "unknown";
  return navigator.userAgent || "unknown";
}

function detectPlatform(): string {
  if (typeof navigator === "undefined") return "unknown";
  const ua = navigator.userAgent;
  if (/iPhone|iPad|iPod/i.test(ua)) return "ios";
  if (/Android/i.test(ua)) return "android";
  if (/Macintosh|Mac OS X/i.test(ua)) return "macos";
  if (/Windows/i.test(ua)) return "windows";
  if (/Linux/i.test(ua)) return "linux";
  return "unknown";
}

function clamp(v: number, min: number, max: number): number {
  if (max < min) return min;
  return Math.max(min, Math.min(v, max));
}

export function useCastTransport({ media, onError }: UseCastTransportOptions): CastTransportResult {
  const [isSupported, setIsSupported] = useState(false);
  const [isAvailable, setIsAvailable] = useState(false);
  const [isConnecting, setIsConnecting] = useState(false);
  const [isConnected, setIsConnected] = useState(false);
  const [deviceName, setDeviceName] = useState("");
  const [playerState, setPlayerState] = useState("");
  const [currentTime, setCurrentTime] = useState(0);
  const [lastStatusAtMs, setLastStatusAtMs] = useState<number | null>(null);
  const [duration, setDuration] = useState(0);
  const [volume, setVolume] = useState(1);
  const [isMuted, setIsMuted] = useState(false);
  const [bufferingSinceMs, setBufferingSinceMs] = useState<number | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const [resumeProposal, setResumeProposal] = useState<ResumeProposal | null>(null);

  // Refs: source-of-truth for values read inside async listeners/handlers
  // (state is async and would be stale inside event handlers).
  const playerRef = useRef<cast.framework.RemotePlayer | null>(null);
  const controllerRef = useRef<cast.framework.RemotePlayerController | null>(null);
  const loadAbortRef = useRef<AbortController | null>(null);
  const seekTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onErrorRef = useRef(onError);
  const mediaRef = useRef(media);

  // Synchronous snapshot of receiver-derived state, mirrored from the player
  // on every status event. Read inside disconnect/reconnect logic.
  const snapshotRef = useRef({
    isSupported: false,
    currentTime: 0,
    duration: 0,
    volume: 1,
    isMuted: false,
    playerState: "",
    lastStatusAtMs: null as number | null,
    bufferingSinceMs: null as number | null,
    // True between a disconnect and the next connect; prevents a reconnect
    // status event from issuing a seek to the receiver.
    disconnectedAt: null as number | null,
  });

  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  useEffect(() => {
    mediaRef.current = media;
  }, [media]);

  const touchStatus = useCallback(() => {
    const now = Date.now();
    snapshotRef.current.lastStatusAtMs = now;
    setLastStatusAtMs(now);
  }, []);

  const reportTransportError = useCallback(
    (message: string, kind: "cast_load" | "cast_transport") => {
      setLastError(message);
      onErrorRef.current?.(message);
      const m = mediaRef.current;
      postClientError({
        message,
        kind,
        meta: {
          browser: detectBrowser(),
          platform: detectPlatform(),
          castAppIdMode: process.env.NEXT_PUBLIC_CAST_RECEIVER_APP_ID
            ? "set"
            : "unset",
          transportKind: "cast",
          mediaSourceKind: m.source.kind,
        },
      });
    },
    [],
  );

  // Status listeners. Attached once when the player/controller are created.
  const listenersRef = useRef<
    Array<{
      type: cast.framework.RemotePlayerEventType;
      handler: (event: cast.framework.RemotePlayerChangedEvent) => void;
    }>
  >([]);

  const buildListeners = useCallback(() => {
    const onCurrentTimeChanged = () => {
      const p = playerRef.current;
      if (!p) return;
      snapshotRef.current.currentTime = p.currentTime;
      setCurrentTime(p.currentTime);
      touchStatus();
    };
    const onPlayerStateChanged = () => {
      const p = playerRef.current;
      if (!p) return;
      snapshotRef.current.playerState = p.playerState;
      setPlayerState(p.playerState);
      if (p.duration !== snapshotRef.current.duration) {
        snapshotRef.current.duration = p.duration;
        setDuration(p.duration);
      }
      if (p.playerState === "buffering") {
        if (snapshotRef.current.bufferingSinceMs === null) {
          const now = Date.now();
          snapshotRef.current.bufferingSinceMs = now;
          setBufferingSinceMs(now);
        }
      } else if (snapshotRef.current.bufferingSinceMs !== null) {
        snapshotRef.current.bufferingSinceMs = null;
        setBufferingSinceMs(null);
      }
      touchStatus();
    };
    const onIsMediaLoadedChanged = () => {
      const p = playerRef.current;
      if (!p) return;
      if (p.duration !== snapshotRef.current.duration) {
        snapshotRef.current.duration = p.duration;
        setDuration(p.duration);
      }
      touchStatus();
    };
    const onVolumeLevelChanged = () => {
      const p = playerRef.current;
      if (!p) return;
      snapshotRef.current.volume = p.volume;
      setVolume(p.volume);
      touchStatus();
    };
    const onIsMutedChanged = () => {
      const p = playerRef.current;
      if (!p) return;
      snapshotRef.current.isMuted = p.isMuted;
      setIsMuted(p.isMuted);
      touchStatus();
    };
    const onIsConnectedChanged = () => {
      const p = playerRef.current;
      if (!p) return;
      if (p.isConnected) {
        // Reconnect: UI reconciliation only. Never issue a seek to the
        // receiver — the receiver already knows its own position.
        snapshotRef.current.disconnectedAt = null;
        setIsConnected(true);
        setDeviceName(p.displayName || "TV");
        // Refresh derived volume/mute/state on reconnect.
        snapshotRef.current.volume = p.volume;
        setVolume(p.volume);
        snapshotRef.current.isMuted = p.isMuted;
        setIsMuted(p.isMuted);
        snapshotRef.current.playerState = p.playerState;
        setPlayerState(p.playerState);
        touchStatus();
      } else {
        // Disconnect.
        snapshotRef.current.disconnectedAt = Date.now();
        setIsConnected(false);
        setDeviceName("");
        if (snapshotRef.current.bufferingSinceMs !== null) {
          snapshotRef.current.bufferingSinceMs = null;
          setBufferingSinceMs(null);
        }
        // Compute resumeProposal.
        const lastMs = snapshotRef.current.lastStatusAtMs;
        const state = snapshotRef.current.playerState;
        const curTime = snapshotRef.current.currentTime;
        const dur = snapshotRef.current.duration;
        const clampTime = (v: number) =>
          dur > 0 ? clamp(v, 0, dur) : Math.max(0, v);
        let proposal: ResumeProposal;
        if (lastMs == null) {
          proposal = { time: clampTime(curTime), isStale: false, lastState: "unknown" };
        } else if (state !== "playing") {
          proposal = {
            time: clampTime(curTime),
            isStale: false,
            lastState: state || "unknown",
          };
        } else {
          const elapsed = (Date.now() - lastMs) / 1000;
          if (elapsed > STALE_THRESHOLD_SECONDS) {
            proposal = {
              time: clampTime(curTime + STALE_EXTRAPOLATION_CAP_SECONDS),
              isStale: true,
              lastState: "playing",
            };
          } else {
            proposal = {
              time: clampTime(curTime + elapsed),
              isStale: false,
              lastState: "playing",
            };
          }
        }
        setResumeProposal(proposal);
      }
    };
    return [
      {
        type: cast.framework.RemotePlayerEventType.CURRENT_TIME_CHANGED,
        handler: onCurrentTimeChanged,
      },
      {
        type: cast.framework.RemotePlayerEventType.PLAYER_STATE_CHANGED,
        handler: onPlayerStateChanged,
      },
      {
        type: cast.framework.RemotePlayerEventType.IS_MEDIA_LOADED_CHANGED,
        handler: onIsMediaLoadedChanged,
      },
      {
        type: cast.framework.RemotePlayerEventType.VOLUME_LEVEL_CHANGED,
        handler: onVolumeLevelChanged,
      },
      {
        type: cast.framework.RemotePlayerEventType.IS_MUTED_CHANGED,
        handler: onIsMutedChanged,
      },
      {
        type: cast.framework.RemotePlayerEventType.IS_CONNECTED_CHANGED,
        handler: onIsConnectedChanged,
      },
    ];
  }, [touchStatus]);

  // Init effect: load SDK, setOptions once, create player+controller, attach
  // listeners. Runs once per mount.
  useEffect(() => {
    const envAppId = process.env.NEXT_PUBLIC_CAST_RECEIVER_APP_ID;

    // isSupported requires both a resolvable receiver application id (the env
    // var) and a browser that can host the Cast Web Sender SDK. When the env
    // var is missing we short-circuit before attempting any SDK load and leave
    // `isSupported=false`.
    if (!envAppId) {
      return;
    }

    if (typeof window === "undefined") return;

    let cancelled = false;
    const ac = new AbortController();
    loadAbortRef.current = ac;

    loadCastSdk({ signal: ac.signal })
      .then(() => {
        if (cancelled) return;
        if (!isCastSdkSupported()) {
          // SDK script reported loaded but globals are not present — browser
          // cannot host Cast (e.g. iOS). Leave isSupported=false.
          return;
        }
        const ctx = cast.framework.CastContext.getInstance();
        // Singleton: setOptions runs at most once per page load.
        if (!castContextInitDone) {
          castContextInitDone = true;
          ctx.setOptions({
            receiverApplicationId:
              envAppId ||
              (typeof chrome !== "undefined" &&
              chrome.cast &&
              chrome.cast.DEFAULT_MEDIA_RECEIVER_APP_ID
                ? chrome.cast.DEFAULT_MEDIA_RECEIVER_APP_ID
                : envAppId),
            autoJoinPolicy: chrome.cast.AutoJoinPolicy.TAB_AND_ORIGIN_SCOPED,
            androidReceiverCompatible: true,
          });
        }
        const player = new cast.framework.RemotePlayer();
        const controller = new cast.framework.RemotePlayerController(player);
        playerRef.current = player;
        controllerRef.current = controller;

        const listeners = buildListeners();
        listenersRef.current = listeners;
        for (const { type, handler } of listeners) {
          controller.addEventListener(type, handler);
        }

        // Seed snapshot from the player's initial values.
        snapshotRef.current.volume = player.volume;
        snapshotRef.current.isMuted = player.isMuted;
        snapshotRef.current.playerState = player.playerState;
        snapshotRef.current.currentTime = player.currentTime;
        snapshotRef.current.duration = player.duration;
        setVolume(player.volume);
        setIsMuted(player.isMuted);
        setPlayerState(player.playerState);
        setCurrentTime(player.currentTime);
        setDuration(player.duration);

        snapshotRef.current.isSupported = true;
        setIsSupported(true);
        setIsAvailable(true);
      })
      .catch(() => {
        // SDK failed to load — leave isSupported=false silently.
      });

    return () => {
      cancelled = true;
      ac.abort();
      loadAbortRef.current = null;
      // Remove listeners without throwing.
      try {
        const controller = controllerRef.current;
        const listeners = listenersRef.current;
        if (controller && listeners) {
          for (const { type, handler } of listeners) {
            controller.removeEventListener(type, handler);
          }
        }
      } catch {
        /* unmount-safe: never throw on cleanup */
      }
      // Cancel any pending debounced seek.
      if (seekTimerRef.current) {
        clearTimeout(seekTimerRef.current);
        seekTimerRef.current = null;
      }
      listenersRef.current = [];
      playerRef.current = null;
      controllerRef.current = null;
    };
  }, [buildListeners]);

  const start = useCallback(async () => {
    if (!snapshotRef.current.isSupported) return;
    const ctx = cast.framework.CastContext.getInstance();
    setIsConnecting(true);
    let session: chrome.cast.Session | null = null;
    try {
      await ctx.requestSession();
      session = ctx.getCurrentSession();
    } catch {
      // User cancelled the device picker or request failed. No dangling
      // session is created by a cancel — just reset connecting state.
      setIsConnecting(false);
      return;
    }
    if (!session) {
      setIsConnecting(false);
      return;
    }
    const m = mediaRef.current;
    try {
      const mediaInfo = new chrome.cast.media.MediaInfo(m.videoUrl, "video/mp4");
      mediaInfo.metadata = {
        title: m.title,
        metadataType: chrome.cast.media.MetadataType.GENERIC,
      };
      mediaInfo.streamType = chrome.cast.StreamType.BUFFERED;
      const loadRequest = new chrome.cast.media.LoadRequest(mediaInfo);
      loadRequest.currentTime = m.startSeconds ?? 0;
      await new Promise<void>((resolve, reject) => {
        session!.loadMedia(
          loadRequest,
          () => resolve(),
          (err: chrome.cast.Error) =>
            reject(
              new Error(
                err?.description || err?.code || "loadMedia failed",
              ),
            ),
        );
      });
      // Success.
      setIsConnected(true);
      const p = playerRef.current;
      setDeviceName(p?.displayName ? p.displayName : "TV");
      setIsConnecting(false);
      setLastError(null);
      setResumeProposal(null);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Cast loadMedia failed";
      setIsConnected(false);
      setIsConnecting(false);
      // No dangling session on loadMedia failure.
      try {
        ctx.endCurrentSession(true);
      } catch {
        /* best-effort */
      }
      reportTransportError(msg, "cast_load");
    }
  }, [reportTransportError]);

  const stop = useCallback(() => {
    const ctx = cast.framework.CastContext.getInstance();
    try {
      ctx.endCurrentSession(true);
    } catch {
      /* best-effort */
    }
    setIsConnected(false);
    setDeviceName("");
    snapshotRef.current.disconnectedAt = Date.now();
  }, []);

  const play = useCallback(() => {
    try {
      controllerRef.current?.play();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Cast play failed";
      reportTransportError(msg, "cast_transport");
    }
  }, [reportTransportError]);

  const pause = useCallback(() => {
    try {
      controllerRef.current?.pause();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Cast pause failed";
      reportTransportError(msg, "cast_transport");
    }
  }, [reportTransportError]);

  const seek = useCallback(
    (seconds: number) => {
      const p = playerRef.current;
      const c = controllerRef.current;
      if (!p || !c) return;
      const dur = snapshotRef.current.duration;
      const clamped = dur > 0 ? clamp(seconds, 0, dur) : Math.max(0, seconds);
      // 200ms trailing debounce, latest-wins.
      if (seekTimerRef.current) {
        clearTimeout(seekTimerRef.current);
      }
      seekTimerRef.current = setTimeout(() => {
        const player = playerRef.current;
        const controller = controllerRef.current;
        if (!player || !controller) return;
        // RemotePlayerController.seek() reads the RemotePlayer.currentTime
        // field, so set it before invoking.
        player.currentTime = clamped;
        try {
          controller.seek();
        } catch (e) {
          const msg = e instanceof Error ? e.message : "Cast seek failed";
          reportTransportError(msg, "cast_transport");
        }
      }, SEEK_DEBOUNCE_MS);
    },
    [reportTransportError],
  );

  const setVolumeLevel = useCallback(
    (level: number) => {
      const c = controllerRef.current;
      if (!c) return;
      const clamped = clamp(level, 0, 1);
      try {
        c.setVolumeLevel(clamped);
      } catch (e) {
        const msg = e instanceof Error ? e.message : "Cast setVolume failed";
        reportTransportError(msg, "cast_transport");
      }
    },
    [reportTransportError],
  );

  const setMuted = useCallback(
    (muted: boolean) => {
      const c = controllerRef.current;
      const p = playerRef.current;
      if (!c || !p) return;
      // Always route through muteOrUnmute — never setVolume(0). The receiver
      // mute bit is distinct from the volume level.
      if (p.isMuted === muted) return;
      try {
        c.muteOrUnmute();
      } catch (e) {
        const msg = e instanceof Error ? e.message : "Cast setMuted failed";
        reportTransportError(msg, "cast_transport");
      }
    },
    [reportTransportError],
  );

  const onErrorExternal = useCallback((message: string) => {
    reportTransportError(message, "cast_transport");
  }, [reportTransportError]);

  return {
    isSupported,
    isAvailable,
    isConnecting,
    isConnected,
    deviceName,
    playerState,
    currentTime,
    lastStatusAtMs,
    duration,
    volume,
    isMuted,
    bufferingSinceMs,
    lastError,
    resumeProposal,
    start,
    stop,
    play,
    pause,
    seek,
    setVolume: setVolumeLevel,
    setMuted,
    onError: onErrorExternal,
  };
}
