/**
 * Frame Renderer for generating video frames with lyrics.
 *
 * Uses node-canvas for rendering text and graphics.
 * Supports multiple templates and font sizes.
 */

import { createCanvas, Canvas, CanvasRenderingContext2D } from "canvas";
import { GlobalLRCLine, estimateLastLyricDuration, groupLyricsBySong } from "./lrc-parser";

export type VideoTemplateName = "dark" | "gradient_warm" | "gradient_blue";
export type FontSizePreset = "S" | "M" | "L" | "XL";

export interface VideoTemplate {
  name: VideoTemplateName;
  backgroundColor: [number, number, number];
  textColor: [number, number, number];
  highlightColor: [number, number, number];
  fontSize: number;
  resolution: [number, number];
}

export interface VideoResolution {
  width: number;
  height: number;
}

// Font size presets in pixels
export const FONT_SIZE_PRESETS: Record<FontSizePreset, number> = {
  S: 32,
  M: 48,
  L: 64,
  XL: 80,
};

// Predefined templates
export const VIDEO_TEMPLATES: Record<VideoTemplateName, VideoTemplate> = {
  dark: {
    name: "dark",
    backgroundColor: [20, 20, 30],
    textColor: [200, 200, 200],
    highlightColor: [255, 255, 255],
    fontSize: 48,
    resolution: [1920, 1080],
  },
  gradient_warm: {
    name: "gradient_warm",
    backgroundColor: [60, 30, 20],
    textColor: [255, 240, 220],
    highlightColor: [255, 200, 150],
    fontSize: 48,
    resolution: [1920, 1080],
  },
  gradient_blue: {
    name: "gradient_blue",
    backgroundColor: [20, 30, 60],
    textColor: [220, 240, 255],
    highlightColor: [150, 200, 255],
    fontSize: 48,
    resolution: [1920, 1080],
  },
};

export interface SegmentInfo {
  id: string;
  songId: string;
  position: number;
  songTitle: string;
  songAlbumName?: string | null;
  songComposer?: string | null;
  songLyricist?: string | null;
  startTimeSeconds: number;
  durationSeconds: number;
  tempoBpm?: number | null;
}

export interface FrameRendererOptions {
  template: VideoTemplate;
  fontSizePreset?: FontSizePreset;
  resolution?: VideoResolution;
}

export interface TitleCardConfig {
  enabled: boolean;
  durationSeconds: number;
  songsetName: string;
  songCount: number;
  totalDurationSeconds: number;
}

/**
 * FrameRenderer generates video frames with synchronized lyrics.
 */
export class FrameRenderer {
  private template: VideoTemplate;
  private fontSizePreset: FontSizePreset;
  private resolution: VideoResolution;
  private baseFontSize: number;
  private fontFamily: string;

  // Track state for stuck lyric detection
  private lastLoggedSong: string | null = null;
  private lastLoggedLyricTime: number | null = null;
  private lastLoggedLyricText: string | null = null;
  private stuckFrameCounter = 0;

  constructor(options: FrameRendererOptions) {
    this.template = options.template;
    this.fontSizePreset = options.fontSizePreset ?? "M";
    this.resolution = options.resolution ?? {
      width: this.template.resolution[0],
      height: this.template.resolution[1],
    };
    this.baseFontSize = FONT_SIZE_PRESETS[this.fontSizePreset];
    this.fontFamily = "sans-serif";
  }

  /**
   * Initialize fonts for rendering.
   * Must be called before rendering frames.
   */
  async initialize(): Promise<void> {
    // node-canvas uses system fonts, no need to load custom fonts
    // Font family will be set when rendering
  }

  /**
   * Get the base font size based on preset.
   */
  getBaseFontSize(): number {
    return this.baseFontSize;
  }

  /**
   * Get font string for canvas context.
   */
  private getFontString(size: number): string {
    return `${size}px ${this.fontFamily}`;
  }

  /**
   * Measure text at target font size and scale down if it exceeds maxWidth.
   * Returns the fitted font size (<= targetFontSize).
   */
  private fitText(
    ctx: CanvasRenderingContext2D,
    text: string,
    targetFontSize: number,
    maxWidth: number
  ): number {
    ctx.font = this.getFontString(targetFontSize);
    const metrics = ctx.measureText(text);
    if (metrics.width <= maxWidth) {
      return targetFontSize;
    }
    const scale = maxWidth / metrics.width;
    return Math.floor(targetFontSize * scale);
  }

  /**
   * Compute a single-character margin width using "中" as reference.
   */
  private getMargin(ctx: CanvasRenderingContext2D, fontSize: number): number {
    ctx.font = this.getFontString(fontSize);
    return ctx.measureText("中").width;
  }

  /**
   * Render a single video frame.
   *
   * @param lyrics - All lyrics with global timing
   * @param segments - Audio segments with timing info
   * @param currentTime - Current playback time in seconds
   * @returns Canvas with rendered frame
   */
  renderFrame(
    lyrics: GlobalLRCLine[],
    segments: SegmentInfo[],
    currentTime: number
  ): Canvas {
    const { width, height } = this.resolution;
    const canvas = createCanvas(width, height);
    const ctx = canvas.getContext("2d");

    // Fill background
    const [bgR, bgG, bgB] = this.template.backgroundColor;
    ctx.fillStyle = `rgb(${bgR}, ${bgG}, ${bgB})`;
    ctx.fillRect(0, 0, width, height);

    // Find current song based on segment timing
    let currentTitle = "";
    let currentSegment: SegmentInfo | null = null;

    for (const segment of segments) {
      const segmentStart = segment.startTimeSeconds;
      const segmentEnd = segmentStart + segment.durationSeconds;
      if (segmentStart <= currentTime && currentTime < segmentEnd) {
        currentTitle = segment.songTitle || "Unknown";
        currentSegment = segment;
        break;
      }
    }

    // Group lyrics by song
    const lyricsBySong = groupLyricsBySong(lyrics);
    const currentSongLyrics = lyricsBySong.get(currentTitle) || [];

    // Track intro info alpha
    let introInfoAlpha = 0;

    // Handle intro period before first lyric
    if (currentSegment && currentSongLyrics.length > 0) {
      const firstLyricTime = currentSongLyrics[0].globalTimeSeconds;

      if (currentTime < firstLyricTime) {
        introInfoAlpha = this.renderIntroInfo(
          currentSegment,
          currentTime,
          firstLyricTime,
          ctx,
          width,
          height
        );
      }
    }

    // Draw title at top (unless intro info is displayed)
    if (currentTitle && introInfoAlpha === 0) {
      const [textR, textG, textB] = this.template.textColor;
      ctx.fillStyle = `rgb(${textR}, ${textG}, ${textB})`;
      const titleFontSize = this.fitText(
        ctx,
        currentTitle,
        Math.floor(this.baseFontSize * 0.8),
        width - this.getMargin(ctx, Math.floor(this.baseFontSize * 0.8)) * 2
      );
      ctx.font = this.getFontString(titleFontSize);
      ctx.textAlign = "center";
      ctx.fillText(currentTitle, width / 2, 50);
    }

    // Render lyrics if within time range
    if (currentSongLyrics.length > 0) {
      const firstLyricTime = currentSongLyrics[0].globalTimeSeconds;

      if (currentTime >= firstLyricTime) {
        this.renderLyrics(
          currentSongLyrics,
          currentTime,
          currentTitle,
          ctx,
          width,
          height
        );
      }
    }

    return canvas;
  }

  /**
   * Render intro information during the gap before first lyric.
   */
  private renderIntroInfo(
    segment: SegmentInfo,
    currentTime: number,
    firstLyricTime: number,
    ctx: CanvasRenderingContext2D,
    width: number,
    height: number
  ): number {
    const segmentStart = segment.startTimeSeconds;
    const gapDuration = firstLyricTime - segmentStart;

    // Not in intro period
    if (currentTime >= firstLyricTime) {
      return 0;
    }

    // Short intro: < 3s gap - skip intro entirely
    if (gapDuration < 3.0) {
      return 0;
    }

    // Calculate phases based on gap duration
    let infoDuration: number;
    let fadeDuration: number;
    let titleOnlyDuration: number;

    if (gapDuration < 7.0) {
      // Short intro: 60% info, 40% fade, no title-only period
      infoDuration = gapDuration * 0.6;
      fadeDuration = gapDuration * 0.4;
      titleOnlyDuration = 0.0;
    } else {
      // Normal intro: transition window + 4s fade + 3s title-only
      fadeDuration = 4.0;
      titleOnlyDuration = 3.0;
      infoDuration = gapDuration - fadeDuration - titleOnlyDuration;
    }

    const timeIntoGap = currentTime - segmentStart;

    // Title-only period: don't render intro info
    if (timeIntoGap >= infoDuration + fadeDuration) {
      return 0;
    }

    // Build info lines with Traditional Chinese labels
    const infoLines: string[] = [];

    if (segment.songTitle) {
      infoLines.push(`歌曲：${segment.songTitle}`);
    }
    if (segment.songAlbumName) {
      infoLines.push(`專輯：${segment.songAlbumName}`);
    }
    if (segment.songComposer) {
      infoLines.push(`作曲：${segment.songComposer}`);
    }
    if (segment.songLyricist) {
      infoLines.push(`作詞：${segment.songLyricist}`);
    }
    infoLines.push("讚美之泉音樂事工");

    if (infoLines.length === 0) {
      return 0;
    }

    // Calculate alpha based on phase
    let alpha = 255;
    if (timeIntoGap >= infoDuration) {
      // In fade-out period
      const fadeProgress =
        (timeIntoGap - infoDuration) / fadeDuration;
      // Use sqrt-based fade for smooth transition
      alpha = Math.floor(255 * (1.0 - Math.sqrt(fadeProgress)));
    }

    // Calculate total block height
    const lineHeight = this.baseFontSize * 1.3;
    const totalHeight = infoLines.length * lineHeight;
    const baseY = height / 2 - totalHeight / 2;

    const [textR, textG, textB] = this.template.textColor;

    // Render each line centered
    const introFontSize = Math.floor(this.baseFontSize * 0.9);
    const margin = this.getMargin(ctx, introFontSize);
    const maxWidth = width - margin * 2;
    ctx.textAlign = "center";
    for (let i = 0; i < infoLines.length; i++) {
      const line = infoLines[i];
      ctx.fillStyle = `rgba(${textR}, ${textG}, ${textB}, ${alpha / 255})`;
      ctx.font = this.getFontString(this.fitText(ctx, line, introFontSize, maxWidth));
      ctx.fillText(line, width / 2, baseY + i * lineHeight + lineHeight / 2);
    }

    return alpha;
  }

  /**
   * Render lyrics on the frame.
   */
  private renderLyrics(
    songLyrics: GlobalLRCLine[],
    currentTime: number,
    currentTitle: string,
    ctx: CanvasRenderingContext2D,
    width: number,
    height: number
  ): void {
    // Find current lyric index
    let currentIndex = -1;
    for (let i = 0; i < songLyrics.length; i++) {
      if (songLyrics[i].globalTimeSeconds <= currentTime) {
        currentIndex = i;
      } else {
        break;
      }
    }

    // If past all lyrics, continue showing the last one
    const lastLyricTime =
      songLyrics[songLyrics.length - 1].globalTimeSeconds;
    if (currentIndex === -1 && currentTime > lastLyricTime) {
      currentIndex = songLyrics.length - 1;
    }

    if (currentIndex < 0) {
      return;
    }

    const currentLine = songLyrics[currentIndex];

    // Detect potentially stuck lyrics
    const isSameSong = this.lastLoggedSong === currentTitle;
    const isSameLyricTime =
      this.lastLoggedLyricTime === currentLine.globalTimeSeconds;
    const isSameText = this.lastLoggedLyricText === currentLine.text;

    if (isSameSong && (isSameLyricTime || isSameText)) {
      this.stuckFrameCounter++;
    } else {
      this.stuckFrameCounter = 0;
      this.lastLoggedSong = currentTitle;
      this.lastLoggedLyricTime = currentLine.globalTimeSeconds;
      this.lastLoggedLyricText = currentLine.text;
    }

    // Check if this is the last lyric and handle fade-out
    const isLastLyric = currentIndex === songLyrics.length - 1;
    let fadeAlpha = 255;
    let isLastLyricFaded = false;

    if (isLastLyric) {
      // Estimate display duration
      const maxDisplay = estimateLastLyricDuration(songLyrics);
      const elapsedSinceLastLyric =
        currentTime - currentLine.globalTimeSeconds;

      // Fade duration: 7 seconds, with 30% margin before fade starts
      const FADE_DURATION = 7.0;
      const MARGIN = 1.3;
      const fadeStartThreshold = maxDisplay * MARGIN;

      if (elapsedSinceLastLyric > fadeStartThreshold + FADE_DURATION) {
        // Fully faded - skip rendering this lyric
        return;
      } else if (elapsedSinceLastLyric > fadeStartThreshold) {
        // In fade-out period
        const fadeProgress = Math.min(
          1.0,
          (elapsedSinceLastLyric - fadeStartThreshold) / FADE_DURATION
        );
        // Logarithmic fade: starts fast, then lingers
        const logAlpha = 1.0 - Math.sqrt(fadeProgress);
        fadeAlpha = Math.floor(255 * logAlpha);
        isLastLyricFaded = true;
      }
    }

    // Draw current line: 2x larger font, centered vertically
    const [highlightR, highlightG, highlightB] = this.template.highlightColor;
    const currentFontSize = this.fitText(
      ctx,
      currentLine.text,
      this.baseFontSize * 2,
      width - this.getMargin(ctx, this.baseFontSize * 2) * 2
    );
    ctx.font = this.getFontString(currentFontSize);
    ctx.textAlign = "center";
    ctx.fillStyle = `rgba(${highlightR}, ${highlightG}, ${highlightB}, ${fadeAlpha / 255})`;
    const y = height * 0.33;
    ctx.fillText(currentLine.text, width / 2, y);

    // Draw next line: 50% transparent, pushed lower
    if (!isLastLyricFaded) {
      const nextIndex = currentIndex + 1;
      if (nextIndex < songLyrics.length) {
        const nextLine = songLyrics[nextIndex];

        // If last lyric is fading, also fade next line
        let nextAlpha = 128;
        if (isLastLyric && fadeAlpha < 255) {
          const fadeProgress = 1.0 - fadeAlpha / 255.0;
          nextAlpha = Math.floor(128 * (1 - fadeProgress));
        }

        const [textR, textG, textB] = this.template.textColor;
        const nextFontSize = this.fitText(
          ctx,
          nextLine.text,
          this.baseFontSize,
          width - this.getMargin(ctx, this.baseFontSize) * 2
        );
        ctx.font = this.getFontString(nextFontSize);
        ctx.textAlign = "center";
        ctx.fillStyle = `rgba(${textR}, ${textG}, ${textB}, ${nextAlpha / 255})`;
        const nextY = height * 0.33 + 200;
        ctx.fillText(nextLine.text, width / 2, nextY);
      }
    }
  }

  /**
   * Render a title card frame.
   *
   * @param config - Title card configuration
   * @returns Canvas with rendered title card
   */
  renderTitleCard(config: TitleCardConfig): Canvas {
    const { width, height } = this.resolution;
    const canvas = createCanvas(width, height);
    const ctx = canvas.getContext("2d");

    // Fill background
    const [bgR, bgG, bgB] = this.template.backgroundColor;
    ctx.fillStyle = `rgb(${bgR}, ${bgG}, ${bgB})`;
    ctx.fillRect(0, 0, width, height);

    const [textR, textG, textB] = this.template.textColor;
    ctx.fillStyle = `rgb(${textR}, ${textG}, ${textB})`;

    // Draw songset name
    const titleCardFontSize = this.fitText(
      ctx,
      config.songsetName,
      this.baseFontSize * 2,
      width - this.getMargin(ctx, this.baseFontSize * 2) * 2
    );
    ctx.font = this.getFontString(titleCardFontSize);
    ctx.textAlign = "center";
    ctx.fillText(config.songsetName, width / 2, height * 0.4);

    // Draw song count and duration
    ctx.font = this.getFontString(this.baseFontSize);
    const durationMinutes = Math.floor(config.totalDurationSeconds / 60);
    const durationSeconds = Math.floor(config.totalDurationSeconds % 60);
    const durationText = `${durationMinutes}:${durationSeconds.toString().padStart(2, "0")}`;
    ctx.fillText(
      `${config.songCount} 首歌曲 · ${durationText}`,
      width / 2,
      height * 0.55
    );

    return canvas;
  }

  /**
   * Convert canvas to RGBA buffer for FFmpeg.
   *
   * @param canvas - Canvas to convert
   * @returns RGBA buffer
   */
  canvasToBuffer(canvas: Canvas): Buffer {
    const ctx = canvas.getContext("2d");
    const { width, height } = this.resolution;
    const imageData = ctx.getImageData(0, 0, width, height);
    return Buffer.from(imageData.data.buffer);
  }

  /**
   * Get available template names.
   */
  static getAvailableTemplates(): VideoTemplateName[] {
    return Object.keys(VIDEO_TEMPLATES) as VideoTemplateName[];
  }

  /**
   * Get a template by name.
   */
  static getTemplate(name: VideoTemplateName): VideoTemplate {
    return VIDEO_TEMPLATES[name] ?? VIDEO_TEMPLATES.dark;
  }

  /**
   * Get available font size presets.
   */
  static getAvailableFontSizes(): FontSizePreset[] {
    return ["S", "M", "L", "XL"];
  }

  /**
   * Get font size for preset.
   */
  static getFontSize(preset: FontSizePreset): number {
    return FONT_SIZE_PRESETS[preset];
  }
}
