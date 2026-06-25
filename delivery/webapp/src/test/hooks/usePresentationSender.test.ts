import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { usePresentationSender } from "@/hooks/usePresentation";
import type { PresentationCommand } from "@/types/presentation-api";

// Captured PresentationRequest start() result + listeners.
interface MockConnection {
  id: string;
  url: string;
  state: string;
  send: ReturnType<typeof vi.fn>;
  close: ReturnType<typeof vi.fn>;
  terminate: ReturnType<typeof vi.fn>;
  addEventListener: ReturnType<typeof vi.fn>;
  removeEventListener: ReturnType<typeof vi.fn>;
  listeners: Map<string, Set<(event: unknown) => void>>;
}

function createMockConnection(url: string): MockConnection {
  const listeners = new Map<string, Set<(event: unknown) => void>>();
  return {
    id: `conn-${Math.random().toString(36).slice(2)}`,
    url,
    state: "connected",
    send: vi.fn(),
    close: vi.fn(),
    terminate: vi.fn(),
    addEventListener: vi.fn((type: string, handler: (event: unknown) => void) => {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type)!.add(handler);
    }),
    removeEventListener: vi.fn((type: string, handler: (event: unknown) => void) => {
      listeners.get(type)?.delete(handler);
    }),
    listeners,
  };
}

describe("usePresentationSender", () => {
  let originalPresentationRequest: typeof PresentationRequest | undefined;

  beforeEach(() => {
    vi.clearAllMocks();
    originalPresentationRequest = (window as unknown as { PresentationRequest?: typeof PresentationRequest }).PresentationRequest;
  });

  afterEach(() => {
    if (originalPresentationRequest) {
      (window as unknown as { PresentationRequest: typeof PresentationRequest }).PresentationRequest =
        originalPresentationRequest;
    } else {
      // @ts-expect-error delete is the cleanest restore path
      delete (window as unknown as { PresentationRequest?: typeof PresentationRequest }).PresentationRequest;
    }
    vi.restoreAllMocks();
  });

  function setPresentationRequest(
    behaviour:
      | { kind: "resolve"; connection?: MockConnection }
      | { kind: "reject"; error: Error } = { kind: "resolve" },
  ): MockConnection {
    const conn = behaviour.connection ?? createMockConnection("/projection");
    class FakePresentationRequest {
      urls: string[];
      constructor(urls: string | string[]) {
        this.urls = Array.isArray(urls) ? urls : [urls];
      }
      start() {
        if (behaviour.kind === "reject") return Promise.reject(behaviour.error);
        return Promise.resolve(conn);
      }
      getAvailability() {
        return Promise.resolve({ value: true });
      }
    }
    (window as unknown as { PresentationRequest: typeof PresentationRequest }).PresentationRequest =
      FakePresentationRequest as unknown as typeof PresentationRequest;
    return conn;
  }

  it("reports isSupported=false when PresentationRequest is undefined", () => {
    // @ts-expect-error delete to simulate unsupported browser
    delete (window as unknown as { PresentationRequest?: typeof PresentationRequest }).PresentationRequest;
    const { result } = renderHook(() =>
      usePresentationSender({ presentationUrl: "/songsets/1/play/projection" }),
    );
    expect(result.current.isSupported).toBe(false);
  });

  it("reports isSupported=true when PresentationRequest exists", () => {
    setPresentationRequest();
    const { result } = renderHook(() =>
      usePresentationSender({ presentationUrl: "/songsets/1/play/projection" }),
    );
    expect(result.current.isSupported).toBe(true);
  });

  it("start() resolves and connects, firing onConnected", async () => {
    const conn = setPresentationRequest();
    const onConnected = vi.fn();
    const { result } = renderHook(() =>
      usePresentationSender({
        presentationUrl: "/songsets/1/play/projection",
        onConnected,
      }),
    );

    await act(async () => {
      await result.current.start();
    });

    expect(result.current.isConnected).toBe(true);
    expect(onConnected).toHaveBeenCalledTimes(1);
    // close + terminate listeners attached.
    expect(conn.addEventListener).toHaveBeenCalledWith("close", expect.any(Function));
    expect(conn.addEventListener).toHaveBeenCalledWith("terminate", expect.any(Function));
  });

  it("start() rejection fires onStartError and stays disconnected", async () => {
    setPresentationRequest({ kind: "reject", error: new Error("user cancelled") });
    const onStartError = vi.fn();
    const { result } = renderHook(() =>
      usePresentationSender({
        presentationUrl: "/songsets/1/play/projection",
        onStartError,
      }),
    );

    await act(async () => {
      await result.current.start();
    });

    expect(result.current.isConnected).toBe(false);
    expect(onStartError).toHaveBeenCalledWith("user cancelled");
  });

  it("send() issues JSON over connection.send", async () => {
    const conn = setPresentationRequest();
    const { result } = renderHook(() =>
      usePresentationSender({ presentationUrl: "/songsets/1/play/projection" }),
    );

    await act(async () => {
      await result.current.start();
    });

    const commands: PresentationCommand[] = [
      { type: "play" },
      { type: "pause" },
      { type: "seek", positionSeconds: 12.5 },
      { type: "volume", level: 0.4 },
      { type: "mute", muted: true },
    ];
    act(() => {
      for (const c of commands) result.current.send(c);
    });

    expect(conn.send).toHaveBeenCalledTimes(5);
    expect(conn.send).toHaveBeenNthCalledWith(1, JSON.stringify({ type: "play" }));
    expect(conn.send).toHaveBeenNthCalledWith(3, JSON.stringify({ type: "seek", positionSeconds: 12.5 }));
    expect(conn.send).toHaveBeenNthCalledWith(5, JSON.stringify({ type: "mute", muted: true }));
  });

  it("send() is a no-op when no connected transport", () => {
    setPresentationRequest();
    const { result } = renderHook(() =>
      usePresentationSender({ presentationUrl: "/songsets/1/play/projection" }),
    );
    // Never started → no connection.
    act(() => {
      result.current.send({ type: "play" });
    });
    // Nothing to assert beyond "did not throw"; verify send() returned undefined.
    expect(result.current.isConnected).toBe(false);
  });

  it("send() clamps volume.level to [0,1] on the wire", async () => {
    const conn = setPresentationRequest();
    const { result } = renderHook(() =>
      usePresentationSender({ presentationUrl: "/songsets/1/play/projection" }),
    );

    await act(async () => {
      await result.current.start();
    });

    act(() => {
      result.current.send({ type: "volume", level: 5 });
    });
    act(() => {
      result.current.send({ type: "volume", level: -2 });
    });

    expect(conn.send).toHaveBeenCalledWith(JSON.stringify({ type: "volume", level: 1 }));
    expect(conn.send).toHaveBeenCalledWith(JSON.stringify({ type: "volume", level: 0 }));
  });

  it("send() coerces mute.muted via Boolean(...)", async () => {
    const conn = setPresentationRequest();
    const { result } = renderHook(() =>
      usePresentationSender({ presentationUrl: "/songsets/1/play/projection" }),
    );

    await act(async () => {
      await result.current.start();
    });

    // Mutating the raw object via send — the hook validates before sending.
    act(() => {
      result.current.send({ type: "mute", muted: "yes" as unknown as boolean });
    });
    act(() => {
      result.current.send({ type: "mute", muted: 0 as unknown as boolean });
    });

    expect(conn.send).toHaveBeenCalledWith(JSON.stringify({ type: "mute", muted: true }));
    expect(conn.send).toHaveBeenCalledWith(JSON.stringify({ type: "mute", muted: false }));
  });

  it("disconnect (close event) fires onDisconnected and clears isConnected", async () => {
    const conn = setPresentationRequest();
    const onDisconnected = vi.fn();
    const { result } = renderHook(() =>
      usePresentationSender({
        presentationUrl: "/songsets/1/play/projection",
        onDisconnected,
      }),
    );

    await act(async () => {
      await result.current.start();
    });

    const closeHandlers = conn.listeners.get("close");
    expect(closeHandlers?.size).toBeGreaterThan(0);
    act(() => {
      for (const h of closeHandlers!) h({});
    });

    expect(result.current.isConnected).toBe(false);
    expect(onDisconnected).toHaveBeenCalledTimes(1);

    // After disconnect, send() is a no-op.
    act(() => {
      result.current.send({ type: "play" });
    });
    expect(conn.send).not.toHaveBeenCalled();
  });

  it("disconnect (terminate event) fires onDisconnected and clears isConnected", async () => {
    const conn = setPresentationRequest();
    const onDisconnected = vi.fn();
    const { result } = renderHook(() =>
      usePresentationSender({
        presentationUrl: "/songsets/1/play/projection",
        onDisconnected,
      }),
    );

    await act(async () => {
      await result.current.start();
    });

    const terminateHandlers = conn.listeners.get("terminate");
    expect(terminateHandlers?.size).toBeGreaterThan(0);
    act(() => {
      for (const h of terminateHandlers!) h({});
    });

    expect(result.current.isConnected).toBe(false);
    expect(onDisconnected).toHaveBeenCalledTimes(1);

    // After termination, send() is a no-op.
    act(() => {
      result.current.send({ type: "play" });
    });
    expect(conn.send).not.toHaveBeenCalled();
  });

  it("start() called twice closes the prior connection before opening a new one", async () => {
    const firstConn = createMockConnection("/projection");
    const secondConn = createMockConnection("/projection");
    let calls = 0;
    class FakePresentationRequest {
      urls: string[];
      constructor(urls: string | string[]) {
        this.urls = Array.isArray(urls) ? urls : [urls];
      }
      start() {
        calls += 1;
        return Promise.resolve(calls === 1 ? firstConn : secondConn);
      }
    }
    (window as unknown as { PresentationRequest: typeof PresentationRequest }).PresentationRequest =
      FakePresentationRequest as unknown as typeof PresentationRequest;

    const { result } = renderHook(() =>
      usePresentationSender({ presentationUrl: "/songsets/1/play/projection" }),
    );

    await act(async () => {
      await result.current.start();
    });
    await act(async () => {
      await result.current.start();
    });

    // The first connection must have been closed when the second start() opened
    // a new one (no leaked listeners / orphaned connection).
    expect(firstConn.close).toHaveBeenCalledTimes(1);
    expect(result.current.isConnected).toBe(true);
  });

  it("ignores stale close events from a replaced connection", async () => {
    const firstConn = createMockConnection("/projection");
    const secondConn = createMockConnection("/projection");
    let calls = 0;
    class FakePresentationRequest {
      urls: string[];
      constructor(urls: string | string[]) {
        this.urls = Array.isArray(urls) ? urls : [urls];
      }
      start() {
        calls += 1;
        return Promise.resolve(calls === 1 ? firstConn : secondConn);
      }
    }
    (window as unknown as { PresentationRequest: typeof PresentationRequest }).PresentationRequest =
      FakePresentationRequest as unknown as typeof PresentationRequest;

    const onDisconnected = vi.fn();
    const { result } = renderHook(() =>
      usePresentationSender({
        presentationUrl: "/songsets/1/play/projection",
        onDisconnected,
      }),
    );

    await act(async () => {
      await result.current.start();
    });
    await act(async () => {
      await result.current.start();
    });

    const firstCloseHandlers = firstConn.listeners.get("close");
    expect(firstCloseHandlers?.size).toBeGreaterThan(0);
    act(() => {
      for (const handler of firstCloseHandlers!) handler({});
    });

    expect(result.current.isConnected).toBe(true);
    expect(onDisconnected).not.toHaveBeenCalled();
  });

  it("unmount closes the current connection", async () => {
    const conn = setPresentationRequest();
    const { result, unmount } = renderHook(() =>
      usePresentationSender({ presentationUrl: "/songsets/1/play/projection" }),
    );

    await act(async () => {
      await result.current.start();
    });

    unmount();

    expect(conn.close).toHaveBeenCalledTimes(1);
  });

  // ── Receiver → sender status channel (onStatus) ─────────────────────────

  it("attaches a message listener and dispatches validated statuses to onStatus", async () => {
    const conn = setPresentationRequest();
    const onStatus = vi.fn();
    const { result } = renderHook(() =>
      usePresentationSender({
        presentationUrl: "/songsets/1/play/projection",
        onStatus,
      }),
    );

    await act(async () => {
      await result.current.start();
    });

    expect(conn.addEventListener).toHaveBeenCalledWith("message", expect.any(Function));

    const messageHandlers = conn.listeners.get("message");
    expect(messageHandlers?.size).toBeGreaterThan(0);

    act(() => {
      for (const h of messageHandlers!) {
        h({ data: JSON.stringify({ type: "ready" }) });
        h({ data: JSON.stringify({ type: "error", message: "boom" }) });
      }
    });

    expect(onStatus).toHaveBeenCalledWith({ type: "ready" });
    expect(onStatus).toHaveBeenCalledWith({ type: "error", message: "boom" });
  });

  it("ignores malformed / unknown status payloads on the message channel", async () => {
    const conn = setPresentationRequest();
    const onStatus = vi.fn();
    const { result } = renderHook(() =>
      usePresentationSender({
        presentationUrl: "/songsets/1/play/projection",
        onStatus,
      }),
    );

    await act(async () => {
      await result.current.start();
    });

    const messageHandlers = conn.listeners.get("message");
    act(() => {
      for (const h of messageHandlers!) {
        h({ data: "not-json" });
        h({ data: JSON.stringify({ type: "bogus" }) });
        h({ data: JSON.stringify({ type: "error", message: 123 }) });
      }
    });

    expect(onStatus).not.toHaveBeenCalled();
  });
});
