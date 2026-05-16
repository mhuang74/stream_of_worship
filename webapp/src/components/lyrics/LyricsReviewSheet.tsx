"use client";

import { useState, useCallback, useEffect } from "react";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { parseLRC, LRCLine } from "@/lib/render/lrc-parser";
import { cn } from "@/lib/utils";
import { AlertCircle } from "lucide-react";
import { LyricsEditor } from "./LyricsEditor";
import { LyricsTimingEditor } from "./LyricsTimingEditor";

function formatTime(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = (seconds % 60).toFixed(2).padStart(5, "0");
  return `${mins.toString().padStart(2, "0")}:${secs}`;
}

interface LyricsReviewSheetProps {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  recordingContentHash: string;
  lrcContent: string;
  songTitle?: string;
}

type TabType = "review" | "edit-text" | "edit-timing";

const TABS: { id: TabType; label: string }[] = [
  { id: "review", label: "Review" },
  { id: "edit-text", label: "Edit Text" },
  { id: "edit-timing", label: "Edit Timing" },
];

export function LyricsReviewSheet({
  isOpen,
  onOpenChange,
  recordingContentHash,
  lrcContent,
  songTitle,
}: LyricsReviewSheetProps) {
  const [activeTab, setActiveTab] = useState<TabType>("review");
  const [lines, setLines] = useState<LRCLine[]>([]);
  const [markedTimestamps, setMarkedTimestamps] = useState<Set<number>>(new Set());
  const [isLoadingMarks, setIsLoadingMarks] = useState(false);
  const [currentLrc, setCurrentLrc] = useState(lrcContent);

  useEffect(() => {
    setLines(parseLRC(currentLrc));
  }, [currentLrc]);

  useEffect(() => {
    if (!isOpen || !recordingContentHash) return;

    setIsLoadingMarks(true);
    fetch(
      `/api/lyrics/marks?recordingContentHash=${encodeURIComponent(recordingContentHash)}`
    )
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.marks) {
          setMarkedTimestamps(new Set(data.marks));
        }
      })
      .catch(console.error)
      .finally(() => setIsLoadingMarks(false));
  }, [isOpen, recordingContentHash]);

  const toggleMark = useCallback(
    async (timestampSeconds: number) => {
      const isMarked = markedTimestamps.has(timestampSeconds);

      setMarkedTimestamps((prev) => {
        const next = new Set(prev);
        if (isMarked) {
          next.delete(timestampSeconds);
        } else {
          next.add(timestampSeconds);
        }
        return next;
      });

      try {
        if (isMarked) {
          await fetch(
            `/api/lyrics/marks?recordingContentHash=${encodeURIComponent(recordingContentHash)}&timestampSeconds=${timestampSeconds}`,
            { method: "DELETE" }
          );
        } else {
          await fetch("/api/lyrics/marks", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ recordingContentHash, timestampSeconds }),
          });
        }
      } catch {
        // Revert optimistic update on failure
        setMarkedTimestamps((prev) => {
          const next = new Set(prev);
          if (isMarked) {
            next.add(timestampSeconds);
          } else {
            next.delete(timestampSeconds);
          }
          return next;
        });
      }
    },
    [markedTimestamps, recordingContentHash]
  );

  const handleLrcSave = useCallback((newLrc: string) => {
    setCurrentLrc(newLrc);
  }, []);

  const hasMarks = markedTimestamps.size > 0;

  return (
    <Sheet open={isOpen} onOpenChange={onOpenChange}>
      <SheetContent side="bottom" className="h-[85vh] flex flex-col gap-0 p-0">
        <SheetHeader className="px-4 pt-4 pb-3">
          <SheetTitle>Lyrics Review</SheetTitle>
          {songTitle && <SheetDescription>{songTitle}</SheetDescription>}
        </SheetHeader>

        {/* Desktop tabs - hidden on mobile */}
        <div className="hidden lg:flex border-b px-4 gap-1" role="tablist">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              role="tab"
              aria-selected={activeTab === tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={cn(
                "px-4 py-2 text-sm font-medium border-b-2 transition-colors",
                activeTab === tab.id
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              )}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* Content area */}
        <div className="flex-1 overflow-y-auto px-4 py-2">
          {/* Review tab - always visible on mobile, tab-controlled on desktop */}
          <div
            role="tabpanel"
            aria-label="Review"
            className={cn(activeTab !== "review" && "lg:hidden")}
          >
            {isLoadingMarks ? (
              <div className="flex items-center justify-center py-8 text-muted-foreground text-sm">
                Loading marks...
              </div>
            ) : lines.length === 0 ? (
              <div className="flex items-center justify-center py-8 text-muted-foreground text-sm">
                No lyrics found
              </div>
            ) : (
              <div className="space-y-1">
                {lines.map((line, idx) => {
                  const isMarked = markedTimestamps.has(line.timeSeconds);
                  return (
                    <button
                      key={idx}
                      onClick={() => toggleMark(line.timeSeconds)}
                      className={cn(
                        "w-full flex items-center gap-3 px-3 py-2 rounded-md text-left transition-colors",
                        isMarked
                          ? "bg-destructive/10 text-destructive hover:bg-destructive/20"
                          : "hover:bg-muted"
                      )}
                      aria-pressed={isMarked}
                      aria-label={`Line at ${formatTime(line.timeSeconds)}: ${line.text}${isMarked ? " (marked as problem)" : ""}`}
                    >
                      <span className="text-xs text-muted-foreground font-mono shrink-0 w-16">
                        {formatTime(line.timeSeconds)}
                      </span>
                      <span className="flex-1 text-sm">{line.text}</span>
                      {isMarked && (
                        <AlertCircle
                          className="size-4 text-destructive shrink-0"
                          aria-hidden="true"
                        />
                      )}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* Edit text tab - desktop only */}
          <div
            role="tabpanel"
            aria-label="Edit Text"
            className={cn("hidden", activeTab === "edit-text" && "lg:block")}
          >
            <LyricsEditor
              recordingContentHash={recordingContentHash}
              lrcContent={currentLrc}
              onSave={handleLrcSave}
            />
          </div>

          {/* Edit timing tab - desktop only */}
          <div
            role="tabpanel"
            aria-label="Edit Timing"
            className={cn("hidden", activeTab === "edit-timing" && "lg:block")}
          >
            <LyricsTimingEditor
              recordingContentHash={recordingContentHash}
              lines={lines}
              originalLrc={currentLrc}
              onSave={handleLrcSave}
            />
          </div>
        </div>

        {/* Footer: "Open on desktop to fix" when marks exist on mobile */}
        {hasMarks && (
          <div className="lg:hidden px-4 py-3 border-t bg-muted/50">
            <p className="text-sm text-muted-foreground text-center">
              Open on desktop to fix {markedTimestamps.size} marked line
              {markedTimestamps.size !== 1 ? "s" : ""}
            </p>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
