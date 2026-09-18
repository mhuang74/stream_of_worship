import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { probeConnectivity, setConnectivityProbe } from "@/hooks/useConnectivity";
import { useOfflineRedirect } from "@/hooks/useOfflineRedirect";

// The offline redirect guard (issue #211 follow-up, Q11): online-dependent
// surfaces bounce to /offline on definitive OS-offline only. Never on
// Unknown (in-flight/failed probe) — a cold boot must not bounce users whose
// connectivity has not been confirmed yet. Navigation is location.replace
// (full document, no history junk).

function setOnLine(online: boolean): void {
  Object.defineProperty(navigator, "onLine", {
    value: online,
    configurable: true,
  });
}

describe("useOfflineRedirect", () => {
  const probe = vi.fn<() => Promise<boolean>>();
  let replaceMock = vi.fn();
  let locationDescriptor: PropertyDescriptor | undefined;

  beforeEach(() => {
    probe.mockReset();
    probe.mockResolvedValue(true);
    setConnectivityProbe(probe);
    setOnLine(true);
    replaceMock = vi.fn();
    locationDescriptor = Object.getOwnPropertyDescriptor(window, "location");
    Object.defineProperty(window, "location", {
      value: { replace: replaceMock },
      configurable: true,
    });
  });

  afterEach(() => {
    setConnectivityProbe(null);
    setOnLine(true);
    if (locationDescriptor) {
      Object.defineProperty(window, "location", locationDescriptor);
    }
  });

  async function confirmOnline(): Promise<void> {
    await act(async () => {
      await probeConnectivity();
    });
  }

  it("replaces to /offline when the OS reports offline", async () => {
    renderHook(() => useOfflineRedirect());
    await confirmOnline();
    expect(replaceMock).not.toHaveBeenCalled();

    await act(async () => {
      setOnLine(false);
      window.dispatchEvent(new Event("offline"));
    });

    expect(replaceMock).toHaveBeenCalledWith("/offline");
  });

  it("does not redirect while the probe has not confirmed online (Unknown)", async () => {
    probe.mockResolvedValue(false); // server unreachable → Unknown
    setConnectivityProbe(probe);

    renderHook(() => useOfflineRedirect());
    await act(async () => {
      await probeConnectivity();
    });

    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("does not redirect when positively online", async () => {
    renderHook(() => useOfflineRedirect());
    await confirmOnline();

    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("redirects via location.replace, not assign or push", async () => {
    renderHook(() => useOfflineRedirect());
    await act(async () => {
      setOnLine(false);
      window.dispatchEvent(new Event("offline"));
    });

    expect(replaceMock).toHaveBeenCalledTimes(1);
  });
});
