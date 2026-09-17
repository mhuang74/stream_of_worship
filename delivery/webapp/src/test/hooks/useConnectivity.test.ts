import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import {
  getConnectivity,
  useConnectivity,
  probeConnectivity,
  setConnectivityProbe,
} from "@/hooks/useConnectivity";

// The shared Connectivity state machine (issue #211). At the hook seam, all
// probe outcomes are driven by the injectable probe — no network: success
// (HTTP 204), failure (rejection / timeout / non-204), and the never-probed
// in-flight state. OS-level state is stubbed via the suite's established
// navigator.onLine convention.

function setOnLine(online: boolean): void {
  Object.defineProperty(navigator, "onLine", {
    value: online,
    configurable: true,
  });
}

describe("useConnectivity (issue #211)", () => {
  const probe = vi.fn<() => Promise<boolean>>();

  beforeEach(() => {
    probe.mockReset();
    probe.mockResolvedValue(true);
    setOnLine(true);
    // Injectable probe seam + reset of the prior test's probe memory — the
    // module's state survives between tests within a file otherwise.
    setConnectivityProbe(probe);
  });

  afterEach(() => {
    setConnectivityProbe(null);
    setOnLine(true);
  });

  describe("state machine", () => {
    it("starts unknown on a fresh client that has never probed (fail toward offline)", async () => {
      setConnectivityProbe(null);
      vi.stubGlobal("fetch", vi.fn(() => new Promise<boolean>(() => {}))); // never settles

      const { result } = renderHook(() => useConnectivity());
      expect(result.current).toBe("unknown");

      probeConnectivity();
      await act(async () => {});
      expect(result.current).toBe("unknown"); // in-flight is Unknown, not Online
      vi.unstubAllGlobals();
    });

    it("turns online when the health probe answers 204", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ status: 204 }));
      setConnectivityProbe(null); // exercise the default (health endpoint) probe

      renderHook(() => useConnectivity());
      await act(async () => {
        await probeConnectivity();
      });

      expect(getConnectivity()).toBe("online");
      vi.unstubAllGlobals();
    });

    it("treats a non-204 health answer as not reachable (Inconclusive → offline affordances)", async () => {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ status: 500 }));
      setConnectivityProbe(null);

      renderHook(() => useConnectivity());
      await act(async () => {
        await probeConnectivity();
      });

      expect(getConnectivity()).not.toBe("online");
      vi.unstubAllGlobals();
    });

    it("treats a probe rejection (network unreachable) as not reachable", async () => {
      vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
      setConnectivityProbe(null);

      renderHook(() => useConnectivity());
      await act(async () => {
        await probeConnectivity();
      });

      expect(getConnectivity()).not.toBe("online");
      vi.unstubAllGlobals();
    });

    it("reads offline from the OS without probing", async () => {
      setOnLine(false);

      const { result } = renderHook(() => useConnectivity());

      expect(result.current).toBe("offline");
      expect(probe).not.toHaveBeenCalled();
      // probeConnectivity must not attempt a HEAD while the OS says offline.
      await act(async () => {
        await probeConnectivity();
      });
      expect(probe).not.toHaveBeenCalled();
    });

    it("recovers to online when the OS comes back and the probe confirms", async () => {
      setOnLine(false);
      setConnectivityProbe(probe);
      const { result } = renderHook(() => useConnectivity());
      expect(result.current).toBe("offline");

      await act(async () => {
        setOnLine(true);
        window.dispatchEvent(new Event("online"));
      });

      expect(probe).toHaveBeenCalled();
      expect(result.current).toBe("online");
    });

    // Fail toward offline (issue #211 story 9): after an offline episode,
    // the last probe result is stale. The `online` event must flip the OS
    // side but NOT certify Online from the stale value — only the refired
    // probe may do that. The probe here never settles, so any window where
    // the stale value leaks through is observable, not masked by a settle.
    it("treats the online event after an offline episode as unknown, not online, until a fresh probe confirms", async () => {
      // One settled probe puts the store in online…
      const { result } = renderHook(() => useConnectivity());
      await act(async () => {
        await probeConnectivity();
      });
      expect(result.current).toBe("online");

      // …then the network drops: definitive Offline, no probe attempted.
      await act(async () => {
        setOnLine(false);
        window.dispatchEvent(new Event("offline"));
      });
      expect(result.current).toBe("offline");

      // …then Airplane mode comes off while the refired probe is stuck
      // (server unreachable, timeout pending): the only probe result on
      // record predates the outage and must not certify Online.
      probe.mockImplementation(() => new Promise<boolean>(() => {}));
      await act(async () => {
        setOnLine(true);
        window.dispatchEvent(new Event("online"));
      });
      expect(result.current).toBe("unknown");
    });
  });

  describe("event-driven probes", () => {
    it("re-probes when the tab becomes visible again", async () => {
      probe.mockResolvedValueOnce(false);
      renderHook(() => useConnectivity());
      await act(async () => {
        await probeConnectivity();
      });
      expect(getConnectivity()).not.toBe("online");

      probe.mockResolvedValue(true);
      await act(async () => {
        document.dispatchEvent(new Event("visibilitychange"));
        await probeConnectivity();
      });
      // The visibilitychange handler fires its own probe; the direct call is
      // deduped as in-flight — the state recovers to Online either way.
      expect(getConnectivity()).toBe("online");
    });

    it("re-probes on the online event", async () => {
      const { result } = renderHook(() => useConnectivity());
      await act(async () => {
        await probeConnectivity();
      });
      const callsBefore = probe.mock.calls.length;

      await act(async () => {
        window.dispatchEvent(new Event("online"));
      });

      expect(probe.mock.calls.length).toBeGreaterThan(callsBefore);
      expect(result.current).toBe("online");
    });

    it("does not probe on visibility change while the OS reports offline", async () => {
      setOnLine(false);
      renderHook(() => useConnectivity());
      const callsBefore = probe.mock.calls.length;

      await act(async () => {
        document.dispatchEvent(new Event("visibilitychange"));
      });

      expect(probe.mock.calls.length).toBe(callsBefore);
    });
  });

  describe("shared store", () => {
    it("notifies every subscriber when one probe settles", async () => {
      const first = renderHook(() => useConnectivity());
      const second = renderHook(() => useConnectivity());

      await act(async () => {
        await probeConnectivity();
      });

      expect(first.result.current).toBe("online");
      expect(second.result.current).toBe("online");
    });

    it("dedupes an in-flight probe instead of stampeding the server", async () => {
      let releaseProbe: ((value: boolean) => void) | undefined;
      probe.mockImplementation(
        () =>
          new Promise<boolean>((resolve) => {
            releaseProbe = resolve;
          })
      );

      renderHook(() => useConnectivity());
      await act(async () => {
        void probeConnectivity();
        void probeConnectivity();
        void probeConnectivity();
        releaseProbe?.(true);
      });

      expect(probe).toHaveBeenCalledTimes(1);
    });
  });
});
