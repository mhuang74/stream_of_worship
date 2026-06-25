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

    const handleTerminate = () => {
      optionsRef.current.onDisconnected?.();
    };

    const connectToConnection = (connection: PresentationConnection) => {
      connection.addEventListener("message", handleMessage);
      connection.addEventListener("close", handleTerminate);
      connection.addEventListener("terminate", handleTerminate);
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
          conn.removeEventListener("message", handleMessage);
          conn.removeEventListener("close", handleTerminate);
          conn.removeEventListener("terminate", handleTerminate);
        } catch {
          /* best-effort cleanup */
        }
      }
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
    try {
      const request = new PresentationRequest([presentationUrl]);
      const connection = await request.start();
      connectionRef.current = connection;
      setIsConnected(true);
      optionsRef.current.onConnected?.();

      const handleClose = () => {
        connectionRef.current = null;
        setIsConnected(false);
        optionsRef.current.onDisconnected?.();
      };
      connection.addEventListener("close", handleClose);
      connection.addEventListener("terminate", handleClose);
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
