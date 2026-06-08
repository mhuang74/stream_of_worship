# Postgres Hot-Page Integration Smoke Test

## Goal

Add a deterministic CI smoke suite for the webapp hot-page database queries that executes real Drizzle SQL against an ephemeral PostgreSQL database.

The smoke suite must catch PostgreSQL-only SQL failures that mocked Vitest tests cannot detect, especially `GROUP BY`, aggregation, join, filter, generated-column, and pgvector migration issues.

Target functions:

- `getRenderPageData(songsetId, userId)`
- `getSongsetEditorData(songsetId, userId)`
- `listSongsetSummaries(userId)`

## Constraints

- Keep existing mocked Vitest unit tests unchanged.
- Keep the existing Neon semantic-search integration test unchanged.
- Use GitHub Actions PostgreSQL service, not Testcontainers or Neon branching.
- Use committed Drizzle migrations with `drizzle-kit migrate`, not `drizzle-kit push`.
- Use a pgvector-capable PostgreSQL image because the committed migrations enable and use `vector`.
- Keep the smoke test Node-only; it should not use `jsdom` or browser APIs.
- Reuse the production schema from `webapp/src/db/schema.ts`.
- Do not use the Neon HTTP client in the smoke suite. The CI database is a local TCP PostgreSQL service.

## Implementation Plan

### 1. Clarify any questions or concerns

- Use Question tool to ask user any questions before you start implementation

### 2. Add a TCP PostgreSQL Drizzle client for tests

Add a test-only module:

- `webapp/src/test/db/postgres-client.ts`

Responsibilities:

- Read `process.env.SOW_DATABASE_URL`.
- Throw a clear error if `SOW_DATABASE_URL` is missing, so local failures explain how to run the test.
- Create a `postgres` client with connection pooling disabled or constrained for a short-lived Vitest process.
- Create a Drizzle client with `drizzle-orm/postgres-js`.
- Re-export the existing schema from `webapp/src/db/schema.ts`.
- Export a cleanup helper, for example `closePostgresSmokeDb()`, that calls `sql.end()` after the smoke suite completes.

Expected shape:

```ts
import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";
import * as schema from "@/db/schema";

const databaseUrl = process.env.SOW_DATABASE_URL;
if (!databaseUrl) {
  throw new Error("SOW_DATABASE_URL is required for Postgres smoke tests");
}

const queryClient = postgres(databaseUrl, { max: 1 });
export const db = drizzle(queryClient, { schema });
export async function closePostgresSmokeDb() {
  await queryClient.end();
}

export * from "@/db/schema";
```

### 3. Add a Node-only Vitest config

Add:

- `webapp/vitest.postgres-smoke.config.ts`

Responsibilities:

- Use `environment: "node"`.
- Avoid the normal `src/test/setup.ts` because this test should not install browser mocks or jsdom helpers.
- Alias `@` to `webapp/src`.
- Alias `@/db` to `webapp/src/test/db/postgres-client.ts`.
- Include only the smoke test file.
- Run serially if needed to avoid fixture races.

Expected include:

- `src/test/integration/postgres-hot-pages.smoke.test.ts`

### 4. Add an npm script

Update `webapp/package.json`:

- Add `postgres` as a dev dependency for `drizzle-orm/postgres-js`.
- Add a separate smoke script:

```json
"test:postgres-smoke": "vitest run --config vitest.postgres-smoke.config.ts"
```

Do not change:

- `test`
- `test:integration`
- `test:watch`

### 5. Add the Postgres smoke test

Add:

- `webapp/src/test/integration/postgres-hot-pages.smoke.test.ts`

Imports:

- target functions from `@/lib/db/songsets`
- Drizzle client and cleanup helper from `@/db`
- schema tables from `@/db/schema`
- `eq`, `inArray`, or `sql` from `drizzle-orm` as needed

Test lifecycle:

- `beforeAll`: seed fixture.
- `afterAll`: remove seeded rows and close the DB client.
- Use deterministic IDs for all text-primary-key tables to make cleanup targeted and repeatable.
- Capture generated numeric user IDs returned from the `user` inserts.

Avoid broad table truncation. The CI DB is ephemeral, but targeted cleanup keeps local runs safer.

### 6. Fixture design

Seed two users:

- owning user
- other user

Seed user settings for the owning user:

- custom values that differ from defaults, so assertions verify the function reads real settings.

Seed catalog rows:

- three `songs`
- three `recordings`
- two recordings visible
- one recording with `deleted_at` set

Seed one owned songset:

- `id`: deterministic, for example `smoke-songset-hot-pages`
- `user_id`: owning user
- `latest_render_job_id`: latest job
- `last_completed_render_job_id`: previous completed job
- `last_failed_render_job_id`: `null`

Seed three songset items:

- item at `position = 0`, visible recording
- item at `position = 1`, visible recording
- item at `position = 2`, deleted recording

The visible item `updated_at` values should be before the latest job `completed_at` so render state is `fresh`.

Seed render jobs:

- latest completed job
- previous completed job

Both jobs must belong to the owning user and songset. Set fields asserted by `mapRenderJobSummary`, including:

- `status`
- `created_at`
- `elapsed_seconds`
- `estimated_total_seconds`
- `template`
- `resolution`
- `audio_enabled`
- `video_enabled`
- `font_family`
- `font_size_preset`
- `include_title_card`
- `title_card_duration_seconds`
- `title_card_lines`
- artifact keys where useful

Seed lyric marks:

- two marks for the first visible recording for the owning user
- one mark for the second visible recording for the owning user
- one mark for the deleted recording for the owning user, to verify deleted recordings are excluded from hot-page counts
- one mark for a visible recording for the other user, to verify per-user filtering

Expected visible aggregate:

- item count: `2`
- duration: sum of the two visible recording durations
- marked lyric count: `3`
- song title order: visible item positions only

### 7. Smoke assertions

#### `getRenderPageData`

For the owning user:

- returns non-null data
- returns the seeded songset ID, name, and description
- returns visible song titles in `position` order
- excludes the deleted recording item from titles and duration
- sums only visible recording durations
- returns marked line count for owning user and visible recordings only
- returns custom user settings
- returns latest job mapped from `latest_render_job_id`
- returns previous completed job mapped from `last_completed_render_job_id`
- returns `renderState: "fresh"`

For the other user:

- returns `null`

#### `getSongsetEditorData`

For the owning user:

- returns non-null data
- returns visible items only
- preserves item order by `position`
- includes song detail for each visible item
- includes recording detail for each visible item
- excludes the deleted recording item
- returns per-item marked line counts for the owning user only
- returns visible duration sum
- returns `renderState: "fresh"`

For the other user:

- returns `null`

#### `listSongsetSummaries`

For the owning user:

- returns total `1` for the seeded fixture
- includes the seeded songset
- returns item count `2`
- returns visible duration sum
- returns `renderState: "fresh"`

For the other user:

- returns total `0`
- returns no rows

### 8. Drizzle migration handling

CI must run:

```bash
SOW_DATABASE_URL=postgresql://sow:sow@localhost:5432/sow_test?sslmode=disable pnpm exec drizzle-kit migrate
```

Before enabling this in CI, verify the committed migration set is journaled correctly.

Known issue to resolve during implementation:

- `webapp/drizzle/0013_page_load_hot_path_indexes.sql` exists and is tracked.
- `webapp/drizzle/meta/_journal.json` currently lists `0013_plain_starfox` as index `13`.
- If `drizzle-kit migrate` ignores `0013_page_load_hot_path_indexes.sql`, add a new journal entry with a unique next index and rename or otherwise reconcile the loose migration according to Drizzle's committed migration format.
- Do not bypass this by using `drizzle-kit push`.

### 9. CI workflow changes

Update:

- `.github/workflows/ci.yml`

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
- Keep existing steps:

```bash
pnpm lint
pnpm typecheck
pnpm test
```

- After existing tests, add migration and smoke-test steps:

```bash
SOW_DATABASE_URL=postgresql://sow:sow@localhost:5432/sow_test?sslmode=disable pnpm exec drizzle-kit migrate
SOW_DATABASE_URL=postgresql://sow:sow@localhost:5432/sow_test?sslmode=disable pnpm test:postgres-smoke
```

### 10. Workflow tests

Update:

- `webapp/src/test/deployment/workflows.test.ts`

Add assertions that the CI workflow:

- defines a Postgres service on the webapp job
- uses a pgvector-capable image
- defines the expected PostgreSQL environment variables
- runs `drizzle-kit migrate`
- passes `SOW_DATABASE_URL` to migration
- runs `pnpm test:postgres-smoke`
- passes `SOW_DATABASE_URL` to the smoke test

Keep existing workflow assertions intact.

### 11. Verification commands

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

The smoke command is expected to fail locally if no PostgreSQL server is running. In that case, CI remains the primary end-to-end verification path.

### 12. Repository maintenance

Because code files will be modified after this spec is written:

- Run `graphify update .` after implementation.
- Follow the repository completion rule:

```bash
git pull --rebase
git push
git status
```

The final status must show the branch up to date with origin unless a real external blocker prevents pushing.

