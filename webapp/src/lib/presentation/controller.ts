// Controller side of the W3C Presentation API.
// Manages session initiation, availability detection, and command dispatch.

export interface PresentationCommand {
  type: "play" | "pause" | "seek" | "volume" | "songTitle";
  positionSeconds?: number;
  level?: number;
  title?: string;
}

type Handler = () => void;

export class PresentationController {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  private connection: any = null;
  private connectHandlers: Handler[] = [];
  private disconnectHandlers: Handler[] = [];

  /** Returns true when the Presentation API is available in this browser/context. */
  static isSupported(): boolean {
    return typeof window !== "undefined" && "PresentationRequest" in window;
  }

  /**
   * Checks whether a Cast / second-screen receiver is available for the given URL.
   * Returns false when the Presentation API is absent (e.g. iOS Safari mirror mode).
   */
  async checkAvailability(url: string): Promise<boolean> {
    if (!PresentationController.isSupported()) return false;
    try {
      // @ts-expect-error - PresentationRequest may not be in TypeScript lib types
      const request = new PresentationRequest([url]);
      // @ts-expect-error - getAvailability may not be in lib types
      const availability = await request.getAvailability();
      return availability.value as boolean;
    } catch {
      return false;
    }
  }

  /**
   * Opens the Cast / second-screen picker and starts a presentation session.
   * Fires onConnect handlers once the connection is established.
   */
  async start(url: string): Promise<void> {
    if (!PresentationController.isSupported()) {
      throw new Error("Presentation API not supported on this device");
    }

    // @ts-expect-error - PresentationRequest may not be in TypeScript lib types
    const request = new PresentationRequest([url]);
    // @ts-expect-error - start may not be in lib types
    const connection = await request.start();
    this.connection = connection;

    connection.addEventListener("connect", () => {
      this.connectHandlers.forEach((h) => h());
    });

    const handleEnd = () => {
      this.connection = null;
      this.disconnectHandlers.forEach((h) => h());
    };

    connection.addEventListener("close", handleEnd);
    connection.addEventListener("terminate", handleEnd);

    // The connection may already be in "connected" state immediately after start()
    if (connection.state === "connected") {
      this.connectHandlers.forEach((h) => h());
    }
  }

  /** Sends a raw command to the connected presentation receiver. No-op if not connected. */
  send(command: PresentationCommand): void {
    if (!this.connection) return;
    this.connection.send(JSON.stringify(command));
  }

  sendPlay(): void {
    this.send({ type: "play" });
  }

  sendPause(): void {
    this.send({ type: "pause" });
  }

  sendSeek(positionSeconds: number): void {
    this.send({ type: "seek", positionSeconds });
  }

  sendVolume(level: number): void {
    this.send({ type: "volume", level });
  }

  sendSongTitle(title: string): void {
    this.send({ type: "songTitle", title });
  }

  /** Terminates the active presentation session. No-op if not connected. */
  close(): void {
    if (!this.connection) return;
    this.connection.terminate();
    this.connection = null;
  }

  /** Registers a handler that fires when a session is established. Returns an unsubscribe function. */
  onConnect(handler: Handler): () => void {
    this.connectHandlers.push(handler);
    return () => {
      this.connectHandlers = this.connectHandlers.filter((h) => h !== handler);
    };
  }

  /** Registers a handler that fires when the session ends. Returns an unsubscribe function. */
  onDisconnect(handler: Handler): () => void {
    this.disconnectHandlers.push(handler);
    return () => {
      this.disconnectHandlers = this.disconnectHandlers.filter((h) => h !== handler);
    };
  }

  get isConnected(): boolean {
    return this.connection !== null;
  }
}
