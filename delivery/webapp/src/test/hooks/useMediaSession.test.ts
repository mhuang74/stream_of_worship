import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useMediaSession } from "@/hooks/useMediaSession";

describe("useMediaSession", () => {
  let setActionHandlerSpy: ReturnType<typeof vi.fn>;
  let metadataSpy: MediaMetadata | null;

  beforeEach(() => {
    vi.clearAllMocks();
    setActionHandlerSpy = vi.fn();
    metadataSpy = null;

    // Mock MediaMetadata constructor (not available in jsdom)
    if (typeof MediaMetadata === "undefined") {
      (globalThis as unknown as { MediaMetadata: typeof MediaMetadata }).MediaMetadata =
        class MockMediaMetadata {
          title: string;
          artist: string;
          album: string;
          artwork: MediaImage[];
          constructor(init: MediaMetadataInit) {
            this.title = init.title || "";
            this.artist = init.artist || "";
            this.album = init.album || "";
            this.artwork = init.artwork || [];
          }
        } as unknown as typeof MediaMetadata;
    }

    Object.defineProperty(navigator, "mediaSession", {
      value: {
        setActionHandler: setActionHandlerSpy,
        get metadata() {
          return metadataSpy;
        },
        set metadata(m: MediaMetadata | null) {
          metadataSpy = m;
        },
        playbackState: "none",
        setPositionState: vi.fn(),
      },
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    Object.defineProperty(navigator, "mediaSession", {
      value: undefined,
      writable: true,
      configurable: true,
    });
    vi.restoreAllMocks();
  });

  const defaultMetadata = {
    title: "Amazing Grace",
    artist: "Stream of Worship",
    album: "Worship Set",
  };

  const defaultActions = {
    onPlay: vi.fn(),
    onPause: vi.fn(),
    onPrevSong: vi.fn(),
    onNextSong: vi.fn(),
    onSeekBack: vi.fn(),
    onSeekForward: vi.fn(),
  };

  describe("when Media Session API is not available", () => {
    it("does not throw when mediaSession is undefined", () => {
      Object.defineProperty(navigator, "mediaSession", {
        value: undefined,
        writable: true,
        configurable: true,
      });

      expect(() => {
        renderHook(() => useMediaSession(defaultMetadata, defaultActions));
      }).not.toThrow();
    });

    it("updatePlaybackState does not throw", () => {
      Object.defineProperty(navigator, "mediaSession", {
        value: undefined,
        writable: true,
        configurable: true,
      });

      const { result } = renderHook(() =>
        useMediaSession(defaultMetadata, defaultActions)
      );

      expect(() => {
        result.current.updatePlaybackState("playing");
      }).not.toThrow();
    });

    it("updatePositionState does not throw", () => {
      Object.defineProperty(navigator, "mediaSession", {
        value: undefined,
        writable: true,
        configurable: true,
      });

      const { result } = renderHook(() =>
        useMediaSession(defaultMetadata, defaultActions)
      );

      expect(() => {
        result.current.updatePositionState({ duration: 100, position: 50 });
      }).not.toThrow();
    });
  });

  describe("metadata", () => {
    it("sets media session metadata", () => {
      renderHook(() => useMediaSession(defaultMetadata, defaultActions));

      expect(metadataSpy).not.toBeNull();
      expect(metadataSpy!.title).toBe("Amazing Grace");
      expect(metadataSpy!.artist).toBe("Stream of Worship");
      expect(metadataSpy!.album).toBe("Worship Set");
    });

    it("does not set metadata when null", () => {
      renderHook(() => useMediaSession(null, defaultActions));

      expect(metadataSpy).toBeNull();
    });

    it("updates metadata when it changes", () => {
      const { rerender } = renderHook(
        ({ metadata }) => useMediaSession(metadata, defaultActions),
        { initialProps: { metadata: defaultMetadata } }
      );

      rerender({
        metadata: {
          title: "How Great Thou Art",
          artist: "Stream of Worship",
          album: "Worship Set",
        },
      });

      expect(metadataSpy!.title).toBe("How Great Thou Art");
    });

    it("handles metadata without optional fields", () => {
      renderHook(() =>
        useMediaSession({ title: "Test Song" }, defaultActions)
      );

      expect(metadataSpy).not.toBeNull();
      expect(metadataSpy!.title).toBe("Test Song");
      expect(metadataSpy!.artist).toBe("");
      expect(metadataSpy!.album).toBe("");
    });

    it("handles metadata with artwork", () => {
      const artwork = [{ src: "https://example.com/art.jpg", sizes: "512x512" }];
      renderHook(() =>
        useMediaSession({ title: "Test", artwork }, defaultActions)
      );

      expect(metadataSpy).not.toBeNull();
    });
  });

  describe("action handlers", () => {
    it("registers play action handler", () => {
      renderHook(() => useMediaSession(defaultMetadata, defaultActions));

      expect(setActionHandlerSpy).toHaveBeenCalledWith(
        "play",
        expect.any(Function)
      );
    });

    it("registers pause action handler", () => {
      renderHook(() => useMediaSession(defaultMetadata, defaultActions));

      expect(setActionHandlerSpy).toHaveBeenCalledWith(
        "pause",
        expect.any(Function)
      );
    });

    it("registers previoustrack action handler", () => {
      renderHook(() => useMediaSession(defaultMetadata, defaultActions));

      expect(setActionHandlerSpy).toHaveBeenCalledWith(
        "previoustrack",
        expect.any(Function)
      );
    });

    it("registers nexttrack action handler", () => {
      renderHook(() => useMediaSession(defaultMetadata, defaultActions));

      expect(setActionHandlerSpy).toHaveBeenCalledWith(
        "nexttrack",
        expect.any(Function)
      );
    });

    it("registers seekbackward action handler", () => {
      renderHook(() => useMediaSession(defaultMetadata, defaultActions));

      expect(setActionHandlerSpy).toHaveBeenCalledWith(
        "seekbackward",
        expect.any(Function)
      );
    });

    it("registers seekforward action handler", () => {
      renderHook(() => useMediaSession(defaultMetadata, defaultActions));

      expect(setActionHandlerSpy).toHaveBeenCalledWith(
        "seekforward",
        expect.any(Function)
      );
    });

    it("sets handler to null when action is not provided", () => {
      renderHook(() =>
        useMediaSession(defaultMetadata, { onPlay: vi.fn() })
      );

      expect(setActionHandlerSpy).toHaveBeenCalledWith("pause", null);
      expect(setActionHandlerSpy).toHaveBeenCalledWith("previoustrack", null);
      expect(setActionHandlerSpy).toHaveBeenCalledWith("nexttrack", null);
      expect(setActionHandlerSpy).toHaveBeenCalledWith("seekbackward", null);
      expect(setActionHandlerSpy).toHaveBeenCalledWith("seekforward", null);
    });

    it("cleans up all handlers on unmount", () => {
      const { unmount } = renderHook(() =>
        useMediaSession(defaultMetadata, defaultActions)
      );

      const cleanupCalls = setActionHandlerSpy.mock.calls.length;

      unmount();

      const cleanupHandlerCalls = setActionHandlerSpy.mock.calls.slice(cleanupCalls);
      expect(cleanupHandlerCalls.length).toBe(6);
      const actionTypes = cleanupHandlerCalls.map((call: unknown[]) => call[0]);
      expect(actionTypes).toContain("play");
      expect(actionTypes).toContain("pause");
      expect(actionTypes).toContain("previoustrack");
      expect(actionTypes).toContain("nexttrack");
      expect(actionTypes).toContain("seekbackward");
      expect(actionTypes).toContain("seekforward");

      cleanupHandlerCalls.forEach((call: unknown[]) => {
        expect(call[1]).toBeNull();
      });
    });
  });

  describe("updatePlaybackState", () => {
    it("sets playbackState to playing", () => {
      const { result } = renderHook(() =>
        useMediaSession(defaultMetadata, defaultActions)
      );

      act(() => {
        result.current.updatePlaybackState("playing");
      });

      expect(navigator.mediaSession.playbackState).toBe("playing");
    });

    it("sets playbackState to paused", () => {
      const { result } = renderHook(() =>
        useMediaSession(defaultMetadata, defaultActions)
      );

      act(() => {
        result.current.updatePlaybackState("paused");
      });

      expect(navigator.mediaSession.playbackState).toBe("paused");
    });
  });

  describe("updatePositionState", () => {
    it("calls setPositionState with provided options", () => {
      const { result } = renderHook(() =>
        useMediaSession(defaultMetadata, defaultActions)
      );

      act(() => {
        result.current.updatePositionState({
          duration: 300,
          position: 150,
          playbackRate: 1,
        });
      });

      expect(navigator.mediaSession.setPositionState).toHaveBeenCalledWith({
        duration: 300,
        position: 150,
        playbackRate: 1,
      });
    });

    it("handles setPositionState errors gracefully", () => {
      navigator.mediaSession.setPositionState = vi.fn(() => {
        throw new Error("Invalid state");
      });

      const { result } = renderHook(() =>
        useMediaSession(defaultMetadata, defaultActions)
      );

      expect(() => {
        act(() => {
          result.current.updatePositionState({
            duration: -1,
            position: 0,
          });
        });
      }).not.toThrow();
    });
  });

  describe("action callback stability", () => {
    it("uses latest callback after rerender via ref pattern", () => {
      const firstOnPlay = vi.fn();
      const secondOnPlay = vi.fn();

      const { rerender } = renderHook(
        ({ onPlay }) => useMediaSession(defaultMetadata, { onPlay }),
        { initialProps: { onPlay: firstOnPlay } }
      );

      rerender({ onPlay: secondOnPlay });

      const playHandler = setActionHandlerSpy.mock.calls.find(
        (call: unknown[]) => call[0] === "play"
      );
      expect(playHandler).toBeDefined();
      const handler = playHandler![1] as () => void;

      handler();

      expect(secondOnPlay).toHaveBeenCalled();
      expect(firstOnPlay).not.toHaveBeenCalled();
    });
  });
});
