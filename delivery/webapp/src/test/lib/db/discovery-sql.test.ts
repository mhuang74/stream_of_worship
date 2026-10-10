import { describe, it, expect, beforeEach, vi } from "vitest";
import { listDiscoverySongs } from "@/lib/db/discovery";
import { db } from "@/db";
import { PgDialect } from "drizzle-orm/pg-core";

vi.mock("@/db", () => ({
  db: {
    execute: vi.fn(),
    query: { songs: { findMany: vi.fn() } },
  },
}));

const dialect = new PgDialect();

describe("listDiscoverySongs SQL", () => {
  beforeEach(() => vi.clearAllMocks());

  it("filters in WHERE not ON, dedupes and orders recency/count", async () => {
    vi.mocked(db.execute).mockResolvedValue({ rows: [] } as never);
    vi.mocked(db.query.songs.findMany).mockResolvedValue([] as never);
    await listDiscoverySongs(7, 20, 0);
    const { sql } = dialect.sqlToQuery(vi.mocked(db.execute).mock.calls[0][0]);
    // predicates live in ranked's WHERE, not the join's ON
    const rankedIdx = sql.indexOf("ranked");
    const joinIdx = sql.indexOf("full join");
    const whereIdx = sql.indexOf("where", joinIdx);
    expect(whereIdx).toBeGreaterThan(joinIdx);
    expect(whereIdx).toBeLessThan(sql.indexOf("limit", whereIdx));
    expect(sql).toContain("not in");
    expect(sql).toContain("visibility_status in ('published', 'review')");
    // params: viewer id used in exclusion subqueries
    expect(sql.match(/\$\d+/g)?.length).toBeGreaterThan(0);
  });
});
