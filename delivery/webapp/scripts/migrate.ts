import { neon } from "@neondatabase/serverless";
import { drizzle } from "drizzle-orm/neon-http";
import { migrate } from "drizzle-orm/neon-http/migrator";

async function main() {
  if (!process.env.SOW_DATABASE_URL) {
    throw new Error("SOW_DATABASE_URL environment variable is required");
  }

  const sql = neon(process.env.SOW_DATABASE_URL);
  const db = drizzle(sql);

  await migrate(db, { migrationsFolder: "./drizzle" });
  console.log("Migrations applied successfully");
}

main().catch((err) => {
  console.error("Migration failed:", err);
  process.exit(1);
});
