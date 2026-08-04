"use client";

import { Loader2 } from "lucide-react";
import { useSongLyrics } from "@/hooks/useSongLyrics";
import { parseLRC, isValidLRC, type LRCLine } from "@/lib/render/lrc-parser";
import { formatTimestamp } from "@/lib/render/lyrics-display";

interface PlayerLyricsPanelProps {
  recordingContentHash: string;
}

export function PlayerLyricsPanel({ recordingContentHash }: PlayerLyricsPanelProps) {
  const { lrcContent, lines, loading, error } = useSongLyrics(recordingContentHash);

  let content: React.ReactNode;
  if (loading) {
    content = (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Loading lyrics…
      </div>
    );
  } else if (error) {
    content = <p className="text-sm text-muted-foreground">Lyrics unavailable</p>;
  } else if (lrcContent !== null && isValidLRC(lrcContent)) {
    const parsed: LRCLine[] = parseLRC(lrcContent);
    content = (
      <div className="space-y-1">
        {parsed.map((line, i) => (
          <div key={i} className="flex flex-col md:flex-row md:items-baseline md:gap-2">
            <span className="font-mono text-xs text-muted-foreground block md:w-16 md:shrink-0">
              {formatTimestamp(line.timeSeconds)}
            </span>
            <span className="text-sm break-words block">{line.text}</span>
          </div>
        ))}
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
      <p className="text-sm text-muted-foreground">No lyrics available for this recording.</p>
    );
  }

  return (
    <div className="max-h-[40dvh] md:max-h-[400px] overflow-y-auto overscroll-y-contain px-3 lg:px-4 py-2">
      {content}
    </div>
  );
}
