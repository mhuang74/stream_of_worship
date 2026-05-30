import { describe, it, expect, beforeEach, vi } from "vitest";
import { findTopMatchingLines } from "@/lib/db/songs";
import { db } from "@/db";
import { PgDialect } from "drizzle-orm/pg-core";

vi.mock("@/db", () => ({
  db: {
    execute: vi.fn(),
  },
}));

const dialect = new PgDialect();

const NON_ZERO_EMBEDDING = Array.from({ length: 1536 }, () => 0.01);

describe("findTopMatchingLines", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("returns empty Map for empty songIds without calling DB", async () => {
    const result = await findTopMatchingLines(NON_ZERO_EMBEDDING, []);
    expect(result.size).toBe(0);
    expect(db.execute).not.toHaveBeenCalled();
  });

  it("builds ARRAY[] syntax for ANY() clause", async () => {
    vi.mocked(db.execute).mockResolvedValue({ rows: [] } as unknown as Awaited<ReturnType<typeof db.execute>>);
    await findTopMatchingLines(NON_ZERO_EMBEDDING, ["song-1", "song-2"]);
    const callArgs = vi.mocked(db.execute).mock.calls[0][0];
    const query = dialect.sqlToQuery(callArgs);
    expect(query.sql).toContain("ARRAY[");
    expect(query.sql).toContain("::text[]");
  });

  it("throws on invalid embedding (wrong dimensions)", async () => {
    const badEmbedding = [1, 2, 3];
    await expect(
      findTopMatchingLines(badEmbedding, ["song-1"])
    ).rejects.toThrow("Invalid embedding");
  });

  it("throws on embedding with NaN", async () => {
    const nanEmbedding = Array.from({ length: 1536 }, (_, i) =>
      i === 0 ? NaN : 0.01
    );
    await expect(
      findTopMatchingLines(nanEmbedding, ["song-1"])
    ).rejects.toThrow("Invalid embedding");
  });
});
