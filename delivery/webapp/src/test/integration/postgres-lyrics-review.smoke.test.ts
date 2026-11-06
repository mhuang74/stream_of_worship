import { afterAll, afterEach, beforeAll, expect, it } from "vitest";
import { eq, inArray } from "drizzle-orm";
import { db, closePostgresSmokeDb, schema } from "@/db";
import { reviewQueue } from "@/lib/lyrics/review";

let reviewerId: number;
let otherUserId: number;
const songIds: string[] = [];
const createdAt = new Date("2025-01-01");

beforeAll(async () => {
  const users = await db.insert(schema.users).values([
    { name: "Lyrics Reviewer", email: "lyrics-reviewer@smoke.test" },
    { name: "Other Listener", email: "lyrics-listener@smoke.test" },
  ]).returning({ id: schema.users.id });
  [reviewerId, otherUserId] = users.map(user => user.id);
});

afterEach(async () => {
  if (!songIds.length) return;
  // Deleting recordings cascades to feedback, including soft-deleted recordings.
  await db.delete(schema.recordings).where(inArray(schema.recordings.songId, songIds));
  await db.delete(schema.songs).where(inArray(schema.songs.id, songIds));
  songIds.length = 0;
});

afterAll(async () => {
  try {
    await db.delete(schema.users).where(inArray(schema.users.id, [reviewerId, otherUserId]));
  } finally {
    await closePostgresSmokeDb();
  }
});

async function addSong(key: string) {
  const id = `lyrics-smoke-${key}`;
  songIds.push(id);
  await db.insert(schema.songs).values({
    id, title: `Lyrics Smoke ${key}`, albumName: "Lyrics Smoke Album",
    sourceUrl: `https://example.com/${id}`, scrapedAt: "2025-01-01", createdAt,
  });
  return addRecording(id, "target");
}

async function addRecording(
  songId: string,
  suffix: string,
  overrides: Partial<typeof schema.recordings.$inferInsert> = {},
) {
  const contentHash = `${songId}-${suffix}`;
  await db.insert(schema.recordings).values({
    contentHash, songId, hashPrefix: contentHash, originalFilename: "song.mp3",
    fileSizeBytes: 1000, importedAt: "2025-01-01", r2AudioUrl: "https://audio.example/song.mp3",
    visibilityStatus: "review", createdAt, ...overrides,
  });
  return contentHash;
}

async function addFeedback(
  recordingContentHash: string,
  overrides: Partial<typeof schema.lyricsFeedback.$inferInsert> = {},
) {
  await db.insert(schema.lyricsFeedback).values({
    id: `${recordingContentHash}-${overrides.userId ?? reviewerId}`,
    userId: reviewerId, recordingContentHash, rating: "happy", ...overrides,
  });
}

it.each([
  ["happy", false, false], ["happy", true, false],
  ["sad", false, false], ["sad", true, false],
  ["happy", false, true], ["happy", true, true],
  ["sad", false, true], ["sad", true, true],
] as const)("excludes %s feedback (resolved=%s, other user=%s)", async (rating, resolved, otherUser) => {
  const target = await addSong("reviewed");
  const unseen = await addSong("unseen");
  await addFeedback(target, {
    rating, reason: rating === "sad" ? "timing" : null,
    resolvedAt: resolved ? createdAt : null,
    userId: otherUser ? otherUserId : reviewerId,
  });

  expect(await reviewQueue("", false, 0)).toEqual({
    recordings: [expect.objectContaining({ contentHash: unseen, reviewed: false })], hasMore: false,
  });
});

it.each(["review", "visible", "hold", null])(
  "counts feedback on another active recording with visibility %s, even without audio",
  async visibilityStatus => {
    const target = await addSong("sibling");
    const sibling = await addRecording("lyrics-smoke-sibling", "sibling", {
      visibilityStatus, r2AudioUrl: null,
    });
    await addFeedback(sibling, { userId: otherUserId, resolvedAt: createdAt });

    expect((await reviewQueue("", false, 0)).recordings).toEqual([]);
    expect((await reviewQueue("", true, 0)).recordings).toEqual([
      expect.objectContaining({ contentHash: target, reviewed: true }),
    ]);
  },
);

it("ignores feedback attached only to soft-deleted recordings", async () => {
  const target = await addSong("deleted-feedback");
  const deleted = await addRecording("lyrics-smoke-deleted-feedback", "deleted", { deletedAt: createdAt });
  await addFeedback(deleted, { rating: "sad", reason: "timing", userId: otherUserId });
  await addFeedback(deleted, { resolvedAt: createdAt });

  for (const [query, showReviewed] of [["", false], ["", true], ["deleted-feedback", false]] as const) {
    expect((await reviewQueue(query, showReviewed, 0)).recordings).toEqual([
      expect.objectContaining({ contentHash: target, reviewed: false }),
    ]);
  }
});

it("searches titles and albums inclusively and marks reviewed songs without duplicates", async () => {
  const target = await addSong("search");
  await addSong("unseen");
  await addFeedback(target, { resolvedAt: createdAt });
  await addFeedback(target, { userId: otherUserId, rating: "sad", reason: "timing" });
  const sibling = await addRecording("lyrics-smoke-search", "sibling", { visibilityStatus: "hold" });
  await addFeedback(sibling);

  const titleSearch = await reviewQueue("SMOKE SEARCH", false, 0);
  expect(titleSearch.recordings).toEqual([expect.objectContaining({ contentHash: target, reviewed: true })]);
  for (const [query, showReviewed] of [["", true], ["smoke album", false]] as const) {
    const result = await reviewQueue(query, showReviewed, 0);
    expect(result.recordings).toHaveLength(2);
    expect(result.recordings.map(row => row.reviewed)).toEqual([true, false]);
    expect(new Set(result.recordings.map(row => row.contentHash)).size).toBe(2);
  }
});

it("preserves recording selection and prioritizes recent unresolved Poor feedback", async () => {
  const older = await addSong("a-older");
  const recent = await addSong("b-recent");
  await addRecording("lyrics-smoke-b-recent", "newer", { createdAt: new Date("2025-06-01") });
  await addFeedback(older, { rating: "sad", reason: "timing", updatedAt: createdAt });
  await addFeedback(recent, { rating: "sad", reason: "timing", updatedAt: new Date("2025-02-01") });

  expect((await reviewQueue("", true, 0)).recordings.map(row => row.contentHash)).toEqual([recent, older]);
  expect((await reviewQueue("", false, 0)).recordings).toEqual([]);
});

it("filters reviewed songs before pagination and keeps one recording per song", async () => {
  const expected: string[] = [];
  for (let i = 0; i < 53; i++) {
    const key = `page-${String(i).padStart(2, "0")}`;
    const target = await addSong(key);
    await addRecording(`lyrics-smoke-${key}`, "older", { createdAt: new Date("2024-01-01") });
    if (i === 0 || i === 25) {
      await addFeedback(target);
      await addFeedback(target, { userId: otherUserId, resolvedAt: createdAt });
    } else expected.push(target);
  }

  const first = await reviewQueue("", false, 0);
  expect(first.hasMore).toBe(true);
  expect(first.recordings.map(row => row.contentHash)).toEqual(expected.slice(0, 50));
  expect(first.recordings.every(row => !row.reviewed)).toBe(true);
  const second = await reviewQueue("", false, 50);
  expect(second.hasMore).toBe(false);
  expect(second.recordings.map(row => row.contentHash)).toEqual(expected.slice(50));
  expect((await reviewQueue("", false, 51)).recordings).toEqual([]);
});

it("removes a newly rated song and restores it only after the last active-recording feedback is retracted", async () => {
  const target = await addSong("save-retract");
  expect((await reviewQueue("", false, 0)).recordings).toHaveLength(1);
  await addFeedback(target);
  expect((await reviewQueue("", false, 0)).recordings).toEqual([]);
  await addFeedback(target, { userId: otherUserId, resolvedAt: createdAt });
  await db.delete(schema.lyricsFeedback).where(eq(schema.lyricsFeedback.id, `${target}-${reviewerId}`));
  expect((await reviewQueue("", false, 0)).recordings).toEqual([]);
  await db.delete(schema.lyricsFeedback).where(eq(schema.lyricsFeedback.id, `${target}-${otherUserId}`));
  expect((await reviewQueue("", false, 0)).recordings).toEqual([
    expect.objectContaining({ contentHash: target, reviewed: false }),
  ]);
});

it("still excludes songs without an eligible Review recording", async () => {
  for (const [key, overrides] of [
    ["published", { visibilityStatus: "visible" }],
    ["hold", { visibilityStatus: "hold" }],
    ["no-audio", { r2AudioUrl: null }],
    ["deleted-recording", { deletedAt: createdAt }],
  ] as const) {
    const target = await addSong(key);
    await db.update(schema.recordings).set(overrides).where(eq(schema.recordings.contentHash, target));
  }
  await addSong("deleted-song");
  await db.update(schema.songs).set({ deletedAt: createdAt }).where(eq(schema.songs.id, "lyrics-smoke-deleted-song"));
  expect((await reviewQueue("", true, 0)).recordings).toEqual([]);
  expect((await reviewQueue("Lyrics Smoke", false, 0)).recordings).toEqual([]);
});
