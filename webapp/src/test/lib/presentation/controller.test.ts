import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { PresentationController } from "@/lib/presentation/controller";
import type { PresentationCommand } from "@/lib/presentation/controller";

describe("PresentationController", () => {
  let mockConnection: {
    send: ReturnType<typeof vi.fn>;
    terminate: ReturnType<typeof vi.fn>;
    state: string;
    addEventListener: ReturnType<typeof vi.fn>;
    removeEventListener: ReturnType<typeof vi.fn>;
  };

  let capturedConnectHandler: (() => void) | null;
  let capturedCloseHandler: (() => void) | null;
  let capturedTerminateHandler: (() => void) | null;

  // Helper that installs a class mock for PresentationRequest that resolves start() with mockConnection
  function installMockRequest() {
    const conn = mockConnection;
    // @ts-expect-error - mocking browser API
    window.PresentationRequest = class {
      start() {
        return Promise.resolve(conn);
      }
    };
  }

  beforeEach(() => {
    vi.clearAllMocks();
    capturedConnectHandler = null;
    capturedCloseHandler = null;
    capturedTerminateHandler = null;

    mockConnection = {
      send: vi.fn(),
      terminate: vi.fn(),
      state: "connecting",
      addEventListener: vi.fn((event: string, handler: unknown) => {
        if (event === "connect") capturedConnectHandler = handler as () => void;
        if (event === "close") capturedCloseHandler = handler as () => void;
        if (event === "terminate") capturedTerminateHandler = handler as () => void;
      }),
      removeEventListener: vi.fn(),
    };
  });

  afterEach(() => {
    // @ts-expect-error - test cleanup
    delete window.PresentationRequest;
  });

  describe("isSupported", () => {
    it("returns false when PresentationRequest is not in window", () => {
      // @ts-expect-error - removing for test
      delete window.PresentationRequest;
      expect(PresentationController.isSupported()).toBe(false);
    });

    it("returns true when PresentationRequest is in window", () => {
      // @ts-expect-error - mocking for test
      window.PresentationRequest = class {};
      expect(PresentationController.isSupported()).toBe(true);
    });
  });

  describe("checkAvailability", () => {
    it("returns false when Presentation API is not supported", async () => {
      // @ts-expect-error - removing for test
      delete window.PresentationRequest;
      const controller = new PresentationController();
      const available = await controller.checkAvailability("/projection");
      expect(available).toBe(false);
    });

    it("returns true when Cast receiver is available", async () => {
      const mockAvailability = { value: true, addEventListener: vi.fn() };
      // @ts-expect-error - mocking PresentationRequest
      window.PresentationRequest = class {
        getAvailability() {
          return Promise.resolve(mockAvailability);
        }
      };

      const controller = new PresentationController();
      const available = await controller.checkAvailability("/projection");
      expect(available).toBe(true);
    });

    it("returns false when Cast receiver is not available", async () => {
      const mockAvailability = { value: false, addEventListener: vi.fn() };
      // @ts-expect-error - mocking PresentationRequest
      window.PresentationRequest = class {
        getAvailability() {
          return Promise.resolve(mockAvailability);
        }
      };

      const controller = new PresentationController();
      const available = await controller.checkAvailability("/projection");
      expect(available).toBe(false);
    });

    it("returns false when getAvailability throws", async () => {
      // @ts-expect-error - mocking PresentationRequest
      window.PresentationRequest = class {
        getAvailability() {
          return Promise.reject(new Error("Not supported"));
        }
      };

      const controller = new PresentationController();
      const available = await controller.checkAvailability("/projection");
      expect(available).toBe(false);
    });
  });

  describe("start", () => {
    it("throws when Presentation API is not supported", async () => {
      // @ts-expect-error - removing for test
      delete window.PresentationRequest;
      const controller = new PresentationController();
      await expect(controller.start("/projection")).rejects.toThrow(
        "Presentation API not supported"
      );
    });

    it("calls PresentationRequest with the given URL", async () => {
      const constructorSpy = vi.fn();
      const conn = mockConnection;
      // @ts-expect-error - mocking PresentationRequest
      window.PresentationRequest = class {
        constructor(urls: string[]) {
          constructorSpy(urls);
        }
        start() {
          return Promise.resolve(conn);
        }
      };

      const controller = new PresentationController();
      await controller.start("/songsets/123/play/projection");

      expect(constructorSpy).toHaveBeenCalledWith([
        "/songsets/123/play/projection",
      ]);
    });

    it("registers event listeners on the connection", async () => {
      installMockRequest();
      const controller = new PresentationController();
      await controller.start("/projection");

      expect(mockConnection.addEventListener).toHaveBeenCalledWith(
        "connect",
        expect.any(Function)
      );
      expect(mockConnection.addEventListener).toHaveBeenCalledWith(
        "close",
        expect.any(Function)
      );
      expect(mockConnection.addEventListener).toHaveBeenCalledWith(
        "terminate",
        expect.any(Function)
      );
    });

    it("fires onConnect handlers when connect event fires", async () => {
      installMockRequest();
      const controller = new PresentationController();
      const onConnect = vi.fn();
      controller.onConnect(onConnect);

      await controller.start("/projection");
      capturedConnectHandler?.();

      expect(onConnect).toHaveBeenCalled();
    });

    it("fires onConnect immediately when connection.state is already connected", async () => {
      mockConnection.state = "connected";
      installMockRequest();
      const controller = new PresentationController();
      const onConnect = vi.fn();
      controller.onConnect(onConnect);

      await controller.start("/projection");

      expect(onConnect).toHaveBeenCalled();
    });

    it("fires onDisconnect handlers when close event fires", async () => {
      installMockRequest();
      const controller = new PresentationController();
      const onDisconnect = vi.fn();
      controller.onDisconnect(onDisconnect);

      await controller.start("/projection");
      capturedCloseHandler?.();

      expect(onDisconnect).toHaveBeenCalled();
    });

    it("fires onDisconnect handlers when terminate event fires", async () => {
      installMockRequest();
      const controller = new PresentationController();
      const onDisconnect = vi.fn();
      controller.onDisconnect(onDisconnect);

      await controller.start("/projection");
      capturedTerminateHandler?.();

      expect(onDisconnect).toHaveBeenCalled();
    });

    it("sets isConnected to true after start", async () => {
      installMockRequest();
      const controller = new PresentationController();
      expect(controller.isConnected).toBe(false);
      await controller.start("/projection");
      expect(controller.isConnected).toBe(true);
    });

    it("sets isConnected to false after disconnect", async () => {
      installMockRequest();
      const controller = new PresentationController();
      await controller.start("/projection");
      expect(controller.isConnected).toBe(true);

      capturedCloseHandler?.();
      expect(controller.isConnected).toBe(false);
    });
  });

  describe("send", () => {
    beforeEach(() => {
      installMockRequest();
    });

    it("sends JSON-serialized command to the connection", async () => {
      const controller = new PresentationController();
      await controller.start("/projection");

      const command: PresentationCommand = { type: "play" };
      controller.send(command);

      expect(mockConnection.send).toHaveBeenCalledWith(JSON.stringify(command));
    });

    it("no-ops when not connected", () => {
      const controller = new PresentationController();
      expect(() => controller.send({ type: "play" })).not.toThrow();
      expect(mockConnection.send).not.toHaveBeenCalled();
    });
  });

  describe("convenience send methods", () => {
    beforeEach(() => {
      installMockRequest();
    });

    it("sendPlay sends play command", async () => {
      const controller = new PresentationController();
      await controller.start("/projection");
      controller.sendPlay();
      expect(mockConnection.send).toHaveBeenCalledWith(
        JSON.stringify({ type: "play" })
      );
    });

    it("sendPause sends pause command", async () => {
      const controller = new PresentationController();
      await controller.start("/projection");
      controller.sendPause();
      expect(mockConnection.send).toHaveBeenCalledWith(
        JSON.stringify({ type: "pause" })
      );
    });

    it("sendSeek sends seek command with positionSeconds", async () => {
      const controller = new PresentationController();
      await controller.start("/projection");
      controller.sendSeek(42.5);
      expect(mockConnection.send).toHaveBeenCalledWith(
        JSON.stringify({ type: "seek", positionSeconds: 42.5 })
      );
    });

    it("sendVolume sends volume command with level", async () => {
      const controller = new PresentationController();
      await controller.start("/projection");
      controller.sendVolume(0.8);
      expect(mockConnection.send).toHaveBeenCalledWith(
        JSON.stringify({ type: "volume", level: 0.8 })
      );
    });

    it("sendSongTitle sends songTitle command with title", async () => {
      const controller = new PresentationController();
      await controller.start("/projection");
      controller.sendSongTitle("Amazing Grace");
      expect(mockConnection.send).toHaveBeenCalledWith(
        JSON.stringify({ type: "songTitle", title: "Amazing Grace" })
      );
    });
  });

  describe("close", () => {
    it("terminates the connection and sets isConnected to false", async () => {
      installMockRequest();
      const controller = new PresentationController();
      await controller.start("/projection");
      expect(controller.isConnected).toBe(true);

      controller.close();

      expect(mockConnection.terminate).toHaveBeenCalled();
      expect(controller.isConnected).toBe(false);
    });

    it("no-ops when not connected", () => {
      const controller = new PresentationController();
      expect(() => controller.close()).not.toThrow();
    });
  });

  describe("onConnect / onDisconnect unsubscribe", () => {
    it("stops calling handler after unsubscribe", async () => {
      installMockRequest();
      const controller = new PresentationController();
      const handler = vi.fn();
      const unsubscribe = controller.onConnect(handler);
      unsubscribe();

      await controller.start("/projection");
      capturedConnectHandler?.();

      expect(handler).not.toHaveBeenCalled();
    });

    it("removes only the unsubscribed handler", async () => {
      installMockRequest();
      const controller = new PresentationController();
      const handler1 = vi.fn();
      const handler2 = vi.fn();
      const unsubscribe1 = controller.onConnect(handler1);
      controller.onConnect(handler2);
      unsubscribe1();

      await controller.start("/projection");
      capturedConnectHandler?.();

      expect(handler1).not.toHaveBeenCalled();
      expect(handler2).toHaveBeenCalled();
    });
  });
});
