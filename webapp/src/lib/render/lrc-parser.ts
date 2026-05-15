/**
 * LRC (LyRiCs) file parser for synchronized lyrics.
 * 
 * Parses LRC format: [mm:ss.xx] or [mm:ss.xxx] lyrics text
 * Supports both 2-digit and 3-digit millisecond precision.
 */

export interface LRCLine {
  /** Timestamp in seconds */
  timeSeconds: number;
  /** Lyric text */
  text: string;
}

export interface GlobalLRCLine extends LRCLine {
  /** Time in the final video (seconds) */
  globalTimeSeconds: number;
  /** Original time within the song (seconds) */
  localTimeSeconds: number;
  /** Song title for this lyric */
  title: string;
}

/**
 * Parse LRC file content into timestamped lines.
 * 
 * @param lrcContent - Raw LRC file content
 * @returns Array of LRC lines sorted by timestamp
 */
export function parseLRC(lrcContent: string): LRCLine[] {
  const lines: LRCLine[] = [];
  // Match [mm:ss.xx] or [mm:ss.xxx] format
  const pattern = /\[(\d{2}):(\d{2})\.(\d{2,3})\](.*)/;

  for (const line of lrcContent.split('\n')) {
    const match = pattern.exec(line.trim());
    if (match) {
      const minutes = parseInt(match[1], 10);
      const seconds = parseInt(match[2], 10);
      // Pad milliseconds to 3 digits if needed
      const milliseconds = parseInt(match[3].padEnd(3, '0').slice(0, 3), 10);
      const text = match[4].trim();

      const timeSeconds = minutes * 60 + seconds + milliseconds / 1000.0;
      if (text) {
        lines.push({ timeSeconds, text });
      }
    }
  }

  // Sort by timestamp
  return lines.sort((a, b) => a.timeSeconds - b.timeSeconds);
}

/**
 * Convert local LRC lines to global timeline.
 * 
 * @param localLines - LRC lines with local timestamps
 * @param segmentStartSeconds - Start time of this segment in the global timeline
 * @param title - Song title for these lyrics
 * @returns Array of global LRC lines
 */
export function convertToGlobalTimeline(
  localLines: LRCLine[],
  segmentStartSeconds: number,
  title: string
): GlobalLRCLine[] {
  return localLines.map((line) => ({
    timeSeconds: segmentStartSeconds + line.timeSeconds,
    globalTimeSeconds: segmentStartSeconds + line.timeSeconds,
    localTimeSeconds: line.timeSeconds,
    text: line.text,
    title,
  }));
}

/**
 * Estimate display duration for the last lyric line.
 * 
 * Uses two-tier approach:
 * 1. Primary: Match previous occurrence of same text in song
 * 2. Fallback: Character count + BPM estimation
 * 
 * @param songLyrics - All lyrics for the current song
 * @param tempoBpm - Song tempo in BPM (optional)
 * @returns Estimated duration in seconds (minimum 3s)
 */
export function estimateLastLyricDuration(
  songLyrics: GlobalLRCLine[],
  tempoBpm?: number | null
): number {
  if (songLyrics.length === 0) {
    return 5.0;
  }

  const lastLyric = songLyrics[songLyrics.length - 1];

  // Primary approach: find previous occurrence of same text
  for (let i = songLyrics.length - 2; i >= 0; i--) {
    if (songLyrics[i].text === lastLyric.text) {
      // Use the duration from the previous occurrence
      if (i + 1 < songLyrics.length) {
        const duration =
          songLyrics[i + 1].globalTimeSeconds - songLyrics[i].globalTimeSeconds;
        return Math.max(3.0, duration);
      }
    }
  }

  // Fallback approach: character count + BPM estimation
  const text = lastLyric.text;
  let charCount = 0;
  for (const char of text) {
    const code = char.charCodeAt(0);
    if (code > 0x7f) {
      // Chinese character
      charCount += 1.0;
    } else if (!char.trim()) {
      // Non-space ASCII ~ half-width
      charCount += 0.5;
    }
  }

  const bpm = tempoBpm && tempoBpm > 0 ? tempoBpm : 70.0;
  // Assume 2 beats per character for comfortable reading pace
  const beatsPerBeat = 60.0 / bpm;
  const duration = charCount * 2 * beatsPerBeat;

  return Math.max(3.0, duration);
}

/**
 * Find the current lyric index based on time.
 * 
 * @param lyrics - Array of LRC lines
 * @param currentTimeSeconds - Current playback time
 * @returns Index of current lyric, or -1 if before first lyric
 */
export function findCurrentLyricIndex(
  lyrics: GlobalLRCLine[],
  currentTimeSeconds: number
): number {
  let currentIndex = -1;
  for (let i = 0; i < lyrics.length; i++) {
    if (lyrics[i].globalTimeSeconds <= currentTimeSeconds) {
      currentIndex = i;
    } else {
      break;
    }
  }
  return currentIndex;
}

/**
 * Group lyrics by song title for easy lookup.
 * 
 * @param lyrics - Array of global LRC lines
 * @returns Map of title to lyrics array
 */
export function groupLyricsBySong(
  lyrics: GlobalLRCLine[]
): Map<string, GlobalLRCLine[]> {
  const grouped = new Map<string, GlobalLRCLine[]>();
  for (const line of lyrics) {
    if (!grouped.has(line.title)) {
      grouped.set(line.title, []);
    }
    grouped.get(line.title)!.push(line);
  }
  return grouped;
}

/**
 * Validate LRC content format.
 * 
 * @param lrcContent - Raw LRC file content
 * @returns True if valid LRC format
 */
export function isValidLRC(lrcContent: string): boolean {
  const pattern = /\[\d{2}:\d{2}\.\d{2,3}\]/;
  return pattern.test(lrcContent);
}

/**
 * Get the time range for lyrics display.
 * 
 * @param lyrics - Array of LRC lines
 * @returns Object with first and last lyric times, or null if empty
 */
export function getLyricsTimeRange(
  lyrics: LRCLine[]
): { firstTime: number; lastTime: number } | null {
  if (lyrics.length === 0) {
    return null;
  }
  return {
    firstTime: lyrics[0].timeSeconds,
    lastTime: lyrics[lyrics.length - 1].timeSeconds,
  };
}
