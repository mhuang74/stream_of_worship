"use client";

// Presentation API receive + (dev-only) sender hooks.
//
// The receiver hook runs on the projection page (the TV side): it listens for
// inbound `PresentationCommand` JSON messages from a controlling page and
// projects them onto local video element callbacks. It also exposes
// `sendStatus(status)` so the projection page can push
// `PresentationStatus` JSON back to the controller over the same
// `PresentationConnection` (the controller-to-projection "ready / disconnected /
// error" channel).
//
// The sender hook is the dev-only fallback transport used when the Cast Web
// Sender SDK is unavailable (e.g. iOS) or in explicit browser-to-browser dev
// mode. `send(command)` issues validated JSON over `PresentationConnection.send`.
// `send({type:"mute",muted})` is simulated via volume level on the receiver
// (the Cast path uses the real receiver mute bit, distinct from volume level).

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { PresentationCommand, PresentationStatus } from "@/types/presentation-api";

export type { PresentationCommand, PresentationStatus };

function clamp(v: number, min: number, max: number): number {
  return Math.max(min, Math.min(v, max));
}

/**
 * Validate + normalize a raw (parsed) value into a `PresentationCommand`, or
 * return `null` when the value is malformed / an unknown command type.
 *
 * Normalization applied (so the wire contract is always well-formed even when
 * a peer emits slightly off-spec JSON):
 *   - `volume.level` clamped to `[0,1]` (non-finite rejected).
 *   - `mute.muted` coerced via `Boolean(...)` (so `"yes"`/1/truthy → true).
 *   - `seek.positionSeconds` required finite and `>=0`.
 *   - `songTitle.title` required to be a string.
 *   - unknown `type` → null (forward-compatible no-op).
 */
export function validatePresentationCommand(raw: unknown): PresentationCommand | null {
  if (typeof raw !== "object" || raw === null) return null;
  const obj = raw as Record<string, unknown>;
  const type = obj.type;
  if (typeof type !== "string") return null;
  switch (type) {
    case "play":
      return { type: "play" };
    case "pause":
      return { type: "pause" };
    case "seek": {
      const p = obj.positionSeconds;
      if (typeof p !== "number" || !Number.isFinite(p) || p < 0) return null;
      return { type: "seek", positionSeconds: p };
    }
    case "volume": {
      const level = obj.level;
      if (typeof level !== "number" || !Number.isFinite(level)) return null;
      return { type: "volume", level: clamp(level, 0, 1) };
    }
    case "mute": {
      return { type: "mute", muted: Boolean(obj.muted) };
    }
    case "songTitle": {
      const title = obj.title;
      if (typeof title !== "string") return null;
      return { type: "songTitle", title };
    }
    default:
      return null;
  }
}

/**
 * Validate a raw (parsed) value into a `PresentationStatus` — the
 * receiver→sender status channel (ready / disconnected / error). Unknown types
 * and malformed payloads return null (forward-compatible no-op) so a peer
 * emitting an off-spec status never throws on the sender side.
 */
export function validatePresentationStatus(raw: unknown): PresentationStatus | null {
  if (typeof raw !== "object" || raw === null) return null;
  const obj = raw as Record<string, unknown>;
  const type = obj.type;
  if (typeof type !== "string") return null;
  switch (type) {
    case "ready":
      return { type: "ready" };
    case "disconnected":
      return { type: "disconnected" };
    case "error": {
      const message = obj.message;
      if (typeof message !== "string") return null;
      return { type: "error", message };
    }
    case "media": {
      const currentTime = obj.currentTime;
      const duration = obj.duration;
      const playerState = obj.playerState;
      const volume = obj.volume;
      if (typeof currentTime !== "number" || !Number.isFinite(currentTime) || currentTime < 0) {
        return null;
      }
      if (typeof duration !== "number" || !Number.isFinite(duration) || duration < 0) {
        return null;
      }
      if (
        playerState !== "playing" &&
        playerState !== "paused" &&
        playerState !== "buffering"
      ) {
        return null;
      }
      if (typeof volume !== "number" || !Number.isFinite(volume)) return null;
      return {
        type: "media",
        currentTime,
        duration,
        playerState,
        volume: clamp(volume, 0, 1),
        isMuted: Boolean(obj.isMuted),
      };
    }
    default:
      return null;
  }
}

export interface UsePresentationReceiverOptions {
  onPlay?: () => void;
  onPause?: () => void;
  onSeek?: (positionSeconds: number) => void;
  onVolume?: (level: number) => void;
  onMute?: (muted: boolean) => void;
  onSongTitle?: (title: string) => void;
  onConnected?: () => void;
  onDisconnected?: () => void;
}

export interface UsePresentationReceiverResult {
  /** Push a `PresentationStatus` JSON message to the controlling page. */
  sendStatus: (status: PresentationStatus) => void;
}

export function usePresentationReceiver(
  options: UsePresentationReceiverOptions,
): UsePresentationReceiverResult {
  const optionsRef = useRef(options);
  useLayoutEffect(() => {
    optionsRef.current = options;
  });

  const connectionsRef = useRef<PresentationConnection[]>([]);

  const sendStatus = useCallback((status: PresentationStatus) => {
    // Best-effort: push to the most recently connected controlling page.
    const conns = connectionsRef.current;
    if (conns.length === 0) return;
    const conn = conns[conns.length - 1];
    try {
      conn.send(JSON.stringify(status));
    } catch {
      /* best-effort: never throw from a status push */
    }
  }, []);

  useEffect(() => {
    if (typeof navigator === "undefined") return;

    const receiver = navigator.presentation?.receiver;
    if (!receiver) return;

    const handleMessage = (event: Event) => {
      try {
        const data = (event as MessageEvent).data as string;
        const raw = JSON.parse(data);
        const command = validatePresentationCommand(raw);
        if (!command) return;
        const opts = optionsRef.current;

        switch (command.type) {
          case "play":
            opts.onPlay?.();
            break;
          case "pause":
            opts.onPause?.();
            break;
          case "seek":
            opts.onSeek?.(command.positionSeconds);
            break;
          case "volume":
            opts.onVolume?.(command.level);
            break;
          case "mute":
            opts.onMute?.(command.muted);
            break;
          case "songTitle":
            opts.onSongTitle?.(command.title);
            break;
        }
      } catch {
        // Ignore malformed JSON.
      }
    };

    // Per-connection terminate handlers so the closed connection is spliced
    // out of the active list (sendStatus must never target a dead connection).
    const terminateHandlers = new Map<PresentationConnection, () => void>();

    const connectToConnection = (connection: PresentationConnection) => {
      const onTerminate = () => {
        const conns = connectionsRef.current;
        const idx = conns.indexOf(connection);
        if (idx >= 0) {
          conns.splice(idx, 1);
        }
        terminateHandlers.delete(connection);
        try {
          connection.removeEventListener("message", handleMessage);
          connection.removeEventListener("close", onTerminate);
          connection.removeEventListener("terminate", onTerminate);
        } catch {
          /* best-effort */
        }
        optionsRef.current.onDisconnected?.();
      };
      terminateHandlers.set(connection, onTerminate);
      connection.addEventListener("message", handleMessage);
      connection.addEventListener("close", onTerminate);
      connection.addEventListener("terminate", onTerminate);
      connectionsRef.current.push(connection);
      optionsRef.current.onConnected?.();
    };

    let cancelled = false;

    receiver.connectionList
      .then((connectionList) => {
        if (cancelled) return;
        connectionList.connections.forEach(connectToConnection);

        connectionList.addEventListener("connectionavailable", (event) => {
          connectToConnection(event.connection);
        });
      })
      .catch(() => {
        // Receiver API not available or connectionList failed.
      });

    return () => {
      cancelled = true;
      const conns = connectionsRef.current;
      for (const conn of conns) {
        try {
          const handler = terminateHandlers.get(conn);
          conn.removeEventListener("message", handleMessage);
          if (handler) {
            conn.removeEventListener("close", handler);
            conn.removeEventListener("terminate", handler);
          }
        } catch {
          /* best-effort cleanup */
        }
      }
      terminateHandlers.clear();
      connectionsRef.current = [];
    };
  }, []);

  return { sendStatus };
}

export interface UsePresentationSenderOptions {
  presentationUrl: string;
  onConnected?: () => void;
  onDisconnected?: () => void;
  onStartError?: (message: string) => void;
  /**
   * Receiver→sender status channel. The receiver pushes
   * `PresentationStatus` JSON (`ready` / `disconnected` / `error`) over the
   * same `PresentationConnection`; the sender parses, validates, and dispatches
   * to this callback. Used to surface "TV projection failed — check
   * connection" on `error` and to know the projection page loaded on `ready`.
   */
  onStatus?: (status: PresentationStatus) => void;
}

export interface UsePresentationSenderResult {
  isSupported: boolean;
  isConnected: boolean;
  start: () => Promise<void>;
  send: (command: PresentationCommand) => void;
}

/**
 * Dev-only Presentation API sender. Establishes a `PresentationConnection` to
 * a projection URL via `PresentationRequest.start()` (requires a user gesture)
 * and forwards validated `PresentationCommand` JSON over `connection.send`.
 *
 * Only used when `useCastTransport.isSupported === false` or in explicit
 * browser-to-browser dev mode — the Cast SDK is the production transport.
 *
 * Bidirectional: the receiver pushes `PresentationStatus` JSON back over the
 * same connection, parsed + validated here and dispatched to `onStatus`.
 */
export function usePresentationSender(
  options: UsePresentationSenderOptions,
): UsePresentationSenderResult {
  const { presentationUrl } = options;
  const [isSupported] = useState(() => {
    if (typeof window === "undefined") return false;
    return typeof PresentationRequest !== "undefined";
  });
  const [isConnected, setIsConnected] = useState(false);
  const connectionRef = useRef<PresentationConnection | null>(null);
  const optionsRef = useRef(options);
  useLayoutEffect(() => {
    optionsRef.current = options;
  });

  const start = useCallback(async () => {
    if (typeof window === "undefined") return;
    if (typeof PresentationRequest === "undefined") {
      optionsRef.current.onStartError?.("Presentation API not supported");
      return;
    }
    // Close any prior connection before opening a new one so its listeners
    // and the connection itself are not leaked across repeated start() calls.
    const prior = connectionRef.current;
    if (prior) {
      connectionRef.current = null;
      try {
        prior.close();
      } catch {
        /* best-effort */
      }
    }
    try {
      const request = new PresentationRequest([presentationUrl]);
      const connection = await request.start();
      connectionRef.current = connection;
      setIsConnected(true);
      optionsRef.current.onConnected?.();

      const handleClose = () => {
        if (connectionRef.current === connection) {
          connectionRef.current = null;
          setIsConnected(false);
          optionsRef.current.onDisconnected?.();
        }
      };
      // Receiver→sender status channel: parse + validate inbound JSON and
      // dispatch to onStatus. Malformed / unknown payloads are dropped silently
      // (forward-compatible).
      const handleMessage = (event: Event) => {
        try {
          const data = (event as MessageEvent).data as string;
          const raw = JSON.parse(data);
          const status = validatePresentationStatus(raw);
          if (!status) return;
          optionsRef.current.onStatus?.(status);
        } catch {
          // Ignore malformed JSON / non-message events.
        }
      };
      connection.addEventListener("close", handleClose);
      connection.addEventListener("terminate", handleClose);
      connection.addEventListener("message", handleMessage);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "Presentation start failed";
      optionsRef.current.onStartError?.(msg);
    }
  }, [presentationUrl]);

  const send = useCallback((command: PresentationCommand) => {
    const conn = connectionRef.current;
    if (!conn) return; // no-op when no connected transport
    const validated = validatePresentationCommand(command);
    if (!validated) return;
    try {
      conn.send(JSON.stringify(validated));
    } catch {
      /* best-effort: never throw from a send path */
    }
  }, []);

  // Close the connection on unmount.
  useEffect(() => {
    return () => {
      const conn = connectionRef.current;
      connectionRef.current = null;
      try {
        conn?.close();
      } catch {
        /* best-effort */
      }
    };
  }, []);

  return { isSupported, isConnected, start, send };
}
