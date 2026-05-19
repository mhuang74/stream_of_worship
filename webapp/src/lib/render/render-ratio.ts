import { db } from "@/db";
import { renderJobs } from "@/db/schema";
import { eq, and, sql } from "drizzle-orm";

const DEFAULT_RENDER_RATIOS: Record<string, number> = {
  "720p_video": 0.6,
  "720p_audio": 0.3,
  "1080p_video": 0.7,
  "1080p_audio": 0.3,
};

const MIN_HISTORICAL_JOBS = 3;
const MIN_REASONABLE_RATIO = 0.05;
const MAX_REASONABLE_RATIO = 5.0;

function getDefaultRatio(resolution: string, videoEnabled: boolean): number {
  const key = `${resolution}_${videoEnabled ? "video" : "audio"}`;
  if (key in DEFAULT_RENDER_RATIOS) return DEFAULT_RENDER_RATIOS[key];
  return Math.max(...Object.values(DEFAULT_RENDER_RATIOS));
}

export async function getRenderRatio(
  resolution: string,
  videoEnabled: boolean
): Promise<number> {
  const rows = await db
    .select({
      ratio: sql<number>`AVG(
        EXTRACT(EPOCH FROM (${renderJobs.completedAt} - ${renderJobs.startedAt})) / ${renderJobs.totalDurationSeconds}
      )`,
      count: sql<number>`COUNT(*)`,
    })
    .from(renderJobs)
    .where(
      and(
        eq(renderJobs.status, "completed"),
        sql`${renderJobs.startedAt} IS NOT NULL`,
        sql`${renderJobs.totalDurationSeconds} IS NOT NULL`,
        sql`${renderJobs.totalDurationSeconds} > 0`,
        eq(renderJobs.resolution, resolution),
        eq(renderJobs.videoEnabled, videoEnabled)
      )
    );

  const result = rows[0];
  if (!result || result.count < MIN_HISTORICAL_JOBS) {
    return getDefaultRatio(resolution, videoEnabled);
  }

  const avgRatio = result.ratio;
  if (avgRatio < MIN_REASONABLE_RATIO || avgRatio > MAX_REASONABLE_RATIO) {
    return getDefaultRatio(resolution, videoEnabled);
  }

  return avgRatio;
}

export { DEFAULT_RENDER_RATIOS, getDefaultRatio };
