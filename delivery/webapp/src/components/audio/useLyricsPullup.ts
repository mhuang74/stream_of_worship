"use client";

/**
 * Lyrics pullup state machine shared by both bottom bars (Song Play and the
 * Songset audition): expand/collapse, keep-mounted-through-collapse, the `L`
 * shortcut, and the automatic collapse on route/sheet/dialog changes.
 *
 * `available` is "there are lyrics to show for what is currently playing"; the
 * keydown listener reads it through a ref so it never re-attaches.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { usePathname } from "next/navigation";

export interface LyricsPullupOptions {
  available: boolean;
  /** Element id of the panel the toggle controls (`aria-controls`). */
  panelId: string;
}

export interface LyricsPullup {
  showLyrics: boolean;
  /** True from the first expand until the collapse transition ends. */
  isMounted: boolean;
  toggle: () => void;
  handleTransitionEnd: () => void;
  panelId: string;
}

export function useLyricsPullup({ available, panelId }: LyricsPullupOptions): LyricsPullup {
  const [showLyrics, setShowLyrics] = useState(false);
  const [isMounted, setIsMounted] = useState(false);
  const pathname = usePathname();

  // Ref to read availability without re-attaching the keydown listener
  const availableRef = useRef(available);
  useEffect(() => {
    availableRef.current = available;
  }, [available]);

  // Auto-collapse when nothing playable has lyrics
  useEffect(() => {
    if (!available) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setShowLyrics(false);
    }
  }, [available]);

  // Mount content when expanding; keep mounted during collapse transition
  useEffect(() => {
    if (showLyrics && available) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setIsMounted(true);
    }
  }, [showLyrics, available]);

  // Unmount content after collapse transition completes
  const handleTransitionEnd = () => {
    if (!showLyrics) {
      setIsMounted(false);
    }
  };

  const toggle = useCallback(() => {
    setShowLyrics((previous) => !previous);
  }, []);

  // Keyboard shortcut ('L') — modal-aware, stable listener, no-op preventDefault
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (!availableRef.current) return;

      const target = e.target as HTMLElement;
      if (
        target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.isContentEditable
      ) {
        return;
      }

      if (
        document.querySelector('[role="dialog"]') ||
        document.querySelector('[data-slot="sheet"]')
      ) {
        return;
      }

      if (e.key === "l" || e.key === "L") {
        e.preventDefault();
        setShowLyrics((previous) => !previous);
      }
    };

    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, []);

  // Auto-collapse on route change
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setShowLyrics(false);
  }, [pathname]);

  // Auto-collapse on modal/sheet open. React only when a modal element is
  // actually inserted: re-querying the whole document on every subtree
  // mutation collapsed the panel on unrelated updates (feedback row clicks,
  // toasts) whenever any stale/closing [role=dialog] was present, and made
  // the lyrics-toggle click race die during sheet close animations.
  useEffect(() => {
    const observer = new MutationObserver((muts) => {
      const modalAdded = muts.some((m) =>
        [...m.addedNodes].some(
          (n) =>
            n.nodeType === 1 &&
            ((n as Element).matches?.('[role="dialog"]') ||
              (n as Element).querySelector?.('[role="dialog"]'))
        )
      );
      if (modalAdded) {
        setShowLyrics(false);
      }
    });

    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, []);

  return { showLyrics, isMounted, toggle, handleTransitionEnd, panelId };
}
