import { describe, expect, it } from "vitest";

import {
  albumFilterKey,
  formatAlbumLabel,
  formatAlbumOptionLabel,
  normalizeAlbumFilters,
  type AlbumOption,
} from "./album-filter";

function makeOption(
  albumName: string,
  albumSeries: string | null,
  songCount: number,
): AlbumOption {
  return { albumName, albumSeries, songCount };
}

describe("formatAlbumLabel", () => {
  it("returns albumName when albumSeries is null", () => {
    expect(formatAlbumLabel(makeOption("My Title", null, 12))).toBe("My Title");
  });

  it("returns albumName when albumSeries is whitespace-only", () => {
    expect(formatAlbumLabel(makeOption("My Title", "  ", 12))).toBe("My Title");
  });

  it("strips full-width parens from CJK series", () => {
    expect(formatAlbumLabel(makeOption("詩歌", "敬拜讚美（22）", 5))).toBe(
      "詩歌 (敬拜讚美 22)",
    );
  });

  it("strips ASCII parens", () => {
    expect(formatAlbumLabel(makeOption("Hymns", "Series (Vol. 1)", 3))).toBe(
      "Hymns (Series Vol. 1)",
    );
  });

  it("strips multiple paren groups", () => {
    expect(formatAlbumLabel(makeOption("M", "Series (A) (B)", 1))).toBe(
      "M (Series A B)",
    );
  });

  it("strips nested full-width parens", () => {
    expect(formatAlbumLabel(makeOption("M", "A（B（C））", 1))).toBe("M (A B C)");
  });

  it("treats parens-only series as absent", () => {
    expect(formatAlbumLabel(makeOption("M", "（）", 1))).toBe("M");
  });

  it("preserves clean series", () => {
    expect(formatAlbumLabel(makeOption("M", "Clean Series", 1))).toBe(
      "M (Clean Series)",
    );
  });
});

describe("formatAlbumOptionLabel", () => {
  it("appends song count for null series", () => {
    expect(formatAlbumOptionLabel(makeOption("My Title", null, 12))).toBe(
      "My Title [12]",
    );
  });

  it("appends song count for whitespace-only series", () => {
    expect(formatAlbumOptionLabel(makeOption("My Title", "  ", 12))).toBe(
      "My Title [12]",
    );
  });

  it("appends song count for stripped CJK series", () => {
    expect(formatAlbumOptionLabel(makeOption("詩歌", "敬拜讚美（22）", 5))).toBe(
      "詩歌 (敬拜讚美 22) [5]",
    );
  });

  it("appends song count for stripped ASCII parens", () => {
    expect(formatAlbumOptionLabel(makeOption("Hymns", "Series (Vol. 1)", 3))).toBe(
      "Hymns (Series Vol. 1) [3]",
    );
  });

  it("appends song count for multiple paren groups", () => {
    expect(formatAlbumOptionLabel(makeOption("M", "Series (A) (B)", 1))).toBe(
      "M (Series A B) [1]",
    );
  });

  it("appends song count for nested full-width parens", () => {
    expect(formatAlbumOptionLabel(makeOption("M", "A（B（C））", 1))).toBe(
      "M (A B C) [1]",
    );
  });

  it("omits parens when series is parens-only", () => {
    expect(formatAlbumOptionLabel(makeOption("M", "（）", 1))).toBe("M [1]");
  });

  it("preserves clean series with song count", () => {
    expect(formatAlbumOptionLabel(makeOption("M", "Clean Series", 1))).toBe(
      "M (Clean Series) [1]",
    );
  });
});

describe("albumFilterKey", () => {
  it("uses the raw albumSeries (not stripped)", () => {
    const key = albumFilterKey(makeOption("詩歌", "敬拜讚美（22）", 5));
    expect(key).toBe(`詩歌\u0000敬拜讚美（22）`);
  });
});

describe("normalizeAlbumFilters", () => {
  it("round-trips albumSeries unchanged (no paren transformation)", () => {
    const input = [
      { albumName: "詩歌", albumSeries: "敬拜讚美（22）" },
      { albumName: "Hymns", albumSeries: "Series (Vol. 1)" },
    ];
    const result = normalizeAlbumFilters(input);
    expect(result).toEqual(input);
  });
});
