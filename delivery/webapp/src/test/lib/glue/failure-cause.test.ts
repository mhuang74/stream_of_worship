import { describe, expect, it } from "vitest";

import { parseGlueFailureCause } from "@/lib/glue/failure-cause";

describe("parseGlueFailureCause", () => {
  it("parses each worker cause token", () => {
    expect(parseGlueFailureCause("unsupported-window supportedBeats=8")).toEqual({
      kind: "unsupported-window",
      supportedBeats: 8,
    });
    expect(parseGlueFailureCause("unsupported-window")).toEqual({
      kind: "unsupported-window",
      supportedBeats: null,
    });
    expect(parseGlueFailureCause("insufficient-search-source")).toEqual({
      kind: "insufficient-search-source",
    });
    expect(parseGlueFailureCause("source-audio-unavailable side=A")).toEqual({
      kind: "source-audio-unavailable",
      side: "A",
    });
    expect(parseGlueFailureCause("decode-failed side=B")).toEqual({
      kind: "decode-failed",
      side: "B",
    });
  });

  it("rejects an unknown beats value and arbitrary text", () => {
    expect(parseGlueFailureCause("unsupported-window supportedBeats=3")).toBeNull();
    expect(parseGlueFailureCause("something went wrong")).toBeNull();
    expect(parseGlueFailureCause("decode-failed side=Z")).toBeNull();
    expect(parseGlueFailureCause(null)).toBeNull();
    expect(parseGlueFailureCause("")).toBeNull();
  });
});
