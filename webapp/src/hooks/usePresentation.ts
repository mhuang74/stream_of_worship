"use client";

import { useEffect, useLayoutEffect, useRef } from "react";

export interface PresentationCommand {
  type: "play" | "pause" | "seek" | "volume" | "songTitle";
  positionSeconds?: number;
  level?: number;
  title?: string;
}

export interface UsePresentationReceiverOptions {
  onPlay?: () => void;
  onPause?: () => void;
  onSeek?: (positionSeconds: number) => void;
  onVolume?: (level: number) => void;
  onSongTitle?: (title: string) => void;
  onConnected?: () => void;
  onDisconnected?: () => void;
}

export function usePresentationReceiver(options: UsePresentationReceiverOptions) {
  const optionsRef = useRef(options);
  useLayoutEffect(() => {
    optionsRef.current = options;
  });

  useEffect(() => {
    if (typeof navigator === "undefined") return;

    // @ts-expect-error - Presentation API may not be in TypeScript types
    const receiver = navigator.presentation?.receiver;
    if (!receiver) return;

    const handleMessage = (event: MessageEvent) => {
      try {
        const command = JSON.parse(event.data as string) as PresentationCommand;
        const opts = optionsRef.current;

        switch (command.type) {
          case "play":
            opts.onPlay?.();
            break;
          case "pause":
            opts.onPause?.();
            break;
          case "seek":
            if (command.positionSeconds !== undefined) {
              opts.onSeek?.(command.positionSeconds);
            }
            break;
          case "volume":
            if (command.level !== undefined) {
              opts.onVolume?.(command.level);
            }
            break;
          case "songTitle":
            if (command.title !== undefined) {
              opts.onSongTitle?.(command.title);
            }
            break;
        }
      } catch {
        // Ignore parse errors
      }
    };

    const handleTerminate = () => {
      optionsRef.current.onDisconnected?.();
    };

    const connectToConnection = (connection: EventTarget) => {
      connection.addEventListener("message", handleMessage as EventListener);
      connection.addEventListener("close", handleTerminate);
      connection.addEventListener("terminate", handleTerminate);
      optionsRef.current.onConnected?.();
    };

    receiver.connectionList
      // @ts-expect-error - connectionList is a promise-like
      .then((connectionList) => {
        (connectionList.connections as EventTarget[]).forEach(connectToConnection);

        // Listen for new connections
        connectionList.addEventListener("connectionavailable", (event: Event) => {
          // @ts-expect-error - connection property may not be typed
          connectToConnection(event.connection as EventTarget);
        });
      })
      .catch(() => {
        // Receiver API not available or connectionList failed
      });
  }, []);
}
