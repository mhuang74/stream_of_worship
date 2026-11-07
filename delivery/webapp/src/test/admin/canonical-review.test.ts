import { beforeEach, expect, it, vi } from "vitest";
import { canonicalReviewLyrics, reviewQueue, reviewSets } from "@/lib/lyrics/review";
import { PgDialect } from "drizzle-orm/pg-core";
const mocks = vi.hoisted(() => ({ execute: vi.fn(), signed: vi.fn() }));
vi.mock("@/db", () => ({ db: { execute: mocks.execute } }));
vi.mock("@/lib/r2/client", () => ({ createR2ClientFromEnv: () => ({ getLrcSignedUrl: mocks.signed }) }));
beforeEach(() => { vi.resetAllMocks(); mocks.signed.mockResolvedValue({ url: "https://canonical" }); });
it("loads canonical content without cache and preserves blank timestamped lines", async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response("[00:01.00]歌詞\n[00:04.00]\n[00:08.00]歌詞"));
  vi.stubGlobal("fetch", fetcher);
  const result = await canonicalReviewLyrics("prefix");
  expect(result.lines).toHaveLength(3);
  expect(result.lines[1]).toEqual({ timeSeconds: 4, text: "" });
  expect(result.synced).toBe(true);
  expect(fetcher).toHaveBeenCalledWith("https://canonical", { cache: "no-store" });
});
it("treats only 404 as missing and propagates upstream failures", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 404 })));
  expect((await canonicalReviewLyrics("prefix")).synced).toBe(false);
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 403 })));
  await expect(canonicalReviewLyrics("prefix")).rejects.toThrow();
});
it("binds search text as data and limits the Review-only queue", async () => {
  mocks.execute.mockResolvedValue({ rows: Array.from({ length: 51 }, (_, i) => ({ contentHash: String(i) })) });
  const result = await reviewQueue("' OR true --", false, 0);
  expect(result.recordings).toHaveLength(50);
  expect(result.hasMore).toBe(true);
  const query = new PgDialect().sqlToQuery(mocks.execute.mock.calls[0][0]);
  expect(query.sql).toContain("r.visibility_status = 'review'");
  expect(query.sql).not.toContain("' OR true --");
  expect(query.params).toContain("' OR true --");
  expect(query.sql).toContain("p.latest_sad_at DESC NULLS LAST");
});
it("subsets the pool by bound set id only when a set is selected", async () => {
  mocks.execute.mockResolvedValue({ rows: [] });
  await reviewQueue("", false, 0);
  const unfiltered = new PgDialect().sqlToQuery(mocks.execute.mock.calls[0][0]);
  expect(unfiltered.sql).not.toContain("lyrics_review_set_songs");

  await reviewQueue("", false, 0, "set-1");
  const filtered = new PgDialect().sqlToQuery(mocks.execute.mock.calls[1][0]);
  expect(filtered.sql).toContain("EXISTS (SELECT 1 FROM lyrics_review_set_songs lrs");
  expect(filtered.sql).not.toContain("set-1");
  expect(filtered.params).toContain("set-1");
  // Set filtering subsets the pool; the cascade order is unchanged.
  expect(filtered.sql).toContain("p.latest_sad_at DESC NULLS LAST");
});
it("counts only non-deleted set members in progress totals", async () => {
  mocks.execute.mockResolvedValue([{ id: "set-1", name: "march", total: 2, done: 1 }]);
  expect(await reviewSets()).toEqual([{ id: "set-1", name: "march", total: 2, done: 1 }]);
  const query = new PgDialect().sqlToQuery(mocks.execute.mock.calls[0][0]);
  expect(query.sql).toContain("LEFT JOIN songs song ON song.id = m.song_id AND song.deleted_at IS NULL");
  expect(query.sql).toContain("COUNT(song.id)");
  expect(query.sql).toContain("reviewed_recording.deleted_at IS NULL");
});
