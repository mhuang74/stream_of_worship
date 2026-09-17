import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen, act, waitFor } from "@testing-library/react";
import { renderWithLocale as render } from "@/test/render";
import { OfflineIndicator } from "@/components/offline/OfflineIndicator";
import {
  probeConnectivity,
  setConnectivityProbe,
} from "@/hooks/useConnectivity";

// Connectivity is the shared state machine (src/hooks/useConnectivity.ts,
// issue #211): navigator.onLine plus the reachability probe. OS state is
// stubbed via the suite's navigator.onLine convention; probe outcomes are
// driven through the injectable-probe seam.
describe("OfflineIndicator", () => {
  const probe = vi.fn<() => Promise<boolean>>();

  beforeEach(() => {
    vi.clearAllMocks();
    probe.mockReset();
    probe.mockResolvedValue(true);
    setConnectivityProbe(probe);
  });

  afterEach(() => {
    setConnectivityProbe(null);
  });

  function setOnLine(online: boolean): void {
    Object.defineProperty(navigator, "onLine", {
      value: online,
      writable: true,
      configurable: true,
    });
  }

  // Fail toward offline means the banner is visible until the probe has
  // positively confirmed Online. Tests that assert the online rendering must
  // settle one successful probe first.
  async function confirmOnline(): Promise<void> {
    await act(async () => {
      await probeConnectivity();
    });
  }

  describe("when online", () => {
    beforeEach(() => {
      setOnLine(true);
    });

    // Probe-confirmed Online: the OS says online AND /api/health answered 204.
    it("renders nothing when online", async () => {
      const { container } = render(<OfflineIndicator />);
      await confirmOnline();
      expect(container.firstChild).toBeNull();
    });

    it("does not show offline banner", async () => {
      render(<OfflineIndicator />);
      await confirmOnline();
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });
  });

  describe("when offline", () => {
    beforeEach(() => {
      setOnLine(false);
    });

    it("shows offline indicator", () => {
      render(<OfflineIndicator />);
      expect(screen.getByRole("status")).toBeInTheDocument();
    });

    it("shows 'You are offline' text", () => {
      render(<OfflineIndicator />);
      expect(screen.getByText(/you are offline/i)).toBeInTheDocument();
    });

    it("has accessible aria-label", () => {
      render(<OfflineIndicator />);
      expect(screen.getByRole("status", { name: /you are offline/i })).toBeInTheDocument();
    });

    it("shows Traditional Chinese message in zh-Hant", () => {
      render(<OfflineIndicator />, "zh-Hant");
      expect(screen.getByText("你目前離線")).toBeInTheDocument();
      expect(screen.getByRole("status", { name: "你目前離線" })).toBeInTheDocument();
    });
  });

  describe("network state transitions", () => {
    beforeEach(() => {
      setOnLine(true);
    });

    it("shows banner when going offline", async () => {
      render(<OfflineIndicator />);
      await confirmOnline();
      expect(screen.queryByRole("status")).not.toBeInTheDocument();

      // A real browser flips navigator.onLine atomically with the events.
      act(() => {
        setOnLine(false);
        window.dispatchEvent(new Event("offline"));
      });

      expect(screen.getByRole("status")).toBeInTheDocument();
    });

    it("hides banner when coming back online", async () => {
      act(() => {
        setOnLine(false);
        window.dispatchEvent(new Event("offline"));
      });

      render(<OfflineIndicator />);
      expect(screen.getByRole("status")).toBeInTheDocument();

      await act(async () => {
        setOnLine(true);
        window.dispatchEvent(new Event("online"));
      });

      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });
  });

  describe("reachability probe (issue #211)", () => {
    // Story 4/9: interface up but no route (captive portal, dead Wi-Fi) —
    // navigator.onLine says online, the probe says unreachable → Offline
    // affordances, banner included.
    it("shows the banner when the probe reports the server unreachable while nominally online", async () => {
      setOnLine(true);
      probe.mockResolvedValue(false);

      render(<OfflineIndicator />);

      await act(async () => {
        await probeConnectivity();
      });

      expect(screen.getByRole("status")).toBeInTheDocument();
    });

    it("hides the banner when a return-to-app probe recovers", async () => {
      setOnLine(true);
      probe.mockResolvedValue(false);

      render(<OfflineIndicator />);
      await act(async () => {
        await probeConnectivity();
      });
      expect(screen.getByRole("status")).toBeInTheDocument();

      probe.mockResolvedValue(true);
      await act(async () => {
        document.dispatchEvent(new Event("visibilitychange"));
      });

      await waitFor(() => {
        expect(screen.queryByRole("status")).not.toBeInTheDocument();
      });
    });
  });

  describe("accessibility", () => {
    beforeEach(() => {
      setOnLine(false);
    });

    it("has aria-live attribute", () => {
      render(<OfflineIndicator />);
      const indicator = screen.getByRole("status");
      expect(indicator).toHaveAttribute("aria-live", "polite");
    });
  });
});
