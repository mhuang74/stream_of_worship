"use client";

import { useState, useCallback } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { LRCLine } from "@/lib/render/lrc-parser";
import { cn } from "@/lib/utils";
import { Loader2, Save } from "lucide-react";
import { toast } from "sonner";

function secondsToLrcTimestamp(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = (seconds % 60).toFixed(2).padStart(5, "0");
  return `${mins.toString().padStart(2, "0")}:${secs}`;
}

function lrcTimestampToSeconds(ts: string): number | null {
  const match = /^(\d{1,2}):(\d{2}\.\d{2,3})$/.exec(ts.trim());
  if (!match) return null;
  const mins = parseInt(match[1], 10);
  const secs = parseFloat(match[2]);
  return mins * 60 + secs;
}

function buildLrc(lines: LRCLine[]): string {
  return lines
    .slice()
    .sort((a, b) => a.timeSeconds - b.timeSeconds)
    .map((line) => `[${secondsToLrcTimestamp(line.timeSeconds)}]${line.text}`)
    .join("\n");
}

interface LyricsTimingEditorProps {
  recordingContentHash: string;
  lines: LRCLine[];
  originalLrc: string;
  onSave: (newLrc: string) => void;
}

export function LyricsTimingEditor({
  recordingContentHash,
  lines,
  originalLrc,
  onSave,
}: LyricsTimingEditorProps) {
  const [editedLines, setEditedLines] = useState<LRCLine[]>(() => lines.map((l) => ({ ...l })));
  const [timestampInputs, setTimestampInputs] = useState<string[]>(() =>
    lines.map((l) => secondsToLrcTimestamp(l.timeSeconds))
  );
  const [isSaving, setIsSaving] = useState(false);
  const [invalidRows, setInvalidRows] = useState<Set<number>>(new Set());

  const handleTimestampChange = useCallback((idx: number, value: string) => {
    setTimestampInputs((prev) => {
      const next = [...prev];
      next[idx] = value;
      return next;
    });

    const secs = lrcTimestampToSeconds(value);
    if (secs !== null) {
      setEditedLines((prev) => {
        const next = prev.map((l) => ({ ...l }));
        next[idx] = { ...next[idx], timeSeconds: secs };
        return next;
      });
      setInvalidRows((prev) => {
        const next = new Set(prev);
        next.delete(idx);
        return next;
      });
    } else {
      setInvalidRows((prev) => new Set(prev).add(idx));
    }
  }, []);

  const handleSave = async () => {
    if (invalidRows.size > 0) {
      toast.error("Fix invalid timestamps before saving");
      return;
    }
    const newLrc = buildLrc(editedLines);
    setIsSaving(true);
    try {
      const res = await fetch("/api/lyrics/overrides", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recordingContentHash, lrcContent: newLrc }),
      });
      if (!res.ok) throw new Error("Failed to save");
      onSave(newLrc);
      toast.success("Timing saved");
    } catch {
      toast.error("Failed to save timing");
    } finally {
      setIsSaving(false);
    }
  };

  const isDirty = buildLrc(editedLines) !== buildLrc(lines);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">Edit timestamps (mm:ss.xx format)</p>
        <Button
          size="sm"
          onClick={handleSave}
          disabled={!isDirty || isSaving || invalidRows.size > 0}
          aria-label="Save timing"
        >
          {isSaving ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Save className="size-4" />
          )}
          Save
        </Button>
      </div>
      <div className="space-y-1">
        {editedLines.map((line, idx) => (
          <div key={idx} className="flex items-center gap-3">
            <Input
              value={timestampInputs[idx]}
              onChange={(e) => handleTimestampChange(idx, e.target.value)}
              className={cn(
                "font-mono text-xs w-24 shrink-0",
                invalidRows.has(idx) && "border-destructive"
              )}
              aria-label={`Timestamp for line ${idx + 1}`}
              aria-invalid={invalidRows.has(idx)}
            />
            <span className="flex-1 text-sm truncate">{line.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export { secondsToLrcTimestamp, lrcTimestampToSeconds, buildLrc };
