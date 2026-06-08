import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { db, closePostgresSmokeDb, schema } from "@/db";
import { getRenderPageData, getSongsetEditorData, listSongsetSummaries } from "@/lib/db/songsets";
import { eq, inArray } from "drizzle-orm";

let owningUserId: number;
let otherUserId: number;

beforeAll(async () => {
  // 1. Insert users — generatedAlwaysAsIdentity, capture returned IDs
  const insertedUsers = await db
    .insert(schema.users)
    .values([
      { name: "Smoke Owner", email: "smoke-owner@test.com" },
      { name: "Smoke Other", email: "smoke-other@test.com" },
    ])
    .returning({ id: schema.users.id });
  owningUserId = insertedUsers[0].id;
  otherUserId = insertedUsers[1].id;

  // 2. Insert user settings for owning user (custom values differ from defaults)
  await db.insert(schema.userSettings).values({
    userId: owningUserId,
    defaultVideoTemplate: "light",
    defaultResolution: "1080p",
    defaultFontSizePreset: "L",
    defaultFontFamily: "noto_sans_tc",
  });

  // 3. Insert songs
  await db.insert(schema.songs).values([
    { id: "smoke-song-1", title: "Smoke Song Alpha", sourceUrl: "https://example.com/1", scrapedAt: "2025-01-01" },
    { id: "smoke-song-2", title: "Smoke Song Beta", sourceUrl: "https://example.com/2", scrapedAt: "2025-01-01" },
    { id: "smoke-song-3", title: "Smoke Song Gamma", sourceUrl: "https://example.com/3", scrapedAt: "2025-01-01" },
  ]);

  // 4. Insert recordings
  // Note: contentHash (smoke-ch-*) vs hashPrefix (smoke-hp-*) are distinct patterns
  await db.insert(schema.recordings).values([
    {
      contentHash: "smoke-ch-1",
      hashPrefix: "smoke-hp-1",
      songId: "smoke-song-1",
      originalFilename: "alpha.mp3",
      fileSizeBytes: 1000,
      importedAt: "2025-01-01",
      durationSeconds: 180,
      visibilityStatus: "visible",
    },
    {
      contentHash: "smoke-ch-2",
      hashPrefix: "smoke-hp-2",
      songId: "smoke-song-2",
      originalFilename: "beta.mp3",
      fileSizeBytes: 2000,
      importedAt: "2025-01-01",
      durationSeconds: 240,
      visibilityStatus: "visible",
    },
    {
      contentHash: "smoke-ch-3",
      hashPrefix: "smoke-hp-3",
      songId: "smoke-song-3",
      originalFilename: "gamma.mp3",
      fileSizeBytes: 3000,
      importedAt: "2025-01-01",
      durationSeconds: 200,
      deletedAt: new Date("2025-06-01"),
    },
  ]);

  // 5. Insert songset
  await db.insert(schema.songsets).values({
    id: "smoke-songset-hot-pages",
    userId: owningUserId,
    name: "Smoke Hot Pages Songset",
    description: "Songset for hot-page smoke testing",
    latestRenderJobId: "smoke-job-2",
    lastCompletedRenderJobId: "smoke-job-1",
    lastFailedRenderJobId: null,
  });

  // 6. Insert songset items
  // Items 1 and 2 updated before job 2 completion; item 3 references deleted recording
  const itemUpdatedAt = new Date("2025-06-01T10:00:00Z");
  await db.insert(schema.songsetItems).values([
    {
      id: "smoke-item-1",
      songsetId: "smoke-songset-hot-pages",
      songId: "smoke-song-1",
      recordingHashPrefix: "smoke-hp-1",
      position: 0,
      updatedAt: itemUpdatedAt,
    },
    {
      id: "smoke-item-2",
      songsetId: "smoke-songset-hot-pages",
      songId: "smoke-song-2",
      recordingHashPrefix: "smoke-hp-2",
      position: 1,
      updatedAt: itemUpdatedAt,
    },
    {
      id: "smoke-item-3",
      songsetId: "smoke-songset-hot-pages",
      songId: "smoke-song-3",
      recordingHashPrefix: "smoke-hp-3",
      position: 2,
      updatedAt: itemUpdatedAt,
    },
  ]);

  // 7. Insert render jobs
  // Job 1 completed earlier; Job 2 completed later (is the latest)
  await db.insert(schema.renderJobs).values([
    {
      id: "smoke-job-1",
      songsetId: "smoke-songset-hot-pages",
      userId: owningUserId,
      status: "completed",
      completedAt: new Date("2025-06-01T11:00:00Z"),
      elapsedSeconds: 30,
      estimatedTotalSeconds: 35,
      template: "dark",
      resolution: "720p",
      audioEnabled: true,
      videoEnabled: true,
      fontFamily: "noto_serif_tc",
      fontSizePreset: "M",
      includeTitleCard: false,
    },
    {
      id: "smoke-job-2",
      songsetId: "smoke-songset-hot-pages",
      userId: owningUserId,
      status: "completed",
      completedAt: new Date("2025-06-01T12:00:00Z"),
      elapsedSeconds: 28,
      estimatedTotalSeconds: 32,
      template: "dark",
      resolution: "720p",
      audioEnabled: true,
      videoEnabled: true,
      fontFamily: "noto_serif_tc",
      fontSizePreset: "M",
      includeTitleCard: false,
    },
  ]);

  // 8. Insert lyric marks
  // Marks 1-3: owning user, visible recordings
  // Mark 4: owning user, deleted recording (should be excluded from counts)
  // Mark 5: other user, visible recording (should be excluded from owning user counts)
  await db.insert(schema.lyricMarks).values([
    { id: "smoke-mark-1", userId: owningUserId, recordingContentHash: "smoke-ch-1", timestampSeconds: 10.0 },
    { id: "smoke-mark-2", userId: owningUserId, recordingContentHash: "smoke-ch-1", timestampSeconds: 20.0 },
    { id: "smoke-mark-3", userId: owningUserId, recordingContentHash: "smoke-ch-2", timestampSeconds: 15.0 },
    { id: "smoke-mark-4", userId: owningUserId, recordingContentHash: "smoke-ch-3", timestampSeconds: 5.0 },
    { id: "smoke-mark-5", userId: otherUserId, recordingContentHash: "smoke-ch-1", timestampSeconds: 12.0 },
  ]);
});

afterAll(async () => {
  try {
    const smokeSongIds = ["smoke-song-1", "smoke-song-2", "smoke-song-3"];

    await db.transaction(async (tx) => {
      await tx.delete(schema.users).where(eq(schema.users.email, "smoke-owner@test.com"));
      await tx.delete(schema.users).where(eq(schema.users.email, "smoke-other@test.com"));
      await tx.delete(schema.recordings).where(inArray(schema.recordings.songId, smokeSongIds));
      await tx.delete(schema.songs).where(inArray(schema.songs.id, smokeSongIds));
    });
  } catch (e) {
    console.error("Smoke test cleanup failed:", e);
  } finally {
    await closePostgresSmokeDb();
  }
});

describe("getRenderPageData", () => {
  it("returns null for non-owner user", async () => {
    const result = await getRenderPageData("smoke-songset-hot-pages", otherUserId);
    expect(result).toBeNull();
  });

  // NOTE: This test will FAIL on this branch due to the known GROUP BY bug
  // (recordings.durationSeconds missing from GROUP BY at songsets.ts:540).
  // The failure is intentional — it proves the smoke test catches real SQL errors.
  it("returns data for owning user", async () => {
    const result = await getRenderPageData("smoke-songset-hot-pages", owningUserId);
    expect(result).not.toBeNull();

    if (!result) return;

    expect(result.songset.id).toBe("smoke-songset-hot-pages");
    expect(result.songset.name).toBe("Smoke Hot Pages Songset");
    expect(result.songset.description).toBe("Songset for hot-page smoke testing");

    expect(result.songset.songTitles).toEqual(["Smoke Song Alpha", "Smoke Song Beta"]);
    expect(result.songset.durationSeconds).toBe(420);

    expect(result.songset.markedLineCount).toBe(3);

    expect(result.userSettings).not.toBeNull();
    expect(result.userSettings!.defaultVideoTemplate).toBe("light");
    expect(result.userSettings!.defaultResolution).toBe("1080p");
    expect(result.userSettings!.defaultFontSizePreset).toBe("L");
    expect(result.userSettings!.defaultFontFamily).toBe("noto_sans_tc");

    expect(result.latestJob).not.toBeNull();
    expect(result.latestJob!.id).toBe("smoke-job-2");

    expect(result.previousCompletedJob).not.toBeNull();
    expect(result.previousCompletedJob!.id).toBe("smoke-job-1");

    expect(result.songset.renderState).toBe("fresh");
  });
});

describe("getSongsetEditorData", () => {
  it("returns null for non-owner user", async () => {
    const result = await getSongsetEditorData("smoke-songset-hot-pages", otherUserId);
    expect(result).toBeNull();
  });

  it("returns data for owning user", async () => {
    const result = await getSongsetEditorData("smoke-songset-hot-pages", owningUserId);
    expect(result).not.toBeNull();

    if (!result) return;

    expect(result.items).toHaveLength(2);

    expect(result.items[0].position).toBe(0);
    expect(result.items[1].position).toBe(1);

    expect(result.items[0].song?.title).toBe("Smoke Song Alpha");
    expect(result.items[1].song?.title).toBe("Smoke Song Beta");

    expect(result.items[0].recording).not.toBeNull();
    expect(result.items[1].recording).not.toBeNull();

    expect(result.items[0].markedLineCount).toBe(2);
    expect(result.items[1].markedLineCount).toBe(1);

    expect(result.durationSeconds).toBe(420);
    expect(result.renderState).toBe("fresh");
  });
});

describe("listSongsetSummaries", () => {
  it("returns no rows for non-owner user", async () => {
    const result = await listSongsetSummaries(otherUserId);
    expect(result.total).toBe(0);
    expect(result.songsets).toHaveLength(0);
  });

  it("returns data for owning user", async () => {
    const result = await listSongsetSummaries(owningUserId);
    expect(result.total).toBe(1);

    const songset = result.songsets[0];
    expect(songset.id).toBe("smoke-songset-hot-pages");
    expect(songset.itemCount).toBe(2);
    expect(songset.durationSeconds).toBe(420);
    expect(songset.renderState).toBe("fresh");
  });
});
