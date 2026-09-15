import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render } from "@testing-library/react";
import { ServiceWorkerRegistrar } from "@/components/system/ServiceWorkerRegistrar";

/**
 * Issue #204: the registrar boots the SW at app mount — the registration
 * itself is the behavior; the component renders nothing.
 */
const registerMock = vi.fn();

describe("ServiceWorkerRegistrar", () => {
  beforeEach(() => {
    registerMock.mockReset().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "serviceWorker", {
      value: { register: registerMock },
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    const descriptor = Object.getOwnPropertyDescriptor(navigator, "serviceWorker");
    if (descriptor) delete (navigator as { serviceWorker?: unknown }).serviceWorker;
    vi.restoreAllMocks();
  });

  it("registers /sw.js on mount", async () => {
    render(<ServiceWorkerRegistrar />);

    expect(registerMock).toHaveBeenCalledWith("/sw.js", { updateViaCache: "none" });
  });

  it("renders nothing", () => {
    const { container } = render(<ServiceWorkerRegistrar />);

    expect(container.firstChild).toBeNull();
  });

  it("does not leak an unhandled rejection when registration fails", async () => {
    registerMock.mockRejectedValue(new Error("Registration failed"));
    const unhandled: unknown[] = [];
    const onUnhandled = (reason: unknown) => unhandled.push(reason);
    process.on("unhandledRejection", onUnhandled);

    try {
      // The boot call is fire-and-forget (`void registerServiceWorker()`), so a
      // rejection escaping the helper would surface here as an unhandled
      // rejection rather than anywhere in the app.
      render(<ServiceWorkerRegistrar />);
      const { promise, resolve } = Promise.withResolvers<void>();
      setTimeout(resolve, 0);
      await promise;
    } finally {
      process.off("unhandledRejection", onUnhandled);
    }

    expect(unhandled).toEqual([]);
  });
});