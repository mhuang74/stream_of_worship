"use client";

/**
 * Presentational scroll container for a timestamped lyric list: one button per
 * line, live highlight, click-to-seek, and the page-flip auto-scroll. Both the
 * Song Play panel and the Songset audition panel render this — they differ
 * only in how they derive `lines`.
 */

import { useEffect, useRef } from "react";
import { THEME_PHASE_COLORS } from "@/lib/constants";
import { formatTimestamp } from "@/lib/render/lyrics-display";
import { cn } from "@/lib/utils";

// Highlight 0.3s before the line's timestamp so the cursor visibly leads the
// sung line.
export const CURSOR_LEAD_SECONDS = 0.3;

export interface LyricsLineView {
  text: string;
  timeSeconds: number;
}

export interface LyricsLinesPanelProps {
  /** null renders `children` (loading / unsynced / unavailable fallbacks). */
  lines: LyricsLineView[] | null;
  activeIndex: number;
  onLineSelect: (lineIndex: number) => void;
  children?: React.ReactNode;
}

export function LyricsLinesPanel({
  lines,
  activeIndex,
  onLineSelect,
  children,
}: LyricsLinesPanelProps) {
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Page-flip auto-scroll (same rule as LyricJumpList): the cursor walks down
  // the visible region untouched; only when the active row leaves it does the
  // panel scroll — a full page at a time, landing the row one row-height below
  // the top so the just-sung line stays visible (instant; no smooth scrolling).
  useEffect(() => {
    if (activeIndex < 0) return;
    const container = scrollRef.current;
    if (!container) return;
    const row = container.querySelector<HTMLElement>(`[data-lyric-index="${activeIndex}"]`);
    if (!row) return;
    const rowTop = row.offsetTop - container.offsetTop;
    const rowBottom = rowTop + row.offsetHeight;
    const viewTop = container.scrollTop;
    const viewBottom = viewTop + container.clientHeight;
    if (rowBottom <= viewBottom && rowTop >= viewTop) return; // fully visible
    container.scrollTo({
      top: Math.max(0, rowTop - row.offsetHeight),
    });
  }, [activeIndex]);

  return (
    <div ref={scrollRef} className="overflow-y-auto overscroll-y-contain px-3 lg:px-4 py-2">
      {lines === null ? (
        children
      ) : (
        <div className="space-y-1">
          {lines.map((line, i) => {
            const isActive = i === activeIndex;
            return (
              <button
                key={i}
                type="button"
                onClick={() => onLineSelect(i)}
                data-lyric-index={i}
                data-active={isActive || undefined}
                className={cn(
                  "flex flex-col w-full text-left px-2 py-1 rounded-md",
                  "md:flex-row md:items-baseline md:gap-2",
                  isActive && "font-medium",
                  !isActive && "hover:bg-accent"
                )}
                style={
                  isActive
                    ? {
                        backgroundColor: THEME_PHASE_COLORS[1].bg,
                        color: THEME_PHASE_COLORS[1].text,
                      }
                    : undefined
                }
              >
                <span
                  className={cn(
                    "font-mono text-xs block md:w-16 md:shrink-0",
                    isActive ? "opacity-80" : "text-muted-foreground"
                  )}
                >
                  {formatTimestamp(line.timeSeconds)}
                </span>
                <span className="text-sm break-words block">{line.text}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
