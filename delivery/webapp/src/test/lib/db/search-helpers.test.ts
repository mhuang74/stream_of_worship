import { describe, it, expect } from "vitest";
import {
  buildKeyRegex,
  buildKeyTokenRegex,
  buildCatalogKeyTokenRegex,
  buildEffectiveKeyPredicate,
  buildBpmPredicate,
  buildBpmPredicates,
  buildVisibilityCondition,
  effectiveKeyMatchesFilter,
  isValidPitchClass,
  isValidBpmBand,
  parseKeysParam,
  parseBpmRangeParam,
  parseBpmRangeParams,
} from "@/lib/db/search-helpers";
import { PgDialect } from "drizzle-orm/pg-core";

const dialect = new PgDialect();

describe("buildKeyRegex", () => {
  it("builds alternation regex for multiple keys", () => {
    const regex = buildKeyRegex(["D", "A"]);
    expect(regex).toBe("^(D|A)(maj|major|minor|min)?(?!\\w)");
  });

  it("escapes special regex characters in key names", () => {
    const regex = buildKeyRegex(["C#", "F#"]);
    expect(regex).toBe("^(C#|F#)(maj|major|minor|min)?(?!\\w)");
  });

  it("handles single key", () => {
    const regex = buildKeyRegex(["G"]);
    expect(regex).toBe("^(G)(maj|major|minor|min)?(?!\\w)");
  });
});

describe("effectiveKeyMatchesFilter", () => {
  it("matches exact natural keys and range endpoints", () => {
    expect(effectiveKeyMatchesFilter({ catalogKey: "A", keys: ["A"] })).toBe(true);
    expect(effectiveKeyMatchesFilter({ catalogKey: "G-A", keys: ["A"] })).toBe(true);
  });

  it("does not let natural keys match sharp or flat pitch classes", () => {
    expect(effectiveKeyMatchesFilter({ catalogKey: "A#", keys: ["A"] })).toBe(false);
    expect(effectiveKeyMatchesFilter({ catalogKey: "Bb", keys: ["A"] })).toBe(false);
  });

  it("matches enharmonic catalog keys for selected sharp pitch classes", () => {
    expect(effectiveKeyMatchesFilter({ catalogKey: "A#", keys: ["A#"] })).toBe(true);
    expect(effectiveKeyMatchesFilter({ catalogKey: "Bb", keys: ["A#"] })).toBe(true);
  });

  it("matches any displayed key in catalog ranges", () => {
    expect(effectiveKeyMatchesFilter({ catalogKey: "E-F", keys: ["E"] })).toBe(true);
    expect(effectiveKeyMatchesFilter({ catalogKey: "D-F", keys: ["A"] })).toBe(false);
  });

  it("prefers parseable catalog keys over mismatched recording keys", () => {
    expect(
      effectiveKeyMatchesFilter({ catalogKey: "D-F", recordingKey: "A", keys: ["A"] })
    ).toBe(false);
    expect(
      effectiveKeyMatchesFilter({ catalogKey: "A-C", recordingKey: "E", keys: ["A"] })
    ).toBe(true);
  });

  it("falls back to recording keys only when catalog key is missing", () => {
    expect(effectiveKeyMatchesFilter({ catalogKey: null, recordingKey: "A", keys: ["A"] })).toBe(
      true
    );
    expect(
      effectiveKeyMatchesFilter({ catalogKey: "unknown", recordingKey: "A", keys: ["A"] })
    ).toBe(false);
  });
});

describe("effective key SQL helpers", () => {
  it("builds token regexes with exact accidental boundaries", () => {
    expect("A").toMatch(new RegExp(buildKeyTokenRegex(["A"]), "i"));
    expect("A major").toMatch(new RegExp(buildKeyTokenRegex(["A"]), "i"));
    expect("A#").not.toMatch(new RegExp(buildKeyTokenRegex(["A"]), "i"));
    expect("Bb").toMatch(new RegExp(buildKeyTokenRegex(["A#"]), "i"));
    expect("G-A").toMatch(new RegExp(buildCatalogKeyTokenRegex(["A"]), "i"));
    expect("D-F").not.toMatch(new RegExp(buildCatalogKeyTokenRegex(["A"]), "i"));
  });

  it("builds effective-key SQL that checks catalog before recording fallback", () => {
    const sqlFragment = buildEffectiveKeyPredicate(["A"], "songs", "r2");
    const query = dialect.sqlToQuery(sqlFragment);

    expect(query.sql).toContain("songs.musical_key");
    expect(query.sql).toContain("songs.musical_key_start_pitch_class");
    expect(query.sql).toContain("songs.musical_key_end_pitch_class");
    expect(query.sql).toContain("r2.musical_key");
    expect(query.sql).toContain("NOT (");
    expect(query.params).toContain(9);
  });
});

describe("buildBpmPredicate", () => {
  it("slow: tempo_bpm < 90 (default alias r)", () => {
    const sqlFragment = buildBpmPredicate("slow");
    const query = dialect.sqlToQuery(sqlFragment);
    expect(query.sql).toContain("r.tempo_bpm");
    expect(query.sql).toContain("< 90");
  });

  it("moderate: 90 <= tempo_bpm < 120 (default alias r)", () => {
    const sqlFragment = buildBpmPredicate("moderate");
    const query = dialect.sqlToQuery(sqlFragment);
    expect(query.sql).toContain("r.tempo_bpm");
    expect(query.sql).toContain(">= 90");
    expect(query.sql).toContain("< 120");
  });

  it("fast: tempo_bpm >= 120 (default alias r)", () => {
    const sqlFragment = buildBpmPredicate("fast");
    const query = dialect.sqlToQuery(sqlFragment);
    expect(query.sql).toContain("r.tempo_bpm");
    expect(query.sql).toContain(">= 120");
  });

  it("uses custom alias when provided", () => {
    const sqlFragment = buildBpmPredicate("slow", "r3");
    const query = dialect.sqlToQuery(sqlFragment);
    expect(query.sql).toContain("r3.tempo_bpm");
    expect(query.sql).not.toContain("r.tempo_bpm");
  });
});

describe("buildBpmPredicates", () => {
  it("returns undefined for empty array", () => {
    expect(buildBpmPredicates([])).toBeUndefined();
  });

  it("returns single predicate (no OR) for one band", () => {
    const sqlFragment = buildBpmPredicates(["slow"]);
    expect(sqlFragment).toBeDefined();
    const query = dialect.sqlToQuery(sqlFragment!);
    expect(query.sql).toContain("r.tempo_bpm");
    expect(query.sql).toContain("< 90");
    expect(query.sql).not.toContain("OR");
  });

  it("ORs multiple bands together", () => {
    const sqlFragment = buildBpmPredicates(["slow", "fast"]);
    expect(sqlFragment).toBeDefined();
    const query = dialect.sqlToQuery(sqlFragment!);
    expect(query.sql).toContain("OR");
    expect(query.sql).toContain("< 90");
    expect(query.sql).toContain(">= 120");
  });

  it("uses custom alias", () => {
    const sqlFragment = buildBpmPredicates(["slow"], "r3");
    const query = dialect.sqlToQuery(sqlFragment!);
    expect(query.sql).toContain("r3.tempo_bpm");
  });
});

describe("buildVisibilityCondition", () => {
  it("returns undefined for undefined visibilityStatus", () => {
    expect(buildVisibilityCondition(undefined, "r2")).toBeUndefined();
  });

  it("returns undefined for 'all'", () => {
    expect(buildVisibilityCondition("all", "r2")).toBeUndefined();
  });

  it("returns undefined for empty array", () => {
    expect(buildVisibilityCondition([], "r2")).toBeUndefined();
  });

  it("builds = ANY() for array", () => {
    const sqlFragment = buildVisibilityCondition(["published", "review"], "r2");
    expect(sqlFragment).toBeDefined();
    const query = dialect.sqlToQuery(sqlFragment!);
    expect(query.sql).toContain("r2.visibility_status");
    expect(query.sql).toContain("ANY");
  });

  it("builds = for single string", () => {
    const sqlFragment = buildVisibilityCondition("published", "r3");
    expect(sqlFragment).toBeDefined();
    const query = dialect.sqlToQuery(sqlFragment!);
    expect(query.sql).toContain("r3.visibility_status");
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

describe("parseBpmRangeParams", () => {
  it("parses valid bands", () => {
    expect(parseBpmRangeParams(["slow"])).toEqual(["slow"]);
    expect(parseBpmRangeParams(["slow", "fast"])).toEqual(["slow", "fast"]);
  });

  it("filters out invalid bands", () => {
    expect(parseBpmRangeParams(["slow", "medium", "fast"])).toEqual(["slow", "fast"]);
  });

  it("deduplicates bands", () => {
    expect(parseBpmRangeParams(["slow", "slow", "fast"])).toEqual(["slow", "fast"]);
  });

  it("returns undefined for empty array", () => {
    expect(parseBpmRangeParams([])).toBeUndefined();
  });

  it("returns undefined when all invalid", () => {
    expect(parseBpmRangeParams(["medium", "unknown"])).toBeUndefined();
  });
});
