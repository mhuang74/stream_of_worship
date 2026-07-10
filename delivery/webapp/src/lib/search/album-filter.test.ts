import { describe, expect, it } from "vitest";

import {
  albumFilterKey,
  extractSeriesPrefix,
  extractTrailingNumber,
  formatAlbumLabel,
  formatAlbumOptionLabel,
  normalizeAlbumFilters,
  sortAlbumOptions,
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

describe("extractTrailingNumber", () => {
  it("extracts trailing number from CJK series", () => {
    expect(extractTrailingNumber("敬拜讚美 13")).toBe(13);
  });

  it("extracts trailing number without space", () => {
    expect(extractTrailingNumber("敬拜讚美13")).toBe(13);
  });

  it("extracts from ASCII series", () => {
    expect(extractTrailingNumber("Series Vol. 42")).toBe(42);
  });

  it("returns null for series without trailing number", () => {
    expect(extractTrailingNumber("敬拜讚美")).toBeNull();
  });

  it("returns null for null input", () => {
    expect(extractTrailingNumber(null)).toBeNull();
  });

  it("extracts only the last number when multiple exist", () => {
    expect(extractTrailingNumber("Vol. 3 No. 7")).toBe(7);
  });

  it("extracts number from full-width parenthesized CJK series", () => {
    expect(extractTrailingNumber("敬拜讚美（1）")).toBe(1);
    expect(extractTrailingNumber("敬拜讚美（22）")).toBe(22);
  });

  it("extracts number from ASCII parenthesized series", () => {
    expect(extractTrailingNumber("Series (Vol. 1)")).toBe(1);
  });

  it("extracts number from series with trailing non-digit suffix (EP)", () => {
    expect(extractTrailingNumber("兒童敬拜讚美 14EP")).toBe(14);
    expect(extractTrailingNumber("兒童敬拜讚美（14EP）")).toBe(14);
  });

  it("extracts number from pure numeric series", () => {
    expect(extractTrailingNumber("123")).toBe(123);
  });
});

describe("extractSeriesPrefix", () => {
  it("extracts prefix from CJK series with space", () => {
    expect(extractSeriesPrefix("敬拜讚美 13")).toBe("敬拜讚美");
  });

  it("extracts prefix from CJK series without space", () => {
    expect(extractSeriesPrefix("敬拜讚美13")).toBe("敬拜讚美");
  });

  it("returns full string when no trailing number", () => {
    expect(extractSeriesPrefix("敬拜讚美")).toBe("敬拜讚美");
  });

  it("returns null for null input", () => {
    expect(extractSeriesPrefix(null)).toBeNull();
  });

  it("extracts prefix from multi-number series", () => {
    expect(extractSeriesPrefix("Vol. 3 No. 7")).toBe("Vol. 3 No.");
  });

  it("extracts prefix from full-width parenthesized CJK series", () => {
    expect(extractSeriesPrefix("敬拜讚美（1）")).toBe("敬拜讚美");
    expect(extractSeriesPrefix("敬拜讚美（22）")).toBe("敬拜讚美");
  });

  it("extracts prefix from ASCII parenthesized series", () => {
    expect(extractSeriesPrefix("Series (Vol. 1)")).toBe("Series Vol.");
  });

  it("extracts prefix from series with trailing non-digit suffix (EP)", () => {
    expect(extractSeriesPrefix("兒童敬拜讚美 14EP")).toBe("兒童敬拜讚美");
    expect(extractSeriesPrefix("兒童敬拜讚美（14EP）")).toBe("兒童敬拜讚美");
  });

  it("returns empty string for pure numeric series", () => {
    expect(extractSeriesPrefix("123")).toBe("");
  });
});

describe("sortAlbumOptions", () => {
  it("sorts albums by trailing number numerically within same series", () => {
    const input = [
      makeOption("Album 13", "敬拜讚美 13", 1),
      makeOption("Album 2", "敬拜讚美 2", 1),
      makeOption("Album 1", "敬拜讚美 1", 1),
      makeOption("Album 12", "敬拜讚美 12", 1),
      makeOption("Album 10", "敬拜讚美 10", 1),
    ];
    const result = sortAlbumOptions(input);
    expect(result.map((a) => a.albumName)).toEqual([
      "Album 1",
      "Album 2",
      "Album 10",
      "Album 12",
      "Album 13",
    ]);
  });

  it("groups different series together by prefix", () => {
    const input = [
      makeOption("B2", "系列B 2", 1),
      makeOption("A2", "系列A 2", 1),
      makeOption("B1", "系列B 1", 1),
      makeOption("A1", "系列A 1", 1),
    ];
    const result = sortAlbumOptions(input);
    expect(result.map((a) => a.albumName)).toEqual(["A1", "A2", "B1", "B2"]);
  });

  it("sorts non-numbered series after numbered within same prefix", () => {
    const input = [
      makeOption("Special", "敬拜讚美 特輯", 1),
      makeOption("Vol2", "敬拜讚美 2", 1),
      makeOption("Vol1", "敬拜讚美 1", 1),
    ];
    const result = sortAlbumOptions(input);
    expect(result.map((a) => a.albumName)).toEqual(["Vol1", "Vol2", "Special"]);
  });

  it("sorts NULL series after all non-null series", () => {
    const input = [
      makeOption("NoSeries", null, 1),
      makeOption("WithSeries2", "敬拜讚美 2", 1),
      makeOption("WithSeries1", "敬拜讚美 1", 1),
    ];
    const result = sortAlbumOptions(input);
    expect(result.map((a) => a.albumName)).toEqual([
      "WithSeries1",
      "WithSeries2",
      "NoSeries",
    ]);
  });

  it("uses albumName as tiebreaker for identical series", () => {
    const input = [
      makeOption("Zeta", "敬拜讚美 1", 1),
      makeOption("Alpha", "敬拜讚美 1", 1),
    ];
    const result = sortAlbumOptions(input);
    expect(result.map((a) => a.albumName)).toEqual(["Alpha", "Zeta"]);
  });

  it("does not mutate the original array", () => {
    const input = [
      makeOption("B", "敬拜讚美 2", 1),
      makeOption("A", "敬拜讚美 1", 1),
    ];
    const original = [...input];
    sortAlbumOptions(input);
    expect(input).toEqual(original);
  });

  it("handles empty array", () => {
    expect(sortAlbumOptions([])).toEqual([]);
  });

  it("handles series without space before number", () => {
    const input = [
      makeOption("A13", "敬拜讚美13", 1),
      makeOption("A2", "敬拜讚美2", 1),
      makeOption("A1", "敬拜讚美1", 1),
    ];
    const result = sortAlbumOptions(input);
    expect(result.map((a) => a.albumName)).toEqual(["A1", "A2", "A13"]);
  });

  it("sorts full-width parenthesized series numerically within same prefix", () => {
    const input = [
      makeOption("詩歌22", "敬拜讚美（22）", 1),
      makeOption("詩歌2", "敬拜讚美（2）", 1),
      makeOption("詩歌1", "敬拜讚美（1）", 1),
      makeOption("詩歌13", "敬拜讚美（13）", 1),
      makeOption("詩歌10", "敬拜讚美（10）", 1),
    ];
    const result = sortAlbumOptions(input);
    expect(result.map((a) => a.albumName)).toEqual([
      "詩歌1",
      "詩歌2",
      "詩歌10",
      "詩歌13",
      "詩歌22",
    ]);
  });

  it("groups parenthesized and non-parenthesized series with same prefix together", () => {
    const input = [
      makeOption("B", "敬拜讚美（2）", 1),
      makeOption("A", "敬拜讚美 1", 1),
    ];
    const result = sortAlbumOptions(input);
    expect(result.map((a) => a.albumName)).toEqual(["A", "B"]);
  });

  it("sorts pure numeric series numerically", () => {
    const input = [
      makeOption("Album 10", "10", 1),
      makeOption("Album 2", "2", 1),
      makeOption("Album 1", "1", 1),
    ];
    const result = sortAlbumOptions(input);
    expect(result.map((a) => a.albumName)).toEqual(["Album 1", "Album 2", "Album 10"]);
  });

  it("sorts EP volume series numerically within same prefix", () => {
    const input = [
      makeOption("EP14", "兒童敬拜讚美（14EP）", 1),
      makeOption("EP2", "兒童敬拜讚美（2EP）", 1),
      makeOption("EP1", "兒童敬拜讚美（1EP）", 1),
      makeOption("EP10", "兒童敬拜讚美（10EP）", 1),
    ];
    const result = sortAlbumOptions(input);
    expect(result.map((a) => a.albumName)).toEqual([
      "EP1",
      "EP2",
      "EP10",
      "EP14",
    ]);
  });
});
