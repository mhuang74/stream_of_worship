import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import * as schema from "../../db/schema";

const databaseUrl = process.env.SOW_DATABASE_URL;
if (!databaseUrl) {
  throw new Error(
    "SOW_DATABASE_URL is required for Postgres smoke tests. " +
      "Set it to a TCP Postgres connection string, e.g. " +
      "postgresql://sow:sow@localhost:5432/sow_test?sslmode=disable"
  );
}

const queryClient = postgres(databaseUrl, { max: 1 });
export const db = drizzle(queryClient, { schema });

export async function closePostgresSmokeDb() {
  try {
    await queryClient.end();
  } catch {
    // Swallow — Vitest must not hang if the connection is already closed or broken.
  }
}

// Re-export schema for convenience, but do NOT re-export from "@/db/schema"
// because the @/db alias is overridden in the smoke Vitest config.
export { schema };
