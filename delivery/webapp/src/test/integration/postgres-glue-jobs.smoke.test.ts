import { randomUUID } from "node:crypto";
import { eq } from "drizzle-orm";
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { closePostgresSmokeDb, db, schema } from "@/db";
import { deleteSongset, deleteSongsetItem } from "@/lib/db/songsets";
import { glueJobRepository } from "@/lib/glue/jobs";
import { readGlueQuota } from "@/lib/glue/quota";
import { getGlueQuote } from "@/lib/glue/quote";
import { loadSongsetBoundaries, updateSongsetBoundary } from "@/lib/glue/boundaries";
import { createRenderGlueSnapshot } from "@/lib/db/songsets";

vi.mock("@/db/mutation-db", async () => ({ mutationDb: (await import("@/db")).db }));

const now = new Date("2026-11-23T00:00:00Z");
let userId: number;
let songId: string;
let songsetId: string;
let fromItemId: string;
let toItemId: string;

beforeEach(async () => {
  const [{ id }] = await db.insert(schema.users).values({
    name: "Glue smoke user",
    email: `glue-smoke-${randomUUID()}@test.com`,
  }).returning({ id: schema.users.id });
  userId = id;
  songId = randomUUID();
  songsetId = randomUUID();
  fromItemId = randomUUID();
  toItemId = randomUUID();
  await db.insert(schema.songs).values({
    id: songId,
    title: "Glue smoke song",
    sourceUrl: "https://example.com/glue-smoke",
    scrapedAt: "2026-11-23",
  });
  await db.insert(schema.songsets).values({ id: songsetId, userId, name: "Glue smoke songset" });
  await db.insert(schema.songsetItems).values([
    { id: fromItemId, songsetId, songId, position: 0 },
    { id: toItemId, songsetId, songId, position: 1 },
  ]);
});

afterEach(async () => {
  await db.delete(schema.users).where(eq(schema.users.id, userId));
  await db.delete(schema.songs).where(eq(schema.songs.id, songId));
});

afterAll(closePostgresSmokeDb);

function jobInput() {
  return {
    userId,
    songsetId,
    fromItemId,
    toItemId,
    jobId: randomUUID(),
    clientRequestId: randomUUID(),
    attemptId: randomUUID(),
    pendingTakeId: randomUUID(),
    reservationId: randomUUID(),
    takeMode: "new-take",
    descriptorKey: "d".repeat(64),
    descriptorJson: "{}",
    expectedFromUpdatedAt: now,
    expectedToUpdatedAt: now,
    seed: 12345,
    now,
  } as const;
}

async function insertFailedJob(takeOrdinal: number) {
  const { jobId, reservationId, now: failedAt, ...fields } = jobInput();
  await db.insert(schema.glueJobs).values({
    ...fields,
    id: jobId,
    takeOrdinal,
    active: false,
    phase: "failed",
    dispatchState: "dispatched",
    providerContacted: true,
    paidReservationId: reservationId,
    paidReservationState: "consumed",
    retryKind: "free",
    heartbeatAt: failedAt,
    finishedAt: failedAt,
  });
  await db.insert(schema.gluePaidAttempts).values({
    id: reservationId,
    userId,
    glueJobId: jobId,
    state: "consumed",
    consumedAt: failedAt,
  });
  return jobId;
}

describe("Glue jobs on Postgres", () => {
  it("pins a boundary take and preserves its job when the boundary item is deleted", async () => {
    const hash = randomUUID();
    await db.insert(schema.recordings).values({
      contentHash: hash, hashPrefix: hash, songId, originalFilename: "test.mp3",
      fileSizeBytes: 100, importedAt: now.toISOString(), durationSeconds: 12,
      tempoBpm: 120, beats: "[0,0.5,1,11.5]", sections: '[{"label":"Verse","start":0,"end":12}]',
    });
    try {
      await db.update(schema.songsetItems).set({
        recordingHashPrefix: hash,
        updatedAt: now,
        arrangementSteps: JSON.stringify({
          version: 1,
          steps: [{ sectionIndex: 0, label: "Verse", startSeconds: 0, endSeconds: 12, manualBounds: true }],
        }),
      }).where(eq(schema.songsetItems.id, fromItemId));
      expect(await updateSongsetBoundary(songsetId, userId, "intro", { enabled: true })).toBe("saved");
      const quote = await getGlueQuote({ songsetId, userId, target: "intro", takeMode: "new-take" });
      expect(quote).toMatchObject({ fromItemId, toItemId: fromItemId, generationSeconds: 4 });
      const [{ boundarySettings }] = await db.select().from(schema.songsets)
        .where(eq(schema.songsets.id, songsetId));
      const [boundary] = await loadSongsetBoundaries(songsetId, userId, boundarySettings);
      const input = jobInput();
      const descriptorJson = JSON.stringify({
        v: 1, target: "intro", model: "music_v2_5", promptVersion: 1, excerptSeconds: 12,
        simdps: { model: "acestep-v15-base", steps: 100, outerContextSeconds: 2 },
        beats: 8, toTempoBpm: 120, recording: { contentHash: hash, steps: [[0, 12]] },
      });
      expect(await glueJobRepository.createJobAtomically({
        ...input, target: "intro", toItemId: fromItemId, descriptorKey: quote!.descriptorKey, descriptorJson,
      })).toMatchObject({ kind: "created", job: { target: "intro", boundaryItemId: fromItemId } });
      expect(await db.transaction((tx) => createRenderGlueSnapshot(tx, songsetId, userId)))
        .toMatchObject({ preflight: { glues: [{ target: "intro" }] }, snapshot: [] });
      const takeId = randomUUID();
      await db.insert(schema.glueTakes).values({
        id: takeId, userId, descriptorKey: boundary.descriptorKey!, descriptorJson, ordinal: 1,
        seed: 123, status: "ready", r2Key: "glues/ready.wav", seconds: 4,
        metricsJson: JSON.stringify({ audioSha256: "a".repeat(64) }),
      });
      await db.update(schema.glueJobs).set({ active: false, phase: "ready" })
        .where(eq(schema.glueJobs.id, input.jobId));
      expect(await updateSongsetBoundary(songsetId, userId, "intro", { takeId })).toBe("saved");
      expect(await db.transaction((tx) => createRenderGlueSnapshot(tx, songsetId, userId)))
        .toMatchObject({ preflight: null, snapshot: [{ target: "intro", take_id: takeId, seconds: 4 }] });
      expect(await deleteSongsetItem(fromItemId, songsetId, userId)).toBe(true);
      expect(await glueJobRepository.findJob(input.jobId, userId)).toMatchObject({ target: "intro" });
      expect(await db.transaction((tx) => createRenderGlueSnapshot(tx, songsetId, userId)))
        .toEqual({ preflight: null, snapshot: [] });
    } finally {
      await db.delete(schema.recordings).where(eq(schema.recordings.contentHash, hash));
    }
  });

  it("clears a recent heartbeat when re-queuing a free failure", async () => {
    const jobId = await insertFailedJob(1);
    const attemptId = randomUUID();
    const resumedAt = new Date(now.getTime() + 1_000);

    await expect(glueJobRepository.prepareResume(jobId, userId, attemptId, resumedAt))
      .resolves.toMatchObject({
        kind: "prepared",
        phase: "queued",
        job: { attemptId, heartbeatAt: null, active: true },
      });
  });

  it.each([
    ["outgoing junction item", () => deleteSongsetItem(fromItemId, songsetId, userId)],
    ["incoming junction item", () => deleteSongsetItem(toItemId, songsetId, userId)],
    ["songset", () => deleteSongset(songsetId, userId)],
  ])("keeps quota consumed after deleting the %s", async (_, remove) => {
    for (let ordinal = 1; ordinal <= 5; ordinal++) {
      await insertFailedJob(ordinal);
    }

    expect(await remove()).toBe(true);
    expect(await glueJobRepository.listJobsForSongset(songsetId, userId)).toEqual([]);
    const resetAt = new Date(now.getTime() + 24 * 60 * 60 * 1000).toISOString();
    expect(await readGlueQuota(userId, now)).toMatchObject({ used: 5, remaining: 0, resetAt });
    expect(await glueJobRepository.createJobAtomically(jobInput())).toEqual({
      kind: "quota",
      resetAt,
    });
  });
});
