# Reduce Webapp Page Load Time Implementation Summary

Date: 2026-06-06
Branch: `reduce_page_load_time`
PR: https://github.com/mhuang74/stream_of_worship/pull/94
Commit: `88f36f1` (`Reduce webapp page load time`)

## Summary

Implemented the page-load optimization plan for the three slow webapp screens:

- Songset list: `/songsets`
- Songset edit: `/songsets/[id]`
- Render setup: `/songsets/[id]/render`

The main change is moving first-load data fetching from client-side `useEffect` calls into authenticated Server Component route entrypoints. The existing interactive behavior remains in client wrapper components, so mutations and UI state continue to use the existing API flows after initial render.

## Implementation Details

- Added server-loaded route entrypoints for the three target pages.
- Added client wrappers:
  - `SongsetsClient`
  - `SongsetEditorClient`
  - `RenderPageClient`
- Added optimized DB helpers in `webapp/src/lib/db/songsets.ts`:
  - `listSongsetSummaries`
  - `getSongsetEditorData`
  - `getRenderPageData`
- Updated `/api/songsets` and `/api/songsets/[id]` to use the optimized helpers for refresh/compatibility paths.
- Added Drizzle hot-path indexes and migration:
  - `idx_songsets_user_updated`
  - `idx_songset_items_songset_position`
  - `idx_songset_items_songset_updated`
  - `idx_render_jobs_songset_created`
  - `idx_render_jobs_status_updated`
- Lazy-loaded secondary first-paint UI such as share/browse dialogs.
- Added `SOW_WEBAPP_TIMING=1` timing logs around the optimized page-load helpers.
- Added implementation spec at `specs/reduce-webapp-page-load-time.md`.
- Ran `graphify update .`.

## Verification Completed

Passed:

```bash
pnpm --filter sow-webapp typecheck
pnpm --filter sow-webapp test
pnpm --filter sow-webapp lint
cd webapp && pnpm exec env-cmd -f /opt/sow/.env pnpm build
```

Test result:

- 82 test files passed
- 1353 tests passed
- 5 skipped
- 10 todo

Build notes:

- Plain `pnpm --filter sow-webapp build` failed in the restricted sandbox because Next.js needed network access for Google Fonts and then required `SOW_DATABASE_URL`.
- The build passed after allowing network access and running with `/opt/sow/.env`.
- Better Auth emitted local build warnings for missing/default auth env values, but the build completed.

## Next Steps: Performance Verification Through Log Trace

1. Apply the DB migration in the target environment:

```bash
cd webapp
npx drizzle-kit migrate
```

2. Start the webapp with timing logs enabled:

```bash
cd webapp
SOW_WEBAPP_TIMING=1 pnpm dev
```

If using the project env file:

```bash
cd webapp
SOW_WEBAPP_TIMING=1 pnpm exec env-cmd -f /opt/sow/.env pnpm dev
```

3. Open the three target screens and capture server logs:

- `/songsets`
- `/songsets/<songset-id>`
- `/songsets/<songset-id>/render`

4. Confirm logs include the page-load helper timings:

```text
[page-load] listSongsetSummaries <N>ms
[page-load] getSongsetEditorData <N>ms
[page-load] getRenderPageData <N>ms
```

5. For each screen, record:

- First navigation after server start
- Warm reload
- Client navigation from another page
- Songset size used for test, especially item count

6. Acceptance target:

- No initial client fetch waterfall for the target pages.
- Warm local page-load helper timings should normally stay below 1 second, excluding auth/database cold starts.
- Render page should not show sequential `/api/songsets/:id`, `/api/settings`, and `/api/render-jobs/:id` startup requests in the browser network trace.

7. If helper timings remain high, run database query inspection next:

- Use Neon/Postgres query logs or `EXPLAIN ANALYZE` for the helper queries.
- Confirm the new indexes are present.
- Compare query timing before/after migration.
- Check whether latency is dominated by DB cold start, auth session lookup, or the helper query itself.

## Residual Risks

- Existing older helpers `listSongsets` and `getSongset` remain for non-page-load paths such as duplication and legacy tests. They were not removed to keep the change scoped.
- The new aggregate helpers should be validated against production-scale songsets after the migration is applied.
- Build-time Better Auth env warnings should be addressed separately if they appear in deployment logs.
