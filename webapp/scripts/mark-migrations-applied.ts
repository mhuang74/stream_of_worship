import { neon } from "@neondatabase/serverless";
import { readMigrationFiles } from "drizzle-orm/migrator";
import { sql } from "drizzle-orm/sql";

async function main() {
  const connectionString = process.env.SOW_DATABASE_URL;
  if (!connectionString) {
    throw new Error("SOW_DATABASE_URL environment variable is required");
  }

  const sqlClient = neon(connectionString);

  console.log("Creating drizzle schema and migrations table...");
  await sqlClient`CREATE SCHEMA IF NOT EXISTS drizzle`;
  await sqlClient`
    CREATE TABLE IF NOT EXISTS drizzle.__drizzle_migrations (
      id SERIAL PRIMARY KEY,
      hash text NOT NULL,
      created_at bigint
    )
  `;

  const existing = await sqlClient`
    SELECT hash FROM drizzle.__drizzle_migrations
  `;
  if (existing.length > 0) {
    console.log(`Migrations table already has ${existing.length} records. Skipping.`);
    process.exit(0);
  }

  const migrations = readMigrationFiles({ migrationsFolder: "./drizzle" });
  console.log(`Found ${migrations.length} migration files`);

  for (const migration of migrations) {
    console.log(`Marking ${migration.folderName} as applied (hash: ${migration.hash}, created_at: ${migration.folderMillis})`);
    await sqlClient`
      INSERT INTO drizzle.__drizzle_migrations (hash, created_at)
      VALUES (${migration.hash}, ${migration.folderMillis})
    `;
  }

  console.log("Done! All existing migrations marked as applied.");
}

main().catch((err) => {
  console.error("Failed:", err);
  process.exit(1);
});
