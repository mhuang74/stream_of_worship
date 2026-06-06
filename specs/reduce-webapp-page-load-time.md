# Reduce Webapp Page Load Time

## Summary

The songset list, songset editor, and render screens currently render client shells and then fetch their initial data after mount. The implementation should move first-load data to server-side page loaders, reduce query fan-out, and add indexes for the hot paths.

## Key Changes

- Convert `/songsets`, `/songsets/[id]`, and `/songsets/[id]/render` to Server Component entrypoints that authenticate, load initial data, and pass it to client interaction components.
- Keep existing API routes for mutations and compatibility, but share optimized DB helpers between pages and APIs where practical.
- Replace broad relational loads with targeted summary/detail helpers for list rows, editor data, and render-page data.
- Add Drizzle indexes for songset listing, songset item ordering/staleness checks, and render job lookups.
- Replace full-page navigation from the songset list with Next.js client navigation.
- Lazy-load secondary editor UI that is not needed for first paint.

## Measurement

- Add local-only timing logs for page-load DB helpers when `SOW_WEBAPP_TIMING=1`.
- Compare before/after elapsed time for `/songsets`, `/songsets/[id]`, and `/songsets/[id]/render`.
- Acceptance target: each screen has no initial client fetch waterfall and reaches meaningful content in under 1 second on a warm local dev server, excluding auth/database cold starts.

## Tests

- Update page/component tests for the server/client split.
- Add or update DB helper tests for render state, item count, duration, and marked-line count behavior.
- Add schema tests for the new indexes.
- Run `cd webapp && pnpm test`, `cd webapp && pnpm lint`, and `cd webapp && pnpm build`.

## Assumptions

- Page load lag means time to meaningful screen content, not render job processing time.
- Database migrations are in scope.
- Existing API JSON shapes should remain compatible.
