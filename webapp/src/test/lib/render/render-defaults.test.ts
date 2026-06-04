import { describe, it, expect } from "vitest"
import { buildInitialRenderData } from "@/lib/render/render-defaults"

describe("buildInitialRenderData", () => {
  it("returns app defaults when no job or settings", () => {
    const result = buildInitialRenderData(null, null)
    expect(result.fontFamily).toBe("noto_serif_tc")
    expect(result.template).toBe("dark")
    expect(result.resolution).toBe("720p")
    expect(result.fontSizePreset).toBe("M")
  })

  it("uses user settings defaultFontFamily when no latest job", () => {
    const result = buildInitialRenderData(null, {
      defaultFontFamily: "lxgw_wenkai_tc",
    })
    expect(result.fontFamily).toBe("lxgw_wenkai_tc")
  })

  it("uses latest completed job fontFamily over user settings", () => {
    const result = buildInitialRenderData(
      { fontFamily: "chiron_goround_tc", template: "dark" },
      { defaultFontFamily: "lxgw_wenkai_tc" }
    )
    expect(result.fontFamily).toBe("chiron_goround_tc")
  })

  it("uses latest failed job fontFamily", () => {
    const result = buildInitialRenderData(
      { fontFamily: "chocolate_classical_sans", template: "dark" },
      null
    )
    expect(result.fontFamily).toBe("chocolate_classical_sans")
  })

  it("falls back to noto_serif_tc for invalid job fontFamily", () => {
    const result = buildInitialRenderData(
      { fontFamily: "bad_value", template: "dark" },
      null
    )
    expect(result.fontFamily).toBe("noto_serif_tc")
  })

  it("job fontFamily wins over different user settings fontFamily", () => {
    const result = buildInitialRenderData(
      { fontFamily: "chiron_goround_tc", template: "dark" },
      { defaultFontFamily: "lxgw_wenkai_tc" }
    )
    expect(result.fontFamily).toBe("chiron_goround_tc")
  })

  it("normalizes invalid user settings defaultFontFamily", () => {
    const result = buildInitialRenderData(null, {
      defaultFontFamily: "invalid_font",
    })
    expect(result.fontFamily).toBe("noto_serif_tc")
  })

  it("job with null fontFamily falls back to user settings", () => {
    const result = buildInitialRenderData(
      { fontFamily: null, template: "dark" },
      { defaultFontFamily: "lxgw_wenkai_tc" }
    )
    expect(result.fontFamily).toBe("lxgw_wenkai_tc")
  })

  it("merges job template and resolution", () => {
    const result = buildInitialRenderData(
      { template: "gradient_warm", resolution: "1080p", fontFamily: "noto_serif_tc" },
      { defaultVideoTemplate: "dark", defaultResolution: "720p" }
    )
    expect(result.template).toBe("gradient_warm")
    expect(result.resolution).toBe("1080p")
  })

  it("uses settings template when no job template", () => {
    const result = buildInitialRenderData(
      { fontFamily: "noto_serif_tc" },
      { defaultVideoTemplate: "gradient_blue" }
    )
    expect(result.template).toBe("gradient_blue")
  })
})
