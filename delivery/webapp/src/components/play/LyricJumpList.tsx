"use client";

import { useState, useRef, useCallback, useEffect, useMemo } from "react";
import { cn } from "@/lib/utils";
import { ChevronDown, Music } from "lucide-react";
import { findCurrentLyricIndex } from "@/lib/render/lrc-parser";
import { useLocale } from "@/hooks/useLocale";
import { LyricsFeedbackRow } from "@/components/audio/LyricsFeedbackRow";

import type { Chapter } from "@/lib/render/chapters";
import { CURSOR_LEAD_SECONDS } from "@/lib/render/line-jump";

// Cursor highlight (worship-arc phase-3 blue, user-picked). Local to this
// sheet — deliberately NOT THEME_PHASE_COLORS, whose pairs are pinned for
// WCAG AA contrast on ThemeLabel badges (light surfaces), not a dark sheet.
const CURSOR_COLORS = { bg: "#bfdbfe", text: "#1e3a8a" };

export interface LyricJumpListProps {
  chapters: Chapter[];
  currentTime: number;
  currentSongIndex: number;
  onJumpToLine: (chapterIndex: number, lineIndex: number) => void;
  /** Controlled open state — owned by the player so it can pin chrome. */
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  /**
   * Content hash of the Recording behind the current chapter, when one is
   * current. Feedback renders only when this is non-null (issue #194:
   * never report against an ambiguous target). The anonymous share-controller
   * variant omits it entirely.
   */
  currentRecordingContentHash?: string | null;
  className?: string;
}

export function LyricJumpList({
  chapters,
  currentTime,
  currentSongIndex,
  onJumpToLine,
  isOpen,
  onOpenChange,
  currentRecordingContentHash,
  className,
}: LyricJumpListProps) {
  const { t } = useLocale();
  const [explicitExpandedChapterIndex, setExplicitExpandedChapterIndex] = useState<number | null>(
    null
  );
  const [contentInteractive, setContentInteractive] = useState(false);
  const contentRef = useRef<HTMLDivElement>(null);

  const chapterLineRefs = useMemo(
    () =>
      chapters.map((chapter) =>
        chapter.lines.map((line) => ({
          text: line.text,
          localTimeSeconds: line.startSeconds,
          globalTimeSeconds: line.startSeconds,
          title: "",
        }))
      ),
    [chapters]
  );
  const activeLineIndex =
    currentSongIndex >= 0 && chapterLineRefs[currentSongIndex]
      ? findCurrentLyricIndex(
          chapterLineRefs[currentSongIndex],
          currentTime + CURSOR_LEAD_SECONDS
        )
      : -1;

  const expandedChapterIndex = explicitExpandedChapterIndex ?? currentSongIndex;

  useEffect(() => {
    if (isOpen) {
      const timer = setTimeout(() => setContentInteractive(true), 350);
      return () => clearTimeout(timer);
    }
  }, [isOpen]);

  // Page-flip auto-scroll: the cursor walks down the visible region untouched;
  // only when the active row leaves it does the sheet scroll — a full page at a
  // time, landing the row one row-height below the top so the just-sung line
  // stays visible as context (instant; no smooth scrolling). Skipped when the
  // user pinned a different chapter: the current chapter's rows aren't rendered
  // then, so the query misses and nothing scrolls.
  useEffect(() => {
    if (!isOpen || activeLineIndex < 0) return;
    const container = contentRef.current;
    if (!container) return;
    const row = container.querySelector<HTMLButtonElement>(
      `[data-lyric-row="${currentSongIndex}-${activeLineIndex}"]`
    );
    if (!row) return;
    const rowTop = row.offsetTop;
    const rowBottom = rowTop + row.offsetHeight;
    const viewTop = container.scrollTop;
    const viewBottom = viewTop + container.clientHeight;
    if (rowBottom <= viewBottom && rowTop >= viewTop) return; // fully visible
    container.scrollTo({
      top: Math.max(0, rowTop - row.offsetHeight),
    });
  }, [isOpen, currentSongIndex, activeLineIndex]);

  // Close paths: in-sheet chip, backdrop tap/Escape, control-bar toggle.
  const closeSheet = useCallback(() => {
    setContentInteractive(false);
    setExplicitExpandedChapterIndex(null);
    onOpenChange(false);
  }, [onOpenChange]);

  const handleChapterExpand = useCallback(
    (chapterIndex: number) => {
      setExplicitExpandedChapterIndex((prev) => {
        const currentExpanded = prev ?? currentSongIndex;
        return chapterIndex === currentExpanded ? null : chapterIndex;
      });
    },
    [currentSongIndex]
  );

  const formatTime = (seconds: number): string => {
    if (!isFinite(seconds) || seconds < 0) return "0:00";
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  // Closed sheet renders nothing — open state lives in the control bar's
  // lyrics toggle (the peek handle and swipe gestures are gone).
  if (!isOpen) return null;

  return (
    <>
      {/* Sheet docked above the control bar: the pinned bar (z-[80]) would
          otherwise cover the sheet's bottom edge, occluding trailing lyric
          rows and the feedback footer. Height budget, not just offset — in
          landscape the scroll area compresses instead of the sheet pushing
          off-screen. --sow-controller-bar-height is measured by
          ControllerPlayer whenever chrome is visible (root default 0px). */}
      <div
        className={cn(
          "fixed left-0 right-0 z-50 flex flex-col",
          "bottom-[var(--sow-controller-bar-height)]",
          "max-h-[calc(100dvh-var(--sow-controller-bar-height))]",
          "bg-black/90 backdrop-blur-sm rounded-t-2xl",
          className
        )}
        data-testid="lyric-jump-sheet"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Floating in-sheet close chip (always visible, out of flow) */}
        <button
          type="button"
          onClick={closeSheet}
          className="absolute top-2 right-2 z-10 size-8 rounded-full bg-white/10 text-white/80 hover:bg-white/20 flex items-center justify-center"
          aria-label={t("lyrics.closeAriaLabel")}
          data-testid="lyric-sheet-close"
        >
          <ChevronDown className="size-4" />
        </button>

        {/* Scrollable content */}
        <div
          ref={contentRef}
          className={cn(
            "relative flex-1 min-h-0 overflow-y-auto",
            !contentInteractive && "pointer-events-none"
          )}
        >
          <div className="p-4 space-y-4">
            {chapters.map((chapter, chapterIndex) => {
              const isCurrentChapter = chapterIndex === currentSongIndex;
              const isExpandedChapter = chapterIndex === expandedChapterIndex;
              const currentLineIndex = isCurrentChapter ? activeLineIndex : -1;

              return (
                <div
                  key={chapterIndex}
                  className={cn(
                    "rounded-lg overflow-hidden",
                    isCurrentChapter ? "bg-white/10" : "bg-white/5"
                  )}
                >
                  {/* Chapter header */}
                  <button
                    className="w-full flex items-center gap-3 p-3 text-left hover:bg-white/5 transition-colors"
                    onClick={() => handleChapterExpand(chapterIndex)}
                    aria-expanded={isExpandedChapter}
                  >
                    <div
                      className={cn(
                        "flex items-center justify-center w-8 h-8 rounded-full shrink-0",
                        isCurrentChapter
                          ? "bg-primary text-primary-foreground"
                          : "bg-white/10 text-white/70"
                      )}
                    >
                      <Music className="size-4" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="font-medium text-white truncate">
                        {chapter.songTitle}
                      </p>
                      <p className="text-xs text-white/50">
                        {formatTime(chapter.startSeconds)} -{" "}
                        {formatTime(chapter.endSeconds)}
                      </p>
                    </div>
                    {isCurrentChapter && (
                      <div className="w-2 h-2 bg-primary rounded-full animate-pulse" />
                    )}
                  </button>

                  {/* Lines */}
                  {isExpandedChapter && chapter.lines.length > 0 && (
                    <div className="px-3 pb-3">
                      <div className="space-y-1">
                        {chapter.lines.map((line, lineIndex) => {
                          const isActive = lineIndex === currentLineIndex;
                          const isPastLine = lineIndex < currentLineIndex;

                          return (
                            <button
                              key={lineIndex}
                              className={cn(
                                "w-full text-left px-3 py-2 rounded transition-all",
                                !isActive &&
                                  (isPastLine
                                    ? "text-white/40"
                                    : "text-white/70 hover:bg-white/5")
                              )}
                              data-active={isActive || undefined}
                              data-lyric-row={`${chapterIndex}-${lineIndex}`}
                              style={
                                isActive
                                  ? {
                                      backgroundColor: CURSOR_COLORS.bg,
                                      color: CURSOR_COLORS.text,
                                    }
                                  : undefined
                              }
                              onClick={() =>
                                onJumpToLine(chapterIndex, lineIndex)
                              }
                            >
                              <p className="text-sm truncate">{line.text}</p>
                              <p className={cn("text-xs", isActive ? "opacity-80" : "text-white/40")}>
                                {formatTime(line.startSeconds)}
                              </p>
                            </button>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Lyrics Feedback footer (issue #194): only when a chapter is
            current, targeting the current chapter's Recording. Chapter
            jump lines come only from parseable synced LRC (see
            generateChaptersManifest), so lines.length > 0 means synced;
            zero lines means no synced Lyrics on this sheet → "none". */}
        {currentRecordingContentHash && chapters[currentSongIndex] && (
          <LyricsFeedbackRow
            recordingContentHash={currentRecordingContentHash}
            situation={
              chapters[currentSongIndex].lines.length > 0 ? "synced" : "none"
            }
            className="shrink-0 border-t border-white/10"
          />
        )}
      </div>

      {/* Backdrop: tap = close the sheet (stopPropagation — must not bubble
          to the player's root tap-toggle underneath the pinned-open sheet) */}
      <div
        className="fixed inset-0 bg-black/50 z-40"
        onClick={(e) => {
          e.stopPropagation();
          closeSheet();
        }}
        role="button"
        tabIndex={0}
        aria-label={t("lyrics.closeAriaLabel")}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === "Escape") {
            closeSheet();
          }
        }}
      />
    </>
  );
}
