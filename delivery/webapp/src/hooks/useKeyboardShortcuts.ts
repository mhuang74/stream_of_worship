"use client";

import { useEffect, useRef } from "react";

export interface KeyboardShortcutActions {
  onTogglePlayback: () => void;
  /** ArrowLeft — jump to the previous lyric line. */
  onPrevLine: () => void;
  /** ArrowRight — jump to the next lyric line. */
  onNextLine: () => void;
  onPrevSong: () => void;
  onNextSong: () => void;
}

export function useKeyboardShortcuts(actions: KeyboardShortcutActions) {
  const actionsRef = useRef(actions);

  useEffect(() => {
    actionsRef.current = actions;
  });

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement;
      if (
        target.tagName === "INPUT" ||
        target.tagName === "TEXTAREA" ||
        target.tagName === "SELECT" ||
        target.isContentEditable
      ) {
        return;
      }
      if (
        // The scrub bar is a role="slider" widget with its own ArrowLeft/
        // ArrowRight handling (±10s seek, slider semantics). Letting the
        // global handler fire too would seek twice — to two different
        // targets once arrows became lyric-line jumps — so defer to it for
        // arrow keys only (Space toggling playback from the slider is safe).
        (event.key === "ArrowLeft" || event.key === "ArrowRight") &&
        target.closest?.('[role="slider"]')
      ) {
        return;
      }

      const { onTogglePlayback, onPrevLine, onNextLine, onPrevSong, onNextSong } =
        actionsRef.current;

      switch (event.key) {
        case " ":
          event.preventDefault();
          onTogglePlayback();
          break;
        case "ArrowLeft":
          event.preventDefault();
          onPrevLine();
          break;
        case "ArrowRight":
          event.preventDefault();
          onNextLine();
          break;
        case "[":
          event.preventDefault();
          onPrevSong();
          break;
        case "]":
          event.preventDefault();
          onNextSong();
          break;
      }
    };

    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, []);
}
