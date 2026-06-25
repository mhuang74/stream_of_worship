import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import {
  usePresentationReceiver,
  validatePresentationCommand,
  validatePresentationStatus,
} from "@/hooks/usePresentation";

describe("usePresentationReceiver", () => {
  let mockConnection: {
    addEventListener: ReturnType<typeof vi.fn>;
    removeEventListener: ReturnType<typeof vi.fn>;
    send: ReturnType<typeof vi.fn>;
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
      send: vi.fn(),
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
          onMute,
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
    const onMute = vi.fn();
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

    it("clamps volume level above 1 to 1", async () => {
      await sendMessage({ type: "volume", level: 5 });
      expect(onVolume).toHaveBeenCalledWith(1);
    });

    it("clamps volume level below 0 to 0", async () => {
      await sendMessage({ type: "volume", level: -2 });
      expect(onVolume).toHaveBeenCalledWith(0);
    });

    it("calls onMute with true for mute message", async () => {
      await sendMessage({ type: "mute", muted: true });
      expect(onMute).toHaveBeenCalledWith(true);
    });

    it("coerces non-boolean mute.muted via Boolean(...)", async () => {
      await sendMessage({ type: "mute", muted: "yes" });
      expect(onMute).toHaveBeenCalledWith(true);
      await sendMessage({ type: "mute", muted: 0 });
      expect(onMute).toHaveBeenCalledWith(false);
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

    it("ignores unknown command type", async () => {
      await sendMessage({ type: "totallyUnknown" });
      expect(onPlay).not.toHaveBeenCalled();
      expect(onSeek).not.toHaveBeenCalled();
    });

    it("ignores non-object payloads", async () => {
      await sendMessage("just a string");
      expect(onPlay).not.toHaveBeenCalled();
    });

    it("rejects seek with negative positionSeconds", async () => {
      await sendMessage({ type: "seek", positionSeconds: -5 });
      expect(onSeek).not.toHaveBeenCalled();
    });

    it("rejects seek with non-finite positionSeconds", async () => {
      await sendMessage({ type: "seek", positionSeconds: Number.POSITIVE_INFINITY });
      expect(onSeek).not.toHaveBeenCalled();
    });

    it("rejects songTitle with non-string title", async () => {
      await sendMessage({ type: "songTitle", title: 42 });
      expect(onSongTitle).not.toHaveBeenCalled();
    });
  });

  describe("sendStatus", () => {
    it("pushes status JSON over connection.send", async () => {
      const { result } = renderHook(() =>
        usePresentationReceiver({ onConnected: vi.fn() }),
      );

      await act(async () => {
        await Promise.resolve();
      });

      act(() => {
        capturedConnectionAvailableHandler?.({ connection: mockConnection });
      });

      act(() => {
        result.current.sendStatus({ type: "ready" });
      });

      expect(mockConnection.send).toHaveBeenCalledWith(
        JSON.stringify({ type: "ready" }),
      );
    });

    it("sendStatus pushes error status with message", async () => {
      const { result } = renderHook(() =>
        usePresentationReceiver({ onConnected: vi.fn() }),
      );

      await act(async () => {
        await Promise.resolve();
      });

      act(() => {
        capturedConnectionAvailableHandler?.({ connection: mockConnection });
      });

      act(() => {
        result.current.sendStatus({ type: "error", message: "boom" });
      });

      expect(mockConnection.send).toHaveBeenCalledWith(
        JSON.stringify({ type: "error", message: "boom" }),
      );
    });

    it("sendStatus is a no-op when no connection", async () => {
      const { result } = renderHook(() => usePresentationReceiver({}));

      await act(async () => {
        await Promise.resolve();
      });

      expect(() => {
        act(() => {
          result.current.sendStatus({ type: "ready" });
        });
      }).not.toThrow();

      expect(mockConnection.send).not.toHaveBeenCalled();
    });
  });

  describe("validatePresentationCommand", () => {
    const cases: Array<{ name: string; input: unknown; expected: object | null }> = [
      { name: "play", input: { type: "play" }, expected: { type: "play" } },
      { name: "pause", input: { type: "pause" }, expected: { type: "pause" } },
      {
        name: "seek",
        input: { type: "seek", positionSeconds: 3 },
        expected: { type: "seek", positionSeconds: 3 },
      },
      {
        name: "volume clamped high",
        input: { type: "volume", level: 9 },
        expected: { type: "volume", level: 1 },
      },
      {
        name: "volume clamped low",
        input: { type: "volume", level: -1 },
        expected: { type: "volume", level: 0 },
      },
      {
        name: "mute true",
        input: { type: "mute", muted: true },
        expected: { type: "mute", muted: true },
      },
      {
        name: "mute coerced from truthy string",
        input: { type: "mute", muted: "yes" },
        expected: { type: "mute", muted: true },
      },
      {
        name: "mute coerced from 0",
        input: { type: "mute", muted: 0 },
        expected: { type: "mute", muted: false },
      },
      {
        name: "songTitle",
        input: { type: "songTitle", title: "Grace" },
        expected: { type: "songTitle", title: "Grace" },
      },
      { name: "null", input: null, expected: null },
      { name: "string", input: "hello", expected: null },
      { name: "unknown type", input: { type: "bogus" }, expected: null },
      { name: "missing type", input: {}, expected: null },
      { name: "seek negative", input: { type: "seek", positionSeconds: -1 }, expected: null },
      {
        name: "seek non-finite",
        input: { type: "seek", positionSeconds: NaN },
        expected: null,
      },
      {
        name: "volume non-number",
        input: { type: "volume", level: "loud" },
        expected: null,
      },
      {
        name: "songTitle non-string",
        input: { type: "songTitle", title: 9 },
        expected: null,
      },
    ];

    for (const { name, input, expected } of cases) {
      it(`${name} → ${expected === null ? "null" : JSON.stringify(expected)}`, () => {
        const out = validatePresentationCommand(input);
        if (expected === null) {
          expect(out).toBeNull();
        } else {
          expect(out).toEqual(expected);
        }
      });
    }
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
      expect(mockConnection.removeEventListener).toHaveBeenCalledWith(
        "message",
        capturedMessageHandler,
      );
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

describe("validatePresentationStatus", () => {
  const cases: Array<{ name: string; input: unknown; expected: object | null }> = [
    { name: "ready", input: { type: "ready" }, expected: { type: "ready" } },
    { name: "disconnected", input: { type: "disconnected" }, expected: { type: "disconnected" } },
    {
      name: "error with message",
      input: { type: "error", message: "boom" },
      expected: { type: "error", message: "boom" },
    },
    { name: "null", input: null, expected: null },
    { name: "string", input: "hello", expected: null },
    { name: "unknown type", input: { type: "bogus" }, expected: null },
    { name: "missing type", input: {}, expected: null },
    { name: "error non-string message", input: { type: "error", message: 123 }, expected: null },
    { name: "error missing message", input: { type: "error" }, expected: null },
  ];

  for (const { name, input, expected } of cases) {
    it(`${name} → ${expected === null ? "null" : JSON.stringify(expected)}`, () => {
      const out = validatePresentationStatus(input);
      if (expected === null) {
        expect(out).toBeNull();
      } else {
        expect(out).toEqual(expected);
      }
    });
  }
});
