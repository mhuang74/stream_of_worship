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
  /** Cast availability signal driving the controller diagnostic sheet.
   * "unknown" until the SDK load settles; "available" when the receiver app
   * id resolves; "unavailable" when the SDK cannot host Cast (e.g. iOS) or no
   * receiver app id could be resolved. */
  availability: "unknown" | "available" | "unavailable";
  isConnecting: boolean;
  isConnected: boolean;
  deviceName: string;
  playerState: string;
  currentTime: number;
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
    platform?: string;
    browser?: string;
    castAppIdMode?: "set" | "default" | "unset";
    castErrorCode?: string;
    castState?: string;
    sessionState?: string;
    transportKind?: "cast" | "presentation" | "none";
    mediaSourceKind?: "songset" | "share";
    /**
     * Pre-redacted URL summary computed on the CLIENT side before POSTing so
     * the raw presigned R2 URL (which carries `X-Amz-Signature` /
     * `X-Amz-Credential` granting 4h of R2 read access) is never transmitted
     * over the network — even to our own server. Defense-in-depth against any
     * server-side body-capture middleware (APM/WAF/Vercel request inspector)
     * that would otherwise persist the unredacted signed URL.
     */
    urlRedacted?: {
      host: string;
      path: string;
      expired: boolean;
    };
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
 * per (page load × receiver application id). Tracks the applied
 * `receiverApplicationId` alongside the done-state so a second mount with a
 * DIFFERENT app id (e.g. a per-route dev/staging receiver) re-runs setOptions
 * instead of silently inheriting the prior page's options. Without this, a
 * mount in an SPA navigation session would skip setOptions thinking it already
 * ran, while the env var may have resolved to a different receiver app id.
 */
let castContextInitDone: { receiverAppId: string } | null = null;

/** Reset the singleton guard. Test-only — exported for vi.resetModules paths. */
export function __resetCastContextInitForTests(): void {
  castContextInitDone = null;
}

const SEEK_DEBOUNCE_MS = 200;
const STALE_THRESHOLD_SECONDS = 60;
const STALE_EXTRAPOLATION_CAP_SECONDS = 60;

function configuredCastReceiverAppId(): string {
  return process.env.NEXT_PUBLIC_CAST_RECEIVER_APP_ID?.trim() ?? "";
}

function defaultCastReceiverAppId(): string {
  if (typeof window === "undefined") return "";
  return window.chrome?.cast?.media?.DEFAULT_MEDIA_RECEIVER_APP_ID ?? "";
}

function resolveCastReceiverAppId(): {
  receiverAppId: string;
  mode: "set" | "default" | "unset";
} {
  const envAppId = configuredCastReceiverAppId();
  if (envAppId) return { receiverAppId: envAppId, mode: "set" };
  const defaultAppId = defaultCastReceiverAppId();
  if (defaultAppId) return { receiverAppId: defaultAppId, mode: "default" };
  return { receiverAppId: "", mode: "unset" };
}

function formatCastRequestError(err: unknown): { message: string; code?: string } {
  const rawCode = (err as { code?: unknown })?.code;
  const code =
    typeof rawCode === "string" || typeof rawCode === "number" ? String(rawCode) : undefined;
  const description = (err as { description?: unknown })?.description;
  const message = (err as { message?: unknown })?.message;
  const primary =
    (typeof description === "string" && description.trim()) ||
    (typeof message === "string" && message.trim()) ||
    "Cast session request failed";
  if (!code || primary.toLowerCase().includes(code.toLowerCase())) {
    return { message: primary, code };
  }
  return { message: `${primary} (${code})`, code };
}

function safeGetCastState(ctx: cast.framework.CastContext): string | undefined {
  try {
    return String(ctx.getCastState());
  } catch {
    return undefined;
  }
}

function safeGetSessionState(ctx: cast.framework.CastContext): string | undefined {
  try {
    return String(ctx.getSessionState());
  } catch {
    return undefined;
  }
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

/** Short browser label derived from navigator.userAgent for telemetry. */
function detectBrowser(): string {
  if (typeof navigator === "undefined") return "unknown";
  const ua = navigator.userAgent;
  if (/Edg\//i.test(ua)) return "edge";
  if (/OPR\//i.test(ua)) return "opera";
  if (/Firefox\//i.test(ua)) return "firefox";
  if (/Chrome\//i.test(ua) && !/Chromium/i.test(ua)) return "chrome";
  if (/Safari\//i.test(ua) && !/Chrome/i.test(ua)) return "safari";
  return "unknown";
}

/**
 * Resolve the Cast receiver app id mode for telemetry. Reflects the same
 * resolution path used at `setOptions` time: env var wins ("set"), otherwise
 * the Default Media Receiver constant ("default"), otherwise "unset". The
 * `${envAppId ? "set" : ...}` form mirrors the runtime fallback at line
 * ~400 (`envAppId || chrome.cast.media.DEFAULT_MEDIA_RECEIVER_APP_ID`).
 */
function castAppIdMode(): "set" | "default" | "unset" {
  return resolveCastReceiverAppId().mode;
}

function clamp(v: number, min: number, max: number): number {
  if (max < min) return min;
  return Math.max(min, Math.min(v, max));
}

/**
 * Normalize the Cast SDK receiver `playerState`. The real Web Sender
 * `RemotePlayer.playerState` is `chrome.cast.media.PlayerState`, whose
 * documented values are UPPERCASE (`PLAYING` / `PAUSED` / `BUFFERING` /
 * `IDLE`). The controller UI, buffering tracker, and disconnect-extrapolation
 * all compare against the lowercase form — normalizing once at every read
 * site keeps them correct in live (uppercase) and mocked (lowercase) sessions
 * alike, without sprinkling `.toLowerCase()` through the comparison logic.
 */
function normalizePlayerState(raw: string | undefined | null): string {
  return typeof raw === "string" ? raw.toLowerCase() : "";
}

/** Parse the AWS SigV4 basic-format date (`YYYYMMDDTHHMMSSZ`) → epoch ms. */
function parseAwsDateMs(s: string): number | null {
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(s);
  if (!m) return null;
  return Date.parse(`${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:${m[6]}Z`);
}

/**
 * Reduce a raw (potentially presigned R2) URL to a PII-safe `{ host, path,
 * expired }` summary ON THE CLIENT SIDE before posting telemetry. The raw
 * URL — which may carry signed parameters granting R2 read access for up to
 * 4 hours — is never transmitted over the network, even to our own server,
 * so any body-capture middleware cannot leak the credentials.
 */
function redactUrlClientSide(
  raw: string,
): { host: string; path: string; expired: boolean } | undefined {
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    return undefined;
  }
  let expired = false;
  const amzDate = parsed.searchParams.get("X-Amz-Date");
  const amzExpires = parsed.searchParams.get("X-Amz-Expires");
  const epochExpires = parsed.searchParams.get("expires");
  if (amzDate && amzExpires) {
    const amzDateMs = parseAwsDateMs(amzDate);
    const expiresInSec = Number(amzExpires);
    if (amzDateMs !== null && Number.isFinite(expiresInSec)) {
      expired = Date.now() > amzDateMs + expiresInSec * 1000;
    }
  } else if (epochExpires) {
    const n = Number(epochExpires);
    if (Number.isFinite(n)) {
      expired = Date.now() > n * 1000;
    }
  }
  return { host: parsed.host, path: parsed.pathname, expired };
}

export function useCastTransport({ media, onError }: UseCastTransportOptions): CastTransportResult {
  const [isSupported, setIsSupported] = useState(false);
  const [availability, setAvailability] = useState<"unknown" | "available" | "unavailable">("unknown");
  const [isConnecting, setIsConnecting] = useState(false);
  const [isConnected, setIsConnected] = useState(false);
  const [deviceName, setDeviceName] = useState("");
  const [playerState, setPlayerState] = useState("");
  const [currentTime, setCurrentTime] = useState(0);
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
  // True between a `stop()` call and the resulting `IS_CONNECTED_CHANGED →
  // false` event. Lets the disconnect listener distinguish an explicit
  // user-initiated stop (skip resumeProposal — the user intended teardown)
  // from an unexpected receiver disconnect (compute resumeProposal so the
  // controller UI can offer local resume).
  const userInitiatedStopRef = useRef(false);

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
    snapshotRef.current.lastStatusAtMs = Date.now();
  }, []);

  const reportTransportError = useCallback(
    (message: string, kind: "cast_load" | "cast_transport") => {
      setLastError(message);
      onErrorRef.current?.(message);
      const m = mediaRef.current;
      // Reduce the raw presigned R2 URL to {host, path, expired} ON THE CLIENT
      // before posting — never transmit the signed URL over the network, even
      // to our own server (defense-in-depth against body-capture middleware
      // leaking 4h-valid R2 credentials).
      const videoUrl = m.videoUrl || undefined;
      const urlRedacted = videoUrl ? redactUrlClientSide(videoUrl) : undefined;
      postClientError({
        message,
        kind,
        meta: {
          platform: detectPlatform(),
          browser: detectBrowser(),
          castAppIdMode: castAppIdMode(),
          transportKind: "cast",
          mediaSourceKind: m.source.kind,
          ...(urlRedacted ? { urlRedacted } : {}),
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
      // Normalize UPPERCASE chrome.cast.media.PlayerState values ("PLAYING",
      // "PAUSED", "BUFFERING", "IDLE") to the lowercase form every downstream
      // comparison expects.
      const state = normalizePlayerState(p.playerState);
      snapshotRef.current.playerState = state;
      setPlayerState(state);
      if (p.duration !== snapshotRef.current.duration) {
        snapshotRef.current.duration = p.duration;
        setDuration(p.duration);
      }
      if (state === "buffering") {
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
      // Real Web Sender RemotePlayer exposes receiver volume as `volumeLevel`
      // (not `volume`, which is undefined in live sessions). Read it here so
      // the controller tracks actual TV volume instead of seeding undefined.
      snapshotRef.current.volume = p.volumeLevel;
      setVolume(p.volumeLevel);
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
        // Clear any stale resumeProposal from a prior disconnect so the
        // controller's disconnect→resume effect doesn't re-fire on the next
        // disconnect with a stale object reference (identical proposal object
        // references would cause the effect not to re-run across reconnects).
        setResumeProposal(null);
        setIsConnected(true);
        setDeviceName(p.displayName || "TV");
        // Refresh derived volume/mute/state on reconnect. Use `volumeLevel`
        // (real RemotePlayer surface) and normalize the SDK's UPPERCASE
        // PlayerState for downstream lowercase comparisons.
        snapshotRef.current.volume = p.volumeLevel;
        setVolume(p.volumeLevel);
        snapshotRef.current.isMuted = p.isMuted;
        setIsMuted(p.isMuted);
        const reconnectState = normalizePlayerState(p.playerState);
        snapshotRef.current.playerState = reconnectState;
        setPlayerState(reconnectState);
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
        // An explicit user-initiated `stop()` is a clean teardown — the user
        // expects no "tap to resume" prompt. Skip the resumeProposal
        // computation entirely (mirrors the v3 contract that manual stop ≠
        // disconnect-resume). Clear the ref after handling.
        if (userInitiatedStopRef.current) {
          userInitiatedStopRef.current = false;
          setResumeProposal(null);
          return;
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
    if (typeof window === "undefined") return;

    let cancelled = false;
    const ac = new AbortController();
    loadAbortRef.current = ac;

    loadCastSdk({ signal: ac.signal })
      .then(() => {
        if (cancelled) return;
        if (!isCastSdkSupported()) {
          // SDK script reported loaded but globals are not present — browser
          // cannot host Cast (e.g. iOS). Leave isSupported=false and mark the
          // transport unavailable so the diagnostic sheet can surface.
          setAvailability("unavailable");
          return;
        }
        const ctx = cast.framework.CastContext.getInstance();
        // Resolve the receiver application id. The env var wins; otherwise fall
        // back to Google's built-in Default Media Receiver constant (the v3
        // production default). If neither resolves, Cast cannot start.
        const { receiverAppId } = resolveCastReceiverAppId();
        if (!receiverAppId) {
          setAvailability("unavailable");
          return;
        }
        // Singleton per receiverApplicationId: setOptions runs at most once per
        // (page load × app id). A second mount with a different receiver app id
        // (e.g. per-route dev/staging) re-runs setOptions rather than silently
        // inheriting the prior page's options.
        if (!castContextInitDone || castContextInitDone.receiverAppId !== receiverAppId) {
          castContextInitDone = { receiverAppId };
          ctx.setOptions({
            receiverApplicationId: receiverAppId,
            autoJoinPolicy: chrome.cast.AutoJoinPolicy.TAB_AND_ORIGIN_SCOPED,
            // androidReceiverCompatible is intentionally omitted. That flag
            // enables Cast Connect (native Android TV receiver app), which SOW
            // does not have — it uses Google's Default Media Receiver. With the
            // flag set, AndroidTV attempts the Cast Connect launch path, falls
            // back to loading the webapp projection page in a WebView, and the
            // page's unauthenticated API fetches hit proxy.ts's redirect to
            // /login (HTML), producing "invalid token '<'" JSON parse errors.
            // Omitting the flag makes AndroidTV use the Default Media Receiver
            // path (MP4 from R2 via loadMedia), identical to Chromecast dongles.
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

        // Seed snapshot from the player's initial values. `volumeLevel` is the
        // real RemotePlayer field (`volume` is undefined in live sessions);
        // `playerState` is normalized from UPPERCASE chrome.cast.media
        // PlayerState to the lowercase form the controller compares against.
        const seededState = normalizePlayerState(player.playerState);
        snapshotRef.current.volume = player.volumeLevel;
        snapshotRef.current.isMuted = player.isMuted;
        snapshotRef.current.playerState = seededState;
        snapshotRef.current.currentTime = player.currentTime;
        snapshotRef.current.duration = player.duration;
        setVolume(player.volumeLevel);
        setIsMuted(player.isMuted);
        setPlayerState(seededState);
        setCurrentTime(player.currentTime);
        setDuration(player.duration);

        snapshotRef.current.isSupported = true;
        setIsSupported(true);
        setAvailability("available");
      })
      .catch(() => {
        // SDK script failed to load (blocked, network blip, 5xx). Functionally
        // equivalent to "Cast cannot be hosted on this browser right now" —
        // mark unavailable so the diagnostic sheet, Presentation fallback, and
        // iPhone AirPlay hint can all render instead of stranding the user
        // with a blank transport surface for the rest of the session.
        if (cancelled) return;
        setAvailability("unavailable");
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
    } catch (err) {
      // The Cast SDK rejects `requestSession()` for both user-cancel AND
      // non-cancel SDK errors (receiver_unavailable, timeout,
      // session_request_failed, framework not ready). Distinguish by error
      // code: `cancel` is a no-op reset; everything else is a real failure
      // that must surface a lastError + onError callback + telemetry POST so
      // the user is not left staring at a non-responsive Cast button with
      // zero feedback.
      setIsConnecting(false);
      const code = (err as { code?: unknown })?.code;
      if (code === "cancel") {
        // User dismissed the device picker — no dangling session, no error.
        return;
      }
      const { message: msg, code: castErrorCode } = formatCastRequestError(err);
      setIsConnected(false);
      setLastError(msg);
      onErrorRef.current?.(msg);
      postClientError({
        message: msg,
        kind: "cast_load",
        meta: {
          platform: detectPlatform(),
          browser: detectBrowser(),
          castAppIdMode: castAppIdMode(),
          castErrorCode,
          castState: safeGetCastState(ctx),
          sessionState: safeGetSessionState(ctx),
          transportKind: "cast",
          mediaSourceKind: mediaRef.current.source.kind,
        },
      });
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
    // Mark this as an explicit user-initiated stop so the disconnect listener
    // skips the resumeProposal computation (manual stop ≠ disconnect-resume).
    userInitiatedStopRef.current = true;
    const ctx = cast.framework.CastContext.getInstance();
    try {
      ctx.endCurrentSession(true);
    } catch {
      /* best-effort */
    }
    setIsConnected(false);
    setDeviceName("");
    setResumeProposal(null);
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
      // 200ms trailing debounce, latest-wins.
      if (seekTimerRef.current) {
        clearTimeout(seekTimerRef.current);
      }
      seekTimerRef.current = setTimeout(() => {
        const player = playerRef.current;
        const controller = controllerRef.current;
        if (!player || !controller) return;
        // Re-clamp against the latest duration at fire time — the duration
        // may have been reported by the receiver between the seek call and
        // the debounce firing (e.g. a chapter jump issued before the media
        // loaded on the receiver). Without re-clamping, an out-of-range
        // positionSeconds can be forwarded to the receiver when duration
        // was 0 at call time.
        const dur = snapshotRef.current.duration;
        const clamped =
          dur > 0 ? clamp(seconds, 0, dur) : Math.max(0, seconds);
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
    availability,
    isConnecting,
    isConnected,
    deviceName,
    playerState,
    currentTime,
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
