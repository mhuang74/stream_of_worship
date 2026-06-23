import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useWakeLock } from "@/hooks/useWakeLock";

describe("useWakeLock", () => {
  let mockSentinel: {
    released: boolean;
    type: "screen";
    release: ReturnType<typeof vi.fn>;
    addEventListener: ReturnType<typeof vi.fn>;
    removeEventListener: ReturnType<typeof vi.fn>;
  };

  beforeEach(() => {
    vi.clearAllMocks();

    mockSentinel = {
      released: false,
      type: "screen",
      release: vi.fn().mockResolvedValue(undefined),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };

    // Reset navigator.wakeLock mock
    Object.defineProperty(navigator, "wakeLock", {
      value: undefined,
      writable: true,
      configurable: true,
    });

    // Reset document.visibilityState
    Object.defineProperty(document, "visibilityState", {
      value: "visible",
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("when Wake Lock API is not supported", () => {
    it("returns isSupported as false", () => {
      const { result } = renderHook(() => useWakeLock());

      expect(result.current.isSupported).toBe(false);
    });

    it("returns isActive as false", () => {
      const { result } = renderHook(() => useWakeLock());

      expect(result.current.isActive).toBe(false);
    });
  });

  describe("when Wake Lock API is supported", () => {
    beforeEach(() => {
      Object.defineProperty(navigator, "wakeLock", {
        value: {
          request: vi.fn().mockResolvedValue(mockSentinel),
        },
        writable: true,
        configurable: true,
      });
    });

    it("returns isSupported as true", async () => {
      const { result } = renderHook(() => useWakeLock());

      await waitFor(() => {
        expect(result.current.isSupported).toBe(true);
      });
    });

    it("automatically requests wake lock on mount", async () => {
      renderHook(() => useWakeLock());

      await waitFor(() => {
        expect(navigator.wakeLock.request).toHaveBeenCalledWith("screen");
      });
    });

    it("returns isActive as true after acquiring lock", async () => {
      const { result } = renderHook(() => useWakeLock());

      await waitFor(() => {
        expect(result.current.isActive).toBe(true);
      });
    });

    it("releases wake lock on unmount", async () => {
      Object.defineProperty(navigator, "wakeLock", {
        value: {
          request: vi.fn().mockResolvedValue(mockSentinel),
        },
        writable: true,
        configurable: true,
      });

      const { unmount } = renderHook(() => useWakeLock());

      // Wait for the lock to be acquired
      await waitFor(() => {
        expect(mockSentinel.release).not.toHaveBeenCalled();
      });

      unmount();

      await waitFor(() => {
        expect(mockSentinel.release).toHaveBeenCalled();
      });
    });

    it("handles release errors gracefully", async () => {
      const consoleSpy = vi.spyOn(console, "error").mockImplementation(() => {});

      Object.defineProperty(navigator, "wakeLock", {
        value: {
          request: vi.fn().mockRejectedValue(new Error("Permission denied")),
        },
        writable: true,
        configurable: true,
      });

      const { result } = renderHook(() => useWakeLock());

      await waitFor(() => {
        expect(result.current.error).toBe("Permission denied");
      });

      consoleSpy.mockRestore();
    });

    it("provides manual request function", async () => {
      Object.defineProperty(navigator, "wakeLock", {
        value: {
          request: vi.fn().mockResolvedValue(mockSentinel),
        },
        writable: true,
        configurable: true,
      });

      const { result } = renderHook(() => useWakeLock());

      await act(async () => {
        await result.current.request();
      });

      expect(navigator.wakeLock.request).toHaveBeenCalledWith("screen");
    });

    it("provides manual release function", async () => {
      Object.defineProperty(navigator, "wakeLock", {
        value: {
          request: vi.fn().mockResolvedValue(mockSentinel),
        },
        writable: true,
        configurable: true,
      });

      const { result } = renderHook(() => useWakeLock());

      await waitFor(() => {
        expect(result.current.isActive).toBe(true);
      });

      await act(async () => {
        await result.current.release();
      });

      expect(mockSentinel.release).toHaveBeenCalled();
      expect(result.current.isActive).toBe(false);
    });
  });
});
