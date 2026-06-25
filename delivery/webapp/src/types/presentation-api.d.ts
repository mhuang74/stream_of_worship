// Ambient W3C Presentation API surface used by usePresentation.ts / PrePlayCard.tsx.
// Version-pinned to the narrow shape required by the receiver/sender hooks:
// navigator.presentation, PresentationRequest, PresentationConnection,
// PresentationConnectionList (receiver.connectionList), and the
// connectionavailable event's `connection` field.
//
// https://www.w3.org/TR/presentation-api/

// ── App-level shared wire contract ──────────────────────────────────────────
// Discriminated unions over the JSON messages exchanged between the sender
// (controller page) and receiver (projection page). Kept here so both sides
// agree on the exact variants.

export type PresentationCommand =
  | { type: "play" }
  | { type: "pause" }
  | { type: "seek"; positionSeconds: number }
  | { type: "volume"; level: number }
  | { type: "mute"; muted: boolean }
  | { type: "songTitle"; title: string };

export type PresentationStatus =
  | { type: "ready" }
  | { type: "disconnected" }
  | { type: "error"; message: string };

// ── Ambient W3C Presentation API global surface ────────────────────────────
// Declared inside `declare global` because this `.d.ts` is a module (it
// exports the wire contract above). The following names therefore augment
// the global lib.dom.d.ts scope rather than re-declaring as module types.

declare global {
  interface Navigator {
    readonly presentation?: Presentation;
  }

  interface Presentation {
    readonly receiver?: PresentationReceiver;
    /** Sender-side default presentation request used when not starting explicitly. */
    defaultRequest?: PresentationRequest;
  }

  interface PresentationReceiver {
    /**
     * Resolves with the connection list once at least one controlling page is
     * connected to this receiving page. Rejects if the receiver could not be
     * established (e.g. not a receiving context).
     */
    readonly connectionList: Promise<PresentationConnectionList>;
  }

  /** A live connection between a controlling page and a presenting page. */
  interface PresentationConnection extends EventTarget {
    readonly id: string;
    readonly url: string;
    readonly state: "connecting" | "connected" | "closed" | "terminated";
    send(data: string): void;
    send(data: ArrayBuffer): void;
    send(data: ArrayBufferView): void;
    close(): void;
    terminate(): void;
    addEventListener(
      type: "message",
      listener: (event: MessageEvent & { data: string | ArrayBuffer | ArrayBufferView }) => void,
      options?: AddEventListenerOptions,
    ): void;
    addEventListener(
      type: "connect" | "close" | "terminate",
      listener: (event: Event) => void,
      options?: AddEventListenerOptions,
    ): void;
    removeEventListener(
      type: "message" | "connect" | "close" | "terminate",
      listener: EventListenerOrEventListenerObject,
      options?: EventListenerOptions,
    ): void;
  }

  /** Read-only list of currently connected PresentationConnections. */
  interface PresentationConnectionList extends EventTarget {
    readonly connections: PresentationConnection[];
    addEventListener(
      type: "connectionavailable",
      listener: (event: PresentationConnectionAvailableEvent) => void,
      options?: AddEventListenerOptions,
    ): void;
    removeEventListener(
      type: "connectionavailable",
      listener: EventListenerOrEventListenerObject,
      options?: EventListenerOptions,
    ): void;
  }

  /** Fired on a PresentationConnectionList when a new connection arrives. */
  interface PresentationConnectionAvailableEvent extends Event {
    readonly connection: PresentationConnection;
  }

  /** Per-URL availability hint returned from PresentationRequest.getAvailability(). */
  interface PresentationAvailability extends EventTarget {
    readonly value: boolean;
    addEventListener(
      type: "change",
      listener: (event: Event) => void,
      options?: AddEventListenerOptions,
    ): void;
    removeEventListener(
      type: "change",
      listener: EventListenerOrEventListenerObject,
      options?: EventListenerOptions,
    ): void;
  }

  /**
   * Sender-side request to start (or resume) a presentation at one of the
   * provided URLs. Globally constructible: `new PresentationRequest([url])`.
   */
  class PresentationRequest extends EventTarget {
    constructor(urls: string | string[]);
    start(): Promise<PresentationConnection>;
    reconnect(id: string): Promise<PresentationConnection>;
    getAvailability(): Promise<PresentationAvailability>;
    addEventListener(
      type: "connectionavailable",
      listener: (event: PresentationConnectionAvailableEvent) => void,
      options?: AddEventListenerOptions,
    ): void;
    removeEventListener(
      type: "connectionavailable",
      listener: EventListenerOrEventListenerObject,
      options?: EventListenerOptions,
    ): void;
  }
}

// Forces this `.d.ts` to be treated as a module so the ambient `declare global`
// block above augments the global scope rather than re-shadowing it.
export {};
