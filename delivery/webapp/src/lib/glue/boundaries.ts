import "server-only";

import { randomUUID } from "node:crypto";
import { and, eq, inArray, or } from "drizzle-orm";
import { db } from "@/db";
import { glueJobs, glueTakes, songsets } from "@/db/schema";
import { defaultBoundarySettings, songsetBoundaries, type BoundarySettings, type SongsetBoundary, type SongsetBoundarySettings } from "./boundary-types";
import { deriveGlueTarget, getGlueTargetItems, loadExcerptKeySets, loadGlueItemRows, type GlueResolverItem } from "./quote";
import { readGlueQuota } from "./quota";
import { glueTakeSelectionError, jobRowFromDb, resolveSongsetBoundaries, takeRowFromDb, type ResolvedSongsetBoundary } from "./resolve";

export async function loadSongsetBoundaries(
  songsetId: string,
  userId: number,
  settings: SongsetBoundarySettings,
  savedItems?: GlueResolverItem[]
): Promise<ResolvedSongsetBoundary[]> {
  const items = savedItems ?? (await loadGlueItemRows([songsetId], userId)).get(songsetId) ?? [];
  const ids = new Set<string>();
  const keys: string[] = [];
  const hashes: string[] = [];
  for (const target of songsetBoundaries) {
    const config = settings[target];
    if (config?.takeId) ids.add(config.takeId);
    for (const id of Object.values(config?.selections ?? {})) ids.add(id);
    const inputs = getGlueTargetItems(items, target);
    if (!inputs) continue;
    const derivation = deriveGlueTarget(target, inputs.from, inputs.to);
    if (!derivation.ok) continue;
    keys.push(derivation.descriptorKey);
    hashes.push(derivation.fromSide.contentHash);
  }
  const clauses = [
    ids.size ? inArray(glueTakes.id, [...ids]) : undefined,
    keys.length ? inArray(glueTakes.descriptorKey, keys) : undefined,
  ];
  const [takes, jobs, quota, excerpts] = await Promise.all([
    ids.size || keys.length
      ? db.select().from(glueTakes).where(and(eq(glueTakes.userId, userId), or(...clauses)))
      : [],
    db.select().from(glueJobs).where(and(eq(glueJobs.songsetId, songsetId), eq(glueJobs.userId, userId))),
    readGlueQuota(userId),
    loadExcerptKeySets(userId, hashes),
  ]);
  return resolveSongsetBoundaries({
    items,
    takes: takes.map(takeRowFromDb),
    jobs: jobs.map(jobRowFromDb),
    quota,
    cachedExcerptKeys: excerpts.cached,
    rejectedExcerptKeys: excerpts.rejected,
  }, settings);
}

export async function updateSongsetBoundary(
  songsetId: string,
  userId: number,
  target: SongsetBoundary,
  patch: Partial<BoundarySettings>,
  expectedDescriptorKey?: string
): Promise<"saved" | "not-found" | "stale"> {
  const { mutationDb } = await import("@/db/mutation-db");
  return mutationDb.transaction(async (tx) => {
    const owner = and(eq(songsets.id, songsetId), eq(songsets.userId, userId));
    const [row] = await tx.select({ settings: songsets.boundarySettings }).from(songsets)
      .where(owner).for("update");
    if (!row) return "not-found";
    const previous = row.settings[target] ?? defaultBoundarySettings;
    const next = { ...previous, ...patch, revision: randomUUID() };
    if (patch.takeId) {
      const items = (await loadGlueItemRows([songsetId], userId, tx)).get(songsetId) ?? [];
      const inputs = getGlueTargetItems(items, target);
      if (!inputs) return "stale";
      const derivation = deriveGlueTarget(target, inputs.from, inputs.to);
      if (!derivation.ok || (expectedDescriptorKey && derivation.descriptorKey !== expectedDescriptorKey)) {
        return "stale";
      }
      const [take] = await tx.select().from(glueTakes)
        .where(and(eq(glueTakes.id, patch.takeId), eq(glueTakes.userId, userId)));
      if (glueTakeSelectionError({
        glueEnabled: next.enabled,
        hasSuccessor: true,
        derivation,
        take: take ? takeRowFromDb(take) : null,
        ownerUserId: userId,
      })) return "stale";
      next.selections = { ...previous.selections, [derivation.descriptorKey]: patch.takeId };
    }
    const now = new Date();
    await tx.update(songsets).set({
      boundarySettings: { ...row.settings, [target]: next },
      boundaryUpdatedAt: now,
      updatedAt: now,
    }).where(owner);
    return "saved";
  });
}
