"use client";

/**
 * Lyrics pullup for the Songset audition.
 *
 * The Recording's LRC is mapped onto the Songset timeline through the Item's
 * arranged clips, so only the lines the cuts actually play are shown — and each
 * row seeks to its timeline position, not its position in the Recording. The
 * bar remounts this panel when the current Item changes, which resets the
 * scroll and the LRC fetch.
 */

import { useMemo } from "react";
import { Loader2 } from "lucide-react";
import { useSongLyrics } from "@/hooks/useSongLyrics";
import { useLocale } from "@/hooks/useLocale";
import { CURSOR_LEAD_SECONDS, LyricsLinesPanel } from "@/components/audio/LyricsLinesPanel";
import { parseLRC, isValidLRC, findCurrentLyricIndex } from "@/lib/render/lrc-parser";
import { toSongsetLyricLines } from "@/lib/arrangements-listen";
import type { RecordingSection, TimelineClip } from "@/lib/arrangements";
import { headingForPositions } from "@/lib/section-labels";

export interface SongsetLyricsPanelProps {
  contentHash: string | null;
  clips: TimelineClip[];
  /** The Recording's detected sections; headings are looked up through them. */
  sections: RecordingSection[] | null;
  /** Current Songset timeline position, in seconds. */
  positionSeconds: number;
  onSeek: (timelineSeconds: number) => void;
}

export function SongsetLyricsPanel({
  contentHash,
  clips,
  sections,
  positionSeconds,
  onSeek,
}: SongsetLyricsPanelProps) {
  const { lrcContent, lines, loading, error } = useSongLyrics(contentHash ?? undefined);
  const { t } = useLocale();

  const parsed = useMemo(
    () => (lrcContent !== null && isValidLRC(lrcContent) ? parseLRC(lrcContent) : null),
    [lrcContent],
  );
  const songsetLines = useMemo(
    () => (parsed ? toSongsetLyricLines(clips, parsed) : null),
    [clips, parsed],
  );
  const activeIndex = songsetLines
    ? findCurrentLyricIndex(songsetLines, positionSeconds + CURSOR_LEAD_SECONDS)
    : -1;
  // Headings are looked up from each line's position in the Recording (its
  // source time), never from its timeline position — the arranged cuts move
  // timeline positions, not the section a line was sung in.
  const headings = useMemo(
    () =>
      songsetLines
        ? headingForPositions(
            sections,
            songsetLines.map((line) => line.localTimeSeconds),
          )
        : null,
    [sections, songsetLines],
  );

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
    fallback = <pre className="text-sm whitespace-pre-wrap break-words">{lines.join("\n")}</pre>;
  } else if (lrcContent !== null) {
    fallback = <pre className="text-sm whitespace-pre-wrap break-words">{lrcContent}</pre>;
  } else {
    fallback = <p className="text-sm text-muted-foreground">{t("audio.lyrics.noLyrics")}</p>;
  }

  return (
    <div className="flex flex-col max-h-[40dvh] md:max-h-[400px]">
      <LyricsLinesPanel
        lines={
          songsetLines?.map((line, index) => ({
            text: line.text,
            timeSeconds: line.timelineSeconds,
            heading: headings?.[index] ?? undefined,
          })) ?? null
        }
        activeIndex={activeIndex}
        onLineSelect={(lineIndex) => onSeek(songsetLines![lineIndex].timelineSeconds)}
      >
        {fallback}
      </LyricsLinesPanel>
    </div>
  );
}
