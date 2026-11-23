import { randomUUID } from "node:crypto";
import { eq } from "drizzle-orm";
import { afterAll, afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { closePostgresSmokeDb, db, schema } from "@/db";
import { deleteSongset, deleteSongsetItem } from "@/lib/db/songsets";
import { glueJobRepository } from "@/lib/glue/jobs";
import { readGlueQuota } from "@/lib/glue/quota";

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
