import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, act } from "@testing-library/react";
import { OfflineIndicator } from "@/components/offline/OfflineIndicator";

describe("OfflineIndicator", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  describe("when online", () => {
    beforeEach(() => {
      Object.defineProperty(navigator, "onLine", {
        value: true,
        writable: true,
        configurable: true,
      });
    });

    it("renders nothing when online", () => {
      const { container } = render(<OfflineIndicator />);
      expect(container.firstChild).toBeNull();
    });

    it("does not show offline banner", () => {
      render(<OfflineIndicator />);
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });
  });

  describe("when offline", () => {
    beforeEach(() => {
      Object.defineProperty(navigator, "onLine", {
        value: false,
        writable: true,
        configurable: true,
      });
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
  });

  describe("network state transitions", () => {
    beforeEach(() => {
      Object.defineProperty(navigator, "onLine", {
        value: true,
        writable: true,
        configurable: true,
      });
    });

    it("shows banner when going offline", () => {
      render(<OfflineIndicator />);
      expect(screen.queryByRole("status")).not.toBeInTheDocument();

      act(() => {
        window.dispatchEvent(new Event("offline"));
      });

      expect(screen.getByRole("status")).toBeInTheDocument();
    });

    it("hides banner when coming back online", () => {
      Object.defineProperty(navigator, "onLine", {
        value: false,
        writable: true,
        configurable: true,
      });

      render(<OfflineIndicator />);
      expect(screen.getByRole("status")).toBeInTheDocument();

      act(() => {
        window.dispatchEvent(new Event("online"));
      });

      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });

    it("removes event listeners on unmount", () => {
      const addSpy = vi.spyOn(window, "addEventListener");
      const removeSpy = vi.spyOn(window, "removeEventListener");

      const { unmount } = render(<OfflineIndicator />);

      expect(addSpy).toHaveBeenCalledWith("online", expect.any(Function));
      expect(addSpy).toHaveBeenCalledWith("offline", expect.any(Function));

      unmount();

      expect(removeSpy).toHaveBeenCalledWith("online", expect.any(Function));
      expect(removeSpy).toHaveBeenCalledWith("offline", expect.any(Function));
    });
  });

  describe("accessibility", () => {
    beforeEach(() => {
      Object.defineProperty(navigator, "onLine", {
        value: false,
        writable: true,
        configurable: true,
      });
    });

    it("has aria-live attribute", () => {
      render(<OfflineIndicator />);
      const indicator = screen.getByRole("status");
      expect(indicator).toHaveAttribute("aria-live", "polite");
    });

    it("accepts custom className", () => {
      render(<OfflineIndicator className="custom-class" />);
      const indicator = screen.getByRole("status");
      expect(indicator).toHaveClass("custom-class");
    });
  });
});
