import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { usePresentationReceiver } from "@/hooks/usePresentation";

describe("usePresentationReceiver", () => {
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
  let capturedConnectionAvailableHandler: ((event: { connection: typeof mockConnection }) => void) | null;

  beforeEach(() => {
    vi.clearAllMocks();

    capturedMessageHandler = null;
    capturedCloseHandler = null;
    capturedTerminateHandler = null;
    capturedConnectionAvailableHandler = null;

    mockConnection = {
      addEventListener: vi.fn((event: string, handler: unknown) => {
        if (event === "message") capturedMessageHandler = handler as (event: MessageEvent) => void;
        if (event === "close") capturedCloseHandler = handler as () => void;
        if (event === "terminate") capturedTerminateHandler = handler as () => void;
      }),
      removeEventListener: vi.fn(),
    };

    mockConnectionList = {
      connections: [],
      addEventListener: vi.fn((event: string, handler: unknown) => {
        if (event === "connectionavailable") {
          capturedConnectionAvailableHandler = handler as (event: { connection: typeof mockConnection }) => void;
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

  describe("when Presentation API is not available", () => {
    it("does not throw when presentation is undefined", () => {
      Object.defineProperty(navigator, "presentation", {
        value: undefined,
        writable: true,
        configurable: true,
      });

      expect(() => {
        renderHook(() => usePresentationReceiver({}));
      }).not.toThrow();
    });

    it("does not throw when receiver is null", () => {
      Object.defineProperty(navigator, "presentation", {
        value: { receiver: null },
        writable: true,
        configurable: true,
      });

      expect(() => {
        renderHook(() => usePresentationReceiver({}));
      }).not.toThrow();
    });
  });

  describe("when Presentation API is available", () => {
    it("registers connectionavailable listener on connectionList", async () => {
      renderHook(() => usePresentationReceiver({}));

      // Flush the promise
      await act(async () => {
        await Promise.resolve();
      });

      expect(mockConnectionList.addEventListener).toHaveBeenCalledWith(
        "connectionavailable",
        expect.any(Function)
      );
    });

    it("handles existing connections in connectionList", async () => {
      mockConnectionList.connections = [mockConnection];

      const onConnected = vi.fn();
      renderHook(() => usePresentationReceiver({ onConnected }));

      await act(async () => {
        await Promise.resolve();
      });

      expect(onConnected).toHaveBeenCalled();
    });

    it("handles new connections via connectionavailable", async () => {
      const onConnected = vi.fn();
      renderHook(() => usePresentationReceiver({ onConnected }));

      await act(async () => {
        await Promise.resolve();
      });

      act(() => {
        capturedConnectionAvailableHandler?.({ connection: mockConnection });
      });

      expect(onConnected).toHaveBeenCalled();
    });
  });

  describe("message handling", () => {
    const sendMessage = async (command: object) => {
      const hook = renderHook(() =>
        usePresentationReceiver({
          onPlay,
          onPause,
          onSeek,
          onVolume,
          onSongTitle,
        })
      );

      await act(async () => {
        await Promise.resolve();
      });

      // Trigger new connection
      act(() => {
        capturedConnectionAvailableHandler?.({ connection: mockConnection });
      });

      // Send message
      act(() => {
        capturedMessageHandler?.({
          data: JSON.stringify(command),
        } as MessageEvent);
      });

      return hook;
    };

    const onPlay = vi.fn();
    const onPause = vi.fn();
    const onSeek = vi.fn();
    const onVolume = vi.fn();
    const onSongTitle = vi.fn();

    beforeEach(() => {
      vi.clearAllMocks();
    });

    it("calls onPlay for play message", async () => {
      await sendMessage({ type: "play" });
      expect(onPlay).toHaveBeenCalled();
    });

    it("calls onPause for pause message", async () => {
      await sendMessage({ type: "pause" });
      expect(onPause).toHaveBeenCalled();
    });

    it("calls onSeek with positionSeconds for seek message", async () => {
      await sendMessage({ type: "seek", positionSeconds: 42.5 });
      expect(onSeek).toHaveBeenCalledWith(42.5);
    });

    it("does not call onSeek when positionSeconds is missing", async () => {
      await sendMessage({ type: "seek" });
      expect(onSeek).not.toHaveBeenCalled();
    });

    it("calls onVolume with level for volume message", async () => {
      await sendMessage({ type: "volume", level: 0.7 });
      expect(onVolume).toHaveBeenCalledWith(0.7);
    });

    it("calls onVolume with 0 for mute", async () => {
      await sendMessage({ type: "volume", level: 0 });
      expect(onVolume).toHaveBeenCalledWith(0);
    });

    it("calls onVolume with 1.0 for max volume", async () => {
      await sendMessage({ type: "volume", level: 1.0 });
      expect(onVolume).toHaveBeenCalledWith(1.0);
    });

    it("does not call onVolume when level is missing", async () => {
      await sendMessage({ type: "volume" });
      expect(onVolume).not.toHaveBeenCalled();
    });

    it("calls onSongTitle with title for songTitle message", async () => {
      await sendMessage({ type: "songTitle", title: "Amazing Grace" });
      expect(onSongTitle).toHaveBeenCalledWith("Amazing Grace");
    });

    it("does not call onSongTitle when title is missing", async () => {
      await sendMessage({ type: "songTitle" });
      expect(onSongTitle).not.toHaveBeenCalled();
    });

    it("ignores malformed JSON messages gracefully", async () => {
      renderHook(() => usePresentationReceiver({ onPlay, onPause }));

      await act(async () => {
        await Promise.resolve();
      });

      act(() => {
        capturedConnectionAvailableHandler?.({ connection: mockConnection });
      });

      expect(() => {
        act(() => {
          capturedMessageHandler?.({
            data: "not valid json {{{",
          } as MessageEvent);
        });
      }).not.toThrow();

      expect(onPlay).not.toHaveBeenCalled();
    });
  });

  describe("disconnect handling", () => {
    it("calls onDisconnected on close event", async () => {
      const onDisconnected = vi.fn();
      renderHook(() => usePresentationReceiver({ onDisconnected }));

      await act(async () => {
        await Promise.resolve();
      });

      act(() => {
        capturedConnectionAvailableHandler?.({ connection: mockConnection });
      });

      act(() => {
        capturedCloseHandler?.();
      });

      expect(onDisconnected).toHaveBeenCalled();
    });

    it("calls onDisconnected on terminate event", async () => {
      const onDisconnected = vi.fn();
      renderHook(() => usePresentationReceiver({ onDisconnected }));

      await act(async () => {
        await Promise.resolve();
      });

      act(() => {
        capturedConnectionAvailableHandler?.({ connection: mockConnection });
      });

      act(() => {
        capturedTerminateHandler?.();
      });

      expect(onDisconnected).toHaveBeenCalled();
    });
  });

  describe("connectionList promise rejection", () => {
    it("handles rejected connectionList gracefully", () => {
      Object.defineProperty(navigator, "presentation", {
        value: {
          receiver: {
            connectionList: Promise.reject(new Error("Not available")),
          },
        },
        writable: true,
        configurable: true,
      });

      expect(() => {
        renderHook(() => usePresentationReceiver({}));
      }).not.toThrow();
    });
  });
});
