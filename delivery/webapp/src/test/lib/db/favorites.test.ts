import { describe, it, expect } from "vitest";
import { PgDialect } from "drizzle-orm/pg-core";
import {
  favoritesFirstOrder,
  favoritesOnlyPredicate,
} from "@/lib/db/favorites";

const dialect = new PgDialect();

describe("favoritesOnlyPredicate", () => {
  it("returns undefined when ids are absent", () => {
    expect(favoritesOnlyPredicate(undefined)).toBeUndefined();
    expect(favoritesOnlyPredicate([])).toBeUndefined();
  });

  it("emits ANY(ARRAY[...]::text[]) — not a parenthesized list (regression for NeonDbError)", () => {
    const query = dialect.sqlToQuery(favoritesOnlyPredicate(["a", "b", "c"])!);
    expect(query.sql).toContain("ANY(ARRAY[");
    expect(query.sql).toContain("::text[]");
    expect(query.sql).not.toMatch(/ANY\(\(/);
    expect(query.params).toEqual(["a", "b", "c"]);
  });

  it("threads every favorite id as a bound parameter", () => {
    const query = dialect.sqlToQuery(favoritesOnlyPredicate(["song-1"])!);
    expect(query.params).toEqual(["song-1"]);
  });
});

describe("favoritesFirstOrder", () => {
  it("returns undefined when ids are absent", () => {
    expect(favoritesFirstOrder(undefined)).toBeUndefined();
    expect(favoritesFirstOrder([])).toBeUndefined();
  });

  it("emits CASE WHEN ... = ANY(ARRAY[...]::text[]) THEN 0 ELSE 1 END", () => {
    const query = dialect.sqlToQuery(favoritesFirstOrder(["fav-1", "fav-2"])!);
    expect(query.sql).toContain("CASE WHEN");
    expect(query.sql).toContain("= ANY(ARRAY[");
    expect(query.sql).toContain("::text[]");
    expect(query.sql).toContain("THEN 0 ELSE 1 END");
    expect(query.sql).not.toMatch(/ANY\(\(/);
    expect(query.params).toEqual(["fav-1", "fav-2"]);
  });
});
