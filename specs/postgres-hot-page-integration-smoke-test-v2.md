# Postgres Hot-Page Integration Smoke Test (v2)

Revised plan addressing data-loss risks, runtime issues, and alias collisions
found during review of v1.

## Goal

Add a deterministic CI smoke suite for the webapp hot-page database queries that
executes real Drizzle SQL against an ephemeral PostgreSQL database.

The smoke suite must catch PostgreSQL-only SQL failures that mocked Vitest tests
cannot detect, especially `GROUP BY`, aggregation, join, filter,
generated-column, and pgvector migration issues.

Target functions:

- `getRenderPageData(songsetId, userId)`
- `getSongsetEditorData(songsetId, userId)`
- `listSongsetSummaries(userId)`

## Constraints

- Keep existing mocked Vitest unit tests unchanged.
- Keep the existing Neon semantic-search integration test unchanged.
- Use GitHub Actions PostgreSQL service, not Testcontainers or Neon branching.
- Use committed Drizzle migrations with `drizzle-kit migrate`, not
  `drizzle-kit push`.
- Use a pgvector-capable PostgreSQL image because the committed migrations
  enable and use `vector`.
- Keep the smoke test Node-only; it should not use `jsdom` or browser APIs.
- Reuse the production schema from `webapp/src/db/schema.ts`.
- Do not use the Neon HTTP client in the smoke suite. The CI database is a
  local TCP PostgreSQL service.

## Changes from v1

| # | Issue | Resolution |
|---|-------|------------|
| 1 | `@/db` string alias also matches `@/db/schema`, breaking all schema imports | Use **regex alias** `{ find: /^@\/db$/, ... }` so only exact `@/db` matches. Test client uses **relative import** for schema. |
| 2 | `getRenderPageData` GROUP BY missing `recordings.durationSeconds` — PostgreSQL will reject the query | **Do not fix the bug in this branch.** Write the test with correct assertions; it will fail in CI, confirming the smoke test catches real SQL errors. Fix lands in a separate branch and merges together. |
| 3 | Orphan migration `0013_page_load_hot_path_indexes.sql` has no journal entry, silently skipped by `drizzle-kit migrate` | **Rename** to `0014_page_load_hot_path_indexes.sql` and add journal entry at idx 14. |
| 4 | Targeted row-by-row cleanup is fragile and risks FK ordering bugs | Use **CASCADE-based cleanup**: delete the 2 seeded users + 3 seeded songs. CASCADE handles all dependent rows. |
| 5 | `afterAll` must be idempotent if `beforeAll` fails partway | Use `DELETE ... WHERE id = $1` (no-op if row absent). Wrap `closePostgresSmokeDb()` in try/catch. |
| 6 | `recordings.contentHash` vs `recordings.hashPrefix` confusion in fixture | Add explicit comments in fixture code and use distinct prefix patterns (e.g., `smoke-hp-` for hashPrefix, `smoke-ch-` for contentHash). |
| 7 | `user` table uses `generatedAlwaysAsIdentity()` — cannot seed deterministic user IDs | Capture returned IDs from user inserts; all downstream inserts await these IDs. Document this constraint in fixture code. |
| 8 | `songs.search_vector` generated column may interact poorly with `postgres-js` driver | Drizzle's `generatedAlwaysAs` should exclude the column from inserts automatically. Verify during implementation; if it doesn't, explicitly omit the column from insert objects. |

## Implementation Plan

### 1. Fix orphan migration

Rename and journal the orphan migration file:

- Rename `webapp/drizzle/0013_page_load_hot_path_indexes.sql` →
  `webapp/drizzle/0014_page_load_hot_path_indexes.sql`
- Add to `webapp/drizzle/meta/_journal.json`:

```json
{
  "idx": 14,
  "version": "7",
  "when": <current epoch ms>,
  "tag": "0014_page_load_hot_path_indexes",
  "breakpoints": true
}
```

Verify: `SOW_DATABASE_URL=... pnpm exec drizzle-kit migrate` should apply
the index migration on a fresh database.

### 2. Add a TCP PostgreSQL Drizzle client for tests

Add: `webapp/src/test/db/postgres-client.ts`

```ts
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
```

**Key difference from v1:** Uses relative import `../../db/schema` instead of
`@/db/schema` to avoid the alias collision. Exports `schema` as a named
object rather than `export *` to make import sites explicit.

### 3. Add a Node-only Vitest config

Add: `webapp/vitest.postgres-smoke.config.ts`

```ts
import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  test: {
    environment: "node",
    // Do NOT use src/test/setup.ts — it installs jsdom helpers.
    include: ["src/test/integration/postgres-hot-pages.smoke.test.ts"],
    // Serial execution avoids fixture races on the shared DB.
    pool: "forks",
    poolOptions: { forks: { singleFork: true } },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      // Regex alias: matches "@/db" exactly, NOT "@/db/schema".
      // This lets the test client intercept `import { db } from "@/db"`
      // while `import { songs } from "@/db/schema"` still resolves normally.
      "/^@\\/db$/": path.resolve(__dirname, "./src/test/db/postgres-client.ts"),
    },
  },
});
```

**Why regex alias:** Vite resolves string aliases by prefix match. A string
alias `"@/db"` would also match `"@/db/schema"`, breaking all schema imports
in `songsets.ts`. The regex `/^@\/db$/` matches only the exact path `@/db`.

### 4. Add npm script and dependency

Update `webapp/package.json`:

- Add `postgres` as a dev dependency: `pnpm add -D postgres`
- Add script:

```json
"test:postgres-smoke": "vitest run --config vitest.postgres-smoke.config.ts"
```

Do not change: `test`, `test:integration`, `test:watch`.

### 5. Add the Postgres smoke test

Add: `webapp/src/test/integration/postgres-hot-pages.smoke.test.ts`

Imports:

```ts
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { db, closePostgresSmokeDb, schema } from "@/db";
import { getRenderPageData, getSongsetEditorData, listSongsetSummaries } from "@/lib/db/songsets";
import { eq } from "drizzle-orm";
```

Note: `@/db` resolves to `postgres-client.ts` (test client) via the regex
alias. `@/db/schema` and `@/lib/db/songsets` resolve normally via the `@`
alias.

### 6. Fixture design

#### Seeding order (respects FK constraints)

1. **Users** (2 rows) — `generatedAlwaysAsIdentity`, capture returned IDs
2. **User settings** (1 row) — for owning user, custom values
3. **Songs** (3 rows) — text PK, deterministic IDs
4. **Recordings** (3 rows) — text PK, deterministic content hashes
5. **Songset** (1 row) — text PK, deterministic ID
6. **Songset items** (3 rows) — text PK, deterministic IDs
7. **Render jobs** (2 rows) — text PK, deterministic IDs
8. **Lyric marks** (4 rows) — text PK, deterministic IDs

#### Deterministic IDs

Use prefixed patterns to distinguish hashPrefix from contentHash and avoid
confusion:

| Entity | ID pattern | Example |
|--------|-----------|---------|
| Song | `smoke-song-{n}` | `smoke-song-1` |
| Recording contentHash | `smoke-ch-{n}` | `smoke-ch-1` |
| Recording hashPrefix | `smoke-hp-{n}` | `smoke-hp-1` |
| Songset | `smoke-songset-hot-pages` | `smoke-songset-hot-pages` |
| Songset item | `smoke-item-{n}` | `smoke-item-1` |
| Render job | `smoke-job-{n}` | `smoke-job-1` |
| Lyric mark | `smoke-mark-{n}` | `smoke-mark-1` |

#### User IDs

`user.id` is `bigint GENERATED ALWAYS AS IDENTITY`. Cannot insert deterministic
values. Instead:

```ts
const [owningUser] = await db.insert(schema.users).values([
  { name: "Smoke Owner", email: "smoke-owner@test.com" },
  { name: "Smoke Other", email: "smoke-other@test.com" },
]).returning({ id: schema.users.id });
```

Capture both IDs. All downstream inserts use the captured `owningUser.id` and
`otherUser.id`.

#### Seed data

**Users:**
- Owning user (email: `smoke-owner@test.com`)
- Other user (email: `smoke-other@test.com`)

**User settings** (owning user only):
- `defaultVideoTemplate`: `"light"` (differs from default `"dark"`)
- `defaultResolution`: `"1080p"` (differs from default `"720p"`)
- `defaultFontSizePreset`: `"L"` (differs from default `"M"`)
- `defaultFontFamily`: `"noto_sans_tc"` (differs from default `"noto_serif_tc"`)

**Songs:**
- `smoke-song-1`: title "Smoke Song Alpha"
- `smoke-song-2`: title "Smoke Song Beta"
- `smoke-song-3`: title "Smoke Song Gamma"

**Recordings:**
- `smoke-ch-1` / `smoke-hp-1`: song 1, visible, `durationSeconds: 180`
- `smoke-ch-2` / `smoke-hp-2`: song 2, visible, `durationSeconds: 240`
- `smoke-ch-3` / `smoke-hp-3`: song 3, `deletedAt` set, `durationSeconds: 200`

**Songset:**
- `smoke-songset-hot-pages`: owning user, `latestRenderJobId: smoke-job-2`,
  `lastCompletedRenderJobId: smoke-job-1`, `lastFailedRenderJobId: null`

**Songset items:**
- `smoke-item-1`: position 0, song 1, recording `smoke-hp-1`, `updatedAt` before job 2 `completedAt`
- `smoke-item-2`: position 1, song 2, recording `smoke-hp-2`, `updatedAt` before job 2 `completedAt`
- `smoke-item-3`: position 2, song 3, recording `smoke-hp-3` (deleted)

**Render jobs:**
- `smoke-job-1`: completed, owning user, songset, `completedAt` set,
  `elapsedSeconds: 30`, `estimatedTotalSeconds: 35`, `template: "dark"`,
  `resolution: "720p"`, `audioEnabled: true`, `videoEnabled: true`,
  `fontFamily: "noto_serif_tc"`, `fontSizePreset: "M"`,
  `includeTitleCard: false`, `titleCardDurationSeconds: null`,
  `titleCardLines: null`
- `smoke-job-2`: completed, owning user, songset, `completedAt` set (later than job 1),
  same render options, `elapsedSeconds: 28`, `estimatedTotalSeconds: 32`

**Lyric marks:**
- `smoke-mark-1`: owning user, recording `smoke-ch-1`, `timestampSeconds: 10.0`
- `smoke-mark-2`: owning user, recording `smoke-ch-1`, `timestampSeconds: 20.0`
- `smoke-mark-3`: owning user, recording `smoke-ch-2`, `timestampSeconds: 15.0`
- `smoke-mark-4`: owning user, recording `smoke-ch-3` (deleted), `timestampSeconds: 5.0`
  — verifies deleted recordings are excluded from counts
- `smoke-mark-5`: other user, recording `smoke-ch-1`, `timestampSeconds: 12.0`
  — verifies per-user filtering

#### Expected visible aggregate

- Item count: `2`
- Duration: `180 + 240 = 420` seconds
- Marked lyric count (owning user, visible only): `3` (marks 1, 2, 3)
- Song title order: `["Smoke Song Alpha", "Smoke Song Beta"]`
- Render state: `"fresh"` (latest job completed, items updated before completion)

### 7. Cleanup strategy (CASCADE-based)

`afterAll` deletes in this order:

1. **Delete the 2 seeded users** — CASCADE removes: songsets, songset_items,
   render_jobs, lyric_marks, user_settings, songset_shares, user_lrc_overrides,
   accounts, sessions
2. **Delete the 3 seeded songs** — CASCADE removes: recordings,
   song_embeddings, song_line_embeddings
3. **Close the DB client** — `closePostgresSmokeDb()`

All deletes use `WHERE id = $1` or `WHERE email = $1`, making them idempotent
(no-op if rows are already gone). Wrap each delete in try/catch so partial
failures don't prevent subsequent cleanup.

```ts
afterAll(async () => {
  try {
    await db.delete(schema.users).where(
      eq(schema.users.email, "smoke-owner@test.com")
    );
    await db.delete(schema.users).where(
      eq(schema.users.email, "smoke-other@test.com")
    );
    await db.delete(schema.songs).where(
      eq(schema.songs.id, "smoke-song-1")
    );
    await db.delete(schema.songs).where(
      eq(schema.songs.id, "smoke-song-2")
    );
    await db.delete(schema.songs).where(
      eq(schema.songs.id, "smoke-song-3")
    );
  } catch (e) {
    console.error("Smoke test cleanup failed:", e);
  } finally {
    await closePostgresSmokeDb();
  }
});
```

### 8. Smoke assertions

#### `getRenderPageData`

For the owning user:

- **This test will FAIL on this branch** due to the known GROUP BY bug
  (`recordings.durationSeconds` missing from GROUP BY at `songsets.ts:540`).
  The failure is intentional — it proves the smoke test catches real SQL
  errors that mocked tests miss.
- Expected assertions (will pass after the GROUP BY fix merges):
  - returns non-null data
  - returns the seeded songset ID, name, and description
  - returns visible song titles in position order
  - excludes the deleted recording item from titles and duration
  - sums only visible recording durations (`420`)
  - returns marked line count for owning user and visible recordings only (`3`)
  - returns custom user settings (`light`, `1080p`, `L`, `noto_sans_tc`)
  - returns latest job mapped from `latestRenderJobId`
  - returns previous completed job mapped from `lastCompletedRenderJobId`
  - returns `renderState: "fresh"`

For the other user:

- returns `null`

#### `getSongsetEditorData`

For the owning user:

- returns non-null data
- returns visible items only (2 items)
- preserves item order by `position`
- includes song detail for each visible item
- includes recording detail for each visible item
- excludes the deleted recording item
- returns per-item marked line counts for the owning user only
- returns visible duration sum (`420`)
- returns `renderState: "fresh"`

For the other user:

- returns `null`

#### `listSongsetSummaries`

For the owning user:

- returns total `1` for the seeded fixture
- includes the seeded songset
- returns item count `2`
- returns visible duration sum (`420`)
- returns `renderState: "fresh"`

For the other user:

- returns total `0`
- returns no rows

### 9. Drizzle migration handling

CI must run:

```bash
SOW_DATABASE_URL=postgresql://sow:sow@localhost:5432/sow_test?sslmode=disable pnpm exec drizzle-kit migrate
```

Before enabling this in CI, verify:

1. The renamed migration `0014_page_load_hot_path_indexes.sql` is tracked in
   `_journal.json` at idx 14.
2. All 15 migrations (idx 0–14) apply cleanly on a fresh pgvector database.
3. The `songs.search_vector` generated column does not cause insert errors
   with the `postgres-js` driver. If it does, explicitly omit the column from
   insert objects (Drizzle's `generatedAlwaysAs` should handle this
   automatically, but verify).

### 10. CI workflow changes

Update: `.github/workflows/ci.yml`

For the `webapp-lint-and-test` job:

- Add a PostgreSQL service using `pgvector/pgvector:pg16`.
- Configure:

```yaml
POSTGRES_USER: sow
POSTGRES_PASSWORD: sow
POSTGRES_DB: sow_test
```

- Expose `5432:5432`.
- Add a health check using `pg_isready`.
- Keep existing steps: `pnpm lint`, `pnpm typecheck`, `pnpm test`.
- After existing tests, add migration and smoke-test steps:

```bash
SOW_DATABASE_URL=postgresql://sow:sow@localhost:5432/sow_test?sslmode=disable pnpm exec drizzle-kit migrate
SOW_DATABASE_URL=postgresql://sow:sow@localhost:5432/sow_test?sslmode=disable pnpm test:postgres-smoke
```

**Note:** The `test:postgres-smoke` step will fail on this branch due to the
known GROUP BY bug. This is intentional. The step will pass after the fix
merges from the other branch. Do NOT add `continue-on-error: true`.

### 11. Workflow tests

Update: `webapp/src/test/deployment/workflows.test.ts`

Add assertions that the CI workflow:

- defines a Postgres service on the webapp job
- uses a pgvector-capable image
- defines the expected PostgreSQL environment variables
- runs `drizzle-kit migrate`
- passes `SOW_DATABASE_URL` to migration
- runs `pnpm test:postgres-smoke`
- passes `SOW_DATABASE_URL` to the smoke test

Keep existing workflow assertions intact.

### 12. Verification commands

After implementation, run from `webapp/`:

```bash
pnpm test src/test/deployment/workflows.test.ts
pnpm test src/test/db/schema.test.ts
pnpm typecheck
```

If a local PostgreSQL/pgvector database is available, also run:

```bash
SOW_DATABASE_URL=postgresql://sow:sow@localhost:5432/sow_test?sslmode=disable pnpm exec drizzle-kit migrate
SOW_DATABASE_URL=postgresql://sow:sow@localhost:5432/sow_test?sslmode=disable pnpm test:postgres-smoke
```

The `getRenderPageData` test is expected to fail with a PostgreSQL GROUP BY
error on this branch. The other two tests (`getSongsetEditorData`,
`listSongsetSummaries`) should pass.

### 13. Repository maintenance

Because code files will be modified after this spec is written:

- Run `graphify update .` after implementation.
- Follow the repository completion rule:

```bash
git pull --rebase
git push
git status
```

The final status must show the branch up to date with origin unless a real
external blocker prevents pushing.
