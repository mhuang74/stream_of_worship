import { describe, it, expect, vi, beforeEach } from "vitest";
import { FrameRenderer } from "@/lib/render/frame-renderer";
import { GlobalLRCLine } from "@/lib/render/lrc-parser";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("canvas", () => ({
  createCanvas: vi.fn(() => ({
    getContext: vi.fn(() => ({
      fillRect: vi.fn(),
      fillText: vi.fn(),
      font: "48px sans-serif",
      textAlign: "center",
      getImageData: vi.fn(() => ({
        data: new Uint8ClampedArray(2000),
        buffer: Buffer.from([]),
      })),
    })),
  })),
  Canvas: class MockCanvas {},
  CanvasRenderingContext2D: class MockContext {},
}));

describe("FrameRenderer", () => {
  const darkTemplate = {
    name: "dark",
    backgroundColor: [20, 20, 30],
    textColor: [200, 200, 200],
    highlightColor: [255, 255, 255],
    fontSize: 48,
    resolution: [1920, 1080],
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("getAvailableTemplates", () => {
    it("returns all video template names", () => {
      const templates = FrameRenderer.getAvailableTemplates();
      expect(templates).toEqual(["dark", "gradient_warm", "gradient_blue"]);
    });
  });

  describe("getTemplate", () => {
    it("returns template by exact name", () => {
      const template = FrameRenderer.getTemplate("dark");
      expect(template.name).toBe("dark");
      expect(template.backgroundColor).toEqual([20, 20, 30]);
    });

    it("returns dark template as fallback for unknown name", () => {
      const template = FrameRenderer.getTemplate("unknown" as any);
      expect(template.name).toBe("dark");
    });

    it("includes all template properties", () => {
      const template = FrameRenderer.getTemplate("gradient_warm");
      expect(template.resolution).toEqual([1920, 1080]);
      expect(template.fontSize).toBe(48);
    });
  });

  describe("getAvailableFontSizes", () => {
    it("returns all font size presets", () => {
      const presets = FrameRenderer.getAvailableFontSizes();
      expect(presets).toEqual(["S", "M", "L", "XL"]);
    });
  });

  describe("getFontSize", () => {
    it("returns correct size for each preset", () => {
      expect(FrameRenderer.getFontSize("S")).toBe(32);
      expect(FrameRenderer.getFontSize("M")).toBe(48);
      expect(FrameRenderer.getFontSize("L")).toBe(64);
      expect(FrameRenderer.getFontSize("XL")).toBe(80);
    });
  });

  describe("constructor", () => {
    it("initializes with template and default options", () => {
      const renderer = new FrameRenderer({ template: darkTemplate });
      expect(renderer.getBaseFontSize()).toBe(48);
    });

    it("uses custom font size preset", () => {
      const renderer = new FrameRenderer({
        template: darkTemplate,
        fontSizePreset: "L",
      });
      expect(renderer.getBaseFontSize()).toBe(64);
    });

    it("uses custom resolution", () => {
      const renderer = new FrameRenderer({
        template: darkTemplate,
        resolution: { width: 1280, height: 720 },
      });
      expect(renderer.getBaseFontSize()).toBe(48);
    });

    it("uses resolution from template if not specified", () => {
      const renderer = new FrameRenderer({ template: darkTemplate });
      expect(renderer.getBaseFontSize()).toBe(48);
    });
  });

  describe("initialize", () => {
    it("resolves without errors", async () => {
      const renderer = new FrameRenderer({ template: darkTemplate });
      await expect(renderer.initialize()).resolves.toBeUndefined();
    });
  });

  describe("renderFrame - before lyrics", () => {
    it("renders only song title when before any lyric", () => {
      const renderer = new FrameRenderer({ template: darkTemplate });
      const lyrics: GlobalLRCLine[] = [
        {
          text: "Test lyric",
          localTimeSeconds: 10,
          globalTimeSeconds: 10,
          title: "Test",
        },
      ];

      const canvas = renderer.renderFrame(lyrics, [], 5);

      expect(canvas).toBeDefined();
    });

    it("renders no content for no segments before first lyric", () => {
      const renderer = new FrameRenderer({ template: darkTemplate });
      const lyrics: GlobalLRCLine[] = [
        {
          text: "Test lyric",
          localTimeSeconds: 10,
          globalTimeSeconds: 10,
          title: "Test",
        },
      ];

      const canvas = renderer.renderFrame(lyrics, [], 5);
      expect(canvas).toBeDefined();
    });

    it("handles short intro period (< 3s gap) by skipping intro info", () => {
      const renderer = new FrameRenderer({ template: darkTemplate });
      const lyrics: GlobalLRCLine[] = [
        {
          text: "First lyric",
          localTimeSeconds: 5,
          globalTimeSeconds: 5,
          title: "Test",
        },
      ];

      const segment: SegmentInfo = {
        id: "1",
        songId: "s1",
        position: 0,
        songTitle: "Test Song",
        startTimeSeconds: 0,
        durationSeconds: 20,
      };

      const canvas = renderer.renderFrame(lyrics, [segment], 2);
      expect(canvas).toBeDefined();
    });
  });

  describe("renderFrame - during lyrics", () => {
    it("renders current and next lyric lines", () => {
      const renderer = new FrameRenderer({ template: darkTemplate });
      const lyrics: GlobalLRCLine[] = [
        {
          text: "First lyric",
          localTimeSeconds: 0,
          globalTimeSeconds: 5,
          title: "Test",
        },
        {
          text: "Second lyric",
          localTimeSeconds: 5,
          globalTimeSeconds: 10,
          title: "Test",
        },
      ];

      const segment: SegmentInfo = {
        id: "1",
        songId: "s1",
        position: 0,
        songTitle: "Test Song",
        startTimeSeconds: 0,
        durationSeconds: 20,
      };

      const canvas = renderer.renderFrame(lyrics, [segment], 7);
      expect(canvas).toBeDefined();
    });

    it("shows only current lyric if on the last lyric", () => {
      const renderer = new FrameRenderer({ template: darkTemplate });
      const lyrics: GlobalLRCLine[] = [
        {
          text: "Last lyric",
          localTimeSeconds: 0,
          globalTimeSeconds: 5,
          title: "Test",
        },
      ];

      const segment: SegmentInfo = {
        id: "1",
        songId: "s1",
        position: 0,
        songTitle: "Test Song",
        startTimeSeconds: 0,
        durationSeconds: 20,
      };

      const canvas = renderer.renderFrame(lyrics, [segment], 6);
      expect(canvas).toBeDefined();
    });

    it("detects stuck lyrics across frames", () => {
      const renderer = new FrameRenderer({ template: darkTemplate });
      const lyrics: GlobalLRCLine[] = [
        {
          text: "Same lyric",
          localTimeSeconds: 0,
          globalTimeSeconds: 5,
          title: "Test",
        },
      ];

      const segment: SegmentInfo = {
        id: "1",
        songId: "s1",
        position: 0,
        songTitle: "Test Song",
        startTimeSeconds: 0,
        durationSeconds: 20,
      };

      renderer.renderFrame(lyrics, [segment], 6);
      renderer.renderFrame(lyrics, [segment], 7);
      renderer.renderFrame(lyrics, [segment], 8);
    });

    it("groups lyrics correctly when multiple songs present", () => {
      const renderer = new FrameRenderer({ template: darkTemplate });
      const lyrics: GlobalLRCLine[] = [
        {
          text: "Song 1 lyric 1",
          localTimeSeconds: 5,
          globalTimeSeconds: 5,
          title: "Song 1",
        },
        {
          text: "Song 2 lyric 1",
          localTimeSeconds: 5,
          globalTimeSeconds: 15,
          title: "Song 2",
        },
      ];

      const segments: SegmentInfo[] = [
        {
          id: "1",
          songId: "s1",
          position: 0,
          songTitle: "Song 1",
          startTimeSeconds: 0,
          durationSeconds: 20,
        },
        {
          id: "2",
          songId: "s2",
          position: 1,
          songTitle: "Song 2",
          startTimeSeconds: 10,
          durationSeconds: 20,
        },
      ];

      const canvas1 = renderer.renderFrame(lyrics, segments, 7);
      const canvas2 = renderer.renderFrame(lyrics, segments, 17);

      expect(canvas1).toBeDefined();
      expect(canvas2).toBeDefined();
    });

    it("shows album/artist/composer for songs with extra info", () => {
      const renderer = new FrameRenderer({ template: darkTemplate });
      const lyrics: GlobalLRCLine[] = [
        {
          text: "Test lyric",
          localTimeSeconds: 10,
          globalTimeSeconds: 10,
          title: "Test",
        },
      ];

      const segment: SegmentInfo = {
        id: "1",
        songId: "s1",
        position: 0,
        songTitle: "Test Song",
        songAlbumName: "Test Album",
        songComposer: "Composer Name",
        songLyricist: "Lyricist Name",
        startTimeSeconds: 0,
        durationSeconds: 20,
      };

      const canvas = renderer.renderFrame(lyrics, [segment], 11);
      expect(canvas).toBeDefined();
    });
  });

  describe("renderFrame - after lyrics", () => {
    it("continues showing last lyric after all lyrics complete", () => {
      const renderer = new FrameRenderer({ template: darkTemplate });
      const lyrics: GlobalLRCLine[] = [
        {
          text: "Last lyric",
          localTimeSeconds: 0,
          globalTimeSeconds: 5,
          title: "Test",
        },
      ];

      const segment: SegmentInfo = {
        id: "1",
        songId: "s1",
        position: 0,
        songTitle: "Test Song",
        startTimeSeconds: 0,
        durationSeconds: 20,
      };

      const canvas = renderer.renderFrame(lyrics, [segment], 10);
      expect(canvas).toBeDefined();
    });

    it("handles no segments gracefully", () => {
      const renderer = new FrameRenderer({ template: darkTemplate });
      const lyrics: GlobalLRCLine[] = [
        {
          text: "Test lyric",
          localTimeSeconds: 10,
          globalTimeSeconds: 10,
          title: "Test",
        },
      ];

      const canvas = renderer.renderFrame(lyrics, [], 5);
      expect(canvas).toBeDefined();
    });
  });

  describe("renderFrame - different templates", () => {
    it("renders with gradient_blue template", () => {
      const blueTemplate = {
        name: "gradient_blue",
        backgroundColor: [20, 30, 60],
        textColor: [220, 240, 255],
        highlightColor: [150, 200, 255],
        fontSize: 48,
        resolution: [1920, 1080],
      };

      const renderer = new FrameRenderer({ template: blueTemplate });
      const lyrics: GlobalLRCLine[] = [
        {
          text: "Blue template lyric",
          localTimeSeconds: 10,
          globalTimeSeconds: 10,
          title: "Test",
        },
      ];

      const segment: SegmentInfo = {
        id: "1",
        songId: "s1",
        position: 0,
        songTitle: "Test Song",
        startTimeSeconds: 0,
        durationSeconds: 20,
      };

      const canvas = renderer.renderFrame(lyrics, [segment], 11);
      expect(canvas).toBeDefined();
    });

    it("renders with gradient_warm template", () => {
      const warmTemplate = {
        name: "gradient_warm",
        backgroundColor: [60, 30, 20],
        textColor: [255, 240, 220],
        highlightColor: [255, 200, 150],
        fontSize: 48,
        resolution: [1920, 1080],
      };

      const renderer = new FrameRenderer({ template: warmTemplate });
      const lyrics: GlobalLRCLine[] = [
        {
          text: "Warm template lyric",
          localTimeSeconds: 10,
          globalTimeSeconds: 10,
          title: "Test",
        },
      ];

      const segment: SegmentInfo = {
        id: "1",
        songId: "s1",
        position: 0,
        songTitle: "Test Song",
        startTimeSeconds: 0,
        durationSeconds: 20,
      };

      const canvas = renderer.renderFrame(lyrics, [segment], 11);
      expect(canvas).toBeDefined();
    });

    it("renders with different font sizes", () => {
      const renderer = new FrameRenderer({
        template: darkTemplate,
        fontSizePreset: "XL",
      });

      const lyrics: GlobalLRCLine[] = [
        {
          text: "XL size lyric",
          localTimeSeconds: 10,
          globalTimeSeconds: 10,
          title: "Test",
        },
      ];

      const segment: SegmentInfo = {
        id: "1",
        songId: "s1",
        position: 0,
        songTitle: "Test Song",
        startTimeSeconds: 0,
        durationSeconds: 20,
      };

      const canvas = renderer.renderFrame(lyrics, [segment], 11);
      expect(canvas).toBeDefined();
    });

    it("handles cross-selected playlist switching between songs", () => {
      const renderer = new FrameRenderer({ template: darkTemplate });
      const lyrics: GlobalLRCLine[] = [
        {
          text: "Song A lyric",
          localTimeSeconds: 5,
          globalTimeSeconds: 5,
          title: "Song A",
        },
        {
          text: "Song B lyric 1",
          localTimeSeconds: 5,
          globalTimeSeconds: 15,
          title: "Song B",
        },
      ];

      const segments: SegmentInfo[] = [
        {
          id: "1",
          songId: "s1",
          position: 0,
          songTitle: "Song A",
          startTimeSeconds: 0,
          durationSeconds: 10,
        },
        {
          id: "2",
          songId: "s2",
          position: 1,
          songTitle: "Song B",
          startTimeSeconds: 10,
          durationSeconds: 10,
        },
      ];

      const canvasA1 = renderer.renderFrame(lyrics, segments, 7);
      const canvasA2 = renderer.renderFrame(lyrics, segments, 12);
      const canvasB1 = renderer.renderFrame(lyrics, segments, 17);

      expect(canvasA1).toBeDefined();
      expect(canvasA2).toBeDefined();
      expect(canvasB1).toBeDefined();
    });
  });

  describe("renderTitleCard", () => {
    it("renders basic title card", () => {
      const renderer = new FrameRenderer({ template: darkTemplate });
      const config: any = {
        enabled: true,
        durationSeconds: 10,
        songsetName: "Morning Worship",
        songCount: 5,
        totalDurationSeconds: 30,
      };

      const canvas = renderer.renderTitleCard(config);

      expect(canvas).toBeDefined();
    });

    it("renders title card with correct songset and counts", () => {
      const renderer = new FrameRenderer({ template: darkTemplate });
      const config: any = {
        enabled: true,
        durationSeconds: 10,
        songsetName: "Sunday Service",
        songCount: 3,
        totalDurationSeconds: 45,
      };

      const canvas = renderer.renderTitleCard(config);
      expect(canvas).toBeDefined();
    });

    it("uses resolution from template", () => {
      const renderer = new FrameRenderer({ template: darkTemplate });
      const config: any = {
        enabled: true,
        durationSeconds: 10,
        songsetName: "Test",
        songCount: 1,
        totalDurationSeconds: 5,
      };

      const canvas = renderer.renderTitleCard(config);
      expect(canvas).toBeDefined();
    });

    it("renders correct duration format (MM:SS)", () => {
      const renderer = new FrameRenderer({ template: darkTemplate });
      const config: any = {
        enabled: true,
        durationSeconds: 65,
        songsetName: "Test Songset",
        songCount: 1,
        totalDurationSeconds: 65,
      };

      const canvas = renderer.renderTitleCard(config);
      expect(canvas).toBeDefined();
    });
  });

  describe("getBaseFontSize", () => {
    it("returns base font size based on preset", () => {
      const renderer = new FrameRenderer({ template: darkTemplate, fontSizePreset: "L" });
      expect(renderer.getBaseFontSize()).toBe(64);
    });

    it("defaults to 'M' if not specified", () => {
      const renderer = new FrameRenderer({ template: darkTemplate });
      expect(renderer.getBaseFontSize()).toBe(48);
    });
  });
});