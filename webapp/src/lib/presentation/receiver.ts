// Receiver side of the W3C Presentation API.
// Connects to the presentation receiver and applies commands to a video element.

import type { PresentationCommand } from "./controller";
export type { PresentationCommand };

export interface ReceiverCallbacks {
  onCommand?: (command: PresentationCommand) => void;
  onConnected?: () => void;
  onDisconnected?: () => void;
}

/**
 * Applies a PresentationCommand directly to a video element.
 * songTitle commands are intentionally not applied here (display handled by UI layer).
 */
export function applyCommandToVideo(
  video: HTMLVideoElement,
  command: PresentationCommand
): void {
  switch (command.type) {
    case "play":
      video.play().catch(() => {});
      break;
    case "pause":
      video.pause();
      break;
    case "seek":
      if (command.positionSeconds !== undefined) {
        video.currentTime = command.positionSeconds;
      }
      break;
    case "volume":
      if (command.level !== undefined) {
        video.volume = Math.max(0, Math.min(1, command.level));
        video.muted = command.level === 0;
      }
      break;
    // "songTitle" is display-only; not applied to the video element
  }
}

/**
 * Connects to the Presentation API receiver, wires up message/disconnect callbacks,
 * and handles both existing and incoming connections.
 *
 * Resolves when the connectionList is obtained (or silently resolves if the
 * Presentation receiver API is unavailable).
 */
export async function connectReceiver(callbacks: ReceiverCallbacks): Promise<void> {
  if (typeof navigator === "undefined") return;

  // @ts-expect-error - Presentation API may not be in TypeScript lib types
  const receiver = navigator.presentation?.receiver;
  if (!receiver) return;

  const handleMessage = (event: MessageEvent) => {
    try {
      const command = JSON.parse(event.data as string) as PresentationCommand;
      callbacks.onCommand?.(command);
    } catch {
      // Silently ignore malformed messages
    }
  };

  const handleEnd = () => {
    callbacks.onDisconnected?.();
  };

  const attachConnection = (connection: EventTarget) => {
    connection.addEventListener("message", handleMessage as EventListener);
    connection.addEventListener("close", handleEnd);
    connection.addEventListener("terminate", handleEnd);
    callbacks.onConnected?.();
  };

  try {
    const connectionList = await receiver.connectionList;

    (connectionList.connections as EventTarget[]).forEach(attachConnection);

    connectionList.addEventListener("connectionavailable", (event: Event) => {
      // @ts-expect-error - connection property may not be typed
      attachConnection(event.connection as EventTarget);
    });
  } catch {
    // Receiver API not available or failed to obtain connectionList
  }
}
