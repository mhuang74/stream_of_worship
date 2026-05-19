import { describe, it, expect, beforeEach, vi } from "vitest";
import { getRenderRatio, getDefaultRatio, DEFAULT_RENDER_RATIOS } from "@/lib/render/render-ratio";
import { db } from "@/db";

/* eslint-disable @typescript-eslint/no-explicit-any */

vi.mock("@/db", () => {
  const selectChain = {
    from: vi.fn().mockReturnThis(),
    where: vi.fn().mockResolvedValue([]),
  };
  return {
    db: {
      select: vi.fn(() => selectChain),
    },
  };
});

describe("getDefaultRatio", () => {
  it("returns 720p_video ratio", () => {
    expect(getDefaultRatio("720p", true)).toBe(DEFAULT_RENDER_RATIOS["720p_video"]);
  });

  it("returns 720p_audio ratio", () => {
    expect(getDefaultRatio("720p", false)).toBe(DEFAULT_RENDER_RATIOS["720p_audio"]);
  });

  it("returns 1080p_video ratio", () => {
    expect(getDefaultRatio("1080p", true)).toBe(DEFAULT_RENDER_RATIOS["1080p_video"]);
  });

  it("returns 1080p_audio ratio", () => {
    expect(getDefaultRatio("1080p", false)).toBe(DEFAULT_RENDER_RATIOS["1080p_audio"]);
  });

  it("returns most conservative default for unknown resolution", () => {
    const maxDefault = Math.max(...Object.values(DEFAULT_RENDER_RATIOS));
    expect(getDefaultRatio("4k", true)).toBe(maxDefault);
    expect(getDefaultRatio("4k", false)).toBe(maxDefault);
  });
});

describe("getRenderRatio", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns default ratio when no historical jobs exist", async () => {
    const selectChain = {
      from: vi.fn().mockReturnThis(),
      where: vi.fn().mockResolvedValue([{ ratio: 0, count: 0 }]),
    };
    vi.mocked(db.select).mockReturnValue(selectChain as any);

    const ratio = await getRenderRatio("720p", true);
    expect(ratio).toBe(DEFAULT_RENDER_RATIOS["720p_video"]);
  });

  it("returns default ratio when fewer than 3 historical jobs exist", async () => {
    const selectChain = {
      from: vi.fn().mockReturnThis(),
      where: vi.fn().mockResolvedValue([{ ratio: 1.2, count: 2 }]),
    };
    vi.mocked(db.select).mockReturnValue(selectChain as any);

    const ratio = await getRenderRatio("720p", true);
    expect(ratio).toBe(DEFAULT_RENDER_RATIOS["720p_video"]);
  });

  it("returns computed average when 3+ historical jobs exist", async () => {
    const selectChain = {
      from: vi.fn().mockReturnThis(),
      where: vi.fn().mockResolvedValue([{ ratio: 1.3, count: 5 }]),
    };
    vi.mocked(db.select).mockReturnValue(selectChain as any);

    const ratio = await getRenderRatio("720p", true);
    expect(ratio).toBe(1.3);
  });

  it("falls back to default when computed average is unreasonably high", async () => {
    const selectChain = {
      from: vi.fn().mockReturnThis(),
      where: vi.fn().mockResolvedValue([{ ratio: 6.0, count: 5 }]),
    };
    vi.mocked(db.select).mockReturnValue(selectChain as any);

    const ratio = await getRenderRatio("720p", true);
    expect(ratio).toBe(DEFAULT_RENDER_RATIOS["720p_video"]);
  });

  it("falls back to default when computed average is unreasonably low", async () => {
    const selectChain = {
      from: vi.fn().mockReturnThis(),
      where: vi.fn().mockResolvedValue([{ ratio: 0.01, count: 5 }]),
    };
    vi.mocked(db.select).mockReturnValue(selectChain as any);

    const ratio = await getRenderRatio("720p", true);
    expect(ratio).toBe(DEFAULT_RENDER_RATIOS["720p_video"]);
  });

  it("differentiates by resolution and videoEnabled", async () => {
    const selectChain720pVideo = {
      from: vi.fn().mockReturnThis(),
      where: vi.fn().mockResolvedValue([{ ratio: 1.5, count: 5 }]),
    };
    const selectChain1080pAudio = {
      from: vi.fn().mockReturnThis(),
      where: vi.fn().mockResolvedValue([{ ratio: 0.4, count: 5 }]),
    };

    vi.mocked(db.select)
      .mockReturnValueOnce(selectChain720pVideo as any)
      .mockReturnValueOnce(selectChain1080pAudio as any);

    const ratio720pVideo = await getRenderRatio("720p", true);
    const ratio1080pAudio = await getRenderRatio("1080p", false);

    expect(ratio720pVideo).toBe(1.5);
    expect(ratio1080pAudio).toBe(0.4);
  });

  it("returns most conservative default for unknown resolution", async () => {
    const selectChain = {
      from: vi.fn().mockReturnThis(),
      where: vi.fn().mockResolvedValue([{ ratio: 0, count: 0 }]),
    };
    vi.mocked(db.select).mockReturnValue(selectChain as any);

    const ratio = await getRenderRatio("4k", true);
    const maxDefault = Math.max(...Object.values(DEFAULT_RENDER_RATIOS));
    expect(ratio).toBe(maxDefault);
  });
});
