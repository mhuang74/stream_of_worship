import { Pool } from "@neondatabase/serverless";
import { drizzle } from "drizzle-orm/neon-serverless";
import type { NeonDatabase } from "drizzle-orm/neon-serverless";
import * as schema from "./schema";

/**
 * Pool-backed client used only by the two persistent mutations that must be
 * atomic (arrangement save, item patch). `@/db` is `neon-http`, whose session
 * rejects `transaction()`, so those writes need a real websocket Pool instead
 * of the read path's HTTP client.
 */
export type NeonServerlessDrizzleDatabase = NeonDatabase<typeof schema>;

if (!process.env.SOW_DATABASE_URL) {
  throw new Error("SOW_DATABASE_URL environment variable is required");
}

const pool = new Pool({ connectionString: process.env.SOW_DATABASE_URL });

export const mutationDb: NeonServerlessDrizzleDatabase = drizzle(pool, { schema });
