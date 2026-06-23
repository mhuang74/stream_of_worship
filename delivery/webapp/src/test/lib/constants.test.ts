import { describe, it, expect } from "vitest"
import { normalizeFontFamily } from "@/lib/constants"

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
