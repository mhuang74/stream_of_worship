import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { applyCommandToVideo, connectReceiver } from "@/lib/presentation/receiver";
import type { PresentationCommand } from "@/lib/presentation/receiver";

// Minimal HTMLVideoElement mock factory
function makeVideoMock() {
  return {
    play: vi.fn().mockResolvedValue(undefined),
    pause: vi.fn(),
    currentTime: 0,
    volume: 1,
    muted: false,
  };
}

describe("applyCommandToVideo", () => {
  let video: ReturnType<typeof makeVideoMock>;

  beforeEach(() => {
    video = makeVideoMock();
  });

  it("calls play() for play command", () => {
    applyCommandToVideo(video as unknown as HTMLVideoElement, { type: "play" });
    expect(video.play).toHaveBeenCalled();
  });

  it("does not throw when play() rejects", async () => {
    video.play.mockRejectedValue(new Error("Not allowed"));
    expect(() =>
      applyCommandToVideo(video as unknown as HTMLVideoElement, { type: "play" })
    ).not.toThrow();
  });

  it("calls pause() for pause command", () => {
    applyCommandToVideo(video as unknown as HTMLVideoElement, { type: "pause" });
    expect(video.pause).toHaveBeenCalled();
  });

  it("sets currentTime for seek command", () => {
    applyCommandToVideo(video as unknown as HTMLVideoElement, {
      type: "seek",
      positionSeconds: 30,
    });
    expect(video.currentTime).toBe(30);
  });

  it("does not set currentTime when positionSeconds is missing in seek command", () => {
    applyCommandToVideo(video as unknown as HTMLVideoElement, { type: "seek" });
    expect(video.currentTime).toBe(0);
  });

  it("sets volume and unmutes for volume command > 0", () => {
    applyCommandToVideo(video as unknown as HTMLVideoElement, {
      type: "volume",
      level: 0.7,
    });
    expect(video.volume).toBe(0.7);
    expect(video.muted).toBe(false);
  });

  it("sets volume to 0 and mutes for volume command level 0", () => {
    applyCommandToVideo(video as unknown as HTMLVideoElement, {
      type: "volume",
      level: 0,
    });
    expect(video.volume).toBe(0);
    expect(video.muted).toBe(true);
  });

  it("clamps volume to 0–1 range (above 1)", () => {
    applyCommandToVideo(video as unknown as HTMLVideoElement, {
      type: "volume",
      level: 1.5,
    });
    expect(video.volume).toBe(1);
  });

  it("clamps volume to 0–1 range (below 0)", () => {
    applyCommandToVideo(video as unknown as HTMLVideoElement, {
      type: "volume",
      level: -0.5,
    });
    expect(video.volume).toBe(0);
    expect(video.muted).toBe(false);
  });

  it("does not set volume when level is missing in volume command", () => {
    applyCommandToVideo(video as unknown as HTMLVideoElement, { type: "volume" });
    expect(video.volume).toBe(1); // unchanged
  });

  it("does not affect video for songTitle command", () => {
    applyCommandToVideo(video as unknown as HTMLVideoElement, {
      type: "songTitle",
      title: "Amazing Grace",
    });
    expect(video.play).not.toHaveBeenCalled();
    expect(video.pause).not.toHaveBeenCalled();
    expect(video.currentTime).toBe(0);
    expect(video.volume).toBe(1);
  });
});

describe("connectReceiver", () => {
  let mockConnection: {
    addEventListener: ReturnType<typeof vi.fn>;
    removeEventListener: ReturnType<typeof vi.fn>;
  };

  let mockConnectionList: {
    connections: typeof mockConnection[];
    addEventListener: ReturnType<typeof vi.fn>;
  };

  let capturedMessageHandler: ((event: MessageEvent) => void) | null;
  let capturedCloseHandler: (() => void) | null;
  let capturedTerminateHandler: (() => void) | null;
  let capturedConnectionAvailableHandler:
    | ((event: { connection: typeof mockConnection }) => void)
    | null;

  beforeEach(() => {
    vi.clearAllMocks();
    capturedMessageHandler = null;
    capturedCloseHandler = null;
    capturedTerminateHandler = null;
    capturedConnectionAvailableHandler = null;

    mockConnection = {
      addEventListener: vi.fn((event: string, handler: unknown) => {
        if (event === "message")
          capturedMessageHandler = handler as (event: MessageEvent) => void;
        if (event === "close") capturedCloseHandler = handler as () => void;
        if (event === "terminate") capturedTerminateHandler = handler as () => void;
      }),
      removeEventListener: vi.fn(),
    };

    mockConnectionList = {
      connections: [],
      addEventListener: vi.fn((event: string, handler: unknown) => {
        if (event === "connectionavailable") {
          capturedConnectionAvailableHandler = handler as (event: {
            connection: typeof mockConnection;
          }) => void;
        }
      }),
    };

    Object.defineProperty(navigator, "presentation", {
      value: {
        receiver: {
          connectionList: Promise.resolve(mockConnectionList),
        },
      },
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    Object.defineProperty(navigator, "presentation", {
      value: undefined,
      writable: true,
      configurable: true,
    });
    vi.restoreAllMocks();
  });

  it("resolves without error when presentation is undefined", async () => {
    Object.defineProperty(navigator, "presentation", {
      value: undefined,
      writable: true,
      configurable: true,
    });
    await expect(connectReceiver({})).resolves.not.toThrow();
  });

  it("resolves without error when receiver is null", async () => {
    Object.defineProperty(navigator, "presentation", {
      value: { receiver: null },
      writable: true,
      configurable: true,
    });
    await expect(connectReceiver({})).resolves.not.toThrow();
  });

  it("handles rejected connectionList gracefully", async () => {
    Object.defineProperty(navigator, "presentation", {
      value: {
        receiver: {
          connectionList: Promise.reject(new Error("Unavailable")),
        },
      },
      writable: true,
      configurable: true,
    });
    await expect(connectReceiver({})).resolves.not.toThrow();
  });

  it("registers connectionavailable listener on the connectionList", async () => {
    await connectReceiver({});
    expect(mockConnectionList.addEventListener).toHaveBeenCalledWith(
      "connectionavailable",
      expect.any(Function)
    );
  });

  it("fires onConnected for existing connections", async () => {
    mockConnectionList.connections = [mockConnection];
    const onConnected = vi.fn();
    await connectReceiver({ onConnected });
    expect(onConnected).toHaveBeenCalled();
  });

  it("fires onConnected for new connections via connectionavailable", async () => {
    const onConnected = vi.fn();
    await connectReceiver({ onConnected });

    capturedConnectionAvailableHandler?.({ connection: mockConnection });

    expect(onConnected).toHaveBeenCalled();
  });

  it("calls onCommand with parsed command on message", async () => {
    const onCommand = vi.fn();
    await connectReceiver({ onCommand });

    capturedConnectionAvailableHandler?.({ connection: mockConnection });

    const command: PresentationCommand = { type: "play" };
    capturedMessageHandler?.({ data: JSON.stringify(command) } as MessageEvent);

    expect(onCommand).toHaveBeenCalledWith(command);
  });

  it("calls onCommand with seek command including positionSeconds", async () => {
    const onCommand = vi.fn();
    await connectReceiver({ onCommand });

    capturedConnectionAvailableHandler?.({ connection: mockConnection });

    const command: PresentationCommand = { type: "seek", positionSeconds: 90 };
    capturedMessageHandler?.({ data: JSON.stringify(command) } as MessageEvent);

    expect(onCommand).toHaveBeenCalledWith(command);
  });

  it("calls onCommand with volume command including level", async () => {
    const onCommand = vi.fn();
    await connectReceiver({ onCommand });

    capturedConnectionAvailableHandler?.({ connection: mockConnection });

    const command: PresentationCommand = { type: "volume", level: 0.5 };
    capturedMessageHandler?.({ data: JSON.stringify(command) } as MessageEvent);

    expect(onCommand).toHaveBeenCalledWith(command);
  });

  it("calls onCommand with songTitle command including title", async () => {
    const onCommand = vi.fn();
    await connectReceiver({ onCommand });

    capturedConnectionAvailableHandler?.({ connection: mockConnection });

    const command: PresentationCommand = {
      type: "songTitle",
      title: "How Great Is Our God",
    };
    capturedMessageHandler?.({ data: JSON.stringify(command) } as MessageEvent);

    expect(onCommand).toHaveBeenCalledWith(command);
  });

  it("ignores malformed JSON messages", async () => {
    const onCommand = vi.fn();
    await connectReceiver({ onCommand });

    capturedConnectionAvailableHandler?.({ connection: mockConnection });

    expect(() => {
      capturedMessageHandler?.({ data: "not valid json {{" } as MessageEvent);
    }).not.toThrow();

    expect(onCommand).not.toHaveBeenCalled();
  });

  it("fires onDisconnected on close event", async () => {
    const onDisconnected = vi.fn();
    await connectReceiver({ onDisconnected });

    capturedConnectionAvailableHandler?.({ connection: mockConnection });
    capturedCloseHandler?.();

    expect(onDisconnected).toHaveBeenCalled();
  });

  it("fires onDisconnected on terminate event", async () => {
    const onDisconnected = vi.fn();
    await connectReceiver({ onDisconnected });

    capturedConnectionAvailableHandler?.({ connection: mockConnection });
    capturedTerminateHandler?.();

    expect(onDisconnected).toHaveBeenCalled();
  });
});

describe("applyCommandToVideo + connectReceiver integration", () => {
  let video: ReturnType<typeof makeVideoMock>;
  let mockConnection: {
    addEventListener: ReturnType<typeof vi.fn>;
    removeEventListener: ReturnType<typeof vi.fn>;
  };
  let mockConnectionList: {
    connections: typeof mockConnection[];
    addEventListener: ReturnType<typeof vi.fn>;
  };
  let capturedMessageHandler: ((event: MessageEvent) => void) | null;
  let capturedConnectionAvailableHandler:
    | ((event: { connection: typeof mockConnection }) => void)
    | null;

  beforeEach(() => {
    vi.clearAllMocks();
    video = makeVideoMock();
    capturedMessageHandler = null;
    capturedConnectionAvailableHandler = null;

    mockConnection = {
      addEventListener: vi.fn((event: string, handler: unknown) => {
        if (event === "message")
          capturedMessageHandler = handler as (event: MessageEvent) => void;
      }),
      removeEventListener: vi.fn(),
    };

    mockConnectionList = {
      connections: [],
      addEventListener: vi.fn((event: string, handler: unknown) => {
        if (event === "connectionavailable") {
          capturedConnectionAvailableHandler = handler as (event: {
            connection: typeof mockConnection;
          }) => void;
        }
      }),
    };

    Object.defineProperty(navigator, "presentation", {
      value: {
        receiver: {
          connectionList: Promise.resolve(mockConnectionList),
        },
      },
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    Object.defineProperty(navigator, "presentation", {
      value: undefined,
      writable: true,
      configurable: true,
    });
  });

  it("applies play command to video when received via connectReceiver", async () => {
    const videoEl = video as unknown as HTMLVideoElement;

    await connectReceiver({
      onCommand: (cmd) => applyCommandToVideo(videoEl, cmd),
    });

    capturedConnectionAvailableHandler?.({ connection: mockConnection });
    capturedMessageHandler?.({
      data: JSON.stringify({ type: "play" }),
    } as MessageEvent);

    expect(video.play).toHaveBeenCalled();
  });

  it("applies seek command to video when received via connectReceiver", async () => {
    const videoEl = video as unknown as HTMLVideoElement;

    await connectReceiver({
      onCommand: (cmd) => applyCommandToVideo(videoEl, cmd),
    });

    capturedConnectionAvailableHandler?.({ connection: mockConnection });
    capturedMessageHandler?.({
      data: JSON.stringify({ type: "seek", positionSeconds: 55 }),
    } as MessageEvent);

    expect(video.currentTime).toBe(55);
  });
});
