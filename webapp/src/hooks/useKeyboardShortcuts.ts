"use client";

import { useEffect, useRef } from "react";

export interface KeyboardShortcutActions {
  onTogglePlayback: () => void;
  onSeekBack: () => void;
  onSeekForward: () => void;
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

      const { onTogglePlayback, onSeekBack, onSeekForward, onPrevSong, onNextSong } =
        actionsRef.current;

      switch (event.key) {
        case " ":
          event.preventDefault();
          onTogglePlayback();
          break;
        case "ArrowLeft":
          event.preventDefault();
          onSeekBack();
          break;
        case "ArrowRight":
          event.preventDefault();
          onSeekForward();
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
