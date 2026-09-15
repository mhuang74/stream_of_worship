import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  registerServiceWorker,
  unregisterServiceWorker,
} from "@/lib/offline/precaching";

/**
 * Issue #204: registration is plain navigator.serviceWorker.register("/sw.js")
 * — activation is owned by the SW itself (workbox.core.skipWaiting +
 * clientsClaim), so there is no Workbox window wrapper to mock.
 */
describe("precaching", () => {
  const registerMock = vi.fn();

  beforeEach(() => {
    registerMock.mockReset().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "serviceWorker", {
      value: { register: registerMock, getRegistrations: vi.fn().mockResolvedValue([]) },
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    const descriptor = Object.getOwnPropertyDescriptor(navigator, "serviceWorker");
    if (descriptor) delete (navigator as { serviceWorker?: unknown }).serviceWorker;
    vi.restoreAllMocks();
  });

  describe("registerServiceWorker", () => {
    it("returns failure when service workers are not supported", async () => {
      delete (navigator as { serviceWorker?: unknown }).serviceWorker;

      const result = await registerServiceWorker();

      expect(result.success).toBe(false);
      expect(result.error).toMatch(/not supported/i);
      expect(registerMock).not.toHaveBeenCalled();
    });

    it("registers /sw.js and returns success", async () => {
      const result = await registerServiceWorker();

      expect(registerMock).toHaveBeenCalledWith("/sw.js");
      expect(result.success).toBe(true);
      expect(result.error).toBeUndefined();
    });

    it("returns the error message when registration throws", async () => {
      registerMock.mockRejectedValue(new Error("Registration failed"));

      const result = await registerServiceWorker();

      expect(result.success).toBe(false);
      expect(result.error).toBe("Registration failed");
    });
  });

  describe("unregisterServiceWorker", () => {
    it("returns false when service workers are not supported", async () => {
      delete (navigator as { serviceWorker?: unknown }).serviceWorker;

      const result = await unregisterServiceWorker();

      expect(result).toBe(false);
    });

    it("unregisters all registrations and returns true", async () => {
      const unregister = vi.fn().mockResolvedValue(true);
      Object.defineProperty(navigator, "serviceWorker", {
        value: {
          register: registerMock,
          getRegistrations: vi.fn().mockResolvedValue([{ unregister }, { unregister }]),
        },
        writable: true,
        configurable: true,
      });

      const result = await unregisterServiceWorker();

      expect(result).toBe(true);
      expect(unregister).toHaveBeenCalledTimes(2);
    });

    it("returns false when getRegistrations throws", async () => {
      Object.defineProperty(navigator, "serviceWorker", {
        value: {
          register: registerMock,
          getRegistrations: vi.fn().mockRejectedValue(new Error("SW error")),
        },
        writable: true,
        configurable: true,
      });

      const result = await unregisterServiceWorker();

      expect(result).toBe(false);
    });

    it("returns true when there are no registrations", async () => {
      Object.defineProperty(navigator, "serviceWorker", {
        value: { register: registerMock, getRegistrations: vi.fn().mockResolvedValue([]) },
        writable: true,
        configurable: true,
      });

      const result = await unregisterServiceWorker();

      expect(result).toBe(true);
    });
  });
});