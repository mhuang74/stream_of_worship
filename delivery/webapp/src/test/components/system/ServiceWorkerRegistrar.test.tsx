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

  it("survives registration failure (fire-and-forget boot)", async () => {
    registerMock.mockRejectedValue(new Error("Registration failed"));

    expect(() => render(<ServiceWorkerRegistrar />)).not.toThrow();
    await Promise.resolve();
  });
});