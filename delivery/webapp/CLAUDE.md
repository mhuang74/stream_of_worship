@AGENTS.md

## Schema Changes & Migrations

CI uses `drizzle-kit migrate` (non-interactive). When modifying `src/db/schema.ts`:
1. Run `npx drizzle-kit generate` to generate migration SQL files in `delivery/webapp/drizzle/`
2. Commit the generated migration files alongside the schema change
3. `drizzle-kit push` is for local dev prototyping only — never rely on it for committed schema changes
