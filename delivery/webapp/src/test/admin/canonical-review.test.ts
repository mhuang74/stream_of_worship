import { beforeEach, expect, it, vi } from "vitest";
import { canonicalReviewLyrics, reviewQueue } from "@/lib/lyrics/review";
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
  const result = await reviewQueue(42, "' OR true --", false, 0);
  expect(result.recordings).toHaveLength(50);
  expect(result.hasMore).toBe(true);
  const query = new PgDialect().sqlToQuery(mocks.execute.mock.calls[0][0]);
  expect(query.sql).toContain("r.visibility_status = 'review'");
  expect(query.sql).not.toContain("' OR true --");
  expect(query.params).toContain("' OR true --");
  expect(query.sql).toContain("mine.resolved_at IS NULL");
  expect(query.sql).toContain("p.latest_sad_at DESC NULLS LAST");
});
