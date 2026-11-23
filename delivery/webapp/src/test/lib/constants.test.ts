import { describe, it, expect } from "vitest"
import { normalizeFontFamily, normalizeTitleCardDuration } from "@/lib/constants"

describe("normalizeFontFamily", () => {
  it("returns valid font family values unchanged", () => {
    expect(normalizeFontFamily("lxgw_wenkai_tc")).toBe("lxgw_wenkai_tc")
    expect(normalizeFontFamily("chocolate_classical_sans")).toBe("chocolate_classical_sans")
    expect(normalizeFontFamily("chiron_goround_tc")).toBe("chiron_goround_tc")
    expect(normalizeFontFamily("noto_serif_tc")).toBe("noto_serif_tc")
  })

  it("returns noto_serif_tc for null", () => {
    expect(normalizeFontFamily(null)).toBe("noto_serif_tc")
  })

  it("returns noto_serif_tc for undefined", () => {
    expect(normalizeFontFamily(undefined)).toBe("noto_serif_tc")
  })

  it("returns noto_serif_tc for empty string", () => {
    expect(normalizeFontFamily("")).toBe("noto_serif_tc")
  })

  it("returns noto_serif_tc for unknown string", () => {
    expect(normalizeFontFamily("bad_value")).toBe("noto_serif_tc")
    expect(normalizeFontFamily("Arial")).toBe("noto_serif_tc")
  })

  it("returns noto_serif_tc for non-string types", () => {
    expect(normalizeFontFamily(123)).toBe("noto_serif_tc")
    expect(normalizeFontFamily(true)).toBe("noto_serif_tc")
    expect(normalizeFontFamily({})).toBe("noto_serif_tc")
  })
})

describe("normalizeTitleCardDuration", () => {
  it("passes offered durations through", () => {
    expect(normalizeTitleCardDuration(5)).toBe(5);
    expect(normalizeTitleCardDuration(10)).toBe(10);
    expect(normalizeTitleCardDuration(15)).toBe(15);
  });

  it("falls back to the default for missing or non-numeric values", () => {
    expect(normalizeTitleCardDuration(null)).toBe(10);
    expect(normalizeTitleCardDuration(undefined)).toBe(10);
    expect(normalizeTitleCardDuration(Number.NaN)).toBe(10);
    expect(normalizeTitleCardDuration("10")).toBe(10);
  });

  it("rounds a legacy duration down onto the offered range", () => {
    expect(normalizeTitleCardDuration(20)).toBe(15);
    expect(normalizeTitleCardDuration(25)).toBe(15);
    expect(normalizeTitleCardDuration(30)).toBe(15);
    expect(normalizeTitleCardDuration(19)).toBe(15);
    expect(normalizeTitleCardDuration(7)).toBe(5);
  });

  it("clamps a too-short legacy duration up to the shortest offered", () => {
    expect(normalizeTitleCardDuration(1)).toBe(5);
    expect(normalizeTitleCardDuration(0)).toBe(5);
  });
});
