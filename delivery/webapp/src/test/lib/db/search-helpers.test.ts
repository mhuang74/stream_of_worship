import { describe, it, expect } from "vitest";
import {
  buildKeyRegex,
  buildBpmPredicate,
  isValidPitchClass,
  isValidBpmBand,
  parseKeysParam,
  parseBpmRangeParam,
} from "@/lib/db/search-helpers";
import { PgDialect } from "drizzle-orm/pg-core";

const dialect = new PgDialect();

describe("buildKeyRegex", () => {
  it("builds alternation regex for multiple keys", () => {
    const regex = buildKeyRegex(["D", "A"]);
    expect(regex).toBe("^(D|A)(maj|major|minor|min)?\\b");
  });

  it("escapes special regex characters in key names", () => {
    const regex = buildKeyRegex(["C#", "F#"]);
    expect(regex).toBe("^(C#|F#)(maj|major|minor|min)?\\b");
  });

  it("handles single key", () => {
    const regex = buildKeyRegex(["G"]);
    expect(regex).toBe("^(G)(maj|major|minor|min)?\\b");
  });
});

describe("buildBpmPredicate", () => {
  it("slow: tempo_bpm < 90", () => {
    const sqlFragment = buildBpmPredicate("slow");
    const query = dialect.sqlToQuery(sqlFragment);
    expect(query.sql).toContain("< 90");
  });

  it("moderate: 90 <= tempo_bpm < 120", () => {
    const sqlFragment = buildBpmPredicate("moderate");
    const query = dialect.sqlToQuery(sqlFragment);
    expect(query.sql).toContain(">= 90");
    expect(query.sql).toContain("< 120");
  });

  it("fast: tempo_bpm >= 120", () => {
    const sqlFragment = buildBpmPredicate("fast");
    const query = dialect.sqlToQuery(sqlFragment);
    expect(query.sql).toContain(">= 120");
  });
});

describe("isValidPitchClass", () => {
  it("returns true for valid pitch classes", () => {
    expect(isValidPitchClass("C")).toBe(true);
    expect(isValidPitchClass("C#")).toBe(true);
    expect(isValidPitchClass("B")).toBe(true);
  });

  it("returns false for invalid pitch classes", () => {
    expect(isValidPitchClass("H")).toBe(false);
    expect(isValidPitchClass("Db")).toBe(false);
    expect(isValidPitchClass("")).toBe(false);
  });
});

describe("isValidBpmBand", () => {
  it("returns true for valid bands", () => {
    expect(isValidBpmBand("slow")).toBe(true);
    expect(isValidBpmBand("moderate")).toBe(true);
    expect(isValidBpmBand("fast")).toBe(true);
  });

  it("returns false for invalid bands", () => {
    expect(isValidBpmBand("medium")).toBe(false);
    expect(isValidBpmBand("")).toBe(false);
  });
});

describe("parseKeysParam", () => {
  it("parses comma-separated keys", () => {
    expect(parseKeysParam("D,A")).toEqual(["D", "A"]);
  });

  it("trims whitespace", () => {
    expect(parseKeysParam(" D , A ")).toEqual(["D", "A"]);
  });

  it("filters out invalid pitch classes", () => {
    expect(parseKeysParam("D,H,Db")).toEqual(["D"]);
  });

  it("deduplicates keys", () => {
    expect(parseKeysParam("D,A,D")).toEqual(["D", "A"]);
  });

  it("returns undefined for null", () => {
    expect(parseKeysParam(null)).toBeUndefined();
  });

  it("returns undefined when no valid keys", () => {
    expect(parseKeysParam("H,Db")).toBeUndefined();
  });

  it("returns undefined for empty string", () => {
    expect(parseKeysParam("")).toBeUndefined();
  });
});

describe("parseBpmRangeParam", () => {
  it("parses valid band", () => {
    expect(parseBpmRangeParam("slow")).toBe("slow");
    expect(parseBpmRangeParam("moderate")).toBe("moderate");
    expect(parseBpmRangeParam("fast")).toBe("fast");
  });

  it("returns undefined for null", () => {
    expect(parseBpmRangeParam(null)).toBeUndefined();
  });

  it("returns undefined for invalid band", () => {
    expect(parseBpmRangeParam("medium")).toBeUndefined();
  });
});
