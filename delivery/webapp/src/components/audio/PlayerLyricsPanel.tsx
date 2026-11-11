"use client";

import { useMemo } from "react";
import { Loader2 } from "lucide-react";
import { useSongLyrics } from "@/hooks/useSongLyrics";
import { useAudioPlayer } from "@/hooks/useAudioPlayer";
import { LyricsFeedbackRow, type LyricsSituationKind } from "@/components/audio/LyricsFeedbackRow";
import { CURSOR_LEAD_SECONDS, LyricsLinesPanel } from "@/components/audio/LyricsLinesPanel";
import { parseLRC, isValidLRC, findCurrentLyricIndex, type LRCLine } from "@/lib/render/lrc-parser";
import { headingForPositions } from "@/lib/section-labels";
import { useLocale } from "@/hooks/useLocale";

interface PlayerLyricsPanelProps {
  recordingContentHash: string;
}

export function PlayerLyricsPanel({ recordingContentHash }: PlayerLyricsPanelProps) {
  const { lrcContent, lines, sections, loading, error } = useSongLyrics(recordingContentHash);
  const { t } = useLocale();
  const { currentTime, seek } = useAudioPlayer();
  const parsed: LRCLine[] | null = useMemo(
    () => (lrcContent !== null && isValidLRC(lrcContent) ? parseLRC(lrcContent) : null),
    [lrcContent]
  );
  // Section Label headings come from the Recording's own sections, looked up
  // at each line's position in the Recording. Unsynced and raw fallbacks never
  // reach this path: they render `children`, so they stay flat.
  const headings = useMemo(
    () => (parsed ? headingForPositions(sections, parsed.map((line) => line.timeSeconds)) : null),
    [parsed, sections]
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
  const viewLines = useMemo(
    () =>
      globalLines?.map((line, index) => ({
        text: line.text,
        timeSeconds: line.globalTimeSeconds,
        heading: headings?.[index] ?? undefined,
      })) ?? null,
    [globalLines, headings]
  );
  const activeIndex = globalLines
    ? findCurrentLyricIndex(globalLines, currentTime + CURSOR_LEAD_SECONDS)
    : -1;

  let fallback: React.ReactNode;
  if (loading) {
    fallback = (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        {t("audio.lyrics.loading")}
      </div>
    );
  } else if (error) {
    fallback = <p className="text-sm text-muted-foreground">{t("audio.lyrics.unavailable")}</p>;
  } else if (lines !== null && lines.length > 0) {
    fallback = (
      <pre className="text-sm whitespace-pre-wrap break-words">{lines.join("\n")}</pre>
    );
  } else if (lrcContent !== null) {
    fallback = (
      <pre className="text-sm whitespace-pre-wrap break-words">{lrcContent}</pre>
    );
  } else {
    fallback = (
      <p className="text-sm text-muted-foreground">{t("audio.lyrics.noLyrics")}</p>
    );
  }

  // Situation mirrors the fallback chain above; also hidden entirely on
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
      <LyricsLinesPanel
        lines={viewLines}
        activeIndex={activeIndex}
        onLineSelect={(lineIndex) => seek(viewLines![lineIndex].timeSeconds)}
      >
        {fallback}
      </LyricsLinesPanel>
      {situation !== null && (
        <LyricsFeedbackRow recordingContentHash={recordingContentHash} situation={situation} />
      )}
    </div>
  );
}
