import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";

// Mocks for workbox-window – defined via vi.hoisted so they are available both
// inside the vi.mock factory (runs before imports) and in the test body.
const mocks = vi.hoisted(() => {
  const addEventListener = vi.fn();
  const messageSkipWaiting = vi.fn();
  const register = vi.fn();

  // A true function (not arrow) so it can be called with `new`.
  function WorkboxCtor(this: unknown, _url: string) {
    return { addEventListener, messageSkipWaiting, register };
  }

  return {
    addEventListener,
    messageSkipWaiting,
    register,
    WorkboxCtor: vi.fn().mockImplementation(WorkboxCtor),
  };
});

vi.mock("workbox-window", () => ({ Workbox: mocks.WorkboxCtor }));

import {
  registerServiceWorker,
  unregisterServiceWorker,
  getWorkboxInstance,
} from "@/lib/offline/precaching";

describe("precaching", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.register.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("registerServiceWorker", () => {
    describe("when service workers are not supported", () => {
      it("returns failure when serviceWorker not in navigator", async () => {
        const descriptor = Object.getOwnPropertyDescriptor(navigator, "serviceWorker");
        // @ts-expect-error - removing for test
        delete (navigator as Navigator).serviceWorker;

        const result = await registerServiceWorker();

        expect(result.success).toBe(false);
        expect(result.error).toMatch(/not supported/i);

        if (descriptor) {
          Object.defineProperty(navigator, "serviceWorker", descriptor);
        }
      });
    });

    describe("when service workers are supported", () => {
      beforeEach(() => {
        Object.defineProperty(navigator, "serviceWorker", {
          value: { getRegistrations: vi.fn().mockResolvedValue([]) },
          writable: true,
          configurable: true,
        });
      });

      it("returns success when registration succeeds", async () => {
        const result = await registerServiceWorker();
        expect(result.success).toBe(true);
        expect(result.error).toBeUndefined();
      });

      it("creates a Workbox instance with the given SW URL", async () => {
        await registerServiceWorker("/custom-sw.js");
        expect(mocks.WorkboxCtor).toHaveBeenCalledWith("/custom-sw.js");
      });

      it("uses /sw.js as the default SW URL", async () => {
        await registerServiceWorker();
        expect(mocks.WorkboxCtor).toHaveBeenCalledWith("/sw.js");
      });

      it("registers the service worker", async () => {
        await registerServiceWorker();
        expect(mocks.register).toHaveBeenCalled();
      });

      it("listens for the waiting event", async () => {
        await registerServiceWorker();
        expect(mocks.addEventListener).toHaveBeenCalledWith("waiting", expect.any(Function));
      });

      it("calls messageSkipWaiting when the waiting event fires", async () => {
        await registerServiceWorker();

        const calls = mocks.addEventListener.mock.calls as [string, () => void][];
        const waitingCall = calls.find(([event]) => event === "waiting");
        expect(waitingCall).toBeDefined();
        waitingCall![1]();

        expect(mocks.messageSkipWaiting).toHaveBeenCalled();
      });

      it("exposes the Workbox instance via getWorkboxInstance after registration", async () => {
        await registerServiceWorker();
        const instance = getWorkboxInstance();
        expect(instance).not.toBeNull();
      });
    });

    describe("when registration throws", () => {
      beforeEach(() => {
        Object.defineProperty(navigator, "serviceWorker", {
          value: { getRegistrations: vi.fn().mockResolvedValue([]) },
          writable: true,
          configurable: true,
        });
        mocks.register.mockRejectedValue(new Error("Registration failed"));
      });

      it("returns failure result with error message", async () => {
        const result = await registerServiceWorker();
        expect(result.success).toBe(false);
        expect(result.error).toBe("Registration failed");
      });
    });
  });

  describe("unregisterServiceWorker", () => {
    it("returns false when service workers are not supported", async () => {
      const descriptor = Object.getOwnPropertyDescriptor(navigator, "serviceWorker");
      // @ts-expect-error - removing for test
      delete (navigator as Navigator).serviceWorker;

      const result = await unregisterServiceWorker();
      expect(result).toBe(false);

      if (descriptor) {
        Object.defineProperty(navigator, "serviceWorker", descriptor);
      }
    });

    it("unregisters all registrations and returns true", async () => {
      const mockUnregister = vi.fn().mockResolvedValue(true);
      Object.defineProperty(navigator, "serviceWorker", {
        value: {
          getRegistrations: vi
            .fn()
            .mockResolvedValue([{ unregister: mockUnregister }, { unregister: mockUnregister }]),
        },
        writable: true,
        configurable: true,
      });

      const result = await unregisterServiceWorker();
      expect(result).toBe(true);
      expect(mockUnregister).toHaveBeenCalledTimes(2);
    });

    it("returns false when getRegistrations throws", async () => {
      Object.defineProperty(navigator, "serviceWorker", {
        value: { getRegistrations: vi.fn().mockRejectedValue(new Error("SW error")) },
        writable: true,
        configurable: true,
      });

      const result = await unregisterServiceWorker();
      expect(result).toBe(false);
    });

    it("returns true when there are no registrations", async () => {
      Object.defineProperty(navigator, "serviceWorker", {
        value: { getRegistrations: vi.fn().mockResolvedValue([]) },
        writable: true,
        configurable: true,
      });

      const result = await unregisterServiceWorker();
      expect(result).toBe(true);
    });
  });
});
