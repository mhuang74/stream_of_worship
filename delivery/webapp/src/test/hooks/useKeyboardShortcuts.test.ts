import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook } from "@testing-library/react";
import { useKeyboardShortcuts } from "@/hooks/useKeyboardShortcuts";

describe("useKeyboardShortcuts", () => {
  const onTogglePlayback = vi.fn();
  const onSeekBack = vi.fn();
  const onSeekForward = vi.fn();
  const onPrevSong = vi.fn();
  const onNextSong = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const defaultActions = {
    onTogglePlayback,
    onSeekBack,
    onSeekForward,
    onPrevSong,
    onNextSong,
  };

  function fireKeyDown(key: string, target?: HTMLElement) {
    const event = new KeyboardEvent("keydown", {
      key,
      bubbles: true,
      cancelable: true,
    });

    if (target) {
      Object.defineProperty(event, "target", { value: target, writable: false });
    }

    document.dispatchEvent(event);
    return event;
  }

  describe("Space key", () => {
    it("calls onTogglePlayback when Space is pressed", () => {
      renderHook(() => useKeyboardShortcuts(defaultActions));

      fireKeyDown(" ");

      expect(onTogglePlayback).toHaveBeenCalledTimes(1);
    });

    it("prevents default behavior on Space", () => {
      renderHook(() => useKeyboardShortcuts(defaultActions));

      const event = fireKeyDown(" ");

      expect(event.defaultPrevented).toBe(true);
    });
  });

  describe("Arrow keys", () => {
    it("calls onSeekBack when ArrowLeft is pressed", () => {
      renderHook(() => useKeyboardShortcuts(defaultActions));

      fireKeyDown("ArrowLeft");

      expect(onSeekBack).toHaveBeenCalledTimes(1);
    });

    it("prevents default on ArrowLeft", () => {
      renderHook(() => useKeyboardShortcuts(defaultActions));

      const event = fireKeyDown("ArrowLeft");

      expect(event.defaultPrevented).toBe(true);
    });

    it("calls onSeekForward when ArrowRight is pressed", () => {
      renderHook(() => useKeyboardShortcuts(defaultActions));

      fireKeyDown("ArrowRight");

      expect(onSeekForward).toHaveBeenCalledTimes(1);
    });

    it("prevents default on ArrowRight", () => {
      renderHook(() => useKeyboardShortcuts(defaultActions));

      const event = fireKeyDown("ArrowRight");

      expect(event.defaultPrevented).toBe(true);
    });
  });

  describe("Bracket keys", () => {
    it("calls onPrevSong when [ is pressed", () => {
      renderHook(() => useKeyboardShortcuts(defaultActions));

      fireKeyDown("[");

      expect(onPrevSong).toHaveBeenCalledTimes(1);
    });

    it("prevents default on [", () => {
      renderHook(() => useKeyboardShortcuts(defaultActions));

      const event = fireKeyDown("[");

      expect(event.defaultPrevented).toBe(true);
    });

    it("calls onNextSong when ] is pressed", () => {
      renderHook(() => useKeyboardShortcuts(defaultActions));

      fireKeyDown("]");

      expect(onNextSong).toHaveBeenCalledTimes(1);
    });

    it("prevents default on ]", () => {
      renderHook(() => useKeyboardShortcuts(defaultActions));

      const event = fireKeyDown("]");

      expect(event.defaultPrevented).toBe(true);
    });
  });

  describe("ignored keys", () => {
    it("does not call any action for unbound keys", () => {
      renderHook(() => useKeyboardShortcuts(defaultActions));

      fireKeyDown("a");
      fireKeyDown("Enter");
      fireKeyDown("Escape");

      expect(onTogglePlayback).not.toHaveBeenCalled();
      expect(onSeekBack).not.toHaveBeenCalled();
      expect(onSeekForward).not.toHaveBeenCalled();
      expect(onPrevSong).not.toHaveBeenCalled();
      expect(onNextSong).not.toHaveBeenCalled();
    });

    it("does not prevent default for unbound keys", () => {
      renderHook(() => useKeyboardShortcuts(defaultActions));

      const event = fireKeyDown("a");

      expect(event.defaultPrevented).toBe(false);
    });
  });

  describe("input element exclusion", () => {
    it("ignores Space when focus is on an INPUT element", () => {
      renderHook(() => useKeyboardShortcuts(defaultActions));

      const input = document.createElement("input");
      fireKeyDown(" ", input);

      expect(onTogglePlayback).not.toHaveBeenCalled();
    });

    it("ignores Space when focus is on a TEXTAREA element", () => {
      renderHook(() => useKeyboardShortcuts(defaultActions));

      const textarea = document.createElement("textarea");
      fireKeyDown(" ", textarea);

      expect(onTogglePlayback).not.toHaveBeenCalled();
    });

    it("ignores Space when focus is on a SELECT element", () => {
      renderHook(() => useKeyboardShortcuts(defaultActions));

      const select = document.createElement("select");
      fireKeyDown(" ", select);

      expect(onTogglePlayback).not.toHaveBeenCalled();
    });

    it("ignores Space when focus is on a contentEditable element", () => {
      renderHook(() => useKeyboardShortcuts(defaultActions));

      const div = document.createElement("div");
      div.isContentEditable = true;
      fireKeyDown(" ", div);

      expect(onTogglePlayback).not.toHaveBeenCalled();
    });

    it("ignores ArrowLeft when focus is on an INPUT element", () => {
      renderHook(() => useKeyboardShortcuts(defaultActions));

      const input = document.createElement("input");
      fireKeyDown("ArrowLeft", input);

      expect(onSeekBack).not.toHaveBeenCalled();
    });

    it("ignores [ when focus is on a TEXTAREA element", () => {
      renderHook(() => useKeyboardShortcuts(defaultActions));

      const textarea = document.createElement("textarea");
      fireKeyDown("[", textarea);

      expect(onPrevSong).not.toHaveBeenCalled();
    });
  });

  describe("cleanup", () => {
    it("removes event listener on unmount", () => {
      const { unmount } = renderHook(() => useKeyboardShortcuts(defaultActions));

      unmount();

      fireKeyDown(" ");

      expect(onTogglePlayback).not.toHaveBeenCalled();
    });
  });

  describe("action updates", () => {
    it("uses the latest action callbacks after rerender", () => {
      const newOnToggle = vi.fn();
      const { rerender } = renderHook(
        (actions) => useKeyboardShortcuts(actions),
        { initialProps: defaultActions }
      );

      rerender({
        ...defaultActions,
        onTogglePlayback: newOnToggle,
      });

      fireKeyDown(" ");

      expect(newOnToggle).toHaveBeenCalledTimes(1);
      expect(onTogglePlayback).not.toHaveBeenCalled();
    });
  });
});
