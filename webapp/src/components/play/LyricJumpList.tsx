"use client";

import { useState, useRef, useCallback } from "react";
import { cn } from "@/lib/utils";
import { ChevronUp, Music } from "lucide-react";
import type { Chapter } from "@/lib/render/chapters";

export type { Chapter, ChapterLine } from "@/lib/render/chapters";

export interface LyricJumpListProps {
  chapters: Chapter[];
  currentTime: number;
  currentSongIndex: number;
  onJumpToChapter: (index: number) => void;
  onJumpToLine: (chapterIndex: number, lineIndex: number) => void;
  className?: string;
}

export function LyricJumpList({
  chapters,
  currentTime,
  currentSongIndex,
  onJumpToChapter,
  onJumpToLine,
  className,
}: LyricJumpListProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [isDragging, setIsDragging] = useState(false);
  const [startY, setStartY] = useState(0);
  const [currentY, setCurrentY] = useState(0);
  const sheetRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const lastToggleTimeRef = useRef(0);

  const handleTouchStart = useCallback(
    (e: React.TouchEvent | React.MouseEvent) => {
      e.stopPropagation();
      const clientY =
        "touches" in e ? e.touches[0].clientY : (e as React.MouseEvent).clientY;
      setStartY(clientY);
      setIsDragging(true);
    },
    []
  );

  const handleTouchMove = useCallback(
    (e: React.TouchEvent | React.MouseEvent) => {
      if (!isDragging) return;
      e.stopPropagation();

      const clientY =
        "touches" in e ? e.touches[0].clientY : (e as React.MouseEvent).clientY;
      const deltaY = startY - clientY;

      if (!isOpen && deltaY > 0) {
        setCurrentY(Math.min(deltaY, 300));
      } else if (isOpen && deltaY < 0) {
        setCurrentY(Math.max(deltaY, -300));
      }
    },
    [isDragging, startY, isOpen]
  );

  const handleTouchEnd = useCallback(
    (e: React.TouchEvent | React.MouseEvent) => {
      if (!isDragging) return;
      e.stopPropagation();

      const now = Date.now();
      const threshold = 100;
      const absY = Math.abs(currentY);

      const shouldToggle =
        (currentY > threshold || absY < 30) && now - lastToggleTimeRef.current > 100;

      if (!isOpen && shouldToggle) {
        setIsOpen(true);
        lastToggleTimeRef.current = now;
      } else if (isOpen && shouldToggle) {
        setIsOpen(false);
        lastToggleTimeRef.current = now;
      }

      setIsDragging(false);
      setCurrentY(0);
    },
    [isDragging, currentY, isOpen]
  );

  const formatTime = (seconds: number): string => {
    if (!isFinite(seconds) || seconds < 0) return "0:00";
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, "0")}`;
  };

  // Find current line in a chapter
  const getCurrentLineIndex = (chapter: Chapter): number => {
    for (let i = chapter.lines.length - 1; i >= 0; i--) {
      if (currentTime >= chapter.lines[i].startSeconds) {
        return i;
      }
    }
    return -1;
  };

  return (
    <>
      {/* Swipe handle */}
      <div
        ref={sheetRef}
        className={cn(
          "fixed bottom-0 left-0 right-0 z-50 transition-transform duration-300 ease-out",
          isOpen ? "translate-y-0" : "translate-y-[calc(100%-48px)]",
          className
        )}
        style={
          isDragging
            ? {
                transform: `translateY(${isOpen ? currentY : currentY - 48}px)`,
              }
            : undefined
        }
        onClick={(e) => e.stopPropagation()}
        onTouchStart={(e) => e.stopPropagation()}
      >
        {/* Handle bar */}
        <div
          className="flex flex-col items-center justify-center h-12 bg-black/90 backdrop-blur-sm rounded-t-2xl cursor-grab active:cursor-grabbing"
          onTouchStart={handleTouchStart}
          onTouchMove={handleTouchMove}
          onTouchEnd={handleTouchEnd}
          onMouseDown={handleTouchStart}
          onMouseMove={handleTouchMove}
          onMouseUp={handleTouchEnd}
          onMouseLeave={handleTouchEnd}
          role="button"
          tabIndex={0}
          aria-label={isOpen ? "Close lyric jump list" : "Open lyric jump list"}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              setIsOpen(!isOpen);
            }
          }}
        >
          <div className="w-12 h-1 bg-white/30 rounded-full mb-1" />
          <div className="flex items-center gap-2 text-white/70 text-sm">
            <ChevronUp
              className={cn(
                "size-4 transition-transform",
                isOpen ? "rotate-180" : ""
              )}
            />
            <span>{isOpen ? "Swipe down to close" : "Lyrics"}</span>
          </div>
        </div>

        {/* Content */}
        <div
          ref={contentRef}
          className="bg-black/90 backdrop-blur-sm max-h-[60vh] overflow-y-auto"
        >
          <div className="p-4 space-y-4">
            {chapters.map((chapter, chapterIndex) => {
              const isCurrentChapter = chapterIndex === currentSongIndex;
              const currentLineIndex = isCurrentChapter
                ? getCurrentLineIndex(chapter)
                : -1;

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
                    onClick={() => onJumpToChapter(chapterIndex)}
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
                  {isCurrentChapter && chapter.lines.length > 0 && (
                    <div className="px-3 pb-3">
                      <div className="space-y-1">
                        {chapter.lines.map((line, lineIndex) => {
                          const isCurrentLine = lineIndex === currentLineIndex;
                          const isPastLine = lineIndex < currentLineIndex;

                          return (
                            <button
                              key={lineIndex}
                              className={cn(
                                "w-full text-left px-3 py-2 rounded transition-all",
                                isCurrentLine
                                  ? "bg-primary/20 text-white"
                                  : isPastLine
                                    ? "text-white/40"
                                    : "text-white/70 hover:bg-white/5"
                              )}
                              onClick={() =>
                                onJumpToLine(chapterIndex, lineIndex)
                              }
                            >
                              <p className="text-sm truncate">{line.text}</p>
                              <p className="text-xs text-white/40">
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
      </div>

      {/* Backdrop */}
      {isOpen && (
        <div
          className="fixed inset-0 bg-black/50 z-40"
          onClick={() => setIsOpen(false)}
          role="button"
          tabIndex={0}
          aria-label="Close lyric jump list"
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === "Escape") {
              setIsOpen(false);
            }
          }}
        />
      )}
    </>
  );
}
