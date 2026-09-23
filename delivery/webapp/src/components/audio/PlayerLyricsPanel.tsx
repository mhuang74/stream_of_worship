"use client";

import { useEffect, useMemo, useRef } from "react";
import { Loader2 } from "lucide-react";
import { useSongLyrics } from "@/hooks/useSongLyrics";
import { useAudioPlayer } from "@/hooks/useAudioPlayer";
import { LyricsFeedbackRow, type LyricsSituationKind } from "@/components/audio/LyricsFeedbackRow";
import { parseLRC, isValidLRC, findCurrentLyricIndex, type LRCLine } from "@/lib/render/lrc-parser";
import { THEME_PHASE_COLORS } from "@/lib/constants";
import { formatTimestamp } from "@/lib/render/lyrics-display";
import { useLocale } from "@/hooks/useLocale";
import { cn } from "@/lib/utils";

interface PlayerLyricsPanelProps {
  recordingContentHash: string;
}

// Highlight 0.3s before the line's timestamp so the cursor visibly leads the
// sung line.
const CURSOR_LEAD_SECONDS = 0.3;

export function PlayerLyricsPanel({ recordingContentHash }: PlayerLyricsPanelProps) {
  const { lrcContent, lines, loading, error } = useSongLyrics(recordingContentHash);
  const { t } = useLocale();
  const { currentTime, seek } = useAudioPlayer();
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const parsed: LRCLine[] | null = useMemo(
    () => (lrcContent !== null && isValidLRC(lrcContent) ? parseLRC(lrcContent) : null),
    [lrcContent]
  );
  const globalLines = useMemo(
    () =>
      parsed?.map((line) => ({
        text: line.text,
        localTimeSeconds: line.timeSeconds,
        globalTimeSeconds: line.timeSeconds,
        title: "",
      })) ?? null,
    [parsed]
  );
  const activeIndex = globalLines
    ? findCurrentLyricIndex(globalLines, currentTime + CURSOR_LEAD_SECONDS)
    : -1;

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

  let content: React.ReactNode;
  if (loading) {
    content = (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        {t("audio.lyrics.loading")}
      </div>
    );
  } else if (error) {
    content = <p className="text-sm text-muted-foreground">{t("audio.lyrics.unavailable")}</p>;
  } else if (parsed !== null) {
    content = (
      <div className="space-y-1">
        {parsed.map((line, i) => {
          const isActive = i === activeIndex;
          return (
            <button
              key={i}
              type="button"
              onClick={() => seek(line.timeSeconds)}
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
    );
  } else if (lines !== null && lines.length > 0) {
    content = (
      <pre className="text-sm whitespace-pre-wrap break-words">{lines.join("\n")}</pre>
    );
  } else if (lrcContent !== null) {
    content = (
      <pre className="text-sm whitespace-pre-wrap break-words">{lrcContent}</pre>
    );
  } else {
    content = (
      <p className="text-sm text-muted-foreground">{t("audio.lyrics.noLyrics")}</p>
    );
  }

  // Situation mirrors the content chain above; also hidden entirely on
  // loading/error states — nothing on screen to give feedback about.
  const situation: LyricsSituationKind | null = loading || error
    ? null
    : lrcContent !== null && isValidLRC(lrcContent)
      ? "synced"
      : lines !== null && lines.length > 0
        ? "unsynced"
        : lrcContent !== null
          ? "unsynced"
          : "none";
  return (
    <div className="flex flex-col max-h-[40dvh] md:max-h-[400px]">
      <div ref={scrollRef} className="overflow-y-auto overscroll-y-contain px-3 lg:px-4 py-2">
        {content}
      </div>
      {situation !== null && (
        <LyricsFeedbackRow recordingContentHash={recordingContentHash} situation={situation} />
      )}
    </div>
  );
}
